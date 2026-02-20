import logging
import asyncio
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher
from aiogram.types import (MenuButtonDefault, MenuButtonWebApp, WebAppInfo, BotCommand)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from bot.services.panel_webhook_service import PanelWebhookService, panel_webhook_route
from sqlalchemy.orm import sessionmaker

from config.settings import Settings

from db.database_setup import init_db_connection

from bot.middlewares.i18n import I18nMiddleware, get_i18n_instance, JsonI18n
from bot.middlewares.db_session import DBSessionMiddleware
from bot.middlewares.ban_check_middleware import BanCheckMiddleware
from bot.middlewares.action_logger_middleware import ActionLoggerMiddleware
from bot.middlewares.profile_sync import ProfileSyncMiddleware
from bot.app.controllers.dispatcher_controller import build_dispatcher
from bot.app.factories.build_services import build_core_services
from bot.app.web.web_server import build_and_start_web_app

from bot.routers import build_root_router

from bot.services.yookassa_service import YooKassaService
from bot.services.panel_api_service import PanelApiService
from bot.services.subscription_service import SubscriptionService
from bot.services.referral_service import ReferralService
from bot.services.promo_code_service import PromoCodeService
from bot.services.stars_service import StarsService
from bot.services.crypto_pay_service import CryptoPayService, cryptopay_webhook_route

from bot.handlers.user import payment as user_payment_webhook_module
from bot.handlers.admin.sync_admin import perform_sync
from bot.utils.message_queue import init_queue_manager


async def register_all_routers(dp: Dispatcher, settings: Settings):
    dp.include_router(build_root_router(settings))
    logging.info("All application routers registered.")


