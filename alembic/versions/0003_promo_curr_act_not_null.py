"""harden promo current activations

Revision ID: 0003_promo_current_activations_not_null
Revises: 0002_active_discount_expires_at
Create Date: 2026-02-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_promo_curr_act_not_null"
down_revision: Union[str, Sequence[str],
                     None] = "0002_active_discount_expires_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(
            sa.text(
                "UPDATE promo_codes SET current_activations = 0 "
                "WHERE current_activations IS NULL"
            )
        )
        op.alter_column(
            "promo_codes",
            "current_activations",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        )
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("promo_codes"):
        return

    promo_columns = {column["name"]
                     for column in inspector.get_columns("promo_codes")}
    if "current_activations" not in promo_columns:
        op.add_column(
            "promo_codes",
            sa.Column("current_activations", sa.Integer(),
                      nullable=False, server_default=sa.text("0")),
        )
        return

    op.execute(
        sa.text(
            "UPDATE promo_codes SET current_activations = 0 "
            "WHERE current_activations IS NULL"
        )
    )
    op.alter_column(
        "promo_codes",
        "current_activations",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )


def downgrade() -> None:
    if context.is_offline_mode():
        op.alter_column(
            "promo_codes",
            "current_activations",
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("promo_codes"):
        return

    promo_columns = {column["name"]
                     for column in inspector.get_columns("promo_codes")}
    if "current_activations" in promo_columns:
        op.alter_column(
            "promo_codes",
            "current_activations",
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )
