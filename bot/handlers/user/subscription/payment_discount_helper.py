"""
Helper функция для применения скидок к платежам
Используется всеми платежными обработчиками
"""
import logging
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from db.dal import active_discount_dal


async def apply_discount_to_payment(
    session: AsyncSession,
    user_id: int,
    original_price: float,
    promo_code_service=None
) -> Tuple[float, Optional[float], Optional[int]]:
    """
    Apply active discount to payment if exists.

    Returns:
        (final_price, discount_amount, promo_code_id)
    """
    if not promo_code_service:
        return original_price, None, None

    active_discount = await active_discount_dal.get_active_discount(session, user_id)
    if not active_discount:
        return original_price, None, None

    # Calculate discounted price
    final_price, discount_amount = promo_code_service.calculate_discounted_price(
        original_price, active_discount.discount_percentage
    )

    logging.info(
        f"Applying {active_discount.discount_percentage}% discount to payment for user {user_id}: "
        f"{original_price} -> {final_price}"
    )

    return final_price, discount_amount, active_discount.promo_code_id
