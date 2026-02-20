"""add active discount expiration

Revision ID: 0002_active_discount_expires_at
Revises: 0001_initial_schema
Create Date: 2026-02-08 00:00:01.000000

"""

from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_active_discount_expires_at"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX_NAME = "idx_active_discounts_expires_at"


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column(
            "active_discounts",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE active_discounts "
                "SET expires_at = COALESCE(activated_at, NOW()) + INTERVAL '10 minutes' "
                "WHERE expires_at IS NULL"
            )
        )
        op.alter_column("active_discounts", "expires_at", nullable=False)
        op.create_index(_INDEX_NAME, "active_discounts", ["expires_at"], unique=False)
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("active_discounts"):
        return

    columns = {column["name"] for column in inspector.get_columns("active_discounts")}
    if "expires_at" not in columns:
        op.add_column(
            "active_discounts",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE active_discounts "
                "SET expires_at = COALESCE(activated_at, NOW()) + INTERVAL '10 minutes' "
                "WHERE expires_at IS NULL"
            )
        )
        op.alter_column("active_discounts", "expires_at", nullable=False)

    indexes = {index["name"] for index in inspector.get_indexes("active_discounts")}
    if _INDEX_NAME not in indexes:
        op.create_index(_INDEX_NAME, "active_discounts", ["expires_at"], unique=False)


def downgrade() -> None:
    if context.is_offline_mode():
        op.drop_index(_INDEX_NAME, table_name="active_discounts")
        op.drop_column("active_discounts", "expires_at")
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("active_discounts"):
        return

    indexes = {index["name"] for index in inspector.get_indexes("active_discounts")}
    if _INDEX_NAME in indexes:
        op.drop_index(_INDEX_NAME, table_name="active_discounts")

    columns = {column["name"] for column in inspector.get_columns("active_discounts")}
    if "expires_at" in columns:
        op.drop_column("active_discounts", "expires_at")
