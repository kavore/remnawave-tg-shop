import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from aiohttp import web
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from yookassa.domain.notification import WebhookNotification
from yookassa.domain.models.amount import Amount as YooKassaAmount

from db.dal import payment_dal, user_dal, user_billing_dal

from bot.services.subscription_service import SubscriptionService
from bot.services.referral_service import ReferralService
from bot.services.panel_api_service import PanelApiService
from bot.services.yookassa_service import YooKassaService
from bot.services.lknpd_service import LknpdService
from bot.middlewares.i18n import JsonI18n
from config.settings import Settings
from bot.services.notification_service import NotificationService
from bot.keyboards.inline.user_keyboards import get_connect_and_main_keyboard
from bot.utils.text_sanitizer import sanitize_display_name, username_for_display
from bot.utils.config_link import prepare_config_links

YOOKASSA_EVENT_PAYMENT_SUCCEEDED = 'payment.succeeded'
YOOKASSA_EVENT_PAYMENT_CANCELED = 'payment.canceled'
YOOKASSA_EVENT_PAYMENT_WAITING_FOR_CAPTURE = 'payment.waiting_for_capture'


async def process_successful_payment(session: AsyncSession, bot: Bot,
                                     payment_info_from_webhook: dict,
                                     i18n: JsonI18n, settings: Settings,
                                     panel_service: PanelApiService,
                                     subscription_service: SubscriptionService,
                                     referral_service: ReferralService,
                                     yookassa_service: Optional[YooKassaService] = None,
                                     lknpd_service: Optional[LknpdService] = None) -> bool:
    metadata = payment_info_from_webhook.get("metadata", {})
    user_id_str = metadata.get("user_id")
    subscription_months_str = metadata.get("subscription_months")
    traffic_gb_str = metadata.get("traffic_gb")
    sale_mode = metadata.get("sale_mode") or ("traffic" if settings.traffic_sale_mode else "subscription")
    promo_code_id_str = metadata.get("promo_code_id")
    payment_db_id_str = metadata.get("payment_db_id")
    auto_renew_subscription_id_str = metadata.get(
        "auto_renew_for_subscription_id")

    if (
        not user_id_str
        or (not subscription_months_str and not traffic_gb_str)
        or not payment_db_id_str
    ):
        logging.error(
            f"Missing crucial metadata for payment: {payment_info_from_webhook.get('id')}, metadata: {metadata}"
        )
        return False

    db_user = None
    try:
        user_id = int(user_id_str)
        subscription_months = float(subscription_months_str or 0)
        traffic_amount_gb = float(traffic_gb_str) if traffic_gb_str else subscription_months
        if not payment_db_id_str.isdigit():
            logging.error(
                "Invalid payment_db_id metadata for payment %s: %s",
                payment_info_from_webhook.get("id"),
                payment_db_id_str,
            )
            return False
        payment_db_id = int(payment_db_id_str)
        is_auto_renew = bool(auto_renew_subscription_id_str and sale_mode != "traffic")
        promo_code_id = int(
            promo_code_id_str
        ) if promo_code_id_str and promo_code_id_str.isdigit() else None

        amount_data = payment_info_from_webhook.get("amount", {})
        payment_value = float(amount_data.get("value", 0.0))
        yk_payment_id_from_hook = payment_info_from_webhook.get("id")

        payment_record = await payment_dal.get_payment_by_db_id(session, payment_db_id)
        if not payment_record:
            logging.error(
                f"Payment record {payment_db_id} not found for YK ID {yk_payment_id_from_hook}."
            )
            return False
        if payment_record.user_id != user_id:
            logging.error(
                "Payment ownership mismatch for payment %s: metadata user_id=%s, db user_id=%s",
                payment_db_id,
                user_id,
                payment_record.user_id,
            )
            return False

        # Provider-backed verification (defense-in-depth): verify actual YooKassa payment state
        if yk_payment_id_from_hook and yookassa_service and yookassa_service.configured:
            provider_payment_info = await yookassa_service.get_payment_info(yk_payment_id_from_hook)
            if not provider_payment_info:
                logging.error(
                    "YooKassa webhook verification failed: payment %s not found via provider API",
                    yk_payment_id_from_hook,
                )
                return False

            provider_status = str(provider_payment_info.get("status") or "")
            provider_paid = bool(provider_payment_info.get("paid"))
            if provider_status != "succeeded" or not provider_paid:
                logging.error(
                    "YooKassa webhook verification failed: payment %s status/paid mismatch (status=%s, paid=%s)",
                    yk_payment_id_from_hook,
                    provider_status,
                    provider_paid,
                )
                return False

            provider_metadata_raw = provider_payment_info.get("metadata") or {}
            provider_metadata = provider_metadata_raw if isinstance(provider_metadata_raw, dict) else {}
            if str(provider_metadata.get("user_id") or "") != str(user_id):
                logging.error(
                    "YooKassa webhook verification failed: user_id mismatch for payment %s (provider=%s, expected=%s)",
                    yk_payment_id_from_hook,
                    provider_metadata.get("user_id"),
                    user_id,
                )
                return False
            provider_payment_db_id = str(provider_metadata.get("payment_db_id") or "").strip()
            if provider_payment_db_id != str(payment_db_id):
                logging.error(
                    "YooKassa webhook verification failed: payment_db_id mismatch for payment %s (provider=%s, expected=%s)",
                    yk_payment_id_from_hook,
                    provider_metadata.get("payment_db_id"),
                    payment_db_id,
                )
                return False

            try:
                provider_amount = float(provider_payment_info.get("amount_value") or 0.0)
                if round(provider_amount, 2) != round(payment_value, 2):
                    logging.error(
                        "YooKassa webhook verification failed: amount mismatch for payment %s (payload %.2f vs provider %.2f)",
                        yk_payment_id_from_hook,
                        payment_value,
                        provider_amount,
                    )
                    return False
                if payment_record and round(float(payment_record.amount), 2) != round(provider_amount, 2):
                    logging.error(
                        "YooKassa webhook verification failed: DB amount mismatch for payment %s (db %.2f vs provider %.2f)",
                        payment_record.payment_id,
                        float(payment_record.amount),
                        provider_amount,
                    )
                    return False
                provider_currency = str(provider_payment_info.get("amount_currency") or "").upper()
                if payment_record and provider_currency and str(payment_record.currency or "").upper() != provider_currency:
                    logging.error(
                        "YooKassa webhook verification failed: currency mismatch for payment %s (db=%s, provider=%s)",
                        payment_record.payment_id,
                        payment_record.currency,
                        provider_currency,
                    )
                    return False
            except Exception as e_amount_verify:
                logging.error(
                    "YooKassa webhook verification failed for payment %s: cannot validate amount (%s)",
                    yk_payment_id_from_hook,
                    e_amount_verify,
                )
                return False

        if payment_record and payment_record.status == "succeeded":
            logging.info(
                f"Skipping duplicate YooKassa webhook for payment {payment_db_id} (YK: {yk_payment_id_from_hook})."
            )
            return True

        db_user = await user_dal.get_user_by_id(session, user_id)
        if not db_user:
            logging.error(
                f"User {user_id} not found in DB during successful payment processing for YK ID {payment_info_from_webhook.get('id')}. Payment record {payment_db_id}."
            )

            await payment_dal.update_payment_status_by_db_id(
                session, payment_db_id, "failed_user_not_found",
                payment_info_from_webhook.get("id"))

            return False

    except (TypeError, ValueError) as e:
        logging.error(
            f"Invalid metadata format for payment processing: {metadata} - {e}"
        )

        if payment_db_id_str and payment_db_id_str.isdigit():
            try:
                await payment_dal.update_payment_status_by_db_id(
                    session, int(payment_db_id_str), "failed_metadata_error",
                    payment_info_from_webhook.get("id"))
            except Exception as e_upd:
                logging.error(
                    f"Failed to update payment status after metadata error: {e_upd}"
                )
        return False

    try:
        yk_payment_id_from_hook = payment_info_from_webhook.get("id")
        provider_payment_id = str(yk_payment_id_from_hook or "").strip()
        if not provider_payment_id:
            raise ValueError(
                f"Missing provider payment id in successful YooKassa webhook for payment {payment_db_id}"
            )

        payment_before_update = None
        if payment_db_id is not None:
            payment_before_update = await payment_dal.get_payment_by_db_id(
                session,
                payment_db_id,
            )
            if payment_before_update and payment_before_update.status == "succeeded":
                logging.info(
                    "YooKassa webhook ignored: payment %s already succeeded (db_id=%s)",
                    yk_payment_id_from_hook,
                    payment_db_id,
                )
                return True

        marked = await payment_dal.mark_provider_payment_succeeded_once(
            session,
            payment_db_id,
            provider_payment_id,
        )
        if not marked:
            logging.info(
                "YooKassa webhook: payment %s already processed atomically",
                payment_db_id,
            )
            return True

        should_send_lknpd_receipt = bool(
            lknpd_service
            and lknpd_service.configured
            and payment_info_from_webhook.get("paid") is True
            and payment_info_from_webhook.get("status") == "succeeded"
            and payment_before_update
            and payment_before_update.status != "succeeded"
        )
        # Try to capture and save payment method for future charges if available
        try:
            payment_method = payment_info_from_webhook.get("payment_method")
            if settings.yookassa_autopayments_active and isinstance(payment_method, dict) and payment_method.get("saved", False):
                pm_id = payment_method.get("id")
                pm_type = payment_method.get("type")
                title = payment_method.get("title")
                card = payment_method.get("card") or {}
                account_number = payment_method.get("account_number") or payment_method.get("account")
                display_network = None
                display_last4 = None
                # Build generic display for various instrument types
                if (pm_type or "").lower() in {"bank_card", "bank-card", "card"}:
                    display_network = card.get("card_type") or title or "Card"
                    display_last4 = card.get("last4")
                elif (pm_type or "").lower() in {"yoo_money", "yoomoney", "yoo-money", "wallet"}:
                    # Normalize wallet display name to avoid leaking full account from title
                    display_network = "YooMoney"
                    if isinstance(account_number, str) and len(account_number) >= 4:
                        display_last4 = account_number[-4:]
                    else:
                        display_last4 = None
                else:
                    # Wallets, SBP, etc. - use provided title/type; no last4
                    display_network = title or (pm_type.upper() if pm_type else "Payment method")
                    display_last4 = None

                await user_billing_dal.upsert_yk_payment_method(
                    session,
                    user_id=user_id,
                    payment_method_id=pm_id,
                    card_last4=display_last4,
                    card_network=display_network,
                )
                try:
                    await user_billing_dal.upsert_user_payment_method(
                        session,
                        user_id=user_id,
                        provider_payment_method_id=pm_id,
                        provider="yookassa",
                        card_last4=display_last4,
                        card_network=display_network,
                        set_default=True,
                    )
                except Exception:
                    logging.exception("Failed to persist multi-card YooKassa method from webhook")
        except Exception:
            logging.exception("Failed to persist YooKassa payment method from webhook")
        months_for_activation = int(subscription_months) if sale_mode != "traffic" else 0
        activation_details = await subscription_service.activate_subscription(
            session,
            user_id,
            months_for_activation,
            payment_value,
            payment_db_id,
            promo_code_id_from_payment=promo_code_id,
            provider="yookassa",
            sale_mode=sale_mode,
            traffic_gb=traffic_amount_gb if sale_mode == "traffic" else None,
        )

        if not activation_details or not activation_details.get('end_date'):
            logging.error(
                f"Failed to activate subscription for user {user_id} after payment {yk_payment_id_from_hook}"
            )
            raise Exception(
                f"Subscription Error: Failed to activate for user {user_id}")

        base_subscription_end_date = activation_details['end_date']
        final_end_date_for_user = base_subscription_end_date
        applied_promo_bonus_days = activation_details.get(
            "applied_promo_bonus_days", 0)

        referral_bonus_info = None
        if sale_mode != "traffic":
            referral_bonus_info = await referral_service.apply_referral_bonuses_for_payment(
                session,
                user_id,
                months_for_activation or int(subscription_months) or 1,
                current_payment_db_id=payment_db_id,
                skip_if_active_before_payment=False,
            )
        applied_referee_bonus_days_from_referral: Optional[int] = None
        if referral_bonus_info and referral_bonus_info.get(
                "referee_new_end_date"):
            final_end_date_for_user = referral_bonus_info[
                "referee_new_end_date"]
            applied_referee_bonus_days_from_referral = referral_bonus_info.get(
                "referee_bonus_applied_days")

        # Use user's DB language for all user-facing messages
        user_lang = db_user.language_code if db_user and db_user.language_code else settings.DEFAULT_LANGUAGE
        _ = lambda key, **kwargs: i18n.gettext(user_lang, key, **kwargs)

        traffic_label = (
            str(int(traffic_amount_gb)) if float(traffic_amount_gb).is_integer() else f"{traffic_amount_gb:g}"
        )
        if should_send_lknpd_receipt:
            receipt_item_name = payment_info_from_webhook.get("description")
            if not receipt_item_name:
                if sale_mode == "traffic":
                    receipt_item_name = settings.LKNPD_RECEIPT_NAME_TRAFFIC.format(gb=traffic_label)
                else:
                    receipt_item_name = settings.LKNPD_RECEIPT_NAME_SUBSCRIPTION.format(months=int(subscription_months))
            try:
                await lknpd_service.create_income_receipt(
                    item_name=receipt_item_name,
                    amount=payment_value,
                    quantity=1.0,
                    operation_time=datetime.now(timezone.utc),
                )
            except Exception:
                logging.exception(
                    "Failed to send LKNPD receipt for payment %s",
                    yk_payment_id_from_hook,
                )
        config_link_display, connect_button_url = await prepare_config_links(
            settings, activation_details.get("subscription_url") if activation_details else None
        )
        config_link_text = config_link_display or _("config_link_not_available")
        # For auto-renew charges, avoid re-sending config link; send concise message
        if sale_mode != "traffic" and is_auto_renew and final_end_date_for_user:
            details_message = _(
                "yookassa_auto_renewal",
                months=int(subscription_months),
                end_date=final_end_date_for_user.strftime('%Y-%m-%d'),
            )
            details_markup = None
        elif sale_mode == "traffic":
            details_message = _(
                "payment_successful_traffic_full",
                traffic_gb=traffic_label,
                end_date=final_end_date_for_user.strftime('%Y-%m-%d') if final_end_date_for_user else "-",
                config_link=config_link_text,
            )
            details_markup = get_connect_and_main_keyboard(
                user_lang,
                i18n,
                settings,
                config_link_display,
                connect_button_url=connect_button_url,
                preserve_message=True,
            )
        else:
            if applied_referee_bonus_days_from_referral and final_end_date_for_user:
                inviter_name_display = _("friend_placeholder")
                if db_user and db_user.referred_by_id:
                    inviter = await user_dal.get_user_by_id(
                        session, db_user.referred_by_id)
                    if inviter:
                        safe_name = sanitize_display_name(inviter.first_name) if inviter.first_name else None
                        if safe_name:
                            inviter_name_display = safe_name
                        elif inviter.username:
                            inviter_name_display = username_for_display(inviter.username, with_at=False)

                details_message = _(
                    "payment_successful_with_referral_bonus_full",
                    months=int(subscription_months),
                    base_end_date=base_subscription_end_date.strftime('%Y-%m-%d'),
                    bonus_days=applied_referee_bonus_days_from_referral,
                    final_end_date=final_end_date_for_user.strftime('%Y-%m-%d'),
                    inviter_name=inviter_name_display,
                    config_link=config_link_text,
                )
            elif applied_promo_bonus_days > 0 and final_end_date_for_user:
                details_message = _(
                    "payment_successful_with_promo_full",
                    months=int(subscription_months),
                    bonus_days=applied_promo_bonus_days,
                    end_date=final_end_date_for_user.strftime('%Y-%m-%d'),
                    config_link=config_link_text,
                )
            elif final_end_date_for_user:
                details_message = _(
                    "payment_successful_full",
                    months=int(subscription_months),
                    end_date=final_end_date_for_user.strftime('%Y-%m-%d'),
                    config_link=config_link_text,
                )
            else:
                logging.error(
                    f"Critical error: final_end_date_for_user is None for user {user_id} after successful payment logic."
                )
                details_message = _("payment_successful_error_details")

            details_markup = get_connect_and_main_keyboard(
                user_lang,
                i18n,
                settings,
                config_link_display,
                connect_button_url=connect_button_url,
                preserve_message=True,
            )
        try:
            await bot.send_message(
                user_id,
                details_message,
                reply_markup=details_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e_notify:
            logging.error(
                f"Failed to send payment details message to user {user_id}: {e_notify}"
            )

        # Send notification about payment
        try:
            notification_service = NotificationService(bot, settings, i18n)
            user = await user_dal.get_user_by_id(session, user_id)
            await notification_service.notify_payment_received(
                user_id=user_id,
                amount=payment_value,
                currency="RUB",
                months=int(subscription_months) if sale_mode != "traffic" else 0,
                payment_provider="yookassa",  # This is specifically for YooKassa webhook
                username=user.username if user else None,
                traffic_gb=traffic_amount_gb if sale_mode == "traffic" else None,
            )
        except Exception as e:
            logging.error(f"Failed to send payment notification: {e}")

        return True

    except Exception as e_process:
        logging.error(
            f"Error during process_successful_payment main try block for user {user_id}: {e_process}",
            exc_info=True)

        raise


async def process_cancelled_payment(session: AsyncSession, bot: Bot,
                                    payment_info_from_webhook: dict,
                                    i18n: JsonI18n, settings: Settings):

    metadata = payment_info_from_webhook.get("metadata", {})
    user_id_str = metadata.get("user_id")
    payment_db_id_str = metadata.get("payment_db_id")

    if not user_id_str or not payment_db_id_str:
        logging.warning(
            f"Missing metadata in cancelled payment webhook: {payment_info_from_webhook.get('id')}"
        )
        return
    try:
        user_id = int(user_id_str)
        payment_db_id = int(payment_db_id_str)
    except ValueError:
        logging.error(
            f"Invalid metadata in cancelled payment webhook: {metadata}")
        return

    try:
        updated_payment = await payment_dal.update_payment_status_by_db_id(
            session,
            payment_db_id=payment_db_id,
            new_status=payment_info_from_webhook.get("status", "canceled"),
            yk_payment_id=payment_info_from_webhook.get("id"))

        if updated_payment:
            logging.info(
                f"Payment {payment_db_id} (YK: {payment_info_from_webhook.get('id')}) status updated to cancelled for user {user_id}."
            )
        else:
            logging.warning(
                f"Could not find payment record {payment_db_id} to update status to cancelled for user {user_id}."
            )

        db_user = await user_dal.get_user_by_id(session, user_id)
        user_lang = settings.DEFAULT_LANGUAGE
        if db_user and db_user.language_code: user_lang = db_user.language_code

        _ = lambda key, **kwargs: i18n.gettext(user_lang, key, **kwargs)
        await bot.send_message(user_id, _("payment_failed"))

    except Exception as e_process_cancel:
        logging.error(
            f"Error processing cancelled payment for user {user_id}, payment_db_id {payment_db_id}: {e_process_cancel}",
            exc_info=True)
        raise


async def yookassa_webhook_route(request: web.Request):

    try:
        bot: Bot = request.app['bot']
        i18n_instance: JsonI18n = request.app['i18n']
        settings: Settings = request.app['settings']
        panel_service: PanelApiService = request.app['panel_service']
        yookassa_service: Optional[YooKassaService] = request.app.get('yookassa_service')
        subscription_service: SubscriptionService = request.app[
            'subscription_service']
        referral_service: ReferralService = request.app['referral_service']
        lknpd_service: Optional[LknpdService] = request.app.get('lknpd_service')
        async_session_factory: sessionmaker = request.app[
            'async_session_factory']
    except KeyError as e_app_ctx:
        logging.error(
            f"KeyError accessing app context in yookassa_webhook_route: {e_app_ctx}.",
            exc_info=True)
        return web.Response(
            status=500,
            text="Internal Server Error: Missing app context component")

    try:
        event_json = await request.json()

        notification_object = WebhookNotification(event_json)
        payment_data_from_notification = notification_object.object

        logging.info(
            f"YooKassa Webhook Parsed: Event='{notification_object.event}', "
            f"PaymentId='{payment_data_from_notification.id}', Status='{payment_data_from_notification.status}'"
        )

        if not payment_data_from_notification or not hasattr(
                payment_data_from_notification,
                'metadata') or payment_data_from_notification.metadata is None:
            logging.error(
                f"YooKassa webhook payment {payment_data_from_notification.id} lacks metadata. Cannot process."
            )
            return web.Response(status=503, text="yookassa_missing_metadata")

        # Safely extract payment_method details (SDK objects may not have to_dict)
        pm_obj = getattr(payment_data_from_notification, 'payment_method', None)
        pm_dict = None
        if pm_obj is not None:
            try:
                card_obj = getattr(pm_obj, 'card', None)
                pm_dict = {
                    "id": getattr(pm_obj, 'id', None),
                    "type": getattr(pm_obj, 'type', None),
                    "saved": bool(getattr(pm_obj, 'saved', False)),
                    "title": getattr(pm_obj, 'title', None),
                    "account_number": (
                        getattr(pm_obj, 'account_number', None)
                        if hasattr(pm_obj, 'account_number') else (
                            getattr(pm_obj, 'account', None)
                            if hasattr(pm_obj, 'account') else None
                        )
                    ),
                    "card": (
                        {
                            "first6": getattr(card_obj, 'first6', None),
                            "last4": getattr(card_obj, 'last4', None),
                            "expiry_month": getattr(card_obj, 'expiry_month', None),
                            "expiry_year": getattr(card_obj, 'expiry_year', None),
                            "card_type": getattr(card_obj, 'card_type', None),
                        }
                        if card_obj is not None
                        else None
                    ),
                }
            except Exception:
                logging.exception("Failed to serialize YooKassa payment_method from webhook")
                pm_dict = None

        payment_dict_for_processing = {
            "id":
            str(payment_data_from_notification.id),
            "status":
            str(payment_data_from_notification.status),
            "paid":
            bool(payment_data_from_notification.paid),
            "amount": {
                "value": str(payment_data_from_notification.amount.value),
                "currency": str(payment_data_from_notification.amount.currency)
            } if payment_data_from_notification.amount else {},
            "metadata":
            dict(payment_data_from_notification.metadata),
            "description":
            str(payment_data_from_notification.description)
            if payment_data_from_notification.description else None,
            "payment_method": pm_dict,
        }

        async with async_session_factory() as session:
            try:
                # Defense-in-depth: for state mutations beyond succeeded, verify against provider API.
                if notification_object.event in {
                    YOOKASSA_EVENT_PAYMENT_CANCELED,
                    YOOKASSA_EVENT_PAYMENT_WAITING_FOR_CAPTURE,
                }:
                    if not yookassa_service or not yookassa_service.configured:
                        logging.critical(
                            "YooKassa webhook rejected: verification service is not configured for event %s (payment_id=%s)",
                            notification_object.event,
                            payment_dict_for_processing.get("id"),
                        )
                        return web.Response(status=503, text="yookassa_verification_required")

                    provider_payment_info = await yookassa_service.get_payment_info(
                        payment_dict_for_processing.get("id")
                    )
                    if not provider_payment_info:
                        logging.error(
                            "YooKassa webhook verification failed: payment %s not found via provider API",
                            payment_dict_for_processing.get("id"),
                        )
                        return web.Response(status=503, text="yookassa_verification_failed")

                    provider_status = str(provider_payment_info.get("status") or "")
                    provider_paid = bool(provider_payment_info.get("paid"))
                    provider_metadata_raw = provider_payment_info.get("metadata") or {}
                    provider_metadata = provider_metadata_raw if isinstance(provider_metadata_raw, dict) else {}

                    payment_dict_for_processing["status"] = provider_status or payment_dict_for_processing.get("status")
                    payment_dict_for_processing["paid"] = provider_paid
                    payment_dict_for_processing["metadata"] = dict(provider_metadata)

                    provider_amount_value = provider_payment_info.get("amount_value")
                    provider_amount_currency = provider_payment_info.get("amount_currency")
                    if provider_amount_value is not None and provider_amount_currency:
                        payment_dict_for_processing["amount"] = {
                            "value": str(provider_amount_value),
                            "currency": str(provider_amount_currency),
                        }

                    provider_pm = provider_payment_info.get("payment_method")
                    if isinstance(provider_pm, dict) and provider_pm.get("id"):
                        # Use provider payment_method as authoritative.
                        payment_dict_for_processing["payment_method"] = provider_pm

                if notification_object.event == YOOKASSA_EVENT_PAYMENT_SUCCEEDED:
                    if not yookassa_service or not yookassa_service.configured:
                        logging.critical(
                            "YooKassa webhook rejected: verification service is not configured for succeeded event (payment_id=%s)",
                            payment_dict_for_processing.get("id"),
                        )
                        return web.Response(status=503, text="yookassa_verification_required")

                    if payment_dict_for_processing.get(
                            "paid") and payment_dict_for_processing.get(
                                "status") == "succeeded":
                        processed = await process_successful_payment(
                            session, bot, payment_dict_for_processing,
                            i18n_instance, settings, panel_service,
                            subscription_service, referral_service,
                            yookassa_service,
                            lknpd_service)
                        if not processed:
                            # process_successful_payment uses False for permanent business failures
                            # (e.g. user not found / metadata issues) and may have already updated
                            # the payment status. Commit the status and ACK the webhook to stop
                            # indefinite provider retries.
                            try:
                                await session.commit()
                            except Exception:
                                await session.rollback()
                                logging.exception(
                                    "Failed to commit failure status for YooKassa payment %s",
                                    payment_dict_for_processing.get("id"),
                                )
                                return web.Response(status=503, text="yookassa_processing_failed_retry")
                            return web.Response(status=200, text="ok")
                        await session.commit()
                    else:
                        logging.warning(
                            f"Payment Succeeded event for {payment_dict_for_processing.get('id')} "
                            f"but data not as expected: status='{payment_dict_for_processing.get('status')}', "
                            f"paid='{payment_dict_for_processing.get('paid')}'"
                        )
                        await session.rollback()
                        return web.Response(status=503, text="yookassa_invalid_succeeded_payload")
                elif notification_object.event == YOOKASSA_EVENT_PAYMENT_CANCELED:
                    if payment_dict_for_processing.get("status") not in {"canceled", "cancelled"}:
                        logging.error(
                            "YooKassa webhook rejected: canceled event status mismatch for payment %s (status=%s)",
                            payment_dict_for_processing.get("id"),
                            payment_dict_for_processing.get("status"),
                        )
                        return web.Response(status=503, text="yookassa_invalid_canceled_payload")
                    await process_cancelled_payment(
                        session, bot, payment_dict_for_processing,
                        i18n_instance, settings)
                    await session.commit()
                elif notification_object.event == YOOKASSA_EVENT_PAYMENT_WAITING_FOR_CAPTURE:
                    # Bind-only flow: save method and cancel auth if metadata has bind_only
                    metadata = payment_dict_for_processing.get("metadata", {}) or {}
                    if settings.yookassa_autopayments_active and metadata.get("bind_only") == "1":
                        if payment_dict_for_processing.get("status") != "waiting_for_capture":
                            logging.error(
                                "YooKassa webhook rejected: waiting_for_capture event status mismatch for payment %s (status=%s)",
                                payment_dict_for_processing.get("id"),
                                payment_dict_for_processing.get("status"),
                            )
                            return web.Response(status=503, text="yookassa_invalid_waiting_payload")
                        try:
                            user_id_str = metadata.get("user_id")
                            if user_id_str and user_id_str.isdigit():
                                user_id = int(user_id_str)
                                payment_method = payment_dict_for_processing.get("payment_method")
                                if isinstance(payment_method, dict) and payment_method.get("id"):
                                    pm_type = payment_method.get("type")
                                    title = payment_method.get("title")

                                    # Support both webhook shape (nested card) and provider shape (card_last4)
                                    last4_val = None
                                    card = payment_method.get("card") or {}
                                    if isinstance(card, dict) and card.get("last4"):
                                        last4_val = card.get("last4")
                                    if not last4_val:
                                        last4_val = payment_method.get("card_last4")

                                    display_network = None
                                    display_last4 = None
                                    if (pm_type or "").lower() in {"bank_card", "bank-card", "card"}:
                                        display_network = title or "Card"
                                        display_last4 = last4_val
                                    elif (pm_type or "").lower() in {"yoo_money", "yoomoney", "yoo-money", "wallet"}:
                                        display_network = "YooMoney"
                                        display_last4 = last4_val
                                    else:
                                        display_network = title or (pm_type.upper() if pm_type else "Payment method")
                                        display_last4 = last4_val

                                    await user_billing_dal.upsert_yk_payment_method(
                                        session,
                                        user_id=user_id,
                                        payment_method_id=payment_method.get("id"),
                                        card_last4=display_last4,
                                        card_network=display_network,
                                    )
                                    await session.commit()
                                    # Save multi-card entry and mark default if first
                                    try:
                                        from db.dal import user_billing_dal as ub
                                        await ub.upsert_user_payment_method(
                                            session,
                                            user_id=user_id,
                                            provider_payment_method_id=payment_method.get("id"),
                                            provider="yookassa",
                                            card_last4=display_last4,
                                            card_network=display_network,
                                            set_default=True,
                                        )
                                        await session.commit()
                                    except Exception:
                                        await session.rollback()
                                    # Notify user about successful binding with Back button
                                    try:
                                        # Use user's DB language for bind success notification
                                        i18n_lang = settings.DEFAULT_LANGUAGE
                                        from db.dal import user_dal
                                        db_user = await user_dal.get_user_by_id(session, user_id)
                                        if db_user and db_user.language_code:
                                            i18n_lang = db_user.language_code
                                        _ = lambda key, **kwargs: i18n_instance.gettext(i18n_lang, key, **kwargs)
                                        from bot.keyboards.inline.user_keyboards import get_back_to_payment_methods_keyboard
                                        await bot.send_message(
                                            chat_id=user_id,
                                            text=_("payment_method_bound_success"),
                                            reply_markup=get_back_to_payment_methods_keyboard(i18n_lang, i18n_instance)
                                        )
                                    except Exception as exc:
                                        logging.debug(
                                            "Failed to notify user %s about payment method binding: %s",
                                            user_id,
                                            exc,
                                        )
                                    # Attempt to cancel the authorization to avoid charge hold
                                    try:
                                        yk: YooKassaService = request.app.get('yookassa_service')
                                        if yk:
                                            await yk.cancel_payment(payment_dict_for_processing.get("id"))
                                    except Exception:
                                        logging.exception("Failed to cancel bind-only payment auth")
                        except Exception:
                            logging.exception("Failed to handle bind-only waiting_for_capture webhook")
            except Exception as e_webhook_db_processing:
                await session.rollback()
                logging.error(
                    f"Error processing YooKassa webhook event '{notification_object.event}' "
                    f"for YK Payment ID {payment_dict_for_processing.get('id')} in DB transaction: {e_webhook_db_processing}",
                    exc_info=True)
                return web.Response(
                    status=503, text="yookassa_processing_error_retry")

        return web.Response(status=200, text="ok")

    except json.JSONDecodeError:
        logging.error("YooKassa Webhook: Invalid JSON received.")
        return web.Response(status=400, text="bad_request_invalid_json")
    except Exception as e_general_webhook:
        logging.error(
            f"YooKassa Webhook general processing error: {e_general_webhook}",
            exc_info=True)
        return web.Response(status=503,
                            text="yookassa_general_error_retry")
