"""Tenant-scoped read-only context; validated output is committed by the job worker."""

import json
from datetime import UTC, datetime, timedelta
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select, text
from app.ai.prompts import PROMPTS
from app.ai.providers import provider_from_config
from app.db.models import IncidentRecord, DispatchRecord, UnitRecord, ActivityRecord
from app.repositories.postgres import PostgresRepository
from app.workers.reports import incident_context, PermanentJobError


async def shift_context(database, organization_id):
    async with database.sessions.begin() as session:
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        await session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        incidents = list(
            await session.scalars(
                select(IncidentRecord)
                .where(
                    IncidentRecord.organization_id == organization_id,
                    IncidentRecord.status != "resolved",
                )
                .order_by(IncidentRecord.created_at, IncidentRecord.id)
            )
        )
        dispatches = list(
            await session.scalars(
                select(DispatchRecord)
                .where(
                    DispatchRecord.organization_id == organization_id,
                    DispatchRecord.status != "completed",
                )
                .order_by(DispatchRecord.created_at, DispatchRecord.id)
            )
        )
        units = list(
            await session.scalars(
                select(UnitRecord)
                .where(UnitRecord.organization_id == organization_id)
                .order_by(UnitRecord.id)
            )
        )
        since = datetime.now(UTC) - timedelta(hours=24)
        activity = list(
            await session.scalars(
                select(ActivityRecord)
                .where(
                    ActivityRecord.organization_id == organization_id,
                    ActivityRecord.created_at >= since,
                )
                .order_by(ActivityRecord.created_at.desc(), ActivityRecord.id)
                .limit(200)
            )
        )
        return jsonable_encoder(
            {
                "active_incidents": [
                    PostgresRepository._incident(i) for i in incidents
                ],
                "dispatches": [PostgresRepository._dispatch(d) for d in dispatches],
                "units": [PostgresRepository._unit(u) for u in units],
                "activity": [PostgresRepository._activity(a) for a in activity],
                "activity_window_hours": 24,
                "activity_limit": 200,
            }
        )


async def generate_artifact(database, job):
    prompt = PROMPTS[job.job_type]
    provider = provider_from_config()
    context = (
        await incident_context(
            database, job.organization_id, job.input_payload["incident_id"]
        )
        if job.job_type == "INCIDENT_SUMMARY"
        else await shift_context(database, job.organization_id)
    )
    if len(json.dumps(context).encode()) > 100_000:
        raise PermanentJobError(
            "LLM_INPUT_TOO_LARGE",
            "The operational context exceeds the summary size limit.",
        )
    raw = await provider.generate(prompt, context)
    try:
        content = prompt.schema.model_validate(raw).model_dump(mode="json")
    except ValidationError:
        raise PermanentJobError(
            "LLM_INVALID_OUTPUT", "The generated summary failed structured validation."
        ) from None
    return {
        "artifact_type": job.job_type,
        "provider": provider.name,
        "model": provider.model,
        "prompt_version": prompt.version,
        "content": content,
    }
