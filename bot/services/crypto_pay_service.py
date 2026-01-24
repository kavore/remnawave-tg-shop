import logging
import json
from typing import Optional

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from aiocryptopay import AioCryptoPay, Networks
from aiocryptopay.models.update import Update

from config.settings import Settings
from bot.middlewares.i18n import JsonI18n
from bot.services.subscription_service import SubscriptionService
from bot.services.referral_service import ReferralService
from bot.keyboards.inline.user_keyboards import get_connect_and_main_keyboard
from bot.services.notification_service import NotificationService
from db.dal import payment_dal, user_dal
from bot.utils.text_sanitizer import sanitize_display_name, username_for_display
from bot.utils.config_link import prepare_config_links


class CryptoPayService:
    def __init__(
        self,
        token: Optional[str],
        network: str,
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
        if token:
            net = Networks.TEST_NET if str(network).lower() == "testnet" else Networks.MAIN_NET
            self.client = AioCryptoPay(token=token, network=net)
            self.client.register_pay_handler(self._invoice_paid_handler)
            self.configured = True
        else:
            logging.warning("CryptoPay token not provided. CryptoPay disabled")
            self.client = None
            self.configured = False

    async def close(self):
        """Close underlying AioCryptoPay session if initialized."""
        if self.client:
            try:
                await self.client.close()
                logging.info("CryptoPay client session closed.")
            except Exception as e:
                logging.warning(f"Failed to close CryptoPay client: {e}")

    async def create_invoice(
        self,
        session: AsyncSession,
        user_id: int,
        months: int,
        amount: float,
        description: str,
        sale_mode: str = "subscription",
        promo_code_service=None,
    ) -> Optional[str]:
        if not self.configured or not self.client:
            logging.error("CryptoPayService not configured")
            return None

        # Check for active discount to save metadata (price already discounted from previous step)
        original_amount = None
        discount_amount = None
        promo_code_id = None

        if promo_code_service:
            from db.dal import active_discount_dal
            active_discount = await active_discount_dal.get_active_discount(session, user_id)
            if active_discount:
                # Price is already discounted, calculate original price backwards
                discount_pct = active_discount.discount_percentage
                original_amount = amount / (1 - discount_pct / 100)
                discount_amount = original_amount - amount
                promo_code_id = active_discount.promo_code_id
                logging.info(
                    f"Recording {discount_pct}% discount for CryptoPay payment: "
                    f"original {original_amount:.2f} -> final {amount}"
                )

        # Create pending payment in DB and commit to persist
        try:
            payment_record = await payment_dal.create_payment_record(
                session,
                {
                    "user_id": user_id,
                    "amount": amount,
                    "original_amount": original_amount,
                    "discount_applied": discount_amount,
                    "currency": self.settings.CRYPTOPAY_ASSET,
                    "status": "pending_cryptopay",
                    "description": description,
                    "subscription_duration_months": int(months),
                    "provider": "cryptopay",
                    "promo_code_id": promo_code_id,
                },
            )
            await session.commit()
        except Exception as e_db_create:
            await session.rollback()
            logging.error(
                f"Failed to create cryptopay payment record for user {user_id}: {e_db_create}",
                exc_info=True,
            )
            return None
        payload = json.dumps({
            "user_id": str(user_id),
            "subscription_months": str(months),
            "payment_db_id": str(payment_record.payment_id),
            "sale_mode": sale_mode,
            "traffic_gb": str(months) if sale_mode == "traffic" else None,
        })
        try:
            invoice = await self.client.create_invoice(
                amount=amount,
                currency_type=self.settings.CRYPTOPAY_CURRENCY_TYPE,
                fiat=self.settings.CRYPTOPAY_ASSET if self.settings.CRYPTOPAY_CURRENCY_TYPE == "fiat" else None,
                asset=self.settings.CRYPTOPAY_ASSET if self.settings.CRYPTOPAY_CURRENCY_TYPE == "crypto" else None,
                description=description,
                payload=payload,
            )
            try:
                await payment_dal.update_provider_payment_and_status(
                    session,
                    payment_record.payment_id,
                    str(invoice.invoice_id),
                    str(invoice.status),
                )
                await session.commit()
            except Exception as e_db_update:
                await session.rollback()
                logging.error(
                    f"Failed to update cryptopay payment record {payment_record.payment_id}: {e_db_update}",
                    exc_info=True,
                )
                return None
            return invoice.bot_invoice_url
        except Exception as e:
            logging.error(f"CryptoPay invoice creation failed: {e}", exc_info=True)
            return None

    async def _invoice_paid_handler(self, update: Update, app: web.Application):
        invoice = update.payload
        if not invoice.payload:
            logging.warning("CryptoPay webhook without payload")
            return
        try:
            meta = json.loads(invoice.payload)
            user_id = int(meta["user_id"])
            months = float(meta.get("subscription_months") or 0)
            payment_db_id = int(meta["payment_db_id"])
            sale_mode = meta.get("sale_mode") or ("traffic" if self.settings.traffic_sale_mode else "subscription")
            traffic_gb = float(meta.get("traffic_gb")) if meta.get("traffic_gb") else months
        except Exception as e:
            logging.error(f"Failed to parse CryptoPay payload: {e}")
            return

        async_session_factory: sessionmaker = app["async_session_factory"]
        bot: Bot = app["bot"]
        settings: Settings = app["settings"]
        i18n: JsonI18n = app["i18n"]
        subscription_service: SubscriptionService = app["subscription_service"]
        referral_service: ReferralService = app["referral_service"]

        async with async_session_factory() as session:
            try:
                # Fetch payment record to get promo_code_id
                payment_record = await payment_dal.get_payment_by_db_id(session, payment_db_id)
                if not payment_record:
                    logging.error(f"CryptoPay: Payment record {payment_db_id} not found")
                    return

                await payment_dal.update_provider_payment_and_status(
                    session,
                    payment_db_id,
                    str(invoice.invoice_id),
                    "succeeded",
                )
                activation = await subscription_service.activate_subscription(
                    session,
                    user_id,
                    int(months) if sale_mode != "traffic" else 0,
                    float(invoice.amount),
                    payment_db_id,
                    promo_code_id_from_payment=payment_record.promo_code_id,
                    provider="cryptopay",
                    sale_mode=sale_mode,
                    traffic_gb=traffic_gb if sale_mode == "traffic" else None,
                )
                referral_bonus = None
                if sale_mode != "traffic":
                    referral_bonus = await referral_service.apply_referral_bonuses_for_payment(
                        session,
                        user_id,
                        int(months) or 1,
                        current_payment_db_id=payment_db_id,
                        skip_if_active_before_payment=False,
                    )
                await session.commit()
            except Exception as e:
                await session.rollback()
                logging.error(f"Failed to process CryptoPay invoice: {e}", exc_info=True)
                return

            db_user = await user_dal.get_user_by_id(session, user_id)
            # Use DB language for user-facing messages
            lang = db_user.language_code if db_user and db_user.language_code else settings.DEFAULT_LANGUAGE
            _ = lambda k, **kw: i18n.gettext(lang, k, **kw)

            raw_config_link = activation.get("subscription_url") if activation else None
            display_link, button_link = await prepare_config_links(settings, raw_config_link)
            config_link_text = display_link or _("config_link_not_available")
            final_end = activation.get("end_date")
            applied_days = 0
            if referral_bonus and referral_bonus.get("referee_new_end_date"):
                final_end = referral_bonus["referee_new_end_date"]
                applied_days = referral_bonus.get("referee_bonus_applied_days", 0)

            if sale_mode == "traffic":
                text = _("payment_successful_traffic_full",
                         traffic_gb=str(int(traffic_gb)) if float(traffic_gb).is_integer() else f"{traffic_gb:g}",
                         end_date=final_end.strftime('%Y-%m-%d') if final_end else "—",
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
                text = _("payment_successful_with_referral_bonus_full",
                         months=int(months),
                         base_end_date=activation["end_date"].strftime('%Y-%m-%d'),
                         bonus_days=applied_days,
                         final_end_date=final_end.strftime('%Y-%m-%d'),
                         inviter_name=inviter_name_display,
                         config_link=config_link_text)
            else:
                text = _("payment_successful_full",
                         months=int(months),
                         end_date=final_end.strftime('%Y-%m-%d') if final_end else "—",
                         config_link=config_link_text)

            markup = get_connect_and_main_keyboard(
                lang,
                i18n,
                settings,
                display_link,
                connect_button_url=button_link,
                preserve_message=True,
            )
            try:
                await bot.send_message(
                    user_id,
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logging.error(f"Failed to send CryptoPay success message: {e}")

            # Send notification about payment
            try:
                notification_service = NotificationService(bot, settings, i18n)
                user = await user_dal.get_user_by_id(session, user_id)
                await notification_service.notify_payment_received(
                    user_id=user_id,
                    amount=float(invoice.amount),
                    currency=invoice.asset or settings.DEFAULT_CURRENCY_SYMBOL,
                    months=int(months) if sale_mode != "traffic" else 0,
                    traffic_gb=traffic_gb if sale_mode == "traffic" else None,
                    payment_provider="crypto_pay",
                    username=user.username if user else None
                )
            except Exception as e:
                logging.error(f"Failed to send crypto_pay payment notification: {e}")

    async def webhook_route(self, request: web.Request) -> web.Response:
        if not self.configured or not self.client:
            return web.Response(status=503, text="cryptopay_disabled")
        return await self.client.get_updates(request)


async def cryptopay_webhook_route(request: web.Request) -> web.Response:
    service: CryptoPayService = request.app["cryptopay_service"]
    return await service.webhook_route(request)