async def on_startup_configured(dispatcher: Dispatcher):
    bot: Bot = dispatcher["bot_instance"]
    settings: Settings = dispatcher["settings"]
    i18n_instance: JsonI18n = dispatcher["i18n_instance"]
    panel_service: PanelApiService = dispatcher["panel_service"]

    async_session_factory: sessionmaker = dispatcher["async_session_factory"]

    logging.info("STARTUP: on_startup_configured executing...")


    telegram_webhook_url_to_set = settings.WEBHOOK_BASE_URL
    if telegram_webhook_url_to_set:
        full_telegram_webhook_url = settings.telegram_full_webhook_url
        if not full_telegram_webhook_url:
            logging.error(
                "STARTUP: Telegram webhook URL could not be built (WEBHOOK_BASE_URL missing)."
            )
            raise SystemExit("WEBHOOK_BASE_URL is required. Polling mode is disabled.")

        logging.info(
            "STARTUP: Attempting to set Telegram webhook (path=%s)",
            settings.telegram_webhook_path,
        )

        try:
            current_webhook_info = await bot.get_webhook_info()
            if current_webhook_info.url:
                logging.info("STARTUP: Telegram webhook already set (non-empty URL).")
            else:
                logging.info("STARTUP: Telegram webhook currently empty (will set).")

            telegram_webhook_secret = (settings.TELEGRAM_WEBHOOK_SECRET or "").strip() or None
            set_success = await bot.set_webhook(
                url=full_telegram_webhook_url,
                drop_pending_updates=True,
                allowed_updates=dispatcher.resolve_used_update_types(),
                secret_token=telegram_webhook_secret,
            )
            if set_success:
                logging.info("STARTUP: bot.set_webhook returned SUCCESS (True).")
            else:
                logging.error("STARTUP: bot.set_webhook returned FAILURE (False).")

            new_webhook_info = await bot.get_webhook_info()
            if not new_webhook_info.url:
                logging.error(
                    "STARTUP: CRITICAL - Telegram Webhook URL is EMPTY after set attempt. Check bot token and URL validity."
                )

        except Exception as e_setwebhook:
            logging.error(
                "STARTUP: EXCEPTION during set/get Telegram webhook: %s",
                e_setwebhook,
                exc_info=True,
            )
    else:
        logging.error(
            "STARTUP: WEBHOOK_BASE_URL not set in environment. Webhook mode is required. Exiting."
        )
        raise SystemExit("WEBHOOK_BASE_URL is required. Polling mode is disabled.")

    if settings.SUBSCRIPTION_MINI_APP_URL:
        try:
            menu_text = i18n_instance.gettext(
                settings.DEFAULT_LANGUAGE,
                "menu_my_subscription_inline",
            )
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text=menu_text,
                    web_app=WebAppInfo(url=settings.SUBSCRIPTION_MINI_APP_URL),
                )
            )
            await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            logging.info(
                "STARTUP: Mini app domain registered and default menu button restored."
            )
        except Exception as e:
            logging.error(
                f"STARTUP: Failed to register mini app domain: {e}", exc_info=True
            )

    if settings.START_COMMAND_DESCRIPTION:
        try:
            await bot.set_my_commands([
                BotCommand(command="start", description=settings.START_COMMAND_DESCRIPTION)
            ])
            logging.info("STARTUP: /start command description set.")
        except Exception as e:
            logging.error(f"STARTUP: Failed to set bot commands: {e}", exc_info=True)

    # Initialize message queue manager
    try:
        queue_manager = init_queue_manager(bot)
        dispatcher["queue_manager"] = queue_manager
        logging.info("STARTUP: Message queue manager initialized")
    except Exception as e:
        logging.error(f"STARTUP: Failed to initialize message queue manager: {e}", exc_info=True)

    # Initialize promo discount expiration worker
    try:
        promo_code_service: Optional[PromoCodeService] = dispatcher.get("promo_code_service")
        if promo_code_service:
            await promo_code_service.setup_discount_expiration_worker(async_session_factory)
            logging.info("STARTUP: Promo discount expiration worker initialized")
    except Exception as e:
        logging.error(
            f"STARTUP: Failed to initialize promo discount expiration worker: {e}",
            exc_info=True,
        )

    # Automatic sync on startup
    try:
        logging.info("STARTUP: Running automatic panel sync...")
        
        async with async_session_factory() as session:
            sync_result = await perform_sync(
                panel_service=panel_service,
                session=session,
                settings=settings,
                i18n_instance=i18n_instance
            )
            
        if sync_result.get("status") == "completed":
            logging.info(f"STARTUP: Automatic sync completed successfully. Details: {sync_result.get('details', 'N/A')}")
        else:
            logging.warning(f"STARTUP: Automatic sync completed with issues. Status: {sync_result.get('status', 'unknown')}")
            
    except Exception as e:
        logging.error(f"STARTUP: Failed to run automatic sync: {e}", exc_info=True)

    logging.info("STARTUP: Bot on_startup_configured completed.")


async def on_shutdown_configured(dispatcher: Dispatcher):
    logging.warning("SHUTDOWN: on_shutdown_configured executing...")

    async def close_service(key: str) -> None:
        service = dispatcher.get(key)
        if not service:
            return
        close_coro = getattr(service, "close", None)
        if callable(close_coro):
            try:
                await close_coro()
                logging.info(f"{key} closed on shutdown.")
            except Exception as e:
                logging.warning(f"Failed to close {key}: {e}")
        else:
            close_session = getattr(service, "close_session", None)
            if callable(close_session):
                try:
                    await close_session()
                    logging.info(f"{key} session closed on shutdown.")
                except Exception as e:
                    logging.warning(f"Failed to close session for {key}: {e}")

    for service_key in (
        "panel_service",
        "cryptopay_service",
        "freekassa_service",
        "panel_webhook_service",
        "yookassa_service",
        "lknpd_service",
        "promo_code_service",
        "stars_service",
        "subscription_service",
        "referral_service",
        "platega_service",
        "severpay_service",
    ):
        await close_service(service_key)

    bot: Bot = dispatcher["bot_instance"]
    if bot and bot.session:
        try:
            await bot.session.close()
            logging.info("SHUTDOWN: Aiogram Bot session closed.")
        except Exception as e:
            logging.warning(f"SHUTDOWN: Failed to close bot session: {e}")

    from db.database_setup import async_engine as global_async_engine

    if global_async_engine:
        logging.info("SHUTDOWN: Disposing SQLAlchemy engine...")
        await global_async_engine.dispose()
        logging.info("SHUTDOWN: SQLAlchemy engine disposed.")

    logging.info("SHUTDOWN: Bot on_shutdown_configured completed.")


