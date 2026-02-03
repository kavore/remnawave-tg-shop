import logging
from dataclasses import dataclass
from typing import Callable, List, Set

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    upgrade: Callable[[Connection], None]


def _ensure_migrations_table(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )


def _migration_0001_add_channel_subscription_fields(connection: Connection) -> None:
    inspector = inspect(connection)
    columns: Set[str] = {col["name"] for col in inspector.get_columns("users")}
    statements: List[str] = []

    if "channel_subscription_verified" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN channel_subscription_verified BOOLEAN"
        )
    if "channel_subscription_checked_at" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN channel_subscription_checked_at TIMESTAMPTZ"
        )
    if "channel_subscription_verified_for" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN channel_subscription_verified_for BIGINT"
        )

    for stmt in statements:
        connection.execute(text(stmt))


def _migration_0002_add_referral_code(connection: Connection) -> None:
    inspector = inspect(connection)
    columns: Set[str] = {col["name"] for col in inspector.get_columns("users")}

    if "referral_code" not in columns:
        connection.execute(
            text("ALTER TABLE users ADD COLUMN referral_code VARCHAR(16)")
        )

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


def _migration_0003_normalize_referral_codes(connection: Connection) -> None:
    inspector = inspect(connection)
    columns: Set[str] = {col["name"] for col in inspector.get_columns("users")}
    if "referral_code" not in columns:
        return

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


def _migration_0004_add_discount_promo_codes(connection: Connection) -> None:
    inspector = inspect(connection)

    # 1. Добавить поля в payments
    payment_columns: Set[str] = {col["name"] for col in inspector.get_columns("payments")}
    if "original_amount" not in payment_columns:
        connection.execute(text("ALTER TABLE payments ADD COLUMN original_amount FLOAT"))
    if "discount_applied" not in payment_columns:
        connection.execute(text("ALTER TABLE payments ADD COLUMN discount_applied FLOAT"))

    # 2. Модифицировать promo_codes
    promo_columns: Set[str] = {col["name"] for col in inspector.get_columns("promo_codes")}

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

    # Изменить bonus_days на nullable (если еще не nullable)
    connection.execute(
        text("ALTER TABLE promo_codes ALTER COLUMN bonus_days DROP NOT NULL")
    )

    # Создать индекс на promo_type
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_promo_codes_promo_type ON promo_codes (promo_type)"
        )
    )

    # 3. Создать таблицу active_discounts
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


def _migration_0005_fix_active_discounts_fk_cascade(connection: Connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("active_discounts"):
        return

    connection.execute(
        text(
            "DELETE FROM active_discounts ad "
            "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = ad.user_id) "
            "OR NOT EXISTS (SELECT 1 FROM promo_codes p WHERE p.promo_code_id = ad.promo_code_id)"
        )
    )

    connection.execute(
        text("ALTER TABLE active_discounts DROP CONSTRAINT IF EXISTS active_discounts_user_id_fkey")
    )
    connection.execute(
        text("ALTER TABLE active_discounts DROP CONSTRAINT IF EXISTS fk_active_discounts_user")
    )
    connection.execute(
        text("ALTER TABLE active_discounts DROP CONSTRAINT IF EXISTS active_discounts_promo_code_id_fkey")
    )
    connection.execute(
        text("ALTER TABLE active_discounts DROP CONSTRAINT IF EXISTS fk_active_discounts_promo_code")
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

MIGRATIONS: List[Migration] = [
    Migration(
        id="0001_add_channel_subscription_fields",
        description="Add columns to track required channel subscription verification",
        upgrade=_migration_0001_add_channel_subscription_fields,
    ),
    Migration(
        id="0002_add_referral_code",
        description="Store short referral codes for users and backfill existing rows",
        upgrade=_migration_0002_add_referral_code,
    ),
    Migration(
        id="0003_normalize_referral_codes",
        description="Normalize referral codes to uppercase for consistent lookups",
        upgrade=_migration_0003_normalize_referral_codes,
    ),
    Migration(
        id="0004_add_discount_promo_codes",
        description="Add support for percentage discount promo codes",
        upgrade=_migration_0004_add_discount_promo_codes,
    ),
    Migration(
        id="0005_fix_active_discounts_fk_cascade",
        description="Ensure active_discounts FKs cascade on user/promo delete",
        upgrade=_migration_0005_fix_active_discounts_fk_cascade,
    ),
]


def run_database_migrations(connection: Connection) -> None:
    """
    Apply pending migrations sequentially. Already applied revisions are skipped.
    """
    _ensure_migrations_table(connection)

    applied_revisions: Set[str] = {
        row[0]
        for row in connection.execute(
            text("SELECT id FROM schema_migrations")
        )
    }

    for migration in MIGRATIONS:
        if migration.id in applied_revisions:
            continue

        logging.info(
            "Migrator: applying %s – %s", migration.id, migration.description
        )
        try:
            with connection.begin_nested():
                migration.upgrade(connection)
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (id) VALUES (:revision)"
                    ),
                    {"revision": migration.id},
                )
        except Exception as exc:
            logging.error(
                "Migrator: failed to apply %s (%s)",
                migration.id,
                migration.description,
                exc_info=True,
            )
            raise exc
        else:
            logging.info("Migrator: migration %s applied successfully", migration.id)
