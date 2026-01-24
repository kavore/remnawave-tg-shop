import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Tuple, Dict
from aiogram import Bot

from config.settings import Settings

from db.dal import promo_code_dal, user_dal, active_discount_dal
from db.models import PromoCode, User

from .subscription_service import SubscriptionService
from bot.middlewares.i18n import JsonI18n
from .notification_service import NotificationService


class PromoCodeService:

    def __init__(self, settings: Settings,
                 subscription_service: SubscriptionService, bot: Bot,
                 i18n: JsonI18n):
        self.settings = settings
        self.subscription_service = subscription_service
        self.bot = bot
        self.i18n = i18n

    async def apply_promo_code(
        self,
        session: AsyncSession,
        user_id: int,
        code_input: str,
        user_lang: str,
    ) -> Tuple[bool, datetime | str]:
        _ = lambda k, **kw: self.i18n.gettext(user_lang, k, **kw)
        code_input_upper = code_input.strip().upper()

        promo_data = await promo_code_dal.get_active_bonus_promo_code_by_code_str(
            session, code_input_upper)

        if not promo_data:
            return False, _("promo_code_not_found", code=code_input_upper)

        existing_activation = await promo_code_dal.get_user_activation_for_promo(
            session, promo_data.promo_code_id, user_id)
        if existing_activation:
            return False, _("promo_code_already_used_by_user",
                            code=code_input_upper)

        bonus_days = promo_data.bonus_days

        new_end_date = await self.subscription_service.extend_active_subscription_days(
            session=session,
            user_id=user_id,
            bonus_days=bonus_days,
            reason=f"promo code {code_input_upper}")

        if new_end_date:
            activation_recorded = await promo_code_dal.record_promo_activation(
                session, promo_data.promo_code_id, user_id, payment_id=None)
            promo_incremented = await promo_code_dal.increment_promo_code_usage(
                session, promo_data.promo_code_id)

            if activation_recorded and promo_incremented:
                # Send notification about promo activation
                try:
                    notification_service = NotificationService(self.bot, self.settings, self.i18n)
                    user = await user_dal.get_user_by_id(session, user_id)
                    await notification_service.notify_promo_activation(
                        user_id=user_id,
                        promo_code=code_input_upper,
                        bonus_days=bonus_days,
                        username=user.username if user else None
                    )
                except Exception as e:
                    logging.error(f"Failed to send promo activation notification: {e}")
                
                return True, new_end_date
            else:

                logging.error(
                    f"Failed to record activation or increment usage for promo {promo_data.code} by user {user_id}"
                )
                return False, _("error_applying_promo_bonus")
        else:
            return False, _("error_applying_promo_bonus")

    async def apply_discount_promo_code(
        self,
        session: AsyncSession,
        user_id: int,
        code_input: str,
        user_lang: str,
    ) -> Tuple[bool, int | str]:
        """
        Apply a discount promo code (sets active discount for user).
        Returns: (success: bool, discount_percentage or error_message)
        """
        _ = lambda k, **kw: self.i18n.gettext(user_lang, k, **kw)
        code_input_upper = code_input.strip().upper()

        # Check if user already has an active discount
        existing_discount = await active_discount_dal.get_active_discount(session, user_id)
        if existing_discount:
            # Get the promo code for the existing discount
            existing_promo = await promo_code_dal.get_promo_code_by_id(
                session, existing_discount.promo_code_id
            )
            if existing_promo:
                return False, _("discount_promo_already_active",
                               code=existing_promo.code,
                               discount_pct=existing_discount.discount_percentage)
            else:
                # Existing discount but promo not found - clear it and continue
                await active_discount_dal.clear_active_discount(session, user_id)

        # Get discount promo code
        promo_data = await promo_code_dal.get_active_discount_promo_code_by_code_str(
            session, code_input_upper
        )

        if not promo_data:
            return False, _("promo_code_not_found_or_not_discount", code=code_input_upper)

        # Check if user already used this code
        existing_activation = await promo_code_dal.get_user_activation_for_promo(
            session, promo_data.promo_code_id, user_id
        )
        if existing_activation:
            return False, _("promo_code_already_used_by_user", code=code_input_upper)

        # Set active discount
        active_discount = await active_discount_dal.set_active_discount(
            session,
            user_id=user_id,
            promo_code_id=promo_data.promo_code_id,
            discount_percentage=promo_data.discount_percentage
        )

        if not active_discount:
            # This shouldn't happen since we checked above, but just in case
            return False, _("error_applying_promo_discount")

        logging.info(
            f"Discount promo code {code_input_upper} activated for user {user_id}: "
            f"{promo_data.discount_percentage}% off"
        )
        return True, promo_data.discount_percentage

    async def get_user_active_discount(
        self,
        session: AsyncSession,
        user_id: int
    ) -> Optional[Tuple[int, str]]:
        """
        Get user's active discount if any.
        Returns: (discount_percentage, promo_code) or None
        """
        active_discount = await active_discount_dal.get_active_discount(session, user_id)
        if not active_discount:
            return None

        # Fetch promo code for code string
        promo = await promo_code_dal.get_promo_code_by_id(
            session, active_discount.promo_code_id
        )
        if not promo:
            # Discount exists but promo not found - clear it
            await active_discount_dal.clear_active_discount(session, user_id)
            return None

        return (active_discount.discount_percentage, promo.code)

    def calculate_discounted_price(
        self,
        original_price: float,
        discount_percentage: int
    ) -> Tuple[float, float]:
        """
        Calculate discounted price and discount amount.
        Returns: (final_price, discount_amount)
        """
        discount_amount = round(original_price * (discount_percentage / 100), 2)
        final_price = round(original_price - discount_amount, 2)

        # Ensure price doesn't go negative
        if final_price < 0:
            final_price = 0
            discount_amount = original_price

        return final_price, discount_amount

    async def consume_discount(
        self,
        session: AsyncSession,
        user_id: int,
        payment_id: int
    ) -> bool:
        """
        Consume active discount: record activation, increment usage, clear active discount.
        Call this AFTER successful payment.
        """
        active_discount = await active_discount_dal.get_active_discount(session, user_id)
        if not active_discount:
            return False

        # Record activation
        activation_recorded = await promo_code_dal.record_promo_activation(
            session,
            active_discount.promo_code_id,
            user_id,
            payment_id=payment_id
        )

        # Increment usage
        promo_incremented = await promo_code_dal.increment_promo_code_usage(
            session,
            active_discount.promo_code_id
        )

        # Clear active discount
        await active_discount_dal.clear_active_discount(session, user_id)

        if activation_recorded and promo_incremented:
            await session.flush()
            logging.info(
                f"Discount consumed for user {user_id}, promo {active_discount.promo_code_id}, "
                f"payment {payment_id}"
            )
            return True
        else:
            logging.error(
                f"Failed to consume discount for user {user_id}, "
                f"promo {active_discount.promo_code_id}"
            )
            return False
