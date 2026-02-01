import logging
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from bot.keyboards.inline.admin_keyboards import (
    get_admin_panel_keyboard, get_stats_monitoring_keyboard, 
    get_user_management_keyboard, get_ban_management_keyboard,
    get_promo_marketing_keyboard, get_system_functions_keyboard
)
from bot.middlewares.i18n import JsonI18n
from bot.services.panel_api_service import PanelApiService
from bot.services.subscription_service import SubscriptionService
from bot.utils.message_queue import get_queue_manager

from . import broadcast as admin_broadcast_handlers
from .promo import create as admin_promo_create_handlers
from .promo import manage as admin_promo_manage_handlers
from .promo import bulk as admin_promo_bulk_handlers
from . import user_management as admin_user_mgmnt_handlers
from . import statistics as admin_stats_handlers
from . import sync_admin as admin_sync_handlers
from . import logs_admin as admin_logs_handlers

router = Router(name="admin_common_router")


@router.message(Command("admin"))
async def admin_panel_command_handler(
    message: types.Message,
    state: FSMContext,
    settings: Settings,
    i18n_data: dict,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        logging.error("i18n missing in admin_panel_command_handler")
        await message.answer("Language service error.")
        return

    await state.clear()
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    await message.answer(_(key="admin_panel_title"),
                         reply_markup=get_admin_panel_keyboard(
                             i18n, current_lang, settings))


@router.callback_query(F.data.startswith("admin_action:"))
async def admin_panel_actions_callback_handler(
        callback: types.CallbackQuery, state: FSMContext, settings: Settings,
        i18n_data: dict, bot: Bot, panel_service: PanelApiService,
        subscription_service: SubscriptionService, session: AsyncSession):
    action_parts = callback.data.split(":")
    action = action_parts[1]

    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        logging.error("i18n missing in admin_panel_actions_callback_handler")
        await callback.answer("Language error.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    if not callback.message:
        logging.error(
            f"CallbackQuery {callback.id} from {callback.from_user.id} has no message for admin_action {action}"
        )
        await callback.answer("Error processing action: message context lost.",
                              show_alert=True)
        return

    if action == "stats":
        await admin_stats_handlers.show_statistics_handler(
            callback, i18n_data, settings, session)
    elif action == "broadcast":
        await admin_broadcast_handlers.broadcast_message_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "create_promo":
        await admin_promo_create_handlers.create_promo_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "create_bulk_promo":
        await admin_promo_bulk_handlers.create_bulk_promo_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "manage_promos":
        await admin_promo_manage_handlers.manage_promo_codes_handler(
            callback, i18n_data, settings, session)
    elif action == "view_promos":
        await admin_promo_manage_handlers.view_promo_codes_handler(
            callback, i18n_data, settings, session)
    elif action == "ban_user_prompt":
        await admin_user_mgmnt_handlers.ban_user_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "unban_user_prompt":
        await admin_user_mgmnt_handlers.unban_user_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "users_management":
        # This is deprecated, kept for compatibility
        from . import user_management as admin_user_management_handlers
        await admin_user_management_handlers.user_search_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "users_list" and len(action_parts) > 2:
        # Route to users list handler with page number
        from . import user_management as admin_user_management_handlers
        try:
            page = int(action_parts[2])
            await admin_user_management_handlers.users_list_handler(
                callback, i18n_data, settings, session, page)
        except (IndexError, ValueError):
            await callback.answer("Invalid page number", show_alert=True)
    elif action == "users_search_prompt":
        from . import user_management as admin_user_management_handlers
        await admin_user_management_handlers.user_search_prompt_handler(
            callback, state, i18n_data, settings, session)
    elif action == "view_banned":

        await admin_user_mgmnt_handlers.view_banned_users_handler(
            callback, state, i18n_data, settings, session)
    elif action == "view_logs_menu":
        await admin_logs_handlers.display_logs_menu(callback, i18n_data,
                                                    settings, session)
    elif action == "promo_management":
        await admin_promo_manage_handlers.promo_management_handler(
            callback, i18n_data, settings, session)
    elif action == "sync_panel":

        await admin_sync_handlers.sync_command_handler(
            message_event=callback,
            bot=bot,
            settings=settings,
            i18n_data=i18n_data,
            panel_service=panel_service,
            session=session)
        await callback.answer(_("admin_sync_initiated_from_panel"))
    elif action == "queue_status":
        await show_queue_status_handler(callback, i18n_data)
    elif action == "view_payments":
        from . import payments as admin_payments_handlers
        await admin_payments_handlers.view_payments_handler(
            callback, i18n_data, settings, session)
    elif action == "withdraw_requests":
        from . import referral_withdrawals as admin_withdrawals_handlers
        await admin_withdrawals_handlers.view_withdraw_requests_handler(
            callback, i18n_data, settings, session)
    elif action == "ads":
        from . import ads as admin_ads_handlers
        await admin_ads_handlers.show_ads_menu(callback, settings, i18n_data, session)
    elif action == "ads_create":
        from . import ads as admin_ads_handlers
        await admin_ads_handlers.ads_create_start(callback, state, settings, i18n_data)
    elif action == "main":
        try:
            await callback.message.edit_text(
                _(key="admin_panel_title"),
                reply_markup=get_admin_panel_keyboard(i18n, current_lang,
                                                      settings))
        except Exception:
            await callback.message.answer(
                _(key="admin_panel_title"),
                reply_markup=get_admin_panel_keyboard(i18n, current_lang,
                                                      settings))
        await callback.answer()
    else:
        logging.warning(
            f"Unknown admin_action received: {action} from callback {callback.data}"
        )
        await callback.answer(_("admin_unknown_action"), show_alert=True)


@router.callback_query(F.data.startswith("admin_section:"))
async def admin_section_handler(callback: types.CallbackQuery, state: FSMContext, 
                               settings: Settings, i18n_data: dict, session: AsyncSession):
    section = callback.data.split(":")[1]
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        await callback.answer("Language error.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    if not callback.message:
        await callback.answer("Error: message context lost.", show_alert=True)
        return

    try:
        if section == "stats_monitoring":
            await callback.message.edit_text(
                _("admin_stats_and_monitoring_section"),
                reply_markup=get_stats_monitoring_keyboard(i18n, current_lang)
            )
        elif section == "user_management":
            await callback.message.edit_text(
                _("admin_user_management_section"),
                reply_markup=get_user_management_keyboard(i18n, current_lang)
            )
        elif section == "ban_management":
            await callback.message.edit_text(
                _("admin_ban_management_section"),
                reply_markup=get_ban_management_keyboard(i18n, current_lang)
            )
        elif section == "promo_marketing":
            await callback.message.edit_text(
                _("admin_promo_marketing_section"),
                reply_markup=get_promo_marketing_keyboard(i18n, current_lang)
            )
        elif section == "system_functions":
            await callback.message.edit_text(
                _("admin_system_functions_section"),
                reply_markup=get_system_functions_keyboard(i18n, current_lang)
            )
        else:
            await callback.answer(_("admin_unknown_action"), show_alert=True)
            return
            
        await callback.answer()
    except Exception as e:
        logging.error(f"Error handling admin section {section}: {e}")
        await callback.message.answer(
            _("error_occurred_try_again"),
            reply_markup=get_admin_panel_keyboard(i18n, current_lang, settings)
        )
        await callback.answer()


async def show_queue_status_handler(callback: types.CallbackQuery, i18n_data: dict):
    """Show message queue status to admin"""
    current_lang = i18n_data.get("current_language", "ru")
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing request.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    queue_manager = get_queue_manager()
    if not queue_manager:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        await callback.message.edit_text(
            "❌ Система очередей не инициализирована",
            reply_markup=InlineKeyboardBuilder().button(
                text=_("back_to_admin_panel_button"),
                callback_data="admin_action:main"
            ).as_markup()
        )
        await callback.answer()
        return

    try:
        stats = queue_manager.get_queue_stats()
        
        message_text = _(
            "admin_queue_status_info",
            user_queue_size=stats['user_queue_size'],
            user_processing="✅ Да" if stats['user_queue_processing'] else "❌ Нет",
            user_recent=stats['user_recent_sends'],
            group_queue_size=stats['group_queue_size'],
            group_processing="✅ Да" if stats['group_queue_processing'] else "❌ Нет",
            group_recent=stats['group_recent_sends']
        )
        
        from bot.keyboards.inline.admin_keyboards import get_back_to_admin_panel_keyboard
        
        await callback.message.edit_text(
            message_text,
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logging.error(f"Error getting queue status: {e}")
        await callback.answer("❌ Ошибка получения статуса очередей", show_alert=True)
