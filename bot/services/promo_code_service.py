import logging
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Tuple
from aiogram import Bot
from sqlalchemy.orm import sessionmaker

from config.settings import Settings

from db.dal import promo_code_dal, user_dal, active_discount_dal, payment_dal

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
        self.discount_payment_timeout_minutes = max(
            1,
            int(getattr(settings, "DISCOUNT_PROMO_PAYMENT_TIMEOUT_MINUTES", 10) or 10),
        )
        self._discount_expiration_task: Optional[asyncio.Task] = None
        self._async_session_factory: Optional[sessionmaker] = None

    async def setup_discount_expiration_worker(
        self,
        async_session_factory: sessionmaker,
    ) -> None:
        """Attach DB session factory and start background cleanup loop."""
        self._async_session_factory = async_session_factory
        if self._discount_expiration_task and not self._discount_expiration_task.done():
            return
        self._discount_expiration_task = asyncio.create_task(
            self._discount_expiration_loop(),
            name="PromoDiscountExpirationLoop",
        )
        logging.info("PromoCodeService: started discount expiration background worker.")

    async def close(self) -> None:
        """Gracefully stop background workers."""
        if not self._discount_expiration_task:
            return
        self._discount_expiration_task.cancel()
        try:
            await self._discount_expiration_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception("PromoCodeService: failed while stopping expiration worker")
        finally:
            self._discount_expiration_task = None

    async def _discount_expiration_loop(self) -> None:
        """Periodically clears expired discount reservations and notifies users."""
        while True:
            try:
                if not self._async_session_factory:
                    await asyncio.sleep(30)
                    continue

                await self._process_expired_discounts_once()
            except asyncio.CancelledError:
                logging.info("PromoCodeService: discount expiration loop cancelled.")
                raise
            except Exception:
                logging.exception("PromoCodeService: unhandled error in discount expiration loop")

            await asyncio.sleep(30)

    async def _process_expired_discounts_once(self) -> None:
        if not self._async_session_factory:
            return

        now_utc = datetime.now(timezone.utc)
        notifications_to_send: list[tuple[int, str]] = []

        async with self._async_session_factory() as session:
            expired_discounts = await active_discount_dal.get_expired_active_discounts(
                session,
                now=now_utc,
                limit=100,
            )
            if not expired_discounts:
                return

            for expired in expired_discounts:
                cleared = await active_discount_dal.clear_active_discount_if_matches(
                    session,
                    user_id=expired.user_id,
                    promo_code_id=expired.promo_code_id,
                    expires_at_lte=now_utc,
                )
                if not cleared:
                    continue

                await promo_code_dal.decrement_promo_code_usage(session, expired.promo_code_id)

                db_user = await user_dal.get_user_by_id(session, expired.user_id)
                user_lang = (
                    db_user.language_code
                    if db_user and db_user.language_code
                    else self.settings.DEFAULT_LANGUAGE
                )
                promo = await promo_code_dal.get_promo_code_by_id(session, expired.promo_code_id)
                promo_code = promo.code if promo else ""
                message_text = self.i18n.gettext(
                    user_lang,
                    "discount_promo_expired_need_reactivate",
                    code_part=(f" (<code>{promo_code}</code>)" if promo_code else ""),
                )

                notifications_to_send.append((expired.user_id, message_text))

                logging.info(
                    "Expired discount reservation removed: user=%s, promo=%s",
                    expired.user_id,
                    expired.promo_code_id,
                )

            await session.commit()

        for user_id, message_text in notifications_to_send:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML",
                )
            except Exception:
                logging.exception(
                    "Failed to send discount expiration message to user %s",
                    user_id,
                )

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
        existing_discount = await active_discount_dal.get_active_discount(
            session,
            user_id,
            include_expired=True,
        )
        if existing_discount:
            now_utc = datetime.now(timezone.utc)
            if existing_discount.expires_at <= now_utc:
                cleared = await active_discount_dal.clear_active_discount_if_expired(
                    session,
                    user_id,
                    now=now_utc,
                )
                if cleared:
                    await promo_code_dal.decrement_promo_code_usage(
                        session,
                        existing_discount.promo_code_id,
                    )
                existing_discount = None

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

        # Reserve discount for limited time and count activation immediately
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.discount_payment_timeout_minutes,
        )
        active_discount = await active_discount_dal.set_active_discount(
            session,
            user_id=user_id,
            promo_code_id=promo_data.promo_code_id,
            discount_percentage=promo_data.discount_percentage,
            expires_at=expires_at,
        )

        if not active_discount:
            # This shouldn't happen since we checked above, but just in case
            return False, _("error_applying_promo_discount")

        promo_incremented = await promo_code_dal.increment_promo_code_usage(
            session,
            promo_data.promo_code_id,
        )
        if not promo_incremented:
            await active_discount_dal.clear_active_discount_if_matches(
                session,
                user_id=user_id,
                promo_code_id=promo_data.promo_code_id,
            )
            return False, _("promo_code_not_found_or_not_discount", code=code_input_upper)

        logging.info(
            f"Discount promo code {code_input_upper} activated for user {user_id}: "
            f"{promo_data.discount_percentage}% off until {expires_at.isoformat()}"
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
        active_discount = await active_discount_dal.get_active_discount(
            session,
            user_id,
            include_expired=True,
        )
        if not active_discount:
            return None

        now_utc = datetime.now(timezone.utc)
        if active_discount.expires_at <= now_utc:
            cleared = await active_discount_dal.clear_active_discount_if_expired(
                session,
                user_id,
                now=now_utc,
            )
            if cleared:
                await promo_code_dal.decrement_promo_code_usage(
                    session,
                    active_discount.promo_code_id,
                )
            return None

        # Fetch promo code for code string
        promo = await promo_code_dal.get_promo_code_by_id(
            session, active_discount.promo_code_id
        )
        if not promo:
            # Discount exists but promo not found - clear it
            await active_discount_dal.clear_active_discount(session, user_id)
            return None

        # Check if promo code has expired
        if promo.valid_until and promo.valid_until <= datetime.now(timezone.utc):
            # Promo code expired - clear the discount
            logging.info(
                f"Promo code {promo.code} expired (valid_until: {promo.valid_until}). "
                f"Clearing active discount for user {user_id}"
            )
            cleared = await active_discount_dal.clear_active_discount(session, user_id)
            if cleared:
                await promo_code_dal.decrement_promo_code_usage(session, promo.promo_code_id)
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
        Consume discount after successful payment.

        The payment record is the source of truth. Even if the active reservation was
        concurrently expired/cleared, we still record promo activation and reconcile
        current_activations so successful discounted payments are always accounted for.
        """
        payment_record = await payment_dal.get_payment_by_db_id(session, payment_id)
        if not payment_record:
            logging.warning(
                "Payment %s not found for discount consumption (user %s).",
                payment_id,
                user_id,
            )
            return False

        if not payment_record.discount_applied:
            return False

        promo_code_id = payment_record.promo_code_id
        if not promo_code_id:
            logging.warning(
                "Payment %s for user %s has discount_applied but no promo_code_id.",
                payment_id,
                user_id,
            )
            return False

        existing_activation = await promo_code_dal.get_user_activation_for_promo(
            session, promo_code_id, user_id
        )

        activation_created = False
        if existing_activation:
            if existing_activation.payment_id is None:
                updated_payment = await promo_code_dal.set_activation_payment_id(
                    session, promo_code_id, user_id, payment_id
                )
                if updated_payment:
                    logging.info(
                        "Linked discount promo %s activation to payment %s for user %s.",
                        promo_code_id,
                        payment_id,
                        user_id,
                    )
        else:
            activation_recorded = await promo_code_dal.record_promo_activation(
                session,
                promo_code_id,
                user_id,
                payment_id=payment_id,
            )
            if not activation_recorded:
                logging.error(
                    "Failed to record discount activation for user %s, promo %s.",
                    user_id,
                    promo_code_id,
                )
                return False
            activation_created = True

        active_discount = await active_discount_dal.get_active_discount(
            session,
            user_id,
            include_expired=True,
        )

        # Reservation is best-effort cleanup at this point; payment success already happened.
        if active_discount and active_discount.promo_code_id == promo_code_id:
            await active_discount_dal.clear_active_discount_if_matches(
                session,
                user_id=user_id,
                promo_code_id=promo_code_id,
            )
        elif active_discount and active_discount.promo_code_id != promo_code_id:
            logging.info(
                "Active discount promo %s differs from payment promo %s during consumption.",
                active_discount.promo_code_id,
                promo_code_id,
            )
        else:
            logging.info(
                "Discount reservation already absent at consumption time (user=%s, promo=%s, payment=%s)",
                user_id,
                promo_code_id,
                payment_id,
            )

        # If reservation was already expired/removed and we had to create activation now,
        # restore current_activations to match the successful payment.
        if activation_created:
            await promo_code_dal.increment_promo_code_usage(
                session,
                promo_code_id,
                allow_overflow=True,
            )

        await session.flush()
        logging.info(
            "Discount consumed for user %s, promo %s, payment %s",
            user_id,
            promo_code_id,
            payment_id,
        )
        return True
