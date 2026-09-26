"""Organization-scoped PostgreSQL repository; never caches operational state."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.geo import coordinates, point
from app.db.models import ActivityRecord, DispatchRecord, IncidentRecord, Organization, UnitRecord
from app.db.session import Database
from app.domain.models import ActivityEvent, Dispatch, EmergencyUnit, Incident
from app.domain.transitions import DomainError
from app.repositories.seed import seed_activity, seed_dispatches, seed_incidents, seed_units

DEMO_ORGANIZATION = 'ORG-DEMO'


class PostgresRepository:
    def __init__(self, url: str | None = None, *, organization_id: str = DEMO_ORGANIZATION, database: Database | None = None):
        self.url = url
        self.organization_id = organization_id
        self.database = database
        self._owns_database = database is None
        self._context: ContextVar[tuple[AsyncSession, bool]] = ContextVar(f'repository_session_{id(self)}')

    async def event_cursor(self):
        row = (await self.session.execute(select(Organization.realtime_stream_id, Organization.realtime_sequence).where(Organization.id == self.organization_id))).one()
        return row.realtime_stream_id, row.realtime_sequence

    async def save_cursor(self, stream_id, sequence):
        await self.session.execute(update(Organization).where(Organization.id == self.organization_id).values(realtime_sequence=sequence))

    async def persist_events(self, events):
        from app.services.webhooks import persist_events
        await persist_events(self.session, self.organization_id, events)

    async def open(self):
        if self.database is None:
            self.database = Database(self.url)

    async def close(self):
        if self.database and self._owns_database:
            await self.database.close()
            self.database = None

    @property
    def session(self):
        return self._context.get()[0]

    @asynccontextmanager
    async def transaction(self, *, write=False):
        if self.database is None:
            raise RuntimeError('Repository must be opened before use.')
        async with self.database.sessions() as session:
            token = self._context.set((session, write))
            try:
                async with session.begin():
                    if not write:
                        await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                    organization = select(Organization.id).where(Organization.id == self.organization_id)
                    # A common lock order makes reset safe and avoids cross-entity deadlocks.
                    # Unit/incident reads additionally lock their own rows during commands.
                    if write:
                        organization = organization.with_for_update()
                    if await session.scalar(organization) is None:
                        raise DomainError('ORGANIZATION_NOT_FOUND', 'Organization is missing.', 503)
                    yield
            except IntegrityError as exc:
                raise DomainError('PERSISTENCE_CONFLICT', 'The operation conflicts with persisted assignments.', 409) from exc
            finally:
                self._context.reset(token)

    def _query(self, model):
        return select(model).where(model.organization_id == self.organization_id)

    async def _get(self, model, identifier, entity):
        query = self._query(model).where(model.id == identifier)
        if self._context.get()[1]:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise DomainError(f'{entity.upper()}_NOT_FOUND', f'{entity.title()} {identifier} was not found.', 404)
        return value

    @staticmethod
    def _incident(row):
        lat, lon = coordinates(row.location)
        return Incident(id=row.id, type=row.type, title=row.title, area=row.area,
                        severity=row.severity, status=row.status, description=row.description,
                        latitude=lat, longitude=lon, created_at=row.created_at,
                        assigned_unit_id=row.assigned_unit_id, response_time_seconds=row.response_time_seconds)

    @staticmethod
    def _unit(row):
        lat, lon = coordinates(row.location)
        return EmergencyUnit(id=row.id, name=row.name, type=row.type, status=row.status,
                             area=row.area, latitude=lat, longitude=lon, last_updated_at=row.last_updated_at,
                             assigned_incident_id=row.assigned_incident_id)

    @staticmethod
    def _dispatch(row):
        return Dispatch(**{name: getattr(row, name) for name in Dispatch.__dataclass_fields__})

    @staticmethod
    def _activity(row):
        return ActivityEvent(id=row.id, event_type=row.event_type, entity_type=row.entity_type,
                             entity_id=row.entity_id, message=row.message, timestamp=row.created_at,
                             metadata=row.details)

    async def incident(self, identifier):
        return self._incident(await self._get(IncidentRecord, identifier, 'incident'))

    async def unit(self, identifier):
        return self._unit(await self._get(UnitRecord, identifier, 'unit'))

    async def dispatch(self, identifier):
        return self._dispatch(await self._get(DispatchRecord, identifier, 'dispatch'))

    async def _save(self, model, value):
        data = asdict(value)
        if 'latitude' in data:
            data['location'] = point(data.pop('latitude'), data.pop('longitude'))
        row = await self.session.get(model, {'organization_id': self.organization_id, 'id': value.id})
        if row is None:
            row = model(organization_id=self.organization_id, **data)
            self.session.add(row)
        else:
            for key, field in data.items():
                setattr(row, key, field)
        if model is IncidentRecord and value.status == 'resolved' and row.resolved_at is None:
            row.resolved_at = datetime.now(UTC)
        await self.session.flush()

    async def save_incident(self, value):
        await self._save(IncidentRecord, value)

    async def save_unit(self, value):
        await self._save(UnitRecord, value)

    async def save_dispatch(self, value):
        await self._save(DispatchRecord, value)

    async def arrived_dispatch(self, incident_id):
        query = self._query(DispatchRecord).where(DispatchRecord.incident_id == incident_id,
                                                DispatchRecord.status == 'arrived').with_for_update()
        row = await self.session.scalar(query)
        return self._dispatch(row) if row else None

    async def add_activity(self, value):
        self.session.add(ActivityRecord(organization_id=self.organization_id, id=value.id,
            event_type=value.event_type, entity_type=value.entity_type, entity_id=value.entity_id,
            message=value.message, details=value.metadata, created_at=value.timestamp))
        await self.session.flush()

    async def snapshot(self):
        result = {}
        for name, model, convert in [('incidents', IncidentRecord, self._incident),
                                    ('units', UnitRecord, self._unit),
                                    ('dispatches', DispatchRecord, self._dispatch)]:
            rows = await self.session.scalars(self._query(model).order_by(model.id))
            result[name] = [convert(row) for row in rows]
        rows = await self.session.scalars(self._query(ActivityRecord)
            .order_by(ActivityRecord.created_at.desc(), ActivityRecord.id.desc()).limit(100))
        result['activity'] = [self._activity(row) for row in rows]
        return result

    async def reset(self):
        # Deferred assignment foreign keys permit clearing the two linked tables.
        for model in [ActivityRecord, DispatchRecord, UnitRecord, IncidentRecord]:
            await self.session.execute(delete(model).where(model.organization_id == self.organization_id))
        for incident in seed_incidents().values():
            await self.save_incident(incident)
        for unit in seed_units().values():
            await self.save_unit(unit)
        for dispatch in seed_dispatches().values():
            await self.save_dispatch(dispatch)
        for event in seed_activity():
            await self.add_activity(event)

    async def nearby_units(self, incident_id, radius, limit):
        await self.incident(incident_id)  # Preserve the ordinary scoped 404 contract.
        distance = func.ST_Distance(UnitRecord.location, IncidentRecord.location).label('distance_meters')
        active = select(DispatchRecord.id).where(
            DispatchRecord.organization_id == self.organization_id,
            DispatchRecord.unit_id == UnitRecord.id, DispatchRecord.status != 'completed').exists()
        query = (select(UnitRecord.id.label('unit_id'), distance)
            .select_from(UnitRecord).join(IncidentRecord, IncidentRecord.organization_id == UnitRecord.organization_id)
            .where(UnitRecord.organization_id == self.organization_id, IncidentRecord.id == incident_id,
                   UnitRecord.status == 'available', UnitRecord.assigned_incident_id.is_(None), ~active,
                   func.ST_DWithin(UnitRecord.location, IncidentRecord.location, radius))
            .order_by(distance, UnitRecord.id).limit(limit))
        return [dict(row) for row in (await self.session.execute(query)).mappings()]
