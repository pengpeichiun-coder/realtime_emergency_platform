"""One asyncio loop/engine per invocation; PostgreSQL locks fence duplicate delivery."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from app.observability import request_id_var, identity_var
from app.db.session import Database
from uuid import uuid4
from app.db.models import JobRecord, AIArtifact
from app.ai.workflows import generate_artifact
from app.ai.prompts import PROMPTS
from app.workers.celery_app import celery_app
from app.workers.config import MAX_ATTEMPTS, backoff
from app.workers.reports import incident_context, PermanentJobError

logger = logging.getLogger(__name__)


async def execute_job(identifier, url=None, handler=None):
    database = Database(url)
    try:
        async with database.engine.connect() as lock:
            acquired = await lock.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:id, 801))"),
                {"id": identifier},
            )
            await lock.commit()
            if not acquired:
                return None
            try:
                async with database.sessions.begin() as session:
                    job = await session.get(JobRecord, identifier, with_for_update=True)
                    if (
                        not job
                        or job.status in {"SUCCEEDED", "FAILED"}
                        or job.next_attempt_at > datetime.now(UTC)
                    ):
                        return None
                    if job.attempt_count >= MAX_ATTEMPTS:
                        job.status = "FAILED"
                        job.error_code = "WORKER_INTERRUPTED"
                        job.error_message = (
                            "The worker could not complete within the attempt limit."
                        )
                        job.completed_at = datetime.now(UTC)
                        return None
                    job.status = "RUNNING"
                    job.attempt_count += 1
                    job.started_at = datetime.now(UTC)
                    job.error_code = job.error_message = None
                request_id_var.set(job.request_id)
                identity_var.set({"organization_id":job.organization_id,"user_id":job.created_by_user_id})
                logger.info("Job running", extra={"job_id":identifier,"job_type":job.job_type,"attempt":job.attempt_count,"status":"RUNNING"})
                try:
                    if handler:
                        result = await handler(database, job)
                    elif job.job_type == "INCIDENT_REPORT":
                        result = await incident_context(
                            database,
                            job.organization_id,
                            job.input_payload["incident_id"],
                        )
                    elif job.job_type in PROMPTS:
                        result = await generate_artifact(database, job)
                    else:
                        raise PermanentJobError(
                            "UNSUPPORTED_JOB", "This job type is unsupported."
                        )
                    async with database.sessions.begin() as session:
                        saved = await session.get(
                            JobRecord, identifier, with_for_update=True
                        )
                        if job.job_type in PROMPTS:
                            artifact_id = "AI-" + uuid4().hex
                            session.add(
                                AIArtifact(
                                    id=artifact_id,
                                    organization_id=job.organization_id,
                                    created_by_user_id=job.created_by_user_id,
                                    job_id=identifier,
                                    incident_id=job.input_payload.get("incident_id"),
                                    **result,
                                )
                            )
                            result = {**result, "artifact_id": artifact_id}
                        saved.result_payload = result
                        saved.status = "SUCCEEDED"
                        saved.completed_at = datetime.now(UTC)
                    logger.info(
                        "Job succeeded id=%s attempt=%s", identifier, job.attempt_count, extra={"job_id":identifier,"job_type":job.job_type,"attempt":job.attempt_count,"status":"SUCCEEDED"}
                    )
                    return None
                except Exception as exc:
                    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", "") or ""
                    transient = isinstance(exc, (TimeoutError, ConnectionError)) or (
                        isinstance(exc, DBAPIError)
                        and (
                            exc.connection_invalidated
                            or sqlstate.startswith("08")
                            or sqlstate in {"40001", "40P01", "57014", "55P03"}
                        )
                    )
                    retry = transient and job.attempt_count < MAX_ATTEMPTS
                    delay = backoff(job.attempt_count)
                    async with database.sessions.begin() as session:
                        saved = await session.get(
                            JobRecord, identifier, with_for_update=True
                        )
                        saved.status = "QUEUED" if retry else "FAILED"
                        saved.error_code = (
                            exc.code
                            if isinstance(exc, PermanentJobError)
                            else ("TRANSIENT_FAILURE" if transient else "JOB_FAILED")
                        )
                        saved.error_message = (
                            exc.message
                            if isinstance(exc, PermanentJobError)
                            else "The background operation could not complete."
                        )
                        saved.next_attempt_at = datetime.now(UTC) + timedelta(
                            seconds=delay
                        )
                        saved.completed_at = None if retry else datetime.now(UTC)
                    logger.warning(
                        "Job attempt failed id=%s attempt=%s retry=%s",
                        identifier,
                        job.attempt_count,
                        retry, extra={"job_id":identifier,"job_type":job.job_type,"attempt":job.attempt_count,"status":saved.status}
                    )
                    return delay if retry else None
            finally:
                await lock.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:id, 801))"),
                    {"id": identifier},
                )
                await lock.commit()
    finally:
        await database.close()


@celery_app.task(bind=True, name="emergency.execute_job", max_retries=2)
def execute(self, identifier):
    try:
        delay = asyncio.run(execute_job(identifier))
    except Exception:
        # PostgreSQL unavailable before state can be persisted: publisher will recover.
        logger.warning(
            "Job database unavailable id=%s; durable publisher will recover", identifier
        )
        return
    if delay is not None:
        raise self.retry(countdown=delay)
