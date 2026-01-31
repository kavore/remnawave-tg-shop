import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from datetime import datetime, timezone

from db.models import ActiveDiscount, PromoCode


async def set_active_discount(
    session: AsyncSession,
    user_id: int,
    promo_code_id: int,
    discount_percentage: int
) -> Optional[ActiveDiscount]:
    """
    Set active discount for user.
    Returns None if user already has an active discount (enforce one-at-a-time rule).
    """
    # Check if user already has an active discount
    existing = await get_active_discount(session, user_id)
    if existing:
        logging.warning(
            f"User {user_id} already has active discount (promo_code_id: {existing.promo_code_id}). "
            f"Cannot activate new discount {promo_code_id}."
        )
        return None

    # Create new active discount
    new_discount = ActiveDiscount(
        user_id=user_id,
        promo_code_id=promo_code_id,
        discount_percentage=discount_percentage,
        activated_at=datetime.now(timezone.utc)
    )
    session.add(new_discount)
    await session.flush()
    await session.refresh(new_discount)
    logging.info(
        f"Active discount set for user {user_id}: promo_code_id={promo_code_id}, "
        f"discount={discount_percentage}%"
    )
    return new_discount


async def get_active_discount(
    session: AsyncSession,
    user_id: int
) -> Optional[ActiveDiscount]:
    """Get active discount for user if exists."""
    stmt = select(ActiveDiscount).where(ActiveDiscount.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def clear_active_discount(
    session: AsyncSession,
    user_id: int
) -> bool:
    """
    Clear active discount for user.
    Returns True if discount was cleared, False if no discount was found.
    """
    stmt = delete(ActiveDiscount).where(ActiveDiscount.user_id == user_id)
    result = await session.execute(stmt)
    await session.flush()
    cleared = result.rowcount > 0
    if cleared:
        logging.info(f"Active discount cleared for user {user_id}")
    return cleared


async def clear_active_discounts_by_promo_code(
    session: AsyncSession,
    promo_code_id: int
) -> int:
    """
    Clear all active discounts associated with a specific promo code.
    Returns the number of discounts cleared.
    """
    stmt = delete(ActiveDiscount).where(ActiveDiscount.promo_code_id == promo_code_id)
    result = await session.execute(stmt)
    await session.flush()
    count = result.rowcount
    if count > 0:
        logging.info(f"Cleared {count} active discount(s) for promo_code_id={promo_code_id}")
    return count
