"""shared tenant realtime cursors and correlation

Revision ID: 0006_realtime
Revises: 0005_ai_artifacts
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_realtime"
down_revision = "0005_ai_artifacts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column(
        "organizations",
        sa.Column(
            "realtime_stream_id",
            sa.String(length=80),
            server_default=sa.text("gen_random_uuid()::text"),
            nullable=False,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "realtime_sequence", sa.BigInteger(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "outbox_events", sa.Column("request_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_column("webhook_deliveries", "request_id")
    op.drop_column("outbox_events", "request_id")
    op.drop_column("organizations", "realtime_sequence")
    op.drop_column("organizations", "realtime_stream_id")
    op.drop_column("jobs", "request_id")
