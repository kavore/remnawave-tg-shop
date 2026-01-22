import json
import logging
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


class PlategaService:
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

        self.base_url = (settings.PLATEGA_BASE_URL or "https://app.platega.io").rstrip("/")
        self.merchant_id = settings.PLATEGA_MERCHANT_ID
        self.secret = settings.PLATEGA_SECRET
        self.payment_method = settings.PLATEGA_PAYMENT_METHOD
        self.return_url = settings.PLATEGA_RETURN_URL or f"https://t.me/{default_return_url}"
        self.failed_url = settings.PLATEGA_FAILED_URL or self.return_url

        self._timeout = ClientTimeout(total=20)
        self._session: Optional[ClientSession] = None
        self._auth_headers = {
            "X-MerchantId": self.merchant_id or "",
            "X-Secret": self.secret or "",
            "Content-Type": "application/json",
        }
        self.configured: bool = bool(
            settings.PLATEGA_ENABLED and self.merchant_id and self.secret
        )
        if not self.configured:
            logging.warning("PlategaService initialized but not fully configured. Payments disabled.")

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def create_transaction(
        self,
        *,
        payment_db_id: int,
        user_id: int,
        months: int,
        amount: float,
        currency: Optional[str],
        description: str,
        payload: Optional[str] = None,
        promo_code_service=None,
        session=None,
    ) -> Tuple[bool, Dict[str, Any]]:
        if not self.configured:
            logging.error("PlategaService is not configured. Cannot create transaction.")
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
                original_amount = amount / (1 - discount_pct / 100)
                discount_amount = original_amount - amount
                promo_code_id = active_discount.promo_code_id
                logging.info(
                    f"Recording {discount_pct}% discount for Platega payment: "
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
                        f"Platega: failed to update discount metadata for payment {payment_db_id}: {e_update}"
                    )

        http_session = await self._get_session()
        url = f"{self.base_url}/transaction/process"
        currency_code = (currency or self.settings.DEFAULT_CURRENCY_SYMBOL or "RUB").upper()

        body: Dict[str, Any] = {
            "paymentMethod": int(self.payment_method),
            "paymentDetails": {"amount": float(amount), "currency": currency_code},
            "description": description,
            "return": self.return_url,
            "failedUrl": self.failed_url,
            "payload": payload,
        }

        # Remove optional keys with falsy values to avoid validation errors
        clean_body = {k: v for k, v in body.items() if v not in (None, "")}

        try:
            async with http_session.post(url, json=clean_body, headers=self._auth_headers) as response:
                response_text = await response.text()
                try:
                    response_data = json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    logging.error("Platega create_transaction: invalid JSON response: %s", response_text)
                    return False, {
                        "status": response.status,
                        "message": "invalid_json",
                        "raw": response_text,
                    }

                if response.status != 200:
                    logging.error(
                        "Platega create_transaction: API returned error (status=%s, body=%s)",
                        response.status,
                        response_data,
                    )
                    return False, {"status": response.status, "message": response_data}

                return True, response_data
        except Exception as exc:
            logging.error("Platega create_transaction: request failed: %s", exc, exc_info=True)
            return False, {"message": str(exc)}

    async def webhook_route(self, request: web.Request) -> web.Response:
        if not self.configured:
            return web.Response(status=503, text="platega_disabled")

        try:
            data = await request.json()
        except Exception as exc:
            logging.error("Platega webhook: failed to parse JSON: %s", exc)
            return web.Response(status=400, text="bad_request")

        header_merchant = request.headers.get("X-MerchantId")
        header_secret = request.headers.get("X-Secret")
        if header_merchant != self.merchant_id or header_secret != self.secret:
            logging.error("Platega webhook: invalid auth headers")
            return web.Response(status=403, text="forbidden")

        transaction_id = str(data.get("id") or data.get("transactionId") or "").strip()
        status = str(data.get("status") or "").upper()
        amount_raw = data.get("amount")
        currency = data.get("currency") or self.settings.DEFAULT_CURRENCY_SYMBOL or "RUB"

        if not transaction_id or not status:
            logging.error("Platega webhook: missing transaction id or status in payload: %s", data)
            return web.Response(status=400, text="missing_fields")

        async with self.async_session_factory() as session:
            payment = await payment_dal.get_payment_by_provider_payment_id(session, transaction_id)
            if not payment:
                logging.error("Platega webhook: payment not found for transaction %s", transaction_id)
                return web.Response(status=404, text="payment_not_found")

            if payment.status == "succeeded" and status == "CONFIRMED":
                return web.Response(text="ok")

            payment_months = payment.subscription_duration_months or 1
            sale_mode = "traffic" if self.settings.traffic_sale_mode else "subscription"

            if status == "CONFIRMED":
                if amount_raw is not None:
                    try:
                        incoming_amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        expected_amount = Decimal(str(payment.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if incoming_amount != expected_amount:
                            logging.warning(
                                "Platega webhook: amount mismatch for payment %s (expected %s, got %s)",
                                payment.payment_id,
                                expected_amount,
                                incoming_amount,
                            )
                    except Exception as exc:
                        logging.warning("Platega webhook: failed to compare amounts for %s: %s", payment.payment_id, exc)

                try:
                    await payment_dal.update_provider_payment_and_status(
                        session,
                        payment.payment_id,
                        transaction_id,
                        "succeeded",
                    )

                    activation = await self.subscription_service.activate_subscription(
                        session,
                        payment.user_id,
                        int(payment_months) if sale_mode != "traffic" else 0,
                        float(payment.amount),
                        payment.payment_id,
                        promo_code_id_from_payment=payment.promo_code_id,
                        provider="platega",
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
                    logging.error("Platega webhook: failed to process payment %s: %s", transaction_id, exc, exc_info=True)
                    return web.Response(status=500, text="processing_error")

                db_user = await user_dal.get_user_by_id(session, payment.user_id)
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
                    logging.error("Platega webhook: failed to notify user %s: %s", payment.user_id, exc)

                try:
                    notification_service = NotificationService(self.bot, self.settings, self.i18n)
                    await notification_service.notify_payment_received(
                        user_id=payment.user_id,
                        amount=float(payment.amount),
                        currency=currency,
                        months=int(payment_months) if sale_mode != "traffic" else 0,
                        traffic_gb=payment_months if sale_mode == "traffic" else None,
                        payment_provider="platega",
                        username=db_user.username if db_user else None,
                    )
                except Exception as exc:
                    logging.error("Platega webhook: failed to notify admins: %s", exc)

                return web.Response(text="ok")

            if status in {"CANCELED", "CANCELLED", "CHARGEBACKED"}:
                try:
                    await payment_dal.update_provider_payment_and_status(
                        session,
                        payment.payment_id,
                        transaction_id,
                        "canceled",
                    )
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    logging.error("Platega webhook: failed to cancel payment %s: %s", transaction_id, exc)
                    return web.Response(status=500, text="processing_error")

                db_user = await user_dal.get_user_by_id(session, payment.user_id)
                lang = db_user.language_code if db_user and db_user.language_code else self.settings.DEFAULT_LANGUAGE
                _ = lambda k, **kw: self.i18n.gettext(lang, k, **kw) if self.i18n else k
                try:
                    await self.bot.send_message(payment.user_id, _("payment_failed"))
                except Exception:
                    pass
                return web.Response(text="ok_canceled")

            logging.warning("Platega webhook: unhandled status '%s' for transaction %s", status, transaction_id)
            return web.Response(status=202, text="status_ignored")


async def platega_webhook_route(request: web.Request) -> web.Response:
    service: PlategaService = request.app["platega_service"]
    return await service.webhook_route(request)
