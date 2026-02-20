import logging
from aiogram import Router, F, types, Bot
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from config.settings import Settings
from bot.services.subscription_service import SubscriptionService
from bot.services.panel_api_service import PanelApiService
from bot.services.notification_service import NotificationService
from bot.keyboards.inline.user_keyboards import (
    get_trial_confirmation_keyboard,
    get_main_menu_inline_keyboard,
    get_connect_and_main_keyboard,
)
from bot.utils.config_link import prepare_config_links
from bot.middlewares.i18n import JsonI18n
from .start import send_main_menu

router = Router(name="user_trial_router")


async def request_trial_confirmation_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    subscription_service: SubscriptionService,
    session: AsyncSession,
):
    user_id = callback.from_user.id
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(_("error_occurred_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        return

    show_trial_btn_in_menu_if_fail = False
    if settings.TRIAL_ENABLED:
        if not await subscription_service.has_had_any_subscription(session, user_id):
            show_trial_btn_in_menu_if_fail = True

    if not settings.TRIAL_ENABLED:
        await callback.message.edit_text(
            _("trial_feature_disabled"),
            reply_markup=get_main_menu_inline_keyboard(
                current_lang, i18n, settings, False
            ),
        )
        try:
            await callback.answer()
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        return

    if await subscription_service.has_had_any_subscription(session, user_id):
        await callback.message.edit_text(
            _("trial_already_had_subscription_or_trial"),
            reply_markup=get_main_menu_inline_keyboard(
                current_lang, i18n, settings, False
            ),
        )
        try:
            await callback.answer()
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        return

    # Directly activate trial without confirmation
    activation_result = await subscription_service.activate_trial_subscription(
        session, user_id
    )

    final_message_text_in_chat = ""
    show_trial_button_after_action = False
    config_link_display_for_trial = None
    config_link_for_trial = None
    connect_button_url_for_trial = None

    if activation_result and activation_result.get("activated"):
        try:
            await callback.answer(_("trial_activated_alert"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)

        end_date_obj = activation_result.get("end_date")
        config_link_display_for_trial, connect_button_url_for_trial = await prepare_config_links(
            settings, activation_result.get("subscription_url")
        )
        config_link_for_trial = config_link_display_for_trial or _("config_link_not_available")

        traffic_gb_val = activation_result.get(
            "traffic_gb", settings.TRIAL_TRAFFIC_LIMIT_GB
        )
        traffic_display = (
            f"{traffic_gb_val} GB"
            if traffic_gb_val and traffic_gb_val > 0
            else _("traffic_unlimited")
        )

        final_message_text_in_chat = _(
            "trial_activated_details_message",
            days=activation_result.get("days", settings.TRIAL_DURATION_DAYS),
            end_date=(
                end_date_obj.strftime("%Y-%m-%d")
                if isinstance(end_date_obj, datetime)
                else "N/A"
            ),
            config_link=config_link_for_trial,
            traffic_gb=traffic_display,
        )
        
        # Send notification to admin about new trial
        notification_service = NotificationService(callback.bot, settings, i18n)
        await notification_service.notify_trial_activation(user_id, end_date_obj)
        # Mark ad attribution trial if exists
        try:
            from db.dal import ad_dal as _ad_dal
            await _ad_dal.mark_trial_activated(session, user_id)
            await session.commit()
        except Exception as e_mark:
            await session.rollback()
            logging.error(f"Failed to mark trial for ad attribution for user {user_id}: {e_mark}")
    else:
        message_key_from_service = (
            activation_result.get("message_key", "trial_activation_failed")
            if activation_result
            else "trial_activation_failed"
        )
        final_message_text_in_chat = _(message_key_from_service)
        try:
            await callback.answer(final_message_text_in_chat, show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        if (
            settings.TRIAL_ENABLED
            and not await subscription_service.has_had_any_subscription(
                session, user_id
            )
        ):
            show_trial_button_after_action = True

    reply_markup = (
        get_connect_and_main_keyboard(
            current_lang,
            i18n,
            settings,
            config_link_display_for_trial,
            connect_button_url=connect_button_url_for_trial,
        )
        if activation_result and activation_result.get("activated")
        else get_main_menu_inline_keyboard(
            current_lang, i18n, settings, show_trial_button_after_action
        )
    )

    try:
        await callback.message.edit_text(
            final_message_text_in_chat,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception as e_edit:
        logging.warning(
            f"Could not edit trial result message: {e_edit}. Sending new one."
        )

        if callback.message:
            await callback.message.answer(
                final_message_text_in_chat,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )


@router.callback_query(F.data == "trial_action:confirm_activate")
async def confirm_activate_trial_handler(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    subscription_service: SubscriptionService,
    panel_service: PanelApiService,
    session: AsyncSession,
):
    user_id = callback.from_user.id

    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs) if i18n else key

    if not i18n or not callback.message:
        try:
            await callback.answer(_("error_occurred_try_again"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        return

    if not settings.TRIAL_ENABLED:
        try:
            await callback.answer(_("trial_feature_disabled"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)

        await send_main_menu(
            callback, settings, i18n_data, subscription_service, session, is_edit=True
        )
        return
    if await subscription_service.has_had_any_subscription(session, user_id):
        try:
            await callback.answer(
                _("trial_already_had_subscription_or_trial"), show_alert=True
            )
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        await send_main_menu(
            callback, settings, i18n_data, subscription_service, session, is_edit=True
        )
        return

    activation_result = await subscription_service.activate_trial_subscription(
        session, user_id
    )

    final_message_text_in_chat = ""
    show_trial_button_after_action = False
    config_link_display_for_trial = None
    config_link_for_trial = None
    connect_button_url_for_trial = None

    if activation_result and activation_result.get("activated"):
        try:
            await callback.answer(_("trial_activated_alert"), show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)

        end_date_obj = activation_result.get("end_date")
        config_link_display_for_trial, connect_button_url_for_trial = await prepare_config_links(
            settings, activation_result.get("subscription_url")
        )
        config_link_for_trial = config_link_display_for_trial or _("config_link_not_available")

        traffic_gb_val = activation_result.get(
            "traffic_gb", settings.TRIAL_TRAFFIC_LIMIT_GB
        )
        traffic_display = (
            f"{traffic_gb_val} GB"
            if traffic_gb_val and traffic_gb_val > 0
            else _("traffic_unlimited")
        )

        final_message_text_in_chat = _(
            "trial_activated_details_message",
            days=activation_result.get("days", settings.TRIAL_DURATION_DAYS),
            end_date=(
                end_date_obj.strftime("%Y-%m-%d")
                if isinstance(end_date_obj, datetime)
                else "N/A"
            ),
            config_link=config_link_for_trial,
            traffic_gb=traffic_display,
        )
    else:
        message_key_from_service = (
            activation_result.get("message_key", "trial_activation_failed")
            if activation_result
            else "trial_activation_failed"
        )
        final_message_text_in_chat = _(message_key_from_service)
        try:
            await callback.answer(final_message_text_in_chat, show_alert=True)
        except Exception as exc:
            logging.debug("Suppressed exception in bot/handlers/user/trial_handler.py: %s", exc)
        if (
            settings.TRIAL_ENABLED
            and not await subscription_service.has_had_any_subscription(
                session, user_id
            )
        ):
            show_trial_button_after_action = True

    reply_markup = (
        get_connect_and_main_keyboard(
            current_lang,
            i18n,
            settings,
            config_link_display_for_trial,
            connect_button_url=connect_button_url_for_trial,
        )
        if activation_result and activation_result.get("activated")
        else get_main_menu_inline_keyboard(
            current_lang, i18n, settings, show_trial_button_after_action
        )
    )

    try:
        await callback.message.edit_text(
            final_message_text_in_chat,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception as e_edit:
        logging.warning(
            f"Could not edit trial result message: {e_edit}. Sending new one."
        )

        if callback.message:
            await callback.message.answer(
                final_message_text_in_chat,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )

    if activation_result and activation_result.get("activated") and end_date_obj:
        notification_service = NotificationService(callback.bot, settings, i18n)
        await notification_service.notify_trial_activation(user_id, end_date_obj)
        try:
            from db.dal import ad_dal as _ad_dal
            await _ad_dal.mark_trial_activated(session, user_id)
            await session.commit()
        except Exception as e_mark:
            await session.rollback()
            logging.error(f"Failed to mark trial for ad attribution for user {user_id}: {e_mark}")


@router.callback_query(F.data == "main_action:cancel_trial")
async def cancel_trial_activation(
    callback: types.CallbackQuery,
    settings: Settings,
    i18n_data: dict,
    subscription_service: SubscriptionService,
    session: AsyncSession,
):
    await send_main_menu(
        callback, settings, i18n_data, subscription_service, session, is_edit=True
    )
