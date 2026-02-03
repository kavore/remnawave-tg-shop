import logging
import math
from typing import Optional

from aiogram import F, Router, types
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.inline.user_keyboards import get_payment_method_keyboard
from bot.middlewares.i18n import JsonI18n
from config.settings import Settings

router = Router(name="user_subscription_payments_selection_router")


@router.callback_query(F.data.startswith("subscribe_period:"))
async def select_subscription_period_callback_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
    promo_code_service=None,  # Injected from dispatcher
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    traffic_packages = getattr(settings, "traffic_packages", {}) or {}
    stars_traffic_packages = getattr(settings, "stars_traffic_packages", {}) or {}
    traffic_mode = bool(getattr(settings, "traffic_sale_mode", False) or stars_traffic_packages)
    try:
        months = float(callback.data.split(":")[-1])
    except (ValueError, IndexError):
        logging.error(f"Invalid subscription period in callback_data: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    price_source = traffic_packages if traffic_mode else settings.subscription_options
    stars_price_source = stars_traffic_packages if traffic_mode else settings.stars_subscription_options

    price_rub = price_source.get(months)
    stars_price = stars_price_source.get(months)
    currency_symbol_val = settings.DEFAULT_CURRENCY_SYMBOL

    # Check for active discount and apply if exists
    discount_text = ""
    if promo_code_service and (price_rub is not None or stars_price is not None):
        active_discount_info = await promo_code_service.get_user_active_discount(
            session, callback.from_user.id
        )

        if active_discount_info:
            discount_pct, promo_code = active_discount_info
            if price_rub is not None:
                original_price_rub = price_rub
                price_rub, discount_amt = promo_code_service.calculate_discounted_price(
                    price_rub, discount_pct
                )
                discount_text = get_text(
                    "active_discount_notice",
                    code=promo_code,
                    discount_pct=discount_pct,
                    original_price=original_price_rub,
                    discounted_price=price_rub,
                    discount_amount=discount_amt,
                    currency_symbol=currency_symbol_val,
                )
            if stars_price is not None:
                original_stars_price = stars_price
                discounted_stars_price, _ = promo_code_service.calculate_discounted_price(
                    float(stars_price), discount_pct
                )
                discounted_stars_price = math.ceil(discounted_stars_price)
                stars_price = discounted_stars_price
                if not discount_text:
                    discount_amt = original_stars_price - discounted_stars_price
                    discount_text = get_text(
                        "active_discount_notice",
                        code=promo_code,
                        discount_pct=discount_pct,
                        original_price=original_stars_price,
                        discounted_price=discounted_stars_price,
                        discount_amount=discount_amt,
                        currency_symbol="⭐",
                    )

    if price_rub is None:
        if traffic_mode and not price_source and stars_price is not None:
            currency_methods_enabled = any(
                [
                    settings.FREEKASSA_ENABLED,
                    settings.PLATEGA_ENABLED,
                    settings.SEVERPAY_ENABLED,
                    settings.YOOKASSA_ENABLED,
                    settings.CRYPTOPAY_ENABLED,
                ]
            )
            if currency_methods_enabled:
                logging.error(
                    "Currency price missing for traffic option %s while fiat providers are enabled.",
                    months,
                )
                try:
                    await callback.answer(get_text("error_try_again"), show_alert=True)
                except Exception:
                    pass
                return
            price_rub = 0.0
            currency_symbol_val = "⭐"
        else:
            logging.error(
                f"Price not found for option {months} using {'traffic_packages' if traffic_mode else 'subscription_options'}."
            )
            try:
                await callback.answer(get_text("error_try_again"), show_alert=True)
            except Exception:
                pass
            return

    text_content = get_text("choose_payment_method_traffic") if traffic_mode else get_text("choose_payment_method")
    if discount_text:
        text_content = f"{discount_text}\n\n{text_content}"

    reply_markup = get_payment_method_keyboard(
        months,
        price_rub,
        stars_price,
        currency_symbol_val,
        current_lang,
        i18n,
        settings,
        sale_mode="traffic" if traffic_mode else "subscription",
    )

    try:
        await callback.message.edit_text(text_content, reply_markup=reply_markup)
    except Exception as e_edit:
        logging.warning(
            f"Edit message for payment method selection failed: {e_edit}. Sending new one."
        )
        await callback.message.answer(text_content, reply_markup=reply_markup)
    try:
        await callback.answer()
    except Exception:
        pass
