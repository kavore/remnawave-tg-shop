import logging
import asyncio
from aiogram import Router, F, types, Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

from aiogram.fsm.context import FSMContext
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings

from db.dal import user_dal, message_log_dal

from bot.states.admin_states import AdminStates
from bot.keyboards.inline.admin_keyboards import (
    get_broadcast_confirmation_keyboard,
    get_back_to_admin_panel_keyboard,
    get_admin_panel_keyboard,
)
from bot.middlewares.i18n import JsonI18n
from bot.utils.message_queue import get_queue_manager
from bot.utils import get_message_content, send_message_by_type, send_message_via_queue, MessageContent

router = Router(name="admin_broadcast_router")


async def broadcast_message_prompt_handler(
    callback: types.CallbackQuery,
    state: FSMContext,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        logging.error("i18n missing in broadcast_message_prompt_handler")
        await callback.answer("Language service error.", show_alert=True)
        return

    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    prompt_text = _("admin_broadcast_enter_message")

    if callback.message:
        try:
            await callback.message.edit_text(
                prompt_text,
                reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            )
        except Exception as e:
            logging.warning(
                f"Could not edit message for broadcast prompt: {e}. Sending new."
            )
            await callback.message.answer(
                prompt_text,
                reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
            )
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_broadcast_message)


