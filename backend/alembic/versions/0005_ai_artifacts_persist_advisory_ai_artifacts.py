"""persist advisory AI artifacts

Revision ID: 0005_ai_artifacts
Revises: 0004_webhooks
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_ai_artifacts"
down_revision = "0004_webhooks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_artifacts",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("organization_id", sa.String(length=80), nullable=False),
        sa.Column("incident_id", sa.String(length=80), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=80), nullable=False),
        sa.Column("job_id", sa.String(length=80), nullable=False),
        sa.Column("artifact_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_type IN ('INCIDENT_SUMMARY','SHIFT_HANDOVER')",
            name="ck_ai_artifact_type",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        op.f("ix_ai_artifacts_organization_id"),
        "ai_artifacts",
        ["organization_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_ai_artifacts_organization_id"), table_name="ai_artifacts")
    op.drop_table("ai_artifacts")
