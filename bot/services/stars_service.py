import logging
import math
from typing import Optional

from aiogram import Bot, types
from aiogram.types import LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from db.dal import payment_dal, user_dal
from .subscription_service import SubscriptionService
from .referral_service import ReferralService
from bot.middlewares.i18n import JsonI18n
from .notification_service import NotificationService
from bot.keyboards.inline.user_keyboards import get_connect_and_main_keyboard
from bot.utils.text_sanitizer import sanitize_display_name, username_for_display
from bot.utils.config_link import prepare_config_links


class StarsService:
    def __init__(self, bot: Bot, settings: Settings, i18n: JsonI18n,
                 subscription_service: SubscriptionService,
                 referral_service: ReferralService):
        self.bot = bot
        self.settings = settings
        self.i18n = i18n
        self.subscription_service = subscription_service
        self.referral_service = referral_service

    async def create_invoice(self, session: AsyncSession, user_id: int, months: int,
                             stars_price: int, description: str, sale_mode: str = "subscription",
                             promo_code_service=None) -> Optional[int]:
        # Apply active discount if exists (Stars use ceiling rounding)
        original_stars_price = stars_price
        discount_amount_stars = None
        promo_code_id = None

        if promo_code_service:
            # Import here to avoid circular import
            from bot.handlers.user.subscription.payment_discount_helper import apply_discount_to_payment

            # Apply discount and round up using ceiling
            final_price_float, discount_float, promo_code_id = await apply_discount_to_payment(
                session, user_id, float(stars_price), promo_code_service
            )
            if discount_float:
                # Apply ceiling rounding for fractional Stars amounts
                stars_price = math.ceil(final_price_float)
                discount_amount_stars = original_stars_price - stars_price
                logging.info(
                    f"Stars discount applied: {original_stars_price} -> {final_price_float:.2f} -> {stars_price} (ceiling)"
                )

        payment_record_data = {
            "user_id": user_id,
            "amount": float(stars_price),
            "original_amount": float(original_stars_price) if discount_amount_stars else None,
            "discount_applied": float(discount_amount_stars) if discount_amount_stars else None,
            "currency": "XTR",
            "status": "pending_stars",
            "description": description,
            "subscription_duration_months": int(months),
            "provider": "telegram_stars",
            "promo_code_id": promo_code_id,
        }
        try:
            db_payment_record = await payment_dal.create_payment_record(
                session, payment_record_data)
            await session.commit()
        except Exception as e_db:
            await session.rollback()
            logging.error(f"Failed to create stars payment record: {e_db}",
                          exc_info=True)
            return None

        payload = f"{db_payment_record.payment_id}:{months}:{sale_mode}"
        prices = [LabeledPrice(label=description, amount=stars_price)]
        try:
            await self.bot.send_invoice(
                chat_id=user_id,
                title=description,
                description=description,
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=prices,
            )
            return db_payment_record.payment_id
        except Exception as e_inv:
            logging.error(f"Failed to send Telegram Stars invoice: {e_inv}",
                          exc_info=True)
            return None

    async def process_successful_payment(self, session: AsyncSession,
                                         message: types.Message,
                                         payment_db_id: int,
                                         months: int,
                                         stars_amount: int,
                                         i18n_data: dict,
                                         sale_mode: str = "subscription") -> None:
        # Fetch payment record to get promo_code_id
        payment_record = await payment_dal.get_payment_by_db_id(session, payment_db_id)
        promo_code_id_from_payment = payment_record.promo_code_id if payment_record else None

        try:
            await payment_dal.update_provider_payment_and_status(
                session, payment_db_id,
                message.successful_payment.provider_payment_charge_id,
                "succeeded")
            await session.commit()
        except Exception as e_upd:
            await session.rollback()
            logging.error(
                f"Failed to update stars payment record {payment_db_id}: {e_upd}",
                exc_info=True)
            return

        activation_details = await self.subscription_service.activate_subscription(
            session,
            message.from_user.id,
            int(months) if sale_mode != "traffic" else 0,
            float(stars_amount),
            payment_db_id,
            promo_code_id_from_payment=promo_code_id_from_payment,
            provider="telegram_stars",
            sale_mode=sale_mode,
            traffic_gb=months if sale_mode == "traffic" else None,
        )
        if not activation_details or not activation_details.get("end_date"):
            logging.error(
                f"Failed to activate subscription after stars payment for user {message.from_user.id}")
            return

        referral_bonus = None
        if sale_mode != "traffic":
            referral_bonus = await self.referral_service.apply_referral_bonuses_for_payment(
                session,
                message.from_user.id,
                int(months) or 1,
                current_payment_db_id=payment_db_id,
                skip_if_active_before_payment=False,
            )
        await session.commit()

        applied_days = referral_bonus.get("referee_bonus_applied_days") if referral_bonus else None
        final_end = referral_bonus.get("referee_new_end_date") if referral_bonus else None
        if not final_end:
            final_end = activation_details["end_date"]

        # Always use user's language from DB for user-facing messages
        db_user = await user_dal.get_user_by_id(session, message.from_user.id)
        current_lang = db_user.language_code if db_user and db_user.language_code else self.settings.DEFAULT_LANGUAGE
        i18n: JsonI18n = i18n_data.get("i18n_instance")
        _ = lambda k, **kw: i18n.gettext(current_lang, k, **kw) if i18n else k

        raw_config_link = activation_details.get("subscription_url") if activation_details else None
        config_link_display, connect_button_url = await prepare_config_links(self.settings, raw_config_link)
        config_link_text = config_link_display or _("config_link_not_available")

        if sale_mode == "traffic":
            success_msg = _(
                "payment_successful_traffic_full",
                traffic_gb=str(int(months)) if float(months).is_integer() else f"{months:g}",
                end_date=final_end.strftime('%Y-%m-%d'),
                config_link=config_link_text,
            )
        elif applied_days:
            inviter_name_display = _("friend_placeholder")
            db_user = await user_dal.get_user_by_id(session, message.from_user.id)
            if db_user and db_user.referred_by_id:
                inviter = await user_dal.get_user_by_id(session, db_user.referred_by_id)
                if inviter:
                    safe_name = sanitize_display_name(inviter.first_name) if inviter.first_name else None
                    if safe_name:
                        inviter_name_display = safe_name
                    elif inviter.username:
                        inviter_name_display = username_for_display(inviter.username, with_at=False)
            success_msg = _(
                "payment_successful_with_referral_bonus_full",
                months=months,
                base_end_date=activation_details["end_date"].strftime('%Y-%m-%d'),
                bonus_days=applied_days,
                final_end_date=final_end.strftime('%Y-%m-%d'),
                inviter_name=inviter_name_display,
                config_link=config_link_text,
            )
        else:
            success_msg = _(
                "payment_successful_full",
                months=months,
                end_date=final_end.strftime('%Y-%m-%d'),
                config_link=config_link_text,
            )
        markup = get_connect_and_main_keyboard(
            current_lang,
            i18n,
            self.settings,
            config_link_display,
            connect_button_url=connect_button_url,
            preserve_message=True,
        )
        try:
            await self.bot.send_message(
                message.from_user.id,
                success_msg,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e_send:
            logging.error(
                f"Failed to send stars payment success message: {e_send}")

        # Send notification about payment
        try:
            notification_service = NotificationService(self.bot, self.settings, self.i18n)
            user = await user_dal.get_user_by_id(session, message.from_user.id)
            await notification_service.notify_payment_received(
                user_id=message.from_user.id,
                amount=float(stars_amount),
                currency="XTR",
                months=int(months) if sale_mode != "traffic" else 0,
                payment_provider="stars",
                username=user.username if user else None,
                traffic_gb=months if sale_mode == "traffic" else None,
            )
        except Exception as e:
            logging.error(f"Failed to send stars payment notification: {e}")
