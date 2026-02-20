import logging
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from config.settings import Settings
from .alembic_runner import run_alembic_migrations

async_engine = None


def _mask_db_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.username is None:
            return url
        username = parsed.username
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{username}:***@{host}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "<masked>"


def init_db_connection(settings: Settings) -> sessionmaker:
    global async_engine

    if async_engine is None:
        masked_url = _mask_db_url(settings.DATABASE_URL)
        logging.info(
            f"Attempting to create SQLAlchemy engine with URL: {masked_url}"
        )
        async_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
        )

    local_async_session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    logging.info(
        "SQLAlchemy Async Engine and SessionFactory configured for PostgreSQL."
    )
    return local_async_session_factory


async def get_async_session(session_factory: sessionmaker) -> AsyncSession:

    if session_factory is None:
        raise RuntimeError(
            "AsyncSessionFactory is not provided or initialized.")

    async_session = session_factory()
    try:
        yield async_session
    finally:
        await async_session.close()


async def init_db(settings: Settings, session_factory: sessionmaker):

    global async_engine
    if async_engine is None:

        logging.warning(
            "init_db: async_engine was None, re-initializing via init_db_connection."
        )

        raise RuntimeError(
            "async_engine is not initialized. Call init_db_connection and get session_factory first."
        )

    await run_alembic_migrations(settings, async_engine)
    logging.info("PostgreSQL database migrations checked/applied via Alembic.")

    async with session_factory() as session:
        from .dal.panel_sync_dal import get_panel_sync_status, update_panel_sync_status
        try:
            current_status = await get_panel_sync_status(session)
            if current_status is None:
                logging.info("Initializing panel_sync_status record.")
                await update_panel_sync_status(session,
                                               status="never_run",
                                               details="System initialized",
                                               users_processed=0,
                                               subs_synced=0)
                await session.commit()
        except Exception as e_sync_init:
            await session.rollback()
            logging.error(
                f"Failed to initialize PanelSyncStatus: {e_sync_init}",
                exc_info=True)
