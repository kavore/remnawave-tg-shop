import logging
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from datetime import datetime, timezone

from db.models import ActiveDiscount


async def set_active_discount(
    session: AsyncSession,
    user_id: int,
    promo_code_id: int,
    discount_percentage: int,
    expires_at: datetime,
) -> Optional[ActiveDiscount]:
    """
    Set active discount for user.
    Returns None if user already has an active discount (enforce one-at-a-time rule).
    """
    now_utc = datetime.now(timezone.utc)

    existing = await get_active_discount(session, user_id, include_expired=True)
    if existing and existing.expires_at > now_utc:
        logging.warning(
            f"User {user_id} already has active discount (promo_code_id: {existing.promo_code_id}). "
            f"Cannot activate new discount {promo_code_id}."
        )
        return None

    if existing and existing.expires_at <= now_utc:
        await clear_active_discount_if_expired(session, user_id, now=now_utc)

    # Create new active discount
    new_discount = ActiveDiscount(
        user_id=user_id,
        promo_code_id=promo_code_id,
        discount_percentage=discount_percentage,
        activated_at=now_utc,
        expires_at=expires_at,
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
    user_id: int,
    include_expired: bool = False,
) -> Optional[ActiveDiscount]:
    """Get active discount for user if exists."""
    now_utc = datetime.now(timezone.utc)
    stmt = select(ActiveDiscount).where(ActiveDiscount.user_id == user_id)
    if not include_expired:
        stmt = stmt.where(ActiveDiscount.expires_at > now_utc)
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


async def clear_active_discount_if_expired(
    session: AsyncSession,
    user_id: int,
    now: Optional[datetime] = None,
) -> bool:
    """
    Clear active discount for user only when it has already expired.
    """
    now_utc = now or datetime.now(timezone.utc)
    stmt = delete(ActiveDiscount).where(
        ActiveDiscount.user_id == user_id,
        ActiveDiscount.expires_at <= now_utc,
    )
    result = await session.execute(stmt)
    await session.flush()
    cleared = result.rowcount > 0
    if cleared:
        logging.info("Expired active discount cleared for user %s", user_id)
    return cleared


async def clear_active_discount_if_matches(
    session: AsyncSession,
    user_id: int,
    promo_code_id: Optional[int] = None,
    expires_at_lte: Optional[datetime] = None,
) -> bool:
    """
    Clear active discount for user only when additional constraints match.
    """
    conditions = [ActiveDiscount.user_id == user_id]
    if promo_code_id is not None:
        conditions.append(ActiveDiscount.promo_code_id == promo_code_id)
    if expires_at_lte is not None:
        conditions.append(ActiveDiscount.expires_at <= expires_at_lte)

    stmt = delete(ActiveDiscount).where(*conditions)
    result = await session.execute(stmt)
    await session.flush()
    cleared = result.rowcount > 0
    if cleared:
        logging.info(
            "Active discount cleared for user %s by constrained cleanup.",
            user_id,
        )
    return cleared


async def get_expired_active_discounts(
    session: AsyncSession,
    now: Optional[datetime] = None,
    limit: int = 100,
) -> List[ActiveDiscount]:
    """Get expired active discount reservations for cleanup/notifications."""
    now_utc = now or datetime.now(timezone.utc)
    stmt = (
        select(ActiveDiscount)
        .where(ActiveDiscount.expires_at <= now_utc)
        .order_by(ActiveDiscount.expires_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


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