async def run_bot(settings_param: Settings):
    local_async_session_factory = init_db_connection(settings_param)
    if local_async_session_factory is None:
        logging.critical(
            "Failed to initialize database connection and session factory. Exiting."
        )
        return
    dp, bot, extra = build_dispatcher(settings_param, local_async_session_factory)
    i18n_instance = extra["i18n_instance"]

    # Get bot username for YooKassa default return URL if needed
    actual_bot_username = "your_bot_username"
    try:
        bot_info = await bot.get_me()
        actual_bot_username = bot_info.username
        logging.info(f"Bot username resolved: @{actual_bot_username}")
    except Exception as e:
        logging.error(
            f"Failed to get bot info (e.g., for YooKassa default URL): {e}. Using fallback: {actual_bot_username}"
        )

    services = build_core_services(
        settings_param,
        bot,
        local_async_session_factory,
        i18n_instance,
        actual_bot_username,
    )
    for key, service in services.items():
        dp[key] = service
    dp["panel_service"] = services["panel_service"]
    dp["async_session_factory"] = local_async_session_factory

    # Wrap startup/shutdown handlers to satisfy aiogram event signature (no args passed)
    async def _on_startup_wrapper():
        await on_startup_configured(dp)
    async def _on_shutdown_wrapper():
        await on_shutdown_configured(dp)
    dp.startup.register(_on_startup_wrapper)
    dp.shutdown.register(_on_shutdown_wrapper)

    await register_all_routers(dp, settings_param)

    tg_webhook_base = settings_param.WEBHOOK_BASE_URL

    # Webhook mode is now required - exit if not configured
    if not tg_webhook_base:
        logging.error("WEBHOOK_BASE_URL is required. Polling mode is disabled. Exiting.")
        await dp.emit_shutdown()
        raise SystemExit("WEBHOOK_BASE_URL is required. Polling mode is disabled.")

    logging.info(f"--- Bot Run Mode Decision ---")
    logging.info(f"Configured WEBHOOK_BASE_URL: '{tg_webhook_base}' -> Webhook Mode: ENABLED")
    logging.info(f"YooKassa webhook path: '{settings_param.yookassa_webhook_path}'")
    logging.info(f"Decision: Run AIOHTTP server: ENABLED (required for webhooks)")
    logging.info(f"--- End Bot Run Mode Decision ---")

    web_app_runner = None
    main_tasks = []

    # Only run AIOHTTP server for webhook mode
    async def web_server_task():
        await build_and_start_web_app(dp, bot, settings_param, local_async_session_factory)

    main_tasks.append(asyncio.create_task(web_server_task(), name="AIOHTTPServerTask"))

    # Recurring billing moved to panel webhook (24h before expiry). No periodic task needed here.

    logging.info("Starting bot in Webhook mode with AIOHTTP server...")
    logging.info(f"Starting bot with main tasks: {[task.get_name() for task in main_tasks]}")

    try:
        await asyncio.gather(*main_tasks)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError) as e:
        logging.info(f"Main bot loop interrupted/cancelled: {type(e).__name__} - {e}")
    finally:
        logging.info("Initiating final bot shutdown sequence...")
        for task in main_tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logging.info(
                        f"Task '{task.get_name()}' was cancelled successfully."
                    )
                except Exception as e_task_cancel:
                    logging.error(
                        f"Error during cancellation of task '{task.get_name()}': {e_task_cancel}",
                        exc_info=True,
                    )

        if web_app_runner:
            await web_app_runner.cleanup()
            logging.info("AIOHTTP AppRunner cleaned up.")

        await dp.emit_shutdown()
        logging.info("Dispatcher shutdown sequence emitted.")

        logging.info("Bot run_bot function finished.")
