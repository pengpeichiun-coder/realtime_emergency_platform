"""Durable business jobs and recoverable publication scheduling."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_background_jobs"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(80),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(80),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_payload", JSONB(), nullable=False),
        sa.Column("result_payload", JSONB()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("celery_task_id", sa.String(80), nullable=False),
        *[
            sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)
            for name, nullable in [
                ("created_at", False),
                ("updated_at", False),
                ("started_at", True),
                ("completed_at", True),
                ("next_attempt_at", False),
                ("last_enqueued_at", True),
            ]
        ],
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_jobs_status"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_jobs_attempts"),
    )
    op.create_index("ix_jobs_organization_id", "jobs", ["organization_id"])
    op.create_index("ix_jobs_pending", "jobs", ["status", "next_attempt_at"])


def downgrade():
    op.drop_table("jobs")
