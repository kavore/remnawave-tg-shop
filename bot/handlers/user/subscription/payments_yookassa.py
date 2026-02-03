import logging
from typing import List, Optional, Tuple

from aiogram import F, Router, types
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.inline.user_keyboards import (
    get_back_to_main_menu_markup,
    get_payment_url_keyboard,
    get_yk_autopay_choice_keyboard,
    get_yk_saved_cards_keyboard,
)
from bot.middlewares.i18n import JsonI18n
from bot.services.yookassa_service import YooKassaService
from config.settings import Settings
from db.dal import payment_dal, user_billing_dal, active_discount_dal

router = Router(name="user_subscription_payments_yookassa_router")


def _format_value(val: float) -> str:
    return str(int(val)) if float(val).is_integer() else f"{val:g}"


def _parse_offer_payload(payload: str) -> Optional[Tuple[float, float, str]]:
    try:
        parts = payload.split(":")
        value = float(parts[0])
        price = float(parts[1])
        sale_mode = parts[2] if len(parts) > 2 else "subscription"
        return value, price, sale_mode
    except (ValueError, IndexError):
        return None


def _format_saved_payment_method_title(get_text, network: Optional[str], last4: Optional[str], is_default: bool) -> str:
    def _is_yoomoney_network(name: Optional[str]) -> bool:
        s = (name or "").lower()
        return "yoomoney" in s or "yoo money" in s or "yoo-money" in s

    def _extract_last4(text: str) -> Optional[str]:
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[-4:] if len(digits) >= 4 else None

    if _is_yoomoney_network(network):
        inferred_last4 = last4 or (_extract_last4(network or "") or "****")
        title = get_text("payment_method_wallet_title", last4=inferred_last4)
    elif last4:
        network_name = network or get_text("payment_network_card")
        title = get_text("payment_method_card_title", network=network_name, last4=last4)
    else:
        network_name = network or get_text("payment_network_generic")
        title = get_text("payment_method_generic_title", network=network_name)
    return f"⭐ {title}" if is_default else title


