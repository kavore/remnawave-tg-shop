"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column("language_code", sa.String(), nullable=True),
        sa.Column("registration_date", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("is_banned", sa.Boolean(), nullable=True),
        sa.Column("panel_user_uuid", sa.String(), nullable=True),
        sa.Column("referral_code", sa.String(length=16), nullable=True),
        sa.Column("referred_by_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_subscription_verified", sa.Boolean(), nullable=True),
        sa.Column("channel_subscription_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel_subscription_verified_for", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["referred_by_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("panel_user_uuid"),
        sa.UniqueConstraint("referral_code"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)

    op.create_table(
        "promo_codes",
        sa.Column("promo_code_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("promo_type", sa.String(), nullable=False),
        sa.Column("bonus_days", sa.Integer(), nullable=True),
        sa.Column("discount_percentage", sa.Integer(), nullable=True),
        sa.Column("max_activations", sa.Integer(), nullable=False),
        sa.Column("current_activations", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("promo_code_id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("idx_promo_codes_promo_type", "promo_codes", ["promo_type"], unique=False)

    op.create_table(
        "ad_campaigns",
        sa.Column("ad_campaign_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("start_param", sa.String(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("ad_campaign_id"),
        sa.UniqueConstraint("start_param"),
    )
    op.create_index("ix_ad_campaigns_source", "ad_campaigns", ["source"], unique=False)
    op.create_index("ix_ad_campaigns_is_active", "ad_campaigns", ["is_active"], unique=False)

    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("panel_user_uuid", sa.String(), nullable=False),
        sa.Column("panel_subscription_uuid", sa.String(), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("status_from_panel", sa.String(), nullable=True),
        sa.Column("traffic_limit_bytes", sa.BigInteger(), nullable=True),
        sa.Column("traffic_used_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_notification_sent", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("skip_notifications", sa.Boolean(), nullable=True),
        sa.Column("auto_renew_enabled", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("subscription_id"),
        sa.UniqueConstraint("panel_subscription_uuid"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=False)
    op.create_index("ix_subscriptions_panel_user_uuid", "subscriptions", ["panel_user_uuid"], unique=False)
    op.create_index("ix_subscriptions_end_date", "subscriptions", ["end_date"], unique=False)
    op.create_index("ix_subscriptions_is_active", "subscriptions", ["is_active"], unique=False)
    op.create_index("ix_subscriptions_auto_renew_enabled", "subscriptions", ["auto_renew_enabled"], unique=False)

    op.create_table(
        "payments",
        sa.Column("payment_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("yookassa_payment_id", sa.String(), nullable=True),
        sa.Column("provider_payment_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("idempotence_key", sa.String(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("original_amount", sa.Float(), nullable=True),
        sa.Column("discount_applied", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("subscription_duration_months", sa.Integer(), nullable=True),
        sa.Column("promo_code_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.promo_code_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("payment_id"),
        sa.UniqueConstraint("idempotence_key"),
        sa.UniqueConstraint("provider_payment_id"),
        sa.UniqueConstraint("yookassa_payment_id"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"], unique=False)
    op.create_index("ix_payments_provider", "payments", ["provider"], unique=False)
    op.create_index("ix_payments_status", "payments", ["status"], unique=False)

    op.create_table(
        "user_billing",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("yookassa_payment_method_id", sa.String(), nullable=True),
        sa.Column("card_last4", sa.String(), nullable=True),
        sa.Column("card_network", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("yookassa_payment_method_id"),
    )

    op.create_table(
        "user_payment_methods",
        sa.Column("method_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_payment_method_id", sa.String(), nullable=False),
        sa.Column("card_last4", sa.String(), nullable=True),
        sa.Column("card_network", sa.String(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("method_id"),
        sa.UniqueConstraint("provider_payment_method_id"),
        sa.UniqueConstraint("user_id", "provider_payment_method_id", name="uq_user_provider_method"),
    )
    op.create_index("ix_user_payment_methods_user_id", "user_payment_methods", ["user_id"], unique=False)
    op.create_index("ix_user_payment_methods_provider", "user_payment_methods", ["provider"], unique=False)
    op.create_index("ix_user_payment_methods_is_default", "user_payment_methods", ["is_default"], unique=False)

    op.create_table(
        "promo_code_activations",
        sa.Column("activation_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("promo_code_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.payment_id"]),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.promo_code_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("activation_id"),
        sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_user_activation"),
    )

    op.create_table(
        "active_discounts",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("promo_code_id", sa.Integer(), nullable=False),
        sa.Column("discount_percentage", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.promo_code_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "message_logs",
        sa.Column("log_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_username", sa.String(), nullable=True),
        sa.Column("telegram_first_name", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("raw_update_preview", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("is_admin_event", sa.Boolean(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("log_id"),
    )
    op.create_index("ix_message_logs_user_id", "message_logs", ["user_id"], unique=False)
    op.create_index("ix_message_logs_event_type", "message_logs", ["event_type"], unique=False)
    op.create_index("ix_message_logs_timestamp", "message_logs", ["timestamp"], unique=False)
    op.create_index("ix_message_logs_target_user_id", "message_logs", ["target_user_id"], unique=False)

    op.create_table(
        "panel_sync_status",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_sync_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("users_processed_from_panel", sa.Integer(), nullable=True),
        sa.Column("subscriptions_synced", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id"),
    )

    op.create_table(
        "ad_attributions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("ad_campaign_id", sa.Integer(), nullable=False),
        sa.Column("first_start_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("trial_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ad_campaign_id"], ["ad_campaigns.ad_campaign_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_ad_attributions_ad_campaign_id", "ad_attributions", ["ad_campaign_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ad_attributions_ad_campaign_id", table_name="ad_attributions")
    op.drop_table("ad_attributions")

    op.drop_table("panel_sync_status")

    op.drop_index("ix_message_logs_target_user_id", table_name="message_logs")
    op.drop_index("ix_message_logs_timestamp", table_name="message_logs")
    op.drop_index("ix_message_logs_event_type", table_name="message_logs")
    op.drop_index("ix_message_logs_user_id", table_name="message_logs")
    op.drop_table("message_logs")

    op.drop_table("active_discounts")

    op.drop_table("promo_code_activations")

    op.drop_index("ix_user_payment_methods_is_default", table_name="user_payment_methods")
    op.drop_index("ix_user_payment_methods_provider", table_name="user_payment_methods")
    op.drop_index("ix_user_payment_methods_user_id", table_name="user_payment_methods")
    op.drop_table("user_payment_methods")

    op.drop_table("user_billing")

    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_provider", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")

    op.drop_index("ix_subscriptions_auto_renew_enabled", table_name="subscriptions")
    op.drop_index("ix_subscriptions_is_active", table_name="subscriptions")
    op.drop_index("ix_subscriptions_end_date", table_name="subscriptions")
    op.drop_index("ix_subscriptions_panel_user_uuid", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_ad_campaigns_is_active", table_name="ad_campaigns")
    op.drop_index("ix_ad_campaigns_source", table_name="ad_campaigns")
    op.drop_table("ad_campaigns")

    op.drop_index("idx_promo_codes_promo_type", table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
