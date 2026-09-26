"""audit administrator-requested retries for durable operations

Revision ID: 0007_operation_retry_audits
Revises: 0006_realtime
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_operation_retry_audits"
down_revision = "0006_realtime"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_retry_audits",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("organization_id", sa.String(length=80), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=80), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.String(length=80), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_type IN ('JOB','WEBHOOK_DELIVERY')",
            name="ck_operation_retry_type",
        ),
        sa.CheckConstraint(
            "retry_count BETWEEN 1 AND 3", name="ck_operation_retry_count"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_operation_retries_scope",
        "operation_retry_audits",
        ["organization_id", "operation_type", "operation_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_operation_retries_scope", table_name="operation_retry_audits")
    op.drop_table("operation_retry_audits")
