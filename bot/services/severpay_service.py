import json
import logging
import secrets
import hmac
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, Tuple

from aiohttp import ClientSession, ClientTimeout, web
from aiogram import Bot
from sqlalchemy.orm import sessionmaker

from config.settings import Settings
from bot.middlewares.i18n import JsonI18n
from bot.services.subscription_service import SubscriptionService
from bot.services.referral_service import ReferralService
from bot.services.notification_service import NotificationService
from bot.keyboards.inline.user_keyboards import get_connect_and_main_keyboard
from db.dal import payment_dal, user_dal
from bot.utils.text_sanitizer import sanitize_display_name, username_for_display
from bot.utils.config_link import prepare_config_links


class SeverPayService:
    def __init__(
        self,
        *,
        bot: Bot,
        settings: Settings,
        i18n: JsonI18n,
        async_session_factory: sessionmaker,
        subscription_service: SubscriptionService,
        referral_service: ReferralService,
        default_return_url: str,
    ):
        self.bot = bot
        self.settings = settings
        self.i18n = i18n
        self.async_session_factory = async_session_factory
        self.subscription_service = subscription_service
        self.referral_service = referral_service

        self.base_url = (settings.SEVERPAY_BASE_URL or "https://severpay.io/api/merchant").rstrip("/")
        self.mid = settings.SEVERPAY_MID
        self.token = settings.SEVERPAY_TOKEN or ""
        self.return_url = settings.SEVERPAY_RETURN_URL or f"https://t.me/{default_return_url}"
        self.lifetime_minutes = settings.SEVERPAY_LIFETIME_MINUTES

        self._timeout = ClientTimeout(total=15)
        self._session: Optional[ClientSession] = None

        self.configured: bool = bool(settings.SEVERPAY_ENABLED and self.mid and self.token)
        if not self.configured:
            logging.warning("SeverPayService initialized but not fully configured. Payments disabled.")

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _format_amount(amount: float) -> str:
        quantized = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{quantized:.2f}"

    def _sign_payload(self, payload: Dict[str, Any]) -> str:
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hmac.new(self.token.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def _build_signed_body(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "mid": self.mid,
            "salt": secrets.token_hex(8),
        }
        body.update(extra)
        sorted_body = dict(sorted(body.items()))
        sorted_body["sign"] = self._sign_payload(sorted_body)
        return sorted_body

    def _validate_signature(self, payload: Dict[str, Any]) -> bool:
        provided_sign = str(payload.get("sign") or "")
        if not provided_sign or not self.token:
            return False
        # Webhook signatures are calculated on the original payload order (without sorting).
        data = {k: v for k, v in payload.items() if k != "sign"}
        expected_sign = self._sign_payload(data)
        return hmac.compare_digest(provided_sign, expected_sign)

    async def create_payment(
        self,
        *,
        payment_db_id: int,
        user_id: int,
        months: int,
        amount: float,
        currency: Optional[str],
        description: str,
        promo_code_service=None,
        session=None,
    ) -> Tuple[bool, Dict[str, Any]]:
        if not self.configured:
            logging.error("SeverPayService is not configured. Cannot create payment.")
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
                            f"Recording {discount_pct}% discount for SeverPay payment: "
                            f"original {original_amount:.2f} -> final {amount}"
                        )
                    else:
                        logging.warning(
                            "SeverPay discount %s%% has invalid denominator and no fallback price for months=%s.",
                            discount_pct,
                            months,
                        )
                else:
                    original_amount = amount / denominator
                    discount_amount = original_amount - amount
                    logging.info(
                        f"Recording {discount_pct}% discount for SeverPay payment: "
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
                        f"SeverPay: failed to update discount metadata for payment {payment_db_id}: {e_update}"
                    )

        http_session = await self._get_session()
        url = f"{self.base_url}/payin/create"
        currency_code = (currency or self.settings.DEFAULT_CURRENCY_SYMBOL or "RUB").upper()
        amount_str = self._format_amount(amount)

        body = {
            "order_id": str(payment_db_id),
            "amount": amount_str,
            "currency": currency_code,
            "client_email": f"{user_id}@telegram.org",
            "client_id": str(user_id),
            "url_return": self.return_url,
        }

        if self.lifetime_minutes:
            body["lifetime"] = int(self.lifetime_minutes)

        signed_body = self._build_signed_body(body)

        try:
            async with http_session.post(url, json=signed_body) as response:
                response_text = await response.text()
                try:
                    response_data = json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    logging.error("SeverPay create_payment: invalid JSON response: %s", response_text)
                    return False, {"status": response.status, "message": "invalid_json", "raw": response_text}

                if response.status != 200 or not response_data.get("status"):
                    logging.error(
                        "SeverPay create_payment: API returned error (status=%s, body=%s)",
                        response.status,
                        response_data,
                    )
                    return False, {"status": response.status, "message": response_data}

                return True, response_data.get("data") or response_data
        except Exception as exc:
            logging.error("SeverPay create_payment: request failed: %s", exc, exc_info=True)
            return False, {"message": str(exc)}

    async def webhook_route(self, request: web.Request) -> web.Response:
        if not self.configured:
            return web.json_response({"status": False, "msg": "severpay_disabled"}, status=503)

        try:
            payload = await request.json()
        except Exception as exc:
            logging.error("SeverPay webhook: failed to parse JSON: %s", exc)
            return web.json_response({"status": False, "msg": "bad_request"}, status=400)

        if not isinstance(payload, dict) or not self._validate_signature(payload):
            logging.error("SeverPay webhook: invalid signature or payload.")
            return web.json_response({"status": False, "msg": "invalid_signature"}, status=403)

        event_type = str(payload.get("type") or "").lower()
        data = payload.get("data") or {}

        if event_type != "payin" or not isinstance(data, dict):
            logging.warning("SeverPay webhook: unsupported event type '%s'", event_type)
            return web.json_response({"status": True})

        provider_payment_id = str(data.get("id") or data.get("uid") or "")
        order_id_raw = data.get("order_id")
        status = str(data.get("status") or "").lower()
        amount_raw = data.get("amount")
        currency_raw = data.get("currency")

        payment_db_id: Optional[int] = None
        try:
            if isinstance(order_id_raw, int):
                payment_db_id = order_id_raw
            elif isinstance(order_id_raw, str) and order_id_raw.isdigit():
                payment_db_id = int(order_id_raw)
        except Exception:
            payment_db_id = None

        async with self.async_session_factory() as session:
            payment = None
            if payment_db_id is not None:
                payment = await payment_dal.get_payment_by_db_id(session, payment_db_id)
            if not payment and provider_payment_id:
                payment = await payment_dal.get_payment_by_provider_payment_id(session, provider_payment_id)

            if not payment:
                logging.error("SeverPay webhook: payment not found (order_id=%s, provider_id=%s)", order_id_raw, provider_payment_id)
                return web.json_response({"status": False, "msg": "payment_not_found"}, status=404)

            if payment.status == "succeeded" and status == "success":
                logging.info("SeverPay webhook: payment %s already succeeded", payment.payment_id)
                return web.json_response({"status": True})

            if status == "success" and amount_raw is not None:
                try:
                    incoming_amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if incoming_amount != expected_amount:
                        logging.error(
                            "SeverPay webhook: amount mismatch for payment %s (expected %s, got %s)",
                            payment.payment_id,
                            expected_amount,
                            incoming_amount,
                        )
                        return web.json_response({"status": False, "msg": "amount_mismatch"}, status=400)
                except Exception as exc:
                    logging.error(
                        "SeverPay webhook: failed to compare amounts for payment %s: %s",
                        payment.payment_id,
                        exc,
                    )
                    return web.json_response({"status": False, "msg": "amount_validation_error"}, status=400)

                if currency_raw:
                    provider_currency = str(currency_raw).upper()
                    expected_currency = str(payment.currency or "").upper()
                    if expected_currency and provider_currency != expected_currency:
                        logging.error(
                            "SeverPay webhook: currency mismatch for payment %s (expected %s, got %s)",
                            payment.payment_id,
                            expected_currency,
                            provider_currency,
                        )
                        return web.json_response({"status": False, "msg": "currency_mismatch"}, status=400)

            payment_months = payment.subscription_duration_months or 1
            sale_mode = "traffic" if self.settings.traffic_sale_mode else "subscription"
            if status == "success":
                try:
                    provider_id = provider_payment_id or str(payment.payment_id)
                    marked = await payment_dal.mark_provider_payment_succeeded_once(
                        session,
                        payment.payment_id,
                        provider_id,
                    )
                    if not marked:
                        logging.info(
                            "SeverPay webhook: payment %s already processed atomically",
                            payment.payment_id,
                        )
                        return web.json_response({"status": True})

                    activation = await self.subscription_service.activate_subscription(
                        session,
                        payment.user_id,
                        int(payment_months) if sale_mode != "traffic" else 0,
                        float(payment.amount),
                        payment.payment_id,
                        promo_code_id_from_payment=payment.promo_code_id,
                        provider="severpay",
                        sale_mode=sale_mode,
                        traffic_gb=payment_months if sale_mode == "traffic" else None,
                    )

                    referral_bonus = None
                    if sale_mode != "traffic":
                        referral_bonus = await self.referral_service.apply_referral_bonuses_for_payment(
                            session,
                            payment.user_id,
                            int(payment_months),
                            current_payment_db_id=payment.payment_id,
                            skip_if_active_before_payment=False,
                        )

                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    logging.error("SeverPay webhook: failed to process payment %s: %s", provider_payment_id, exc, exc_info=True)
                    return web.json_response({"status": False, "msg": "processing_error"}, status=500)

                db_user = payment.user or await user_dal.get_user_by_id(session, payment.user_id)
                lang = db_user.language_code if db_user and db_user.language_code else self.settings.DEFAULT_LANGUAGE
                _ = lambda k, **kw: self.i18n.gettext(lang, k, **kw) if self.i18n else k

                raw_config_link = activation.get("subscription_url") if activation else None
                config_link_display, connect_button_url = await prepare_config_links(self.settings, raw_config_link)
                config_link_text = config_link_display or _("config_link_not_available")
                final_end = activation.get("end_date") if activation else None
                applied_days = 0
                applied_promo_days = activation.get("applied_promo_bonus_days", 0) if activation else 0

                if referral_bonus and referral_bonus.get("referee_new_end_date"):
                    final_end = referral_bonus["referee_new_end_date"]
                    applied_days = referral_bonus.get("referee_bonus_applied_days", 0)

                traffic_label = str(int(payment_months)) if float(payment_months).is_integer() else f"{payment_months:g}"

                if sale_mode == "traffic":
                    text = _(
                        "payment_successful_traffic_full",
                        traffic_gb=traffic_label,
                        end_date=final_end.strftime("%Y-%m-%d") if final_end else "",
                        config_link=config_link_text,
                    )
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
                        months=payment_months,
                        base_end_date=activation["end_date"].strftime("%Y-%m-%d") if activation and activation.get("end_date") else final_end.strftime("%Y-%m-%d") if final_end else "",
                        bonus_days=applied_days,
                        final_end_date=final_end.strftime("%Y-%m-%d") if final_end else "",
                        inviter_name=inviter_name_display,
                        config_link=config_link_text,
                    )
                elif applied_promo_days and final_end:
                    text = _(
                        "payment_successful_with_promo_full",
                        months=payment_months,
                        bonus_days=applied_promo_days,
                        end_date=final_end.strftime("%Y-%m-%d"),
                        config_link=config_link_text,
                    )
                else:
                    text = _(
                        "payment_successful_full",
                        months=payment_months,
                        end_date=final_end.strftime("%Y-%m-%d") if final_end else "",
                        config_link=config_link_text,
                    )

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
                except Exception as exc:
                    logging.error("SeverPay webhook: failed to notify user %s: %s", payment.user_id, exc)

                try:
                    notification_service = NotificationService(self.bot, self.settings, self.i18n)
                    await notification_service.notify_payment_received(
                        user_id=payment.user_id,
                        amount=float(payment.amount),
                        currency=payment.currency,
                        months=int(payment_months) if sale_mode != "traffic" else 0,
                        traffic_gb=payment_months if sale_mode == "traffic" else None,
                        payment_provider="severpay",
                        username=db_user.username if db_user else None,
                    )
                except Exception as exc:
                    logging.error("SeverPay webhook: failed to notify admins: %s", exc)

                return web.json_response({"status": True})

            if status in {"fail", "decline"}:
                try:
                    await payment_dal.update_provider_payment_and_status(
                        session,
                        payment.payment_id,
                        provider_payment_id or str(payment.payment_id),
                        "failed",
                    )
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    logging.error("SeverPay webhook: failed to mark payment %s as failed: %s", provider_payment_id, exc)
                    return web.json_response({"status": False, "msg": "processing_error"}, status=500)

                db_user = payment.user or await user_dal.get_user_by_id(session, payment.user_id)
                lang = db_user.language_code if db_user and db_user.language_code else self.settings.DEFAULT_LANGUAGE
                _ = lambda k, **kw: self.i18n.gettext(lang, k, **kw) if self.i18n else k
                try:
                    await self.bot.send_message(payment.user_id, _("payment_failed"))
                except Exception as exc:
                    logging.debug("SeverPay webhook: failed to send cancellation message to user %s: %s", payment.user_id, exc)
                return web.json_response({"status": True})

            if status in {"process", "new"}:
                try:
                    await payment_dal.update_provider_payment_and_status(
                        session,
                        payment.payment_id,
                        provider_payment_id or str(payment.payment_id),
                        "pending_severpay",
                    )
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    logging.error("SeverPay webhook: failed to update pending status for %s: %s", provider_payment_id, exc)
                return web.json_response({"status": True})

            logging.warning("SeverPay webhook: unhandled status '%s' for payment %s", status, provider_payment_id)
            return web.json_response({"status": True})


async def severpay_webhook_route(request: web.Request) -> web.Response:
    service: SeverPayService = request.app["severpay_service"]
    return await service.webhook_route(request)
