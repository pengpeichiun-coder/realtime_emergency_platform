"""durable webhook subscriptions outbox and delivery

Revision ID: 0004_webhooks
Revises: 0003_background_jobs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_webhooks"
down_revision = "0003_background_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("organization_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "subscription_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_outbox_events_organization_id"),
        "outbox_events",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_processed_at"),
        "outbox_events",
        ["processed_at"],
        unique=False,
    )
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("organization_id", sa.String(length=80), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("encrypted_signing_secret", sa.Text(), nullable=False),
        sa.Column(
            "event_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_webhook_subscriptions_organization_id"),
        "webhook_subscriptions",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("organization_id", sa.String(length=80), nullable=False),
        sa.Column("subscription_id", sa.String(length=80), nullable=False),
        sa.Column("event_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','DELIVERED','FAILED')",
            name="ck_delivery_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_delivery_attempts"),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["webhook_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deliveries_pending",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_deliveries_organization_id"),
        "webhook_deliveries",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "uq_delivery_event_subscription",
        "webhook_deliveries",
        ["event_id", "subscription_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_delivery_event_subscription", table_name="webhook_deliveries")
    op.drop_index(
        op.f("ix_webhook_deliveries_organization_id"), table_name="webhook_deliveries"
    )
    op.drop_index("ix_deliveries_pending", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index(
        op.f("ix_webhook_subscriptions_organization_id"),
        table_name="webhook_subscriptions",
    )
    op.drop_table("webhook_subscriptions")
    op.drop_index(op.f("ix_outbox_events_processed_at"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_organization_id"), table_name="outbox_events")
    op.drop_table("outbox_events")
