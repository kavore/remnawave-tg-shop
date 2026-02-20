import logging
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from typing import Optional, Union
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from bot.services.referral_service import ReferralService

from bot.keyboards.inline.user_keyboards import get_back_to_main_menu_markup
from bot.middlewares.i18n import JsonI18n

router = Router(name="user_referral_router")


async def referral_command_handler(event: Union[types.Message,
                                                types.CallbackQuery],
                                   settings: Settings, i18n_data: dict,
                                   referral_service: ReferralService, bot: Bot,
                                   session: AsyncSession):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")

    target_message_obj = event.message if isinstance(
        event, types.CallbackQuery) else event
    if not target_message_obj:
        logging.error(
            "Target message is None in referral_command_handler (possibly from callback without message)."
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer("Error displaying referral info.",
                               show_alert=True)
        return

    if not i18n or not referral_service:
        logging.error(
            "Dependencies (i18n or ReferralService) missing in referral_command_handler"
        )
        await target_message_obj.answer(
            "Service error. Please try again later.")
        if isinstance(event, types.CallbackQuery): await event.answer()
        return

    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    if not settings.REFERRAL_ENABLED:
        await target_message_obj.answer(
            _("referral_no_bonuses_configured"),
            reply_markup=get_back_to_main_menu_markup(current_lang, i18n),
        )
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username
    except Exception as e_bot_info:
        logging.error(
            f"Failed to get bot info for referral link: {e_bot_info}")
        await target_message_obj.answer(_("error_generating_referral_link"))
        if isinstance(event, types.CallbackQuery): await event.answer()
        return

    if not bot_username:
        logging.error("Bot username is None, cannot generate referral link.")
        await target_message_obj.answer(_("error_generating_referral_link"))
        if isinstance(event, types.CallbackQuery): await event.answer()
        return

    inviter_user_id = event.from_user.id
    referral_link = await referral_service.generate_referral_link(
        session, bot_username, inviter_user_id)

    if not referral_link:
        logging.error(
            "Failed to generate referral link for user %s (probably missing DB record).",
            inviter_user_id,
        )
        await target_message_obj.answer(_("error_generating_referral_link"))
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        return

    bonus_info_parts = []
    if getattr(settings, "traffic_sale_mode", False):
        bonus_details_str = _("referral_not_available_for_traffic")
    else:
        if settings.subscription_options:
            for months_period_key, _price in sorted(
                    settings.subscription_options.items()):

                inv_bonus = settings.referral_bonus_inviter.get(months_period_key)
                ref_bonus = settings.referral_bonus_referee.get(months_period_key)
                if inv_bonus is not None or ref_bonus is not None:
                    bonus_info_parts.append(
                        _("referral_bonus_per_period",
                          months=months_period_key,
                          inviter_bonus_days=inv_bonus
                          if inv_bonus is not None else _("no_bonus_placeholder"),
                          referee_bonus_days=ref_bonus
                          if ref_bonus is not None else _("no_bonus_placeholder")))

        bonus_details_str = "\n".join(bonus_info_parts) if bonus_info_parts else _(
            "referral_no_bonuses_configured")

    # Get referral statistics
    referral_stats = await referral_service.get_referral_stats(session, inviter_user_id)

    text = _("referral_program_info_new",
             referral_link=referral_link,
             bonus_details=bonus_details_str,
             invited_count=referral_stats["invited_count"],
             purchased_count=referral_stats["purchased_count"])

    from bot.keyboards.inline.user_keyboards import get_referral_link_keyboard
    reply_markup_val = get_referral_link_keyboard(current_lang, i18n)

    if isinstance(event, types.Message):
        await event.answer(text,
                           reply_markup=reply_markup_val,
                           disable_web_page_preview=True)
    elif isinstance(event, types.CallbackQuery) and event.message:
        try:
            await event.message.edit_text(text,
                                          reply_markup=reply_markup_val,
                                          disable_web_page_preview=True)
        except Exception as e_edit:
            logging.warning(
                f"Failed to edit message for referral info: {e_edit}. Sending new one."
            )
            await event.message.answer(text,
                                       reply_markup=reply_markup_val,
                                       disable_web_page_preview=True)
        await event.answer()


@router.callback_query(F.data.startswith("referral_action:"))
async def referral_action_handler(callback: types.CallbackQuery, settings: Settings, 
                                 i18n_data: dict, referral_service: ReferralService, 
                                 bot: Bot, session: AsyncSession):
    action = callback.data.split(":")[1]
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n = i18n_data.get("i18n_instance")
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    if action == "share_message":
        if not settings.REFERRAL_ENABLED:
            await callback.answer(_("referral_no_bonuses_configured"), show_alert=True)
            return
        try:
            bot_info = await bot.get_me()
            bot_username = bot_info.username
            if not bot_username:
                await callback.answer("Ошибка получения имени бота", show_alert=True)
                return

            inviter_user_id = callback.from_user.id
            referral_link = await referral_service.generate_referral_link(
                session, bot_username, inviter_user_id)

            if not referral_link:
                logging.error(
                    "Failed to generate referral link for user %s via inline button.",
                    inviter_user_id,
                )
                await callback.answer(_("error_generating_referral_link"), show_alert=True)
                return
            
            friend_message = _("referral_friend_message", referral_link=referral_link)
            
            await callback.message.answer(
                friend_message,
                disable_web_page_preview=True
            )
            
        except Exception as e:
            logging.error(f"Error in referral share message: {e}")
            await callback.answer("Произошла ошибка", show_alert=True)
        
    await callback.answer()
