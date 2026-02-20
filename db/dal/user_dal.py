import logging
import secrets
import string
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import update, delete, func, and_, or_
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import (
    User,
    Subscription,
    Payment,
    PromoCodeActivation,
    MessageLog,
    UserBilling,
    UserPaymentMethod,
    AdAttribution,
)

REFERRAL_CODE_ALPHABET = string.ascii_uppercase + string.digits
REFERRAL_CODE_LENGTH = 9
MAX_REFERRAL_CODE_ATTEMPTS = 25


def _generate_referral_code_candidate() -> str:
    return "".join(
        secrets.choice(REFERRAL_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH)
    )


async def _referral_code_exists(session: AsyncSession, code: str) -> bool:
    stmt = select(User.user_id).where(User.referral_code == code)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def generate_unique_referral_code(session: AsyncSession) -> str:
    """
    Generate a unique referral code consisting of uppercase alphanumeric characters.
    Retries until a free code is found or raises RuntimeError after exceeding attempts.
    """
    for _ in range(MAX_REFERRAL_CODE_ATTEMPTS):
        candidate = _generate_referral_code_candidate()
        if not await _referral_code_exists(session, candidate):
            return candidate
    raise RuntimeError("Failed to generate a unique referral code after several attempts.")


async def ensure_referral_code(session: AsyncSession, user: User) -> str:
    """
    Ensure the provided user has a referral code, generating and persisting it if missing.
    Returns the existing or newly generated code.
    """
    if user.referral_code:
        normalized = user.referral_code.strip().upper()
        if normalized != user.referral_code:
            user.referral_code = normalized
            await session.flush()
            await session.refresh(user)
        return user.referral_code

    user.referral_code = await generate_unique_referral_code(session)
    await session.flush()
    await session.refresh(user)
    return user.referral_code