async def _initiate_yk_payment(
    callback: types.CallbackQuery,
    *,
    settings: Settings,
    session: AsyncSession,
    yookassa_service: YooKassaService,
    promo_code_service,  # NEW: Added promo_code_service
    i18n: Optional[JsonI18n],
    current_lang: str,
    get_text,
    user_id: int,
    months: int,
    price_rub: float,
    currency_code_for_yk: str,
    save_payment_method: bool,
    back_callback: str,
    payment_method_id: Optional[str] = None,
    selected_method_internal_id: Optional[int] = None,
    sale_mode: str = "subscription",
) -> bool:
    """Create payment record and initiate YooKassa payment (new card or saved card)."""
    if not callback.message:
        return False

    # Check for active discount to save metadata (price already discounted from previous step)
    original_price = None
    discount_amount = None
    active_promo_code_id = None

    if promo_code_service:
        active_discount = await active_discount_dal.get_active_discount(session, user_id)
        if active_discount:
            # Price is already discounted, calculate original price backwards
            discount_pct = active_discount.discount_percentage
            active_promo_code_id = active_discount.promo_code_id
            denominator = 1 - discount_pct / 100
            if denominator <= 0:
                price_source = (
                    getattr(settings, "traffic_packages", {}) or {}
                    if sale_mode == "traffic"
                    else (settings.subscription_options or {})
                )
                fallback_original = price_source.get(months)
                if fallback_original is not None:
                    original_price = fallback_original
                    discount_amount = original_price - price_rub
                    logging.info(
                        f"Recording {discount_pct}% discount for YooKassa payment: "
                        f"original {original_price:.2f} -> final {price_rub}"
                    )
                else:
                    logging.warning(
                        "YooKassa discount %s%% has invalid denominator and no fallback price for months=%s.",
                        discount_pct,
                        months,
                    )
            else:
                original_price = price_rub / denominator
                discount_amount = original_price - price_rub
                logging.info(
                    f"Recording {discount_pct}% discount for YooKassa payment: "
                    f"original {original_price:.2f} -> final {price_rub}"
                )

    payment_description = (
        get_text("payment_description_traffic", traffic_gb=_format_value(months))
        if sale_mode == "traffic"
        else get_text("payment_description_subscription", months=int(months))
    )
    payment_record_data = {
        "user_id": user_id,
        "amount": price_rub,  # Discounted amount
        "original_amount": original_price if discount_amount else None,  # NEW
        "discount_applied": discount_amount,  # NEW
        "currency": currency_code_for_yk,
        "status": "pending_yookassa",
        "description": payment_description,
        "subscription_duration_months": int(months),
        "promo_code_id": active_promo_code_id,  # NEW: Link to promo code
    }

    db_payment_record = None
    try:
        db_payment_record = await payment_dal.create_payment_record(session, payment_record_data)
        await session.commit()
        logging.info(
            f"Payment record {db_payment_record.payment_id} created for user {user_id} with status 'pending_yookassa'."
        )
    except Exception as e_db_payment:
        await session.rollback()
        logging.error(
            f"Failed to create payment record in DB for user {user_id}: {e_db_payment}",
            exc_info=True,
        )
        try:
            await callback.message.edit_text(get_text("error_creating_payment_record"))
        except Exception:
            pass
        return False

    if not db_payment_record:
        try:
            await callback.message.edit_text(get_text("error_creating_payment_record"))
        except Exception:
            pass
        return False

    yookassa_metadata = {
        "user_id": str(user_id),
        "subscription_months": str(months),
        "payment_db_id": str(db_payment_record.payment_id),
        "sale_mode": sale_mode,
    }
    if sale_mode == "traffic":
        yookassa_metadata["traffic_gb"] = str(months)
    if payment_method_id:
        yookassa_metadata["used_saved_payment_method_id"] = payment_method_id

    receipt_email_for_yk = settings.YOOKASSA_DEFAULT_RECEIPT_EMAIL

    payment_response_yk = await yookassa_service.create_payment(
        amount=price_rub,
        currency=currency_code_for_yk,
        description=payment_description,
        metadata=yookassa_metadata,
        receipt_email=receipt_email_for_yk,
        save_payment_method=save_payment_method,
        payment_method_id=payment_method_id,
    )

    if payment_response_yk and payment_response_yk.get("confirmation_url"):
        pm = payment_response_yk.get("payment_method")
        try:
            if pm and pm.get("id"):
                pm_type = pm.get("type")
                title = pm.get("title")
                card = pm.get("card") or {}
                account_number = pm.get("account_number") or pm.get("account")
                if isinstance(card, dict) and (pm_type or "").lower() in {"bank_card", "bank-card", "card"}:
                    display_network = card.get("card_type") or title or "Card"
                    display_last4 = card.get("last4")
                elif (pm_type or "").lower() in {"yoo_money", "yoomoney", "yoo-money", "wallet"}:
                    display_network = "YooMoney"
                    display_last4 = (
                        account_number[-4:]
                        if isinstance(account_number, str) and len(account_number) >= 4
                        else None
                    )
                else:
                    display_network = title or (pm_type.upper() if pm_type else "Payment method")
                    display_last4 = None
                await user_billing_dal.upsert_yk_payment_method(
                    session,
                    user_id=user_id,
                    payment_method_id=pm["id"],
                    card_last4=display_last4,
                    card_network=display_network,
                )
                try:
                    await user_billing_dal.upsert_user_payment_method(
                        session,
                        user_id=user_id,
                        provider_payment_method_id=pm["id"],
                        provider="yookassa",
                        card_last4=display_last4,
                        card_network=display_network,
                        set_default=save_payment_method,
                    )
                except Exception:
                    pass
                await session.commit()
        except Exception:
            await session.rollback()
            logging.exception("Failed to save YooKassa payment method preliminarily")
        try:
            await payment_dal.update_payment_status_by_db_id(
                session,
                payment_db_id=db_payment_record.payment_id,
                new_status=payment_response_yk.get("status", "pending"),
                yk_payment_id=payment_response_yk.get("id"),
            )
            if selected_method_internal_id is not None:
                try:
                    await user_billing_dal.set_user_default_payment_method(
                        session, user_id, selected_method_internal_id
                    )
                except Exception:
                    logging.exception("Failed to set default payment method after initiating payment")
            await session.commit()
        except Exception as e_db_update_ykid:
            await session.rollback()
            logging.error(
                f"Failed to update payment record {db_payment_record.payment_id} with YK ID: {e_db_update_ykid}",
                exc_info=True,
            )
            try:
                await callback.message.edit_text(get_text("error_payment_gateway_link_failed"))
            except Exception:
                pass
            return False

        try:
            await callback.message.edit_text(
                get_text(
                    key="payment_link_message_traffic" if sale_mode == "traffic" else "payment_link_message",
                    months=int(months),
                    traffic_gb=_format_value(months),
                ),
                reply_markup=get_payment_url_keyboard(
                    payment_response_yk["confirmation_url"],
                    current_lang,
                    i18n,
                    back_callback=back_callback,
                    back_text_key="back_to_payment_methods_button",
                ),
                disable_web_page_preview=False,
            )
        except Exception as e_edit:
            logging.warning(
                f"Edit message for payment link failed: {e_edit}. Sending new one."
            )
            try:
                await callback.message.answer(
                    get_text(
                        key="payment_link_message_traffic" if sale_mode == "traffic" else "payment_link_message",
                        months=int(months),
                        traffic_gb=_format_value(months),
                    ),
                    reply_markup=get_payment_url_keyboard(
                        payment_response_yk["confirmation_url"],
                        current_lang,
                        i18n,
                        back_callback=back_callback,
                        back_text_key="back_to_payment_methods_button",
                    ),
                    disable_web_page_preview=False,
                )
            except Exception:
                pass
        return True

    if payment_response_yk and payment_method_id:
        status_to_store = payment_response_yk.get("status", "pending")
        try:
            await payment_dal.update_payment_status_by_db_id(
                session,
                payment_db_id=db_payment_record.payment_id,
                new_status=status_to_store,
                yk_payment_id=payment_response_yk.get("id"),
            )
            if selected_method_internal_id is not None:
                try:
                    await user_billing_dal.set_user_default_payment_method(
                        session, user_id, selected_method_internal_id
                    )
                except Exception:
                    logging.exception("Failed to set default payment method after saved-card payment start")
            await session.commit()
        except Exception as e_db_update_saved:
            await session.rollback()
            logging.error(
                f"Failed to update saved-card payment record {db_payment_record.payment_id}: {e_db_update_saved}",
                exc_info=True,
            )
            try:
                await callback.message.edit_text(get_text("error_payment_gateway"))
            except Exception:
                pass
            return False

        message_text = get_text("yookassa_autopay_charge_initiated")
        try:
            await callback.message.edit_text(
                message_text,
                reply_markup=get_back_to_main_menu_markup(current_lang, i18n),
            )
        except Exception as e_edit:
            logging.warning(f"Failed to notify about saved-card charge start: {e_edit}")
            try:
                await callback.message.answer(
                    message_text,
                    reply_markup=get_back_to_main_menu_markup(current_lang, i18n),
                )
            except Exception:
                pass
        return True

    try:
        await payment_dal.update_payment_status_by_db_id(
            session, db_payment_record.payment_id, "failed_creation"
        )
        await session.commit()
    except Exception as e_db_fail_create:
        await session.rollback()
        logging.error(
            f"Additionally failed to update payment record to 'failed_creation': {e_db_fail_create}",
            exc_info=True,
        )
    logging.error(
        f"Failed to create payment in YooKassa for user {user_id}, payment_db_id {db_payment_record.payment_id}. Response: {payment_response_yk}"
    )
    try:
        await callback.message.edit_text(get_text("error_payment_gateway"))
    except Exception:
        pass
    return False


