import logging
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, func, and_
from sqlalchemy.orm import selectinload

from db.models import Payment, User


async def create_payment_record(session: AsyncSession,
                                payment_data: Dict[str, Any]) -> Payment:

    from .user_dal import get_user_by_id
    user = await get_user_by_id(session, payment_data["user_id"])
    if not user:

        raise ValueError(
            f"User with id {payment_data['user_id']} not found for creating payment."
        )

    if payment_data.get("promo_code_id"):
        from .promo_code_dal import get_promo_code_by_id
        promo = await get_promo_code_by_id(session,
                                           payment_data["promo_code_id"])
        if not promo:
            raise ValueError(
                f"Promo code with id {payment_data['promo_code_id']} not found."
            )

    new_payment = Payment(**payment_data)
    session.add(new_payment)
    await session.flush()
    await session.refresh(new_payment)
    logging.info(
        f"Payment record {new_payment.payment_id} created for user {new_payment.user_id}"
    )
    return new_payment


async def get_payment_by_provider_payment_id(
        session: AsyncSession, provider_payment_id: str) -> Optional[Payment]:
    """Fetch a payment by provider-specific identifier."""
    stmt = select(Payment).where(
        Payment.provider_payment_id == provider_payment_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def ensure_payment_with_provider_id(
        session: AsyncSession,
        *,
        user_id: int,
        amount: float,
        currency: str,
        months: int,
        description: str,
        provider: str,
        provider_payment_id: str) -> Payment:
    """Idempotently create a payment record for a provider event.

    If a payment with the same provider_payment_id already exists, returns it.
    Otherwise creates a new pending payment with provided data.
    """
    existing = await get_payment_by_provider_payment_id(session, provider_payment_id)
    if existing:
        return existing

    pending_status = f"pending_{provider}" if provider else "pending"
    payment_payload: Dict[str, Any] = {
        "user_id": user_id,
        "amount": float(amount),
        "currency": currency,
        "status": pending_status,
        "description": description,
        "subscription_duration_months": months,
        "provider_payment_id": provider_payment_id,
        "provider": provider,
    }
    return await create_payment_record(session, payment_payload)


async def get_payment_by_db_id(session: AsyncSession,
                               payment_db_id: int) -> Optional[Payment]:

    stmt = select(Payment).where(Payment.payment_id == payment_db_id).options(
        selectinload(Payment.user), selectinload(Payment.promo_code_used))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_payment_status_by_db_id(
        session: AsyncSession,
        payment_db_id: int,
        new_status: str,
        yk_payment_id: Optional[str] = None) -> Optional[Payment]:
    payment = await get_payment_by_db_id(session, payment_db_id)
    if payment:
        payment.status = new_status
        payment.updated_at = func.now()
        if yk_payment_id and payment.yookassa_payment_id is None:
            payment.yookassa_payment_id = yk_payment_id
        await session.flush()
        await session.refresh(payment)
        logging.info(
            f"Payment record {payment.payment_id} status updated to {new_status}."
        )
    else:
        logging.warning(
            f"Payment record with DB ID {payment_db_id} not found for status update."
        )
    return payment


async def get_recent_payment_logs_with_user(session: AsyncSession,
                                            limit: int = 20,
                                            offset: int = 0) -> List[Payment]:
    stmt = (select(Payment).options(selectinload(Payment.user))
            .where(Payment.status == 'succeeded')
            .order_by(Payment.created_at.desc())
            .limit(limit).offset(offset))
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_payments_count(session: AsyncSession) -> int:
    """Get total count of successful payments."""
    stmt = select(func.count(Payment.payment_id)).where(Payment.status == 'succeeded')
    result = await session.execute(stmt)
    return result.scalar() or 0


async def get_all_succeeded_payments_with_user(session: AsyncSession) -> List[Payment]:
    """Get all successful payments with user data for export."""
    stmt = (select(Payment).options(selectinload(Payment.user))
            .where(Payment.status == 'succeeded')
            .order_by(Payment.created_at.desc()))
    result = await session.execute(stmt)
    return result.scalars().all()


async def count_user_succeeded_payments(
    session: AsyncSession, user_id: int, exclude_payment_id: Optional[int] = None
) -> int:
    """Count succeeded payments for a specific user.

    If exclude_payment_id is provided, that specific payment will be excluded
    from the count. Useful to check "prior" payments while processing the
    current payment in the same transaction.
    """
    conditions = [Payment.user_id == user_id, Payment.status == 'succeeded']
    if exclude_payment_id is not None:
        conditions.append(Payment.payment_id != exclude_payment_id)
    stmt = select(func.count(Payment.payment_id)).where(and_(*conditions))
    result = await session.execute(stmt)
    return result.scalar() or 0


async def update_provider_payment_and_status(
        session: AsyncSession, payment_db_id: int,
        provider_payment_id: str, new_status: str) -> Optional[Payment]:
    payment = await get_payment_by_db_id(session, payment_db_id)
    if payment:
        payment.status = new_status
        payment.provider_payment_id = provider_payment_id
        payment.updated_at = func.now()
        await session.flush()
        await session.refresh(payment)
        logging.info(
            f"Payment record {payment.payment_id} updated with provider id {provider_payment_id} and status {new_status}."
        )
    else:
        logging.warning(
            f"Payment record with DB ID {payment_db_id} not found for provider update."
        )
    return payment


async def update_payment_discount_info(
        session: AsyncSession,
        payment_db_id: int,
        original_amount: Optional[float],
        discount_applied: Optional[float],
        promo_code_id: Optional[int]) -> Optional[Payment]:
    """Update payment record with discount metadata."""
    payment = await get_payment_by_db_id(session, payment_db_id)
    if payment:
        payment.original_amount = original_amount
        payment.discount_applied = discount_applied
        payment.promo_code_id = promo_code_id
        payment.updated_at = func.now()
        await session.flush()
        await session.refresh(payment)
        logging.info(
            f"Payment record {payment.payment_id} updated with discount info: "
            f"original {original_amount}, discount {discount_applied}, promo {promo_code_id}"
        )
    else:
        logging.warning(
            f"Payment record with DB ID {payment_db_id} not found for discount info update."
        )
    return payment


async def get_financial_statistics(session: AsyncSession) -> Dict[str, Any]:
    """Get comprehensive financial statistics."""
    from datetime import datetime, timedelta
    from sqlalchemy import and_, text
    
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)
    
    # Today's revenue
    stmt_today = select(func.sum(Payment.amount)).where(
        and_(
            Payment.status == 'succeeded',
            Payment.created_at >= today_start
        )
    )
    today_revenue = await session.execute(stmt_today)
    today_amount = today_revenue.scalar() or 0
    
    # Week revenue
    stmt_week = select(func.sum(Payment.amount)).where(
        and_(
            Payment.status == 'succeeded',
            Payment.created_at >= week_start
        )
    )
    week_revenue = await session.execute(stmt_week)
    week_amount = week_revenue.scalar() or 0
    
    # Month revenue
    stmt_month = select(func.sum(Payment.amount)).where(
        and_(
            Payment.status == 'succeeded',
            Payment.created_at >= month_start
        )
    )
    month_revenue = await session.execute(stmt_month)
    month_amount = month_revenue.scalar() or 0
    
    # All time revenue
    stmt_all = select(func.sum(Payment.amount)).where(Payment.status == 'succeeded')
    all_revenue = await session.execute(stmt_all)
    all_amount = all_revenue.scalar() or 0
    
    # Count of successful payments today
    stmt_count_today = select(func.count(Payment.payment_id)).where(
        and_(
            Payment.status == 'succeeded',
            Payment.created_at >= today_start
        )
    )
    today_count = await session.execute(stmt_count_today)
    today_payments_count = today_count.scalar() or 0
    
    return {
        "today_revenue": float(today_amount),
        "week_revenue": float(week_amount),
        "month_revenue": float(month_amount),
        "all_time_revenue": float(all_amount),
        "today_payments_count": today_payments_count
    }


async def get_user_total_paid(session: AsyncSession, user_id: int) -> float:
    """Get total amount paid by a specific user (sum of all succeeded payments)."""
    stmt = select(func.sum(Payment.amount)).where(
        and_(
            Payment.user_id == user_id,
            Payment.status == 'succeeded'
        )
    )
    result = await session.execute(stmt)
    total = result.scalar()
    return float(total or 0)


async def get_referral_revenue(session: AsyncSession, referrer_id: int) -> float:
    """Get total revenue generated from referred users' payments.
    
    This calculates the sum of all succeeded payments made by users
    where referred_by_id equals the referrer_id.
    """
    from db.models import User
    
    stmt = select(func.sum(Payment.amount)).join(
        User, Payment.user_id == User.user_id
    ).where(
        and_(
            User.referred_by_id == referrer_id,
            Payment.status == 'succeeded'
        )
    )
    result = await session.execute(stmt)
    total = result.scalar()
    return float(total or 0)