async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    stmt = select(User).where(User.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    clean_username = username.lstrip("@").lower()
    stmt = select(User).where(func.lower(User.username) == clean_username)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_panel_uuid(
    session: AsyncSession, panel_uuid: str
) -> Optional[User]:
    stmt = select(User).where(User.panel_user_uuid == panel_uuid)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


## Removed unused generic get_user helper to keep DAL explicit and simple


async def create_user(session: AsyncSession, user_data: Dict[str, Any]) -> Tuple[User, bool]:
    """Create a user if not exists in a race-safe way.

    Returns a tuple of (user, created_flag).
    """

    if "registration_date" not in user_data:
        user_data["registration_date"] = datetime.now(timezone.utc)

    if not user_data.get("referral_code"):
        user_data["referral_code"] = await generate_unique_referral_code(session)
    else:
        user_data["referral_code"] = user_data["referral_code"].strip().upper()

    # Use PostgreSQL upsert to avoid IntegrityError on concurrent inserts
    stmt = (
        pg_insert(User)
        .values(**user_data)
        .on_conflict_do_nothing(index_elements=[User.user_id])
        .returning(User.user_id)
    )

    result = await session.execute(stmt)
    inserted_row = result.first()
    created = inserted_row is not None

    # Fetch the user (inserted just now or pre-existing)
    user_id: int = user_data["user_id"]
    user = await get_user_by_id(session, user_id)

    if created and user is not None:
        logging.info(
            f"New user {user.user_id} created in DAL. Referred by: {user.referred_by_id or 'N/A'}."
        )
    elif user is not None:
        logging.info(
            f"User {user.user_id} already exists in DAL. Proceeding without creation."
        )

    return user, created


async def get_user_by_referral_code(session: AsyncSession, referral_code: str) -> Optional[User]:
    normalized = referral_code.strip().upper()
    if not normalized:
        return None
    stmt = select(User).where(User.referral_code == normalized)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_user(
    session: AsyncSession, user_id: int, update_data: Dict[str, Any]
) -> Optional[User]:
    user = await get_user_by_id(session, user_id)
    if user:
        for key, value in update_data.items():
            setattr(user, key, value)
        await session.flush()
        await session.refresh(user)
    return user


async def update_user_language(
    session: AsyncSession, user_id: int, lang_code: str
) -> bool:
    stmt = update(User).where(User.user_id == user_id).values(language_code=lang_code)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def get_banned_users(session: AsyncSession) -> List[User]:
    """Get all banned users"""
    stmt = (
        select(User)
        .where(User.is_banned == True)
        .order_by(User.registration_date.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_all_users_paginated(
    session: AsyncSession, *, page: int = 0, page_size: int = 15
) -> List[User]:
    """Return a slice of users ordered by newest registration first."""
    safe_page = max(page, 0)
    safe_page_size = max(page_size, 1)

    stmt = (
        select(User)
        .order_by(User.registration_date.desc())
        .offset(safe_page * safe_page_size)
        .limit(safe_page_size)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def count_all_users(session: AsyncSession) -> int:
    """Count total number of users."""
    result = await session.execute(select(func.count(User.user_id)))
    return result.scalar_one()


async def get_all_active_user_ids_for_broadcast(session: AsyncSession) -> List[int]:
    stmt = select(User.user_id).where(User.is_banned == False)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_all_users_with_panel_uuid(session: AsyncSession) -> List[User]:
    stmt = select(User).where(User.panel_user_uuid.is_not(None))
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_enhanced_user_statistics(session: AsyncSession) -> Dict[str, Any]:
    """Get comprehensive user statistics including active users, trial users, etc."""
    from datetime import datetime, timezone
    
    # Use timezone-aware UTC to avoid naive/aware comparison issues in SQL queries
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Total users
    total_users_stmt = select(func.count(User.user_id))
    total_users = (await session.execute(total_users_stmt)).scalar() or 0
    
    # Banned users
    banned_users_stmt = select(func.count(User.user_id)).where(User.is_banned == True)
    banned_users = (await session.execute(banned_users_stmt)).scalar() or 0
    
    # Active users today (proxy: registered today)
    active_today_stmt = select(func.count(User.user_id)).where(User.registration_date >= today_start)
    active_today = (await session.execute(active_today_stmt)).scalar() or 0
    
    # Users with active paid subscriptions (non-trial providers only)
    paid_subs_stmt = (
        select(func.count(func.distinct(Subscription.user_id)))
        .join(User, Subscription.user_id == User.user_id)
        .where(
            and_(
                Subscription.is_active == True,
                Subscription.end_date > now,
                Subscription.provider.is_not(None)  # Not trial
            )
        )
    )
    paid_subs_users = (await session.execute(paid_subs_stmt)).scalar() or 0
    
    # Users on trial period
    trial_subs_stmt = (
        select(func.count(func.distinct(Subscription.user_id)))
        .join(User, Subscription.user_id == User.user_id)
        .where(
            and_(
                Subscription.is_active == True,
                Subscription.end_date > now,
                Subscription.provider.is_(None)  # Trial subscriptions
            )
        )
    )
    trial_users = (await session.execute(trial_subs_stmt)).scalar() or 0
    
    # Inactive users (no active subscription)
    inactive_users = total_users - paid_subs_users - trial_users - banned_users
    
    # Users attracted via referral
    referral_users_stmt = select(func.count(User.user_id)).where(User.referred_by_id.is_not(None))
    referral_users = (await session.execute(referral_users_stmt)).scalar() or 0
    
    return {
        "total_users": total_users,
        "banned_users": banned_users,
        "active_today": active_today,
        "paid_subscriptions": paid_subs_users,
        "trial_users": trial_users,
        "inactive_users": max(0, inactive_users),
        "referral_users": referral_users
    }


async def get_user_ids_with_active_subscription(session: AsyncSession) -> List[int]:
    """Return non-banned user IDs who have an active subscription (paid or trial)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    stmt = (
        select(func.distinct(Subscription.user_id))
        .join(User, Subscription.user_id == User.user_id)
        .where(
            and_(
                User.is_banned == False,
                Subscription.is_active == True,
                Subscription.end_date > now,
            )
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_ids_without_active_subscription(session: AsyncSession) -> List[int]:
    """Return non-banned user IDs who do NOT have any active subscription."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Subquery for users with active subscription
    active_subs_subq = (
        select(Subscription.user_id)
        .where(
            and_(
                Subscription.is_active == True,
                Subscription.end_date > now,
            )
        )
    ).scalar_subquery()

    stmt = (
        select(User.user_id)
        .where(
            and_(
                User.is_banned == False,
                ~User.user_id.in_(active_subs_subq),
            )
        )
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def delete_user_and_relations(session: AsyncSession, user_id: int) -> bool:
    """Completely remove a user and all dependent records from the database.

    This helper ensures we do not leave dangling foreign keys or orphaned data.
    """
    user = await get_user_by_id(session, user_id)
    if not user:
        return False

    # Ensure referral pointers do not block deletion
    await session.execute(
        update(User).where(User.referred_by_id == user_id).values(referred_by_id=None)
    )

    # Clean up dependent tables that do not cascade automatically
    await session.execute(
        delete(MessageLog).where(
            or_(MessageLog.user_id == user_id, MessageLog.target_user_id == user_id)
        )
    )

    user_payment_ids_subquery = select(Payment.payment_id).where(Payment.user_id == user_id)

    # Keep activations of other users intact if they reference this user's payments
    await session.execute(
        update(PromoCodeActivation)
        .where(
            PromoCodeActivation.payment_id.in_(user_payment_ids_subquery),
            PromoCodeActivation.user_id != user_id,
        )
        .values(payment_id=None)
    )
    await session.execute(
        delete(PromoCodeActivation).where(PromoCodeActivation.user_id == user_id)
    )
    await session.execute(delete(Payment).where(Payment.user_id == user_id))
    await session.execute(
        delete(Subscription).where(Subscription.user_id == user_id)
    )
    await session.execute(
        delete(UserPaymentMethod).where(UserPaymentMethod.user_id == user_id)
    )
    await session.execute(delete(UserBilling).where(UserBilling.user_id == user_id))
    await session.execute(delete(AdAttribution).where(AdAttribution.user_id == user_id))

    await session.delete(user)
    await session.flush()
    return True
