import logging
from pathlib import Path
from typing import Tuple

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from config.settings import Settings


import os
_BASELINE_REVISION = "0001_initial_schema"


def _build_alembic_config(settings: Settings) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    return config


def _inspect_database_state(connection: Connection) -> Tuple[bool, bool, bool]:
    db_inspector = inspect(connection)
    has_alembic_version = db_inspector.has_table("alembic_version")
    has_users_table = db_inspector.has_table("users")
    has_legacy_migrator_table = db_inspector.has_table("schema_migrations")
    return has_alembic_version, has_users_table, has_legacy_migrator_table


def _run_legacy_migrator_compatibility(connection: Connection) -> None:
    db_inspector = inspect(connection)
    if not db_inspector.has_table("users"):
        return

    users_columns = {
        column["name"]
        for column in db_inspector.get_columns("users")
    }
    user_alter_statements = []

    if "channel_subscription_verified" not in users_columns:
        user_alter_statements.append(
            "ALTER TABLE users ADD COLUMN channel_subscription_verified BOOLEAN"
        )
    if "channel_subscription_checked_at" not in users_columns:
        user_alter_statements.append(
            "ALTER TABLE users ADD COLUMN channel_subscription_checked_at TIMESTAMPTZ"
        )
    if "channel_subscription_verified_for" not in users_columns:
        user_alter_statements.append(
            "ALTER TABLE users ADD COLUMN channel_subscription_verified_for BIGINT"
        )
    if "referral_code" not in users_columns:
        user_alter_statements.append(
            "ALTER TABLE users ADD COLUMN referral_code VARCHAR(16)"
        )

    for statement in user_alter_statements:
        connection.execute(text(statement))

    users_columns = {
        column["name"]
        for column in inspect(connection).get_columns("users")
    }
    if "referral_code" in users_columns:
        connection.execute(
            text(
                """
                WITH generated_codes AS (
                    SELECT
                        user_id,
                        UPPER(
                            SUBSTRING(
                                md5(
                                    user_id::text
                                    || clock_timestamp()::text
                                    || random()::text
                                )
                                FROM 1 FOR 9
                            )
                        ) AS referral_code
                    FROM users
                    WHERE referral_code IS NULL OR referral_code = ''
                )
                UPDATE users AS u
                SET referral_code = g.referral_code
                FROM generated_codes AS g
                WHERE u.user_id = g.user_id
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_users_referral_code
                ON users (referral_code)
                WHERE referral_code IS NOT NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE users
                SET referral_code = UPPER(referral_code)
                WHERE referral_code IS NOT NULL
                  AND referral_code <> UPPER(referral_code)
                """
            )
        )

    db_inspector = inspect(connection)
    if db_inspector.has_table("payments"):
        payments_columns = {
            column["name"]
            for column in db_inspector.get_columns("payments")
        }
        if "original_amount" not in payments_columns:
            connection.execute(text("ALTER TABLE payments ADD COLUMN original_amount FLOAT"))
        if "discount_applied" not in payments_columns:
            connection.execute(text("ALTER TABLE payments ADD COLUMN discount_applied FLOAT"))

    db_inspector = inspect(connection)
    has_promo_codes = db_inspector.has_table("promo_codes")
    if has_promo_codes:
        promo_columns = {
            column["name"]
            for column in db_inspector.get_columns("promo_codes")
        }
        if "promo_type" not in promo_columns:
            connection.execute(
                text(
                    "ALTER TABLE promo_codes ADD COLUMN promo_type VARCHAR NOT NULL DEFAULT 'bonus_days'"
                )
            )
        if "discount_percentage" not in promo_columns:
            connection.execute(
                text("ALTER TABLE promo_codes ADD COLUMN discount_percentage INTEGER")
            )
        if "current_activations" not in promo_columns:
            connection.execute(
                text(
                    "ALTER TABLE promo_codes ADD COLUMN current_activations INTEGER NOT NULL DEFAULT 0"
                )
            )
        else:
            connection.execute(
                text(
                    "UPDATE promo_codes SET current_activations = 0 "
                    "WHERE current_activations IS NULL"
                )
            )
            connection.execute(
                text("ALTER TABLE promo_codes ALTER COLUMN current_activations SET DEFAULT 0")
            )
            connection.execute(
                text("ALTER TABLE promo_codes ALTER COLUMN current_activations SET NOT NULL")
            )
        if "bonus_days" in promo_columns:
            connection.execute(
                text("ALTER TABLE promo_codes ALTER COLUMN bonus_days DROP NOT NULL")
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_promo_codes_promo_type ON promo_codes (promo_type)"
            )
        )

    db_inspector = inspect(connection)
    has_active_discounts = db_inspector.has_table("active_discounts")
    if not has_active_discounts and has_promo_codes:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS active_discounts (
                    user_id BIGINT PRIMARY KEY,
                    promo_code_id INTEGER NOT NULL,
                    discount_percentage INTEGER NOT NULL,
                    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT fk_active_discounts_user
                        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    CONSTRAINT fk_active_discounts_promo_code
                        FOREIGN KEY (promo_code_id) REFERENCES promo_codes (promo_code_id) ON DELETE CASCADE
                )
                """
            )
        )
        has_active_discounts = True

    if has_active_discounts and has_promo_codes:
        connection.execute(
            text(
                "DELETE FROM active_discounts ad "
                "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = ad.user_id) "
                "OR NOT EXISTS (SELECT 1 FROM promo_codes p WHERE p.promo_code_id = ad.promo_code_id)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE active_discounts "
                "DROP CONSTRAINT IF EXISTS active_discounts_user_id_fkey"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE active_discounts "
                "DROP CONSTRAINT IF EXISTS fk_active_discounts_user"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE active_discounts "
                "DROP CONSTRAINT IF EXISTS active_discounts_promo_code_id_fkey"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE active_discounts "
                "DROP CONSTRAINT IF EXISTS fk_active_discounts_promo_code"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE active_discounts "
                "ADD CONSTRAINT fk_active_discounts_user "
                "FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE active_discounts "
                "ADD CONSTRAINT fk_active_discounts_promo_code "
                "FOREIGN KEY (promo_code_id) REFERENCES promo_codes (promo_code_id) ON DELETE CASCADE"
            )
        )
    elif has_active_discounts and not has_promo_codes:
        logging.warning(
            "Alembic legacy compatibility: skipped active_discounts FK repair "
            "because promo_codes table is missing."
        )


def _run_stamp(connection: Connection, alembic_config: Config, revision: str) -> None:
    alembic_config.attributes["connection"] = connection
    command.stamp(alembic_config, revision)


def _run_upgrade(connection: Connection, alembic_config: Config) -> None:
    alembic_config.attributes["connection"] = connection
    command.upgrade(alembic_config, "head")


async def run_alembic_migrations(settings: Settings, async_engine: AsyncEngine) -> None:
    """Apply Alembic migrations with bootstrap for existing installations."""

    alembic_config = _build_alembic_config(settings)
    allow_bootstrap_without_legacy = os.getenv("ALEMBIC_ALLOW_STAMP_WITHOUT_LEGACY", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    async with async_engine.begin() as async_connection:
        (
            has_alembic_version,
            has_users_table,
            has_legacy_migrator_table,
        ) = await async_connection.run_sync(
            _inspect_database_state
        )

        if not has_alembic_version and has_users_table:
            if not has_legacy_migrator_table and not allow_bootstrap_without_legacy:
                raise RuntimeError(
                    "Alembic bootstrap refused: found existing users table without "
                    "alembic_version and without legacy schema_migrations marker. "
                    "Set ALEMBIC_ALLOW_STAMP_WITHOUT_LEGACY=true to explicitly allow "
                    f"stamping baseline {_BASELINE_REVISION} after manual verification."
                )

            if has_legacy_migrator_table:
                logging.info(
                    "Alembic: applying legacy migrator compatibility fixes before stamp."
                )
                await async_connection.run_sync(_run_legacy_migrator_compatibility)
            else:
                logging.warning(
                    "Alembic: existing users table without legacy schema_migrations; "
                    "proceeding with explicit bootstrap override flag."
                )

            logging.info(
                "Alembic: existing schema detected without alembic_version; stamping %s.",
                _BASELINE_REVISION,
            )
            await async_connection.run_sync(
                _run_stamp,
                alembic_config,
                _BASELINE_REVISION,
            )

        logging.info("Alembic: running upgrade to head...")
        await async_connection.run_sync(_run_upgrade, alembic_config)

    logging.info("Alembic: migrations applied successfully.")
