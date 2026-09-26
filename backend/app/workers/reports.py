"""Deterministic operational context, always filtered by the persisted job tenant."""

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text, or_
from app.db.models import IncidentRecord, DispatchRecord, UnitRecord, ActivityRecord
from app.repositories.postgres import PostgresRepository


class PermanentJobError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


async def incident_context(database, organization_id, incident_id):
    async with database.sessions.begin() as session:
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        await session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        incident = await session.get(IncidentRecord, (organization_id, incident_id))
        if incident is None:
            raise PermanentJobError(
                "INCIDENT_NOT_FOUND", "The incident is no longer available."
            )
        dispatches = list(
            await session.scalars(
                select(DispatchRecord)
                .where(
                    DispatchRecord.organization_id == organization_id,
                    DispatchRecord.incident_id == incident_id,
                )
                .order_by(DispatchRecord.created_at, DispatchRecord.id)
            )
        )
        unit_ids = {d.unit_id for d in dispatches}
        units = list(
            await session.scalars(
                select(UnitRecord)
                .where(
                    UnitRecord.organization_id == organization_id,
                    UnitRecord.id.in_(unit_ids),
                )
                .order_by(UnitRecord.id)
            )
        )
        activity = list(
            await session.scalars(
                select(ActivityRecord)
                .where(
                    ActivityRecord.organization_id == organization_id,
                    or_(
                        ActivityRecord.entity_id == incident_id,
                        ActivityRecord.details["incident_id"].astext == incident_id,
                    ),
                )
                .order_by(ActivityRecord.created_at, ActivityRecord.id)
            )
        )
        return jsonable_encoder(
            {
                "schema_version": 1,
                "incident": PostgresRepository._incident(incident),
                "dispatches": [PostgresRepository._dispatch(d) for d in dispatches],
                "units": [PostgresRepository._unit(u) for u in units],
                "activity": [PostgresRepository._activity(a) for a in activity],
            }
        )
