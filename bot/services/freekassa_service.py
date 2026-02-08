import asyncio
from datetime import datetime
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, Tuple

from aiohttp import ClientSession, ClientTimeout, web
from aiogram import Bot
from sqlalchemy.orm import sessionmaker

from config.settings import Settings
from bot.middlewares.i18n import JsonI18n
from bot.services.subscription_service import SubscriptionService
from bot.services.referral_service import ReferralService
from bot.keyboards.inline.user_keyboards import get_connect_and_main_keyboard
from bot.services.notification_service import NotificationService
from db.dal import payment_dal, user_dal
from bot.utils.text_sanitizer import sanitize_display_name, username_for_display
from bot.utils.config_link import prepare_config_links


class FreeKassaService:
    def __init__(
        self,
        *,
        bot: Bot,
        settings: Settings,
        i18n: JsonI18n,
        async_session_factory: sessionmaker,
        subscription_service: SubscriptionService,
        referral_service: ReferralService,
    ):
        self.bot = bot
        self.settings = settings
        self.i18n = i18n
        self.async_session_factory = async_session_factory
        self.subscription_service = subscription_service
        self.referral_service = referral_service

        self.shop_id: Optional[str] = settings.FREEKASSA_MERCHANT_ID
        self.api_key: Optional[str] = settings.FREEKASSA_API_KEY
        self.second_secret: Optional[str] = settings.FREEKASSA_SECOND_SECRET
        self.default_currency: str = (settings.DEFAULT_CURRENCY_SYMBOL or "RUB").upper()
        self.server_ip: Optional[str] = settings.FREEKASSA_PAYMENT_IP
        self.payment_method_id: Optional[int] = settings.FREEKASSA_PAYMENT_METHOD_ID

        self.api_base_url: str = "https://api.fk.life/v1"
        self._timeout = ClientTimeout(total=15)
        self._session: Optional[ClientSession] = None
        self._nonce_lock = asyncio.Lock()
        self._last_nonce = int(time.time() * 1000)

        self.configured: bool = bool(settings.FREEKASSA_ENABLED and self.shop_id and self.api_key)
        if not self.configured:
            logging.warning("FreeKassaService initialized but not fully configured. Payments disabled.")
        if settings.FREEKASSA_ENABLED and not self.server_ip:
            logging.warning("FreeKassaService: FREEKASSA_PAYMENT_IP is not set. Requests may be rejected by the provider.")

    @staticmethod
    def _format_amount(amount: float) -> str:
        """Format amount for payloads and signature with two decimal places."""
        quantized = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{quantized:.2f}"

    async def create_order(
        self,
        *,
        payment_db_id: int,
        user_id: int,
        months: int,
        amount: float,
        currency: Optional[str],
        email: Optional[str] = None,
        ip_address: Optional[str] = None,
        payment_method_id: Optional[int] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        promo_code_service=None,
        session=None,
    ) -> Tuple[bool, Dict[str, Any]]:
        if not self.configured:
            logging.error("FreeKassaService is not configured. Cannot create order.")
            return False, {"message": "service_not_configured"}

        # Check for active discount to save metadata (price already discounted from previous step)
        original_amount = None
        discount_amount = None
        promo_code_id = None

        if promo_code_service and session:
            from db.dal import active_discount_dal
            active_discount = await active_discount_dal.get_active_discount(session, user_id)
            if active_discount:
                # Price is already discounted, calculate original price backwards
                discount_pct = active_discount.discount_percentage
                promo_code_id = active_discount.promo_code_id
                denominator = 1 - discount_pct / 100
                if denominator <= 0:
                    traffic_mode = bool(getattr(self.settings, "traffic_sale_mode", False))
                    price_source = (
                        getattr(self.settings, "traffic_packages", {}) or {}
                        if traffic_mode
                        else (self.settings.subscription_options or {})
                    )
                    fallback_original = price_source.get(months)
                    if fallback_original is not None:
                        original_amount = fallback_original
                        discount_amount = original_amount - amount
                        logging.info(
                            f"Recording {discount_pct}% discount for FreeKassa payment: "
                            f"original {original_amount:.2f} -> final {amount}"
                        )
                    else:
                        logging.warning(
                            "FreeKassa discount %s%% has invalid denominator and no fallback price for months=%s.",
                            discount_pct,
                            months,
                        )
                else:
                    original_amount = amount / denominator
                    discount_amount = original_amount - amount
                    logging.info(
                        f"Recording {discount_pct}% discount for FreeKassa payment: "
                        f"original {original_amount:.2f} -> final {amount}"
                    )

                # Update payment record with discount metadata
                try:
                    await payment_dal.update_payment_discount_info(
                        session,
                        payment_db_id,
                        original_amount,
                        discount_amount,
                        promo_code_id,
                    )
                    await session.commit()
                except Exception as e_update:
                    logging.warning(
                        f"FreeKassa: failed to update discount metadata for payment {payment_db_id}: {e_update}"
                    )

        ip_address = ip_address or self.server_ip
        if not ip_address:
            logging.error("FreeKassaService: payment IP is required but not configured.")
            return False, {"message": "missing_ip"}

        email = email or f"{user_id}@telegram.org"
        amount_str = self._format_amount(amount)
        currency_code = (currency or self.default_currency or "RUB").upper()

        payload: Dict[str, Any] = {
            "shopId": int(self.shop_id),
            "nonce": await self._generate_nonce(),
            "paymentId": str(payment_db_id),
            "i": int(payment_method_id),
            "amount": amount_str,
            "currency": currency_code,
            "email": email,
            "ip": ip_address,
            "us_user_id": str(user_id),
            "us_months": str(months),
            "us_payment_db_id": str(payment_db_id),
        }

        if extra_params:
            for key, value in extra_params.items():
                if value is None:
                    continue
                payload[key] = value

        payload["signature"] = self._sign_payload(payload)

        session = await self._get_session()
        url = f"{self.api_base_url}/orders/create"
        try:
            async with session.post(url, json=payload) as response:
                response_text = await response.text()
                try:
                    response_data = json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    logging.error("FreeKassa create_order: failed to decode JSON: %s", response_text)
                    return False, {"status": response.status, "message": "invalid_json", "raw": response_text}

                if response.status != 200 or response_data.get("type") != "success":
                    logging.error(
                        "FreeKassa create_order: API returned error (status=%s, body=%s)",
                        response.status,
                        response_data,
                    )
                    return False, {"status": response.status, "message": response_data}

                return True, response_data
        except Exception as exc:
            logging.error("FreeKassa create_order: request failed: %s", exc, exc_info=True)
            return False, {"message": str(exc)}

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self._timeout)
        return self._session

    async def _generate_nonce(self) -> int:
        async with self._nonce_lock:
            candidate = int(time.time() * 1000)
            if candidate <= self._last_nonce:
                candidate = self._last_nonce + 1
            self._last_nonce = candidate
            return candidate

    def _sign_payload(self, payload: Dict[str, Any]) -> str:
        if not self.api_key:
            raise RuntimeError("FreeKassa API key is not configured.")
        items = [
            (key, value)
            for key, value in payload.items()
            if key != "signature" and value is not None
        ]
        items.sort(key=lambda pair: pair[0])
        message = "|".join(str(value) for _, value in items)
        return hmac.new(self.api_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _validate_signature(
        self,
        merchant_order_id: str,
        amount: str,
        provided_signature: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not provided_signature:
            return False

        if self.shop_id and self.second_secret:
            signature_source = f"{self.shop_id}:{amount}:{self.second_secret}:{merchant_order_id}"
            expected_signature = hashlib.md5(
                signature_source.encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()
            if expected_signature.lower() == provided_signature.lower():
                return True

        if self.api_key and payload:
            items = [
                (key, value)
                for key, value in payload.items()
                if key not in {"signature", "SIGN"} and value is not None
            ]
            items.sort(key=lambda pair: pair[0])
            message = "|".join(str(value) for _, value in items)
            alt_signature = hmac.new(self.api_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
            if alt_signature.lower() == provided_signature.lower():
                return True

        return False

    async def webhook_route(self, request: web.Request) -> web.Response:
        if not self.configured:
            return web.Response(status=503, text="freekassa_disabled")

        try:
            data = await request.post()
        except Exception as e:
            logging.error(f"FreeKassa webhook: failed to read POST data: {e}")
            return web.Response(status=400, text="bad_request")

        payload_dict: Dict[str, Any]
        if data:
            payload_dict = {str(k): v for k, v in data.items()}
        else:
            try:
                json_payload = await request.json()
                payload_dict = {str(k): v for k, v in json_payload.items()} if isinstance(json_payload, dict) else {}
                data = json_payload
            except Exception:
                payload_dict = {}
                data = {}

        def _get(key: str, default: Optional[str] = None) -> Optional[str]:
            if isinstance(data, dict):
                return data.get(key) or data.get(key.lower()) or default
            return payload_dict.get(key) or payload_dict.get(key.lower()) or default

        merchant_id = _get("MERCHANT_ID")
        if merchant_id != self.shop_id:
            logging.error(f"FreeKassa webhook: merchant mismatch (got {merchant_id})")
            return web.Response(status=403, text="merchant_mismatch")

        signature = _get("SIGN") or _get("signature")
        if not signature:
            logging.error("FreeKassa webhook: missing signature")
            return web.Response(status=400, text="missing_signature")

        order_id_str = _get("MERCHANT_ORDER_ID") or _get("ORDER_ID") or _get("o")
        amount_str = _get("AMOUNT") or _get("OA") or _get("amount")
        provider_payment_id = _get("intid") or _get("payment_id") or _get("transaction_id")

        if not order_id_str or not amount_str:
            logging.error("FreeKassa webhook: missing order_id or amount")
            return web.Response(status=400, text="missing_data")

        if not self._validate_signature(order_id_str, amount_str, signature, payload_dict):
            logging.error("FreeKassa webhook: invalid signature")
            return web.Response(status=403, text="invalid_signature")

        try:
            payment_db_id = int(order_id_str)
        except (TypeError, ValueError):
            logging.error(f"FreeKassa webhook: invalid order_id value '{order_id_str}'")
            return web.Response(status=400, text="invalid_order_id")

        async with self.async_session_factory() as session:
            payment = await payment_dal.get_payment_by_db_id(session, payment_db_id)
            if not payment:
                logging.error(f"FreeKassa webhook: payment {payment_db_id} not found")
                return web.Response(status=404, text="payment_not_found")

            if payment.currency and str(payment.currency).upper() != str(self.default_currency or payment.currency).upper():
                # FreeKassa sends amount without currency; ensure DB currency matches configured service currency
                logging.error(
                    "FreeKassa webhook: currency mismatch for payment %s (db=%s, expected=%s)",
                    payment_db_id,
                    payment.currency,
                    self.default_currency,
                )
                return web.Response(status=400, text="currency_mismatch")

            if payment.status == "succeeded":
                logging.info(f"FreeKassa webhook: payment {payment_db_id} already succeeded")
                return web.Response(text="YES")

            # Optional amount verification
            try:
                amount_decimal = Decimal(amount_str)
                expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if amount_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != expected_amount:
                    logging.error(
                        f"FreeKassa webhook: amount mismatch for payment {payment_db_id} "
                        f"(expected {expected_amount}, got {amount_decimal})"
                    )
                    return web.Response(status=400, text="amount_mismatch")
            except Exception as e:
                logging.error(f"FreeKassa webhook: failed to compare amount for payment {payment_db_id}: {e}")
                return web.Response(status=400, text="amount_validation_error")

            activation = None
            referral_bonus = None
            try:
                provider_id = str(provider_payment_id or f"freekassa:{order_id_str}")
                marked = await payment_dal.mark_provider_payment_succeeded_once(
                    session=session,
                    payment_db_id=payment.payment_id,
                    provider_payment_id=provider_id,
                )
                if not marked:
                    logging.info(
                        "FreeKassa webhook: payment %s already processed atomically",
                        payment.payment_id,
                    )
                    return web.Response(text="YES")

                months = payment.subscription_duration_months or 1
                sale_mode = "traffic" if self.settings.traffic_sale_mode else "subscription"

                activation = await self.subscription_service.activate_subscription(
                    session,
                    payment.user_id,
                    int(months) if sale_mode != "traffic" else 0,
                    float(payment.amount),
                    payment.payment_id,
                    promo_code_id_from_payment=payment.promo_code_id,
                    provider="freekassa",
                    sale_mode=sale_mode,
                    traffic_gb=months if sale_mode == "traffic" else None,
                )

                referral_bonus = None
                if sale_mode != "traffic":
                    referral_bonus = await self.referral_service.apply_referral_bonuses_for_payment(
                        session,
                        payment.user_id,
                        int(months),
                        current_payment_db_id=payment.payment_id,
                        skip_if_active_before_payment=False,
                    )

                await session.commit()
            except Exception as e:
                await session.rollback()
                logging.error(f"FreeKassa webhook: failed to process payment {payment_db_id}: {e}", exc_info=True)
                return web.Response(status=500, text="processing_error")

            db_user = payment.user or await user_dal.get_user_by_id(session, payment.user_id)
            lang = db_user.language_code if db_user and db_user.language_code else self.settings.DEFAULT_LANGUAGE
            _ = lambda k, **kw: self.i18n.gettext(lang, k, **kw) if self.i18n else k

            raw_config_link = activation.get("subscription_url") if activation else None
            config_link_display, connect_button_url = await prepare_config_links(self.settings, raw_config_link)
            config_link_text = config_link_display or _("config_link_not_available")
            final_end = activation.get("end_date") if activation else None
            months = payment.subscription_duration_months or 1
            sale_mode = "traffic" if self.settings.traffic_sale_mode else "subscription"

            applied_days = 0
            if referral_bonus and referral_bonus.get("referee_new_end_date"):
                final_end = referral_bonus["referee_new_end_date"]
                applied_days = referral_bonus.get("referee_bonus_applied_days", 0)

            if not final_end and activation and activation.get("end_date"):
                final_end = activation["end_date"]

            if final_end:
                end_date_str = final_end.strftime("%Y-%m-%d")
            else:
                end_date_str = _("config_link_not_available")

            traffic_label = str(int(months)) if float(months).is_integer() else f"{months:g}"

            if sale_mode == "traffic":
                text = _("payment_successful_traffic_full",
                         traffic_gb=traffic_label,
                         end_date=end_date_str if final_end else "",
                         config_link=config_link_text)
            elif applied_days:
                inviter_name_display = _("friend_placeholder")
                if db_user and db_user.referred_by_id:
                    inviter = await user_dal.get_user_by_id(session, db_user.referred_by_id)
                    if inviter:
                        safe_name = sanitize_display_name(inviter.first_name) if inviter.first_name else None
                        if safe_name:
                            inviter_name_display = safe_name
                        elif inviter.username:
                            inviter_name_display = username_for_display(inviter.username, with_at=False)
                text = _(
                    "payment_successful_with_referral_bonus_full",
                    months=months,
                    base_end_date=activation["end_date"].strftime("%Y-%m-%d") if activation and activation.get("end_date") else end_date_str,
                    bonus_days=applied_days,
                    final_end_date=end_date_str,
                    inviter_name=inviter_name_display,
                    config_link=config_link_text,
                )
            else:
                text = _(
                    "payment_successful_full",
                    months=months,
                    end_date=end_date_str,
                    config_link=config_link_text,
                )
            if provider_payment_id:
                order_info_text = _(
                    "free_kassa_order_full",
                    order_id=provider_payment_id,
                    date=datetime.now().strftime("%Y-%m-%d"),
                )
                text = f"{order_info_text}\n{text}"

            markup = get_connect_and_main_keyboard(
                lang,
                self.i18n,
                self.settings,
                config_link_display,
                connect_button_url=connect_button_url,
                preserve_message=True,
            )
            try:
                await self.bot.send_message(
                    payment.user_id,
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logging.error(f"FreeKassa notification: failed to send message to user {payment.user_id}: {e}")

            try:
                notification_service = NotificationService(self.bot, self.settings, self.i18n)
                await notification_service.notify_payment_received(
                    user_id=payment.user_id,
                    amount=float(payment.amount),
                    currency=self.default_currency,
                    months=int(months) if sale_mode != "traffic" else 0,
                    traffic_gb=months if sale_mode == "traffic" else None,
                    payment_provider="freekassa",
                    username=db_user.username if db_user else None,
                )
            except Exception as e:
                logging.error(f"FreeKassa notification: failed to notify admins: {e}")

        return web.Response(text="YES")


async def freekassa_webhook_route(request: web.Request) -> web.Response:
    service: FreeKassaService = request.app["freekassa_service"]
    return await service.webhook_route(request)
