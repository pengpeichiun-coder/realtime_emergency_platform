"""Index the unprocessed outbox for publisher polling and aggregate telemetry.

Revision ID: 0008_queue_observability
Revises: 0007_operation_retry_audits
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_queue_observability"
down_revision = "0007_operation_retry_audits"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_outbox_pending_created", "outbox_events", ["created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade():
    op.drop_index("ix_outbox_pending_created", table_name="outbox_events")
