"""PostgreSQL is the job authority; the publisher recovers enqueue gaps."""

from datetime import UTC, datetime
from uuid import uuid4
from app.observability import request_id_var
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import JobRecord, IncidentRecord, OperationRetryAudit
from app.services.operation_retries import record_manual_retry


class JobService:
    def __init__(self, database):
        self.database = database

    async def create(self, context, job_type, incident_id=None):
        async with self.database.sessions.begin() as session:
            if (
                incident_id
                and await session.get(
                    IncidentRecord, (context.organization_id, incident_id)
                )
                is None
            ):
                raise HTTPException(404, "Incident was not found.")
            identifier = "JOB-" + uuid4().hex
            job = JobRecord(
                id=identifier,
                request_id=request_id_var.get(),
                organization_id=context.organization_id,
                created_by_user_id=context.user_id,
                job_type=job_type,
                input_payload={"incident_id": incident_id} if incident_id else {},
                celery_task_id=identifier,
            )
            session.add(job)
        # The durable row itself is publication intent. A separate publisher sends
        # only committed jobs and periodically recovers lost broker messages.
        return job

    async def list(self, context):
        async with self.database.sessions() as session:
            return list(
                await session.scalars(
                    select(JobRecord)
                    .where(JobRecord.organization_id == context.organization_id)
                    .order_by(JobRecord.created_at.desc(), JobRecord.id)
                    .limit(100)
                )
            )

    async def get(self, context, identifier):
        async with self.database.sessions() as session:
            row = await session.scalar(
                select(JobRecord).where(
                    JobRecord.id == identifier,
                    JobRecord.organization_id == context.organization_id,
                )
            )
            if row is None:
                raise HTTPException(404, "Job was not found.")
            return row

    async def retry_failed(self, context, identifier):
        async with self.database.sessions.begin() as session:
            row = await session.scalar(
                select(JobRecord)
                .where(
                    JobRecord.id == identifier,
                    JobRecord.organization_id == context.organization_id,
                )
                .with_for_update()
            )
            if row is None:
                raise HTTPException(404, "Job was not found.")
            if row.status in {"QUEUED", "RUNNING"}:
                return row
            if row.status != "FAILED":
                raise HTTPException(409, "Only failed jobs can be retried.")

            await record_manual_retry(session, context, "JOB", row.id)
            now = datetime.now(UTC)
            row.status = "QUEUED"
            row.attempt_count = 0
            row.result_payload = None
            row.error_code = None
            row.error_message = None
            row.completed_at = None
            row.next_attempt_at = now
            row.last_enqueued_at = None
            row.updated_at = now
            row.request_id = request_id_var.get()
            await session.flush()
            return row

    async def retry_history(self, context):
        async with self.database.sessions() as session:
            return list(
                await session.scalars(
                    select(OperationRetryAudit)
                    .where(
                        OperationRetryAudit.organization_id == context.organization_id
                    )
                    .order_by(
                        OperationRetryAudit.created_at.desc(),
                        OperationRetryAudit.id,
                    )
                    .limit(100)
                )
            )