@router.message(AdminStates.waiting_for_broadcast_message)
async def process_broadcast_message_handler(
    message: types.Message,
    state: FSMContext,
    i18n_data: dict,
    settings: Settings,
    session: AsyncSession,
    bot: Bot,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        logging.error("i18n missing in process_broadcast_message_handler")
        await message.reply("Language service error.")
        return

    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    # Определяем тип содержимого и сохраняем данные в state
    entities = message.entities or message.caption_entities or []
    content = get_message_content(message)

    # Если нет ни текста, ни медиа — ошибка
    if not content.text and not content.file_id:
        await message.answer(_("admin_broadcast_error_no_message"))
        return

    # Сохраняем данные для рассылки
    await state.update_data(
        broadcast_text=content.text,
        broadcast_entities=entities,
        broadcast_content_type=content.content_type,
        broadcast_file_id=content.file_id,
        broadcast_target="all",
    )

    # Отправляем превью-копию того, что будет разослано
    try:
        # Для медиа-сообщений используем caption_entities, для текста - entities
        if content.content_type == "text":
            await send_message_by_type(
                bot, 
                chat_id=message.chat.id, 
                content=content,
                parse_mode="HTML",
                entities=entities,
                disable_web_page_preview=True,
                disable_notification=True,
            )
        else:
            await send_message_by_type(
                bot, 
                chat_id=message.chat.id, 
                content=content,
                parse_mode="HTML",
                caption_entities=entities,
                disable_web_page_preview=True,
                disable_notification=True,
            )
    except TelegramBadRequest as e:
        await message.answer(
            _(
                "admin_broadcast_invalid_html",
                error=str(e),
            )
        )
        return

    # Показываем короткое подтверждение без дублирования текста — сообщение выше служит превью
    confirmation_prompt = _("admin_broadcast_confirm_prompt_short")

    await message.answer(
        confirmation_prompt,
        reply_markup=get_broadcast_confirmation_keyboard(current_lang, i18n, target="all"),
    )
    await state.set_state(AdminStates.confirming_broadcast)


@router.callback_query(
    F.data.startswith("broadcast_target:"),
    AdminStates.confirming_broadcast,
)
async def change_broadcast_target_handler(
    callback: types.CallbackQuery,
    state: FSMContext,
    i18n_data: dict,
    settings: Settings,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error updating selection.", show_alert=True)
        return

    new_target = callback.data.split(":")[1]
    if new_target not in {"all", "active", "inactive"}:
        await callback.answer("Unknown target.", show_alert=True)
        return

    await state.update_data(broadcast_target=new_target)
    user_fsm_data = await state.get_data()
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    confirmation_prompt = _(
        "admin_broadcast_confirm_prompt_short"
    )
    try:
        await callback.message.edit_text(
            confirmation_prompt,
            reply_markup=get_broadcast_confirmation_keyboard(
                current_lang, i18n, target=new_target
            ),
        )
    except Exception as exc:
        logging.debug("Suppressed exception in bot/handlers/admin/broadcast.py: %s", exc)
    await callback.answer()


@router.callback_query(
    F.data == "admin_action:main", AdminStates.waiting_for_broadcast_message
)
async def cancel_broadcast_at_prompt_stage(
    callback: types.CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n_data: dict,
    session: AsyncSession,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error cancelling.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    try:
        await callback.message.edit_text(
            _("admin_broadcast_cancelled_nav_back"), reply_markup=None
        )
    except Exception:
        await callback.message.answer(_("admin_broadcast_cancelled_nav_back"))

    await callback.answer(_("admin_broadcast_cancelled_alert"))
    await state.clear()

    await callback.message.answer(
        _(key="admin_panel_title"),
        reply_markup=get_admin_panel_keyboard(i18n, current_lang, settings),
    )


@router.callback_query(
    F.data.startswith("broadcast_final_action:"),
    AdminStates.confirming_broadcast,
)
async def confirm_broadcast_callback_handler(
    callback: types.CallbackQuery,
    state: FSMContext,
    i18n_data: dict,
    bot: Bot,
    settings: Settings,
    session: AsyncSession,
):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Error processing broadcast confirmation.", show_alert=True)
        return
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)

    action = callback.data.split(":")[1]
    user_fsm_data = await state.get_data()

    if action == "send":
        # Создаем объект контента из сохраненных данных
        content = MessageContent(
            content_type=user_fsm_data.get("broadcast_content_type", "text"),
            file_id=user_fsm_data.get("broadcast_file_id"),
            text=user_fsm_data.get("broadcast_text")
        )
        entities = user_fsm_data.get("broadcast_entities", [])
        
        if not content.text and content.content_type == "text":
            await callback.message.edit_text(_("admin_broadcast_error_no_message"))
            await state.clear()
            await callback.answer(
                _("admin_broadcast_error_no_message_alert"), show_alert=True
            )
            return

        await callback.message.edit_text(_("admin_broadcast_sending_started"), reply_markup=None)
        await callback.answer()

        target = user_fsm_data.get("broadcast_target", "all")
        if target == "active":
            user_ids = await user_dal.get_user_ids_with_active_subscription(session)
        elif target == "inactive":
            user_ids = await user_dal.get_user_ids_without_active_subscription(session)
        else:
            user_ids = await user_dal.get_all_active_user_ids_for_broadcast(session)

        sent_count = 0
        failed_count = 0
        admin_user = callback.from_user
        logging.info(
            f"Admin {admin_user.id} broadcasting '{(content.text or '')[:50]}...' to {len(user_ids)} users."
        )

        # Get message queue manager
        queue_manager = get_queue_manager()
        if not queue_manager:
            await callback.message.edit_text("❌ Ошибка: система очередей не инициализирована", reply_markup=None)
            return

        # Queue all messages for sending
        for uid in user_ids:
            try:
                # Для медиа-сообщений используем caption_entities, для текста - entities
                if content.content_type == "text":
                    await send_message_via_queue(
                        queue_manager, 
                        uid, 
                        content,
                        parse_mode="HTML",
                        entities=entities,
                        disable_web_page_preview=True,
                    )
                else:
                    await send_message_via_queue(
                        queue_manager, 
                        uid, 
                        content,
                        parse_mode="HTML",
                        caption_entities=entities,
                        disable_web_page_preview=True,
                    )
                sent_count += 1
                
                # Log successful queuing
                await message_log_dal.create_message_log(
                    session,
                    {
                        "user_id": admin_user.id,
                        "telegram_username": admin_user.username,
                        "telegram_first_name": admin_user.first_name,
                        "event_type": "admin_broadcast_queued",
                        "content": f"To user {uid}: [{content.content_type}] {(content.text or '')[:70]}...",
                        "is_admin_event": True,
                        "target_user_id": uid,
                    },
                )
            except Exception as e:
                failed_count += 1
                logging.warning(
                    f"Failed to queue broadcast to {uid}: {type(e).__name__} – {e}"
                )
                await message_log_dal.create_message_log(
                    session,
                    {
                        "user_id": admin_user.id,
                        "telegram_username": admin_user.username,
                        "telegram_first_name": admin_user.first_name,
                        "event_type": "admin_broadcast_failed",
                        "content": f"For user {uid}: {type(e).__name__} – {str(e)[:70]}...",
                        "is_admin_event": True,
                        "target_user_id": uid,
                    },
                )

        try:
            await session.commit()
        except Exception as e_commit:
            await session.rollback()
            logging.error(f"Error committing broadcast logs: {e_commit}")

        # Prepare queue stats presentation
        queue_stats = queue_manager.get_queue_stats()
        back_keyboard = get_back_to_admin_panel_keyboard(current_lang, i18n)
        initial_user_failed = queue_stats.get("user_failed_messages", 0)
        initial_group_failed = queue_stats.get("group_failed_messages", 0)

        def build_queue_status(stats: dict) -> str:
            dynamic_failed = max(
                0, stats.get("user_failed_messages", 0) - initial_user_failed
            ) + max(0, stats.get("group_failed_messages", 0) - initial_group_failed)
            total_failed = failed_count + dynamic_failed
            return _(
                "broadcast_queue_result",
                sent_count=sent_count,
                failed_count=total_failed,
                user_queue_size=stats["user_queue_size"],
                group_queue_size=stats["group_queue_size"],
            )

        result_message = build_queue_status(queue_stats)

        status_message = await callback.message.answer(
            result_message,
            reply_markup=back_keyboard,
        )

        async def auto_update_queue_status() -> None:
            """Refresh queue stats message twice per second via message edit."""
            last_text = result_message
            # Update for up to 2 minutes (240 iterations at 0.5s intervals)
            max_iterations = 240
            for _ in range(max_iterations):
                await asyncio.sleep(0.5)

                stats = queue_manager.get_queue_stats()
                new_text = build_queue_status(stats)
                queues_drained = (
                    stats["user_queue_size"] == 0
                    and stats["group_queue_size"] == 0
                    and not stats.get("user_queue_processing")
                    and not stats.get("group_queue_processing")
                )

                if new_text != last_text:
                    try:
                        await status_message.edit_text(
                            new_text,
                            reply_markup=back_keyboard,
                        )
                        last_text = new_text
                    except TelegramBadRequest as e:
                        if "message is not modified" in str(e):
                            last_text = new_text
                        else:
                            logging.debug(
                                "Broadcast queue auto-update stopped: %s", e
                            )
                            break
                    except Exception as e:
                        logging.debug(
                            "Broadcast queue auto-update unexpected error: %s", e
                        )
                        break

                if queues_drained:
                    # Final refresh already attempted; exit loop.
                    break
            else:
                logging.debug("Broadcast queue auto-update reached time limit.")

        asyncio.create_task(auto_update_queue_status())

    elif action == "cancel":
        await callback.message.edit_text(
            _("admin_broadcast_cancelled"),
            reply_markup=get_back_to_admin_panel_keyboard(current_lang, i18n),
        )
        await callback.answer()

    await state.clear()
