import json
import logging
from typing import Optional

from aiogram import F, Router, types
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.inline.user_keyboards import get_payment_url_keyboard
from bot.middlewares.i18n import JsonI18n
from bot.services.platega_service import PlategaService
from config.settings import Settings
from db.dal import payment_dal

router = Router(name="user_subscription_payments_platega_router")


from bot.handlers.user.subscription.payments_subscription import resolve_fiat_offer_price_for_user

@router.callback_query(F.data.startswith("pay_platega:"))
async def pay_platega_callback_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    platega_service: PlategaService,
    session: AsyncSession,
    promo_code_service=None,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        return

    if not platega_service or not platega_service.configured:
        logging.error("Platega service is not configured or unavailable.")
        try:
            await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        try:
            await callback.message.edit_text(get_text("payment_service_unavailable"))
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        return

    try:
        _, data_payload = callback.data.split(":", 1)
        parts = data_payload.split(":")
        months = float(parts[0])
        callback_price_rub = float(parts[1])
        sale_mode = parts[2] if len(parts) > 2 else "subscription"
    except (ValueError, IndexError):
        logging.error(f"Invalid pay_platega data in callback: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        return

    user_id = callback.from_user.id
    resolved_price_rub = await resolve_fiat_offer_price_for_user(
        session=session,
        settings=settings,
        user_id=user_id,
        months=months,
        sale_mode=sale_mode,
        promo_code_service=promo_code_service,
    )
    if resolved_price_rub is None:
        logging.warning(
            "Platega: no server-side price for user %s, value=%s, mode=%s",
            user_id,
            months,
            sale_mode,
        )
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        return

    if abs(resolved_price_rub - callback_price_rub) > 0.01:
        logging.warning(
            "Platega: callback price mismatch for user %s, value=%s, mode=%s, callback=%.2f, resolved=%.2f",
            user_id,
            months,
            sale_mode,
            callback_price_rub,
            resolved_price_rub,
        )
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        return

    price_rub = resolved_price_rub
    human_value = str(int(months)) if float(months).is_integer() else f"{months:g}"
    payment_description = (
        get_text("payment_description_traffic", traffic_gb=human_value)
        if sale_mode == "traffic"
        else get_text("payment_description_subscription", months=int(months))
    )
    currency_code = "RUB"

    # Price is already discounted at payments_subscription.py stage
    # Service will handle discount metadata if needed
    payment_record_payload = {
        "user_id": user_id,
        "amount": price_rub,
        "original_amount": None,
        "discount_applied": None,
        "currency": currency_code,
        "status": "pending_platega",
        "description": payment_description,
        "subscription_duration_months": int(months),
        "provider": "platega",
        "promo_code_id": None,
    }

    try:
        payment_record = await payment_dal.create_payment_record(session, payment_record_payload)
        await session.commit()
    except Exception as e_db_create:
        await session.rollback()
        logging.error(
            f"Platega: failed to create payment record for user {user_id}: {e_db_create}",
            exc_info=True,
        )
        try:
            await callback.message.edit_text(get_text("error_creating_payment_record"))
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
        return

    payload_meta = json.dumps(
        {
            "payment_db_id": payment_record.payment_id,
            "user_id": user_id,
            "months": months,
            "sale_mode": sale_mode,
        }
    )

    success, response_data = await platega_service.create_transaction(
        payment_db_id=payment_record.payment_id,
        user_id=user_id,
        months=months,
        amount=price_rub,
        currency=currency_code,
        description=payment_description,
        payload=payload_meta,
        promo_code_service=promo_code_service,
        session=session,
    )

    if success:
        transaction_id = response_data.get("transactionId") or response_data.get("id")
        redirect_url = (
            response_data.get("redirect")
            or response_data.get("url")
            or response_data.get("paymentUrl")
        )
        provider_status = response_data.get("status", payment_record.status)

        if transaction_id and redirect_url:
            try:
                await payment_dal.update_provider_payment_and_status(
                    session,
                    payment_record.payment_id,
                    str(transaction_id),
                    str(provider_status),
                )
                await session.commit()
            except Exception as e_status:
                await session.rollback()
                logging.error(
                    f"Platega: failed to store transaction id for payment {payment_record.payment_id}: {e_status}",
                    exc_info=True,
                )

            try:
                await callback.message.edit_text(
                    get_text(
                        key="payment_link_message_traffic" if sale_mode == "traffic" else "payment_link_message",
                        months=int(months),
                        traffic_gb=human_value,
                    ),
                    reply_markup=get_payment_url_keyboard(
                        redirect_url,
                        current_lang,
                        i18n,
                        back_callback=f"subscribe_period:{human_value}",
                        back_text_key="back_to_payment_methods_button",
                    ),
                    disable_web_page_preview=False,
                )
            except Exception as e_edit:
                logging.warning(f"Platega: failed to display payment link ({e_edit}), sending new message.")
                try:
                    await callback.message.answer(
                        get_text(
                            key="payment_link_message_traffic" if sale_mode == "traffic" else "payment_link_message",
                            months=int(months),
                            traffic_gb=human_value,
                        ),
                        reply_markup=get_payment_url_keyboard(
                            redirect_url,
                            current_lang,
                            i18n,
                            back_callback=f"subscribe_period:{human_value}",
                            back_text_key="back_to_payment_methods_button",
                        ),
                        disable_web_page_preview=False,
                    )
                except Exception as exc:
                    logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
            try:
                await callback.answer()
            except Exception as exc:
                logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
            return

        logging.error(
            "Platega: transaction created but missing transaction id or payment link for payment %s. Response: %s",
            payment_record.payment_id,
            response_data,
        )

    try:
        await payment_dal.update_payment_status_by_db_id(
            session,
            payment_record.payment_id,
            "failed_creation",
        )
        await session.commit()
    except Exception as e_status:
        await session.rollback()
        logging.error(f"Platega: failed to mark payment {payment_record.payment_id} as failed_creation: {e_status}", exc_info=True)

    try:
        await callback.message.edit_text(get_text("error_payment_gateway"))
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
    try:
        await callback.answer(get_text("error_payment_gateway"), show_alert=True)
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/user/subscription/payments_platega.py: %s", exc)