@router.callback_query(F.data.startswith("pay_yk:"))
async def pay_yk_callback_handler(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, yookassa_service: YooKassaService, session: AsyncSession, promo_code_service=None):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    if not yookassa_service or not yookassa_service.configured:
        logging.error("YooKassa service is not configured or unavailable.")
        target_msg_edit = callback.message
        await target_msg_edit.edit_text(get_text("payment_service_unavailable"))
        try:
            await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
    except ValueError:
        logging.error(f"Invalid pay_yk data in callback: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    parsed = _parse_offer_payload(data_payload)
    if not parsed:
        logging.error(f"Invalid pay_yk payload structure: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    months, price_rub, sale_mode = parsed
    user_id = callback.from_user.id
    currency_code_for_yk = "RUB"
    autopay_enabled = bool(settings.yookassa_autopayments_active and sale_mode != "traffic" and not settings.traffic_sale_mode)
    autopay_require_binding = bool(
        getattr(settings, 'YOOKASSA_AUTOPAYMENTS_REQUIRE_CARD_BINDING', True)
    )
    saved_methods: List = []
    if autopay_enabled:
        try:
            saved_methods = await user_billing_dal.list_user_payment_methods(
                session, user_id, provider="yookassa"
            )
        except Exception as e_list:
            logging.exception(f"Failed to load saved payment methods for user {user_id}: {e_list}")
            saved_methods = []

    if autopay_enabled and saved_methods:
        try:
            await callback.message.edit_text(
                get_text("yookassa_autopay_flow_prompt"),
                reply_markup=get_yk_autopay_choice_keyboard(
                    months,
                    price_rub,
                    current_lang,
                    i18n,
                    has_saved_cards=True,
                    sale_mode=sale_mode,
                ),
            )
        except Exception as e_edit:
            logging.warning(f"Failed to show autopay choice: {e_edit}. Sending new message.")
            try:
                await callback.message.answer(
                    get_text("yookassa_autopay_flow_prompt"),
                    reply_markup=get_yk_autopay_choice_keyboard(
                        months,
                        price_rub,
                        current_lang,
                        i18n,
                        has_saved_cards=True,
                        sale_mode=sale_mode,
                    ),
                )
            except Exception:
                pass
        try:
            await callback.answer()
        except Exception:
            pass
        return

    await _initiate_yk_payment(
        callback,
        settings=settings,
        session=session,
        yookassa_service=yookassa_service,
        promo_code_service=promo_code_service,
        i18n=i18n,
        current_lang=current_lang,
        get_text=get_text,
        user_id=user_id,
        months=months,
        price_rub=price_rub,
        currency_code_for_yk=currency_code_for_yk,
        save_payment_method=autopay_enabled and autopay_require_binding,
        back_callback=f"subscribe_period:{_format_value(months)}",
        sale_mode=sale_mode,
    )
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("pay_yk_new:"))
async def pay_yk_new_card_handler(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, yookassa_service: YooKassaService, session: AsyncSession, promo_code_service=None):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    if not yookassa_service or not yookassa_service.configured:
        logging.error("YooKassa service unavailable for pay_yk_new.")
        try:
            await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
        except Exception:
            pass
        try:
            await callback.message.edit_text(get_text("payment_service_unavailable"))
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
    except ValueError:
        logging.error(f"Invalid pay_yk_new data in callback: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    parsed = _parse_offer_payload(data_payload)
    if not parsed:
        logging.error(f"Invalid pay_yk_new payload structure: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    months, price_rub, sale_mode = parsed
    user_id = callback.from_user.id
    currency_code_for_yk = "RUB"
    autopay_enabled = bool(settings.yookassa_autopayments_active and sale_mode != "traffic" and not settings.traffic_sale_mode)
    autopay_require_binding = bool(
        getattr(settings, 'YOOKASSA_AUTOPAYMENTS_REQUIRE_CARD_BINDING', True)
    )

    await _initiate_yk_payment(
        callback,
        settings=settings,
        session=session,
        yookassa_service=yookassa_service,
        promo_code_service=promo_code_service,
        i18n=i18n,
        current_lang=current_lang,
        get_text=get_text,
        user_id=user_id,
        months=months,
        price_rub=price_rub,
        currency_code_for_yk=currency_code_for_yk,
        save_payment_method=autopay_enabled and autopay_require_binding,
        back_callback=f"subscribe_period:{_format_value(months)}",
        sale_mode=sale_mode,
    )
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("pay_yk_saved_list:"))
async def pay_yk_saved_list_handler(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, yookassa_service: YooKassaService, session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
    except ValueError:
        logging.error(f"Invalid pay_yk_saved_list data: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    parts = data_payload.split(":")
    if len(parts) < 2:
        logging.error(f"pay_yk_saved_list payload missing components: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    try:
        months = float(parts[0])
        price_rub = float(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
        sale_mode = parts[3] if len(parts) > 3 else "subscription"
    except (ValueError, IndexError):
        logging.error(f"pay_yk_saved_list payload parsing error: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    autopay_enabled = bool(settings.yookassa_autopayments_active and sale_mode != "traffic" and not settings.traffic_sale_mode)
    if not autopay_enabled:
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    user_id = callback.from_user.id
    try:
        saved_methods = await user_billing_dal.list_user_payment_methods(
            session, user_id, provider="yookassa"
        )
    except Exception as e_list:
        logging.exception(f"Failed to list saved payment methods for user {user_id}: {e_list}")
        saved_methods = []

    if not saved_methods:
        try:
            await callback.message.edit_text(
                get_text("yookassa_autopay_no_saved_cards"),
                reply_markup=get_yk_autopay_choice_keyboard(
                    months,
                    price_rub,
                    current_lang,
                    i18n,
                    has_saved_cards=False,
                    sale_mode=sale_mode,
                ),
            )
        except Exception as e_edit:
            logging.warning(f"Failed to display no-saved-card notice: {e_edit}")
            try:
                await callback.message.answer(
                    get_text("yookassa_autopay_no_saved_cards"),
                    reply_markup=get_yk_autopay_choice_keyboard(
                        months,
                        price_rub,
                        current_lang,
                        i18n,
                        has_saved_cards=False,
                        sale_mode=sale_mode,
                    ),
                )
            except Exception:
                pass
        try:
            await callback.answer()
        except Exception:
            pass
        return

    cards: List[Tuple[str, str]] = []
    for method in saved_methods:
        title = _format_saved_payment_method_title(
            get_text, method.card_network, method.card_last4, method.is_default
        )
        cards.append((str(method.method_id), title))

    per_page = 5
    max_page = max(0, (len(cards) - 1) // per_page)
    page = max(0, min(page, max_page))

    try:
        await callback.message.edit_text(
            get_text("yookassa_autopay_choose_saved_card"),
            reply_markup=get_yk_saved_cards_keyboard(
                cards,
                months,
                price_rub,
                current_lang,
                i18n,
                page=page,
                sale_mode=sale_mode,
            ),
        )
    except Exception as e_edit:
        logging.warning(f"Failed to display saved card list: {e_edit}")
        try:
            await callback.message.answer(
                get_text("yookassa_autopay_choose_saved_card"),
                reply_markup=get_yk_saved_cards_keyboard(
                    cards,
                    months,
                    price_rub,
                    current_lang,
                    i18n,
                    page=page,
                    sale_mode=sale_mode,
                ),
            )
        except Exception:
            pass
    try:
        await callback.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("pay_yk_use_saved:"))
async def pay_yk_use_saved_handler(callback: types.CallbackQuery, settings: Settings, i18n_data: dict, yookassa_service: YooKassaService, session: AsyncSession, promo_code_service=None):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    get_text = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(get_text("error_occurred_try_again"), show_alert=True)
        except Exception:
            pass
        return

    if not yookassa_service or not yookassa_service.configured:
        logging.error("YooKassa service unavailable for pay_yk_use_saved.")
        try:
            await callback.answer(get_text("payment_service_unavailable_alert"), show_alert=True)
        except Exception:
            pass
        try:
            await callback.message.edit_text(get_text("payment_service_unavailable"))
        except Exception:
            pass
        return

    try:
        _, data_payload = callback.data.split(":", 1)
    except ValueError:
        logging.error(f"Invalid pay_yk_use_saved data: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    parts = data_payload.split(":")
    if len(parts) < 3:
        logging.error(f"pay_yk_use_saved payload missing components: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    try:
        months = float(parts[0])
        price_rub = float(parts[1])
        sale_mode = parts[3] if len(parts) > 3 else "subscription"
    except (ValueError, IndexError):
        logging.error(f"pay_yk_use_saved months/price parsing error: {callback.data}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    autopay_enabled = bool(settings.yookassa_autopayments_active and sale_mode != "traffic" and not settings.traffic_sale_mode)
    if not autopay_enabled:
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    method_identifier = parts[2]
    user_id = callback.from_user.id

    try:
        saved_methods = await user_billing_dal.list_user_payment_methods(
            session, user_id, provider="yookassa"
        )
    except Exception as e_list:
        logging.exception(f"Failed to list saved payment methods for user {user_id}: {e_list}")
        saved_methods = []

    selected_method = None
    for method in saved_methods:
        if method_identifier.isdigit():
            if method.method_id == int(method_identifier):
                selected_method = method
                break
        if method.provider_payment_method_id == method_identifier:
            selected_method = method
            break

    if not selected_method:
        logging.warning(f"Selected payment method not found for user {user_id}: {method_identifier}")
        try:
            await callback.answer(get_text("error_try_again"), show_alert=True)
        except Exception:
            pass
        return

    currency_code_for_yk = "RUB"

    await _initiate_yk_payment(
        callback,
        settings=settings,
        session=session,
        yookassa_service=yookassa_service,
        promo_code_service=promo_code_service,
        i18n=i18n,
        current_lang=current_lang,
        get_text=get_text,
        user_id=user_id,
        months=months,
        price_rub=price_rub,
        currency_code_for_yk=currency_code_for_yk,
        save_payment_method=False,
        back_callback=f"pay_yk_saved_list:{_format_value(months)}:{price_rub}:{sale_mode}",
        payment_method_id=selected_method.provider_payment_method_id,
        selected_method_internal_id=selected_method.method_id,
        sale_mode=sale_mode,
    )
    try:
        await callback.answer()
    except Exception:
        pass
