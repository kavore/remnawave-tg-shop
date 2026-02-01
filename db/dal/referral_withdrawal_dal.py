import logging
from typing import Optional, List
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, func
from sqlalchemy.orm import selectinload

from db.models import ReferralWithdrawRequest


async def create_withdraw_request(
    session: AsyncSession,
    *,
    user_id: int,
    amount: float,
    contact: str,
    status: str = "pending",
) -> ReferralWithdrawRequest:
    request = ReferralWithdrawRequest(
        user_id=user_id,
        amount=float(amount),
        contact=contact,
        status=status,
    )
    session.add(request)
    await session.flush()
    await session.refresh(request)
    logging.info(
        "Referral withdraw request %s created for user %s (amount=%.2f)",
        request.request_id,
        user_id,
        amount,
    )
    return request


async def get_pending_request_by_user(
    session: AsyncSession, user_id: int
) -> Optional[ReferralWithdrawRequest]:
    stmt = (
        select(ReferralWithdrawRequest)
        .where(
            ReferralWithdrawRequest.user_id == user_id,
            ReferralWithdrawRequest.status == "pending",
        )
        .order_by(ReferralWithdrawRequest.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_request_by_id(
    session: AsyncSession, request_id: int
) -> Optional[ReferralWithdrawRequest]:
    stmt = (
        select(ReferralWithdrawRequest)
        .where(ReferralWithdrawRequest.request_id == request_id)
        .options(selectinload(ReferralWithdrawRequest.user))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_requests(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> List[ReferralWithdrawRequest]:
    stmt = select(ReferralWithdrawRequest).options(selectinload(ReferralWithdrawRequest.user))
    if status:
        stmt = stmt.where(ReferralWithdrawRequest.status == status)
    stmt = stmt.order_by(ReferralWithdrawRequest.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()


async def count_requests(session: AsyncSession, *, status: Optional[str] = None) -> int:
    stmt = select(func.count(ReferralWithdrawRequest.request_id))
    if status:
        stmt = stmt.where(ReferralWithdrawRequest.status == status)
    result = await session.execute(stmt)
    return result.scalar() or 0


async def update_request_status(
    session: AsyncSession,
    request_id: int,
    *,
    status: str,
    processed_by_admin_id: Optional[int] = None,
    admin_comment: Optional[str] = None,
) -> Optional[ReferralWithdrawRequest]:
    stmt = (
        update(ReferralWithdrawRequest)
        .where(ReferralWithdrawRequest.request_id == request_id)
        .values(
            status=status,
            processed_at=datetime.now(timezone.utc),
            processed_by_admin_id=processed_by_admin_id,
            admin_comment=admin_comment,
        )
        .returning(ReferralWithdrawRequest.request_id)
    )
    result = await session.execute(stmt)
    updated_id = result.scalar_one_or_none()
    if not updated_id:
        return None
    return await get_request_by_id(session, request_id)
