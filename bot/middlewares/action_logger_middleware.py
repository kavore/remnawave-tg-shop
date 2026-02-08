import logging
from typing import Callable, Dict, Any, Awaitable, Optional
from datetime import datetime, timezone

from aiogram import BaseMiddleware
from aiogram.types import Update, User, Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import message_log_dal, user_dal
from config.settings import Settings


class ActionLoggerMiddleware(BaseMiddleware):

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings

    async def __call__(self, handler: Callable[[Update, Dict[str, Any]],
                                               Awaitable[Any]], event: Update,
                       data: Dict[str, Any]) -> Any:

        result = await handler(event, data)

        session: AsyncSession = data["session"]
        event_user: Optional[User] = data.get("event_from_user")

        user_id: Optional[int] = None
        telegram_username: Optional[str] = None
        telegram_first_name: Optional[str] = None
        content: Optional[str] = None
        is_admin_event_flag: bool = False
        target_user_id_for_log: Optional[int] = None

        if event_user:
            user_id = event_user.id
            telegram_username = event_user.username
            telegram_first_name = event_user.first_name
            if user_id in self.settings.ADMIN_IDS:
                is_admin_event_flag = True

        raw_update_snippet = None
        if self.settings.LOG_STORE_RAW_UPDATES:
            try:
                raw_update_snippet = event.model_dump_json(exclude_none=True,
                                                           indent=None)[:1000]
            except AttributeError:
                raw_update_snippet = str(event)[:1000]
            except Exception:
                raw_update_snippet = str(event)[:1000]

        current_event_type = event.event_type

        if event.message:
            msg: Message = event.message
            if msg.text:
                content = msg.text
                if msg.text.startswith('/'):
                    current_event_type = f"command:{msg.text.split()[0]}"

            else:
                content = f"[{msg.content_type or 'unknown_content_type'}]"
                current_event_type = f"message:{msg.content_type or 'unknown'}"
        elif event.callback_query:
            cb: CallbackQuery = event.callback_query
            content = cb.data
            action_part = cb.data.split(
                ":")[0] if cb.data and ":" in cb.data else cb.data
            current_event_type = f"callback:{action_part}"

        if user_id or current_event_type not in ["update"]:

            log_user_id_for_db = user_id
            if user_id:
                user_exists = await user_dal.get_user_by_id(session, user_id)
                if not user_exists:
                    logging.warning(
                        f"ActionLoggerMiddleware: User {user_id} not found in DB. Logging action with user_id=NULL."
                    )
                    log_user_id_for_db = None

            log_payload = {
                "user_id": log_user_id_for_db,
                "telegram_username": telegram_username,
                "telegram_first_name": telegram_first_name,
                "event_type": current_event_type,
                "content": (content[:1000] if content else "N/A") if self.settings.LOG_STORE_MESSAGE_CONTENT else None,
                "raw_update_preview": raw_update_snippet,
                "is_admin_event": is_admin_event_flag,
                "target_user_id": target_user_id_for_log,
                "timestamp": datetime.now(timezone.utc)
            }
            try:

                await message_log_dal.create_message_log_no_commit(
                    session, log_payload)
            except Exception as e_log:
                logging.error(
                    f"ActionLoggerMiddleware: Failed to add log to session for user {user_id}, type {current_event_type}: {e_log}",
                    exc_info=True)

        return result
