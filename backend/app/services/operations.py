"""Atomic domain operations, shared by HTTP commands and realtime tasks."""

import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import logging
import math
from uuid import uuid4

from app.domain.models import ActivityEvent, Dispatch, EmergencyUnit, Incident
from app.domain.transitions import DomainError, require_transition
from app.repositories.base import Repository, new_id
from app.schemas.events import (
    ActivityCreatedEvent, ConnectionReadyEvent, DispatchEvent, DispatchEventType,
    IncidentEvent, IncidentEventType, StateResetEvent, UnitEvent, UnitEventType,
)
from app.schemas.operations import (
    ActivityResponse, DispatchCreate, DispatchResponse, IncidentCreate, IncidentPatch,
    IncidentResponse, StateResponse, UnitResponse,
)
from app.services.connections import ConnectionManager
from app.services.simulator import MovementSimulator

logger = logging.getLogger(__name__)


class OperationsService:
    def __init__(self, repository: Repository, connections: ConnectionManager):
        self.event_bus = None
        self.repository = repository
        self.lock = asyncio.Lock()
        self.connections = connections
        self.simulator: MovementSimulator | None = None
        self.stream_id = uuid4().hex
        self.sequence = 0
        self._pending_events = []
        self._pending_tasks = []

    @asynccontextmanager
    async def _transaction(self, *, write=True):
        """Caller holds service.lock; publish only after repository commit succeeds."""
        before = self.sequence
        self._pending_events, self._pending_tasks = [], []
        try:
            async with self.repository.transaction(write=write):
                cursor = await self.repository.event_cursor()
                if cursor:
                    self.stream_id, self.sequence = cursor
                yield
                if write:
                    await self.repository.save_cursor(self.stream_id, self.sequence)
                    await self.repository.persist_events(self._pending_events)
        except BaseException:
            self.sequence = before
            self._pending_events, self._pending_tasks = [], []
            raise
        else:
            if self.event_bus:
                await self.event_bus.publish(self.repository.organization_id, self._pending_events)
            else:
                for event in self._pending_events:
                    self.connections.broadcast(event)
            if self.simulator:
                for identifier in self._pending_tasks:
                    self.simulator.start(identifier)
        finally:
            self._pending_events, self._pending_tasks = [], []

    async def _persist(self, *entities):
        for entity in entities:
            if isinstance(entity, Incident):
                await self.repository.save_incident(entity)
            elif isinstance(entity, EmergencyUnit):
                await self.repository.save_unit(entity)
            elif isinstance(entity, Dispatch):
                await self.repository.save_dispatch(entity)

    async def resume(self):
        """Restore tasks from committed state; never reseed on application startup."""
        async with self.lock, self._transaction(write=False):
            if self.simulator:
                snapshot = await self.repository.snapshot()
                self._pending_tasks.extend(item.id for item in snapshot['dispatches']
                                           if item.status in ('created', 'en_route'))

    async def nearby_units(self, identifier: str, radius: float, limit: int):
        async with self.lock, self._transaction(write=False):
            return await self.repository.nearby_units(identifier, radius, limit)

    def _next_event(self) -> dict:
        # Called only inside the mutation lock. A REST snapshot sees this exact cursor.
        self.sequence += 1
        return {"timestamp": datetime.now(UTC), "stream_id": self.stream_id, "sequence": self.sequence}

    def _emit_incident(self, event_type: IncidentEventType, incident: Incident) -> None:
        self._pending_events.append(IncidentEvent(
            **self._next_event(), type=event_type, data=IncidentResponse.model_validate(incident),
        ))

    def _emit_unit(self, event_type: UnitEventType, unit) -> None:
        self._pending_events.append(UnitEvent(
            **self._next_event(), type=event_type, data=UnitResponse.model_validate(unit),
        ))

    def _emit_dispatch(self, event_type: DispatchEventType, dispatch: Dispatch) -> None:
        self._pending_events.append(DispatchEvent(
            **self._next_event(), type=event_type, data=DispatchResponse.model_validate(dispatch),
        ))

    def connection_ready(self) -> ConnectionReadyEvent:
        """Handshake shares the current cursor without consuming an event sequence."""
        return ConnectionReadyEvent(timestamp=datetime.now(UTC), stream_id=self.stream_id, sequence=self.sequence)

    async def _snapshot(self) -> dict:
        return {**await self.repository.snapshot(), "stream_id": self.stream_id, "sequence": self.sequence}

    async def _activity(self, event_type: str, entity_type: str, entity_id: str,
                  message: str, now: datetime, **metadata: str) -> None:
        activity = ActivityEvent(
            id=new_id("ACT"), event_type=event_type,
            entity_type=entity_type, entity_id=entity_id,
            message=message, timestamp=now, metadata=metadata,
        )
        await self.repository.add_activity(activity)
        self._pending_events.append(ActivityCreatedEvent(
            **self._next_event(), data=ActivityResponse.model_validate(activity),
        ))

    async def snapshot(self) -> dict:
        async with self.lock, self._transaction(write=False):
            return await self._snapshot()

    async def get_incident(self, identifier: str):
        async with self.lock, self._transaction(write=False):
            return deepcopy(await self.repository.incident(identifier))

    async def get_unit(self, identifier: str):
        async with self.lock, self._transaction(write=False):
            return deepcopy(await self.repository.unit(identifier))

    async def get_dispatch(self, identifier: str):
        async with self.lock, self._transaction(write=False):
            return deepcopy(await self.repository.dispatch(identifier))

    async def create_incident(self, body: IncidentCreate) -> Incident:
        async with self.lock, self._transaction():
            now = datetime.now(UTC)
            incident = Incident(id=new_id("INC"), status="active",
                                created_at=now, **body.model_dump())
            await self._persist(incident)
            self._emit_incident("incident.created", incident)
            await self._activity("incident.created", "incident", incident.id,
                           f"{incident.id} reported · {incident.area}.", now,
                           incident_id=incident.id)
            return deepcopy(incident)

    async def update_incident(self, identifier: str, body: IncidentPatch) -> Incident:
        async with self.lock, self._transaction():
            incident = await self.repository.incident(identifier)
            changes = body.model_dump(exclude_unset=True)
            if incident.status == "resolved":
                raise DomainError("INCIDENT_RESOLVED", "Resolved incidents cannot be edited.")
            if incident.assigned_unit_id and {"latitude", "longitude"} & changes.keys():
                raise DomainError("INCIDENT_ASSIGNED", "Coordinates cannot change after dispatch.")
            for key, value in changes.items():
                setattr(incident, key, value)
            await self._persist(incident)
            self._emit_incident("incident.updated", incident)
            await self._activity("incident.updated", "incident", incident.id,
                           f"{incident.id} details updated.", datetime.now(UTC), incident_id=incident.id)
            return deepcopy(incident)

    async def create_dispatch(self, body: DispatchCreate) -> Dispatch:
        async with self.lock, self._transaction():
            incident = await self.repository.incident(body.incident_id)
            unit = await self.repository.unit(body.unit_id)
            if incident.status != "active" or incident.assigned_unit_id:
                raise DomainError("INCIDENT_NOT_ACTIVE", f"Incident {incident.id} is not awaiting dispatch.")
            if unit.status != "available" or unit.assigned_incident_id:
                raise DomainError("UNIT_NOT_AVAILABLE", f"Unit {unit.id} is not available for dispatch.")
            require_transition("incident", incident.status, "dispatched")
            require_transition("unit", unit.status, "dispatched")
            now = datetime.now(UTC)
            dispatch = Dispatch(
                id=new_id("DSP"), incident_id=incident.id,
                unit_id=unit.id, status="created", created_at=now,
                origin_latitude=unit.latitude, origin_longitude=unit.longitude,
            )
            incident.status, incident.assigned_unit_id = "dispatched", unit.id
            unit.status, unit.assigned_incident_id = "dispatched", incident.id
            unit.last_updated_at = now
            await self._persist(incident, unit, dispatch)
            self._emit_incident("incident.updated", incident)
            self._emit_unit("unit.status.updated", unit)
            self._emit_dispatch("dispatch.created", dispatch)
            await self._activity("dispatch.created", "dispatch", dispatch.id,
                           f"{unit.id} dispatched to {incident.id} · {incident.area}.", now,
                           incident_id=incident.id, unit_id=unit.id, dispatch_id=dispatch.id)
            logger.info("Dispatch prepared for commit: %s unit=%s incident=%s", dispatch.id, unit.id, incident.id)
            if self.simulator:
                self._pending_tasks.append(dispatch.id)
            return deepcopy(dispatch)

    async def _assignment(self, identifier: str):
        dispatch = await self.repository.dispatch(identifier)
        incident = await self.repository.incident(dispatch.incident_id)
        unit = await self.repository.unit(dispatch.unit_id)
        if incident.assigned_unit_id != unit.id or unit.assigned_incident_id != incident.id:
            raise DomainError("ASSIGNMENT_CONFLICT", "Dispatch assignment is inconsistent.")
        return dispatch, incident, unit

    async def start_dispatch(self, identifier: str) -> Dispatch:
        async with self.lock, self._transaction():
            dispatch, incident, unit = await self._assignment(identifier)
            require_transition("dispatch", dispatch.status, "en_route")
            require_transition("unit", unit.status, "en_route")
            if incident.status != "dispatched":
                raise DomainError("INVALID_TRANSITION", "Incident is not dispatched.", 400)
            now = datetime.now(UTC)
            dispatch.status, dispatch.started_at = "en_route", now
            unit.status, unit.last_updated_at = "en_route", now
            unit.area = f"En route to {incident.area}"
            await self._persist(unit, dispatch)
            self._emit_unit("unit.status.updated", unit)
            self._emit_dispatch("dispatch.updated", dispatch)
            await self._activity("unit.status.updated", "unit", unit.id,
                           f"{unit.id} en route to {incident.id} · {incident.area}.", now,
                           incident_id=incident.id, unit_id=unit.id, dispatch_id=dispatch.id)
            return deepcopy(dispatch)

    async def update_location(self, identifier: str, latitude: float, longitude: float) -> None:
        async with self.lock, self._transaction():
            dispatch, _, unit = await self._assignment(identifier)
            if dispatch.status != "en_route" or unit.status != "en_route":
                raise DomainError("INVALID_TRANSITION", "Only en-route units can move.", 400)
            if not (math.isfinite(latitude) and math.isfinite(longitude)
                    and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise DomainError("INVALID_LOCATION", "Coordinates are outside valid ranges.", 400)
            unit.latitude, unit.longitude = latitude, longitude
            unit.last_updated_at = datetime.now(UTC)
            await self._persist(unit)
            self._emit_unit("unit.location.updated", unit)

    async def arrive_dispatch(self, identifier: str) -> Dispatch:
        async with self.lock, self._transaction():
            dispatch, incident, unit = await self._assignment(identifier)
            require_transition("dispatch", dispatch.status, "arrived")
            require_transition("unit", unit.status, "on_scene")
            require_transition("incident", incident.status, "on_scene")
            now = datetime.now(UTC)
            dispatch.status, dispatch.arrived_at = "arrived", now
            unit.status, unit.last_updated_at = "on_scene", now
            unit.latitude, unit.longitude = incident.latitude, incident.longitude
            unit.area = incident.area
            incident.status = "on_scene"
            await self._persist(unit, incident, dispatch)
            self._emit_unit("unit.location.updated", unit)
            self._emit_unit("unit.status.updated", unit)
            self._emit_incident("incident.updated", incident)
            self._emit_dispatch("dispatch.updated", dispatch)
            await self._activity("dispatch.updated", "dispatch", dispatch.id,
                           f"{unit.id} arrived on scene at {incident.id} · {incident.area}.", now,
                           incident_id=incident.id, unit_id=unit.id, dispatch_id=dispatch.id)
            return deepcopy(dispatch)

    async def resolve_incident(self, identifier: str) -> Incident:
        async with self.lock, self._transaction():
            incident = await self.repository.incident(identifier)
            require_transition("incident", incident.status, "resolved")
            assignment = await self.repository.arrived_dispatch(identifier)
            if assignment is None:
                raise DomainError("ASSIGNMENT_CONFLICT", "An arrived dispatch is required to resolve this incident.")
            dispatch, _, unit = await self._assignment(assignment.id)
            require_transition("dispatch", dispatch.status, "completed")
            require_transition("unit", unit.status, "available")
            now = datetime.now(UTC)
            incident.status, incident.assigned_unit_id = "resolved", None
            dispatch.status, dispatch.completed_at = "completed", now
            unit.status, unit.assigned_incident_id = "available", None
            unit.last_updated_at = now
            await self._persist(unit, incident, dispatch)
            self._emit_incident("incident.resolved", incident)
            self._emit_unit("unit.status.updated", unit)
            self._emit_dispatch("dispatch.completed", dispatch)
            await self._activity("incident.resolved", "incident", incident.id,
                           f"{incident.id} resolved; {unit.id} is available.", now,
                           incident_id=incident.id, unit_id=unit.id, dispatch_id=dispatch.id)
            return deepcopy(incident)

    async def reset(self) -> dict:
        """Reset only demo data; failed transactions restore active task ownership."""
        async with self.lock:
            try:
                async with self._transaction():
                    if self.simulator:
                        await self.simulator.cancel_all()
                    await self.repository.reset()
                    metadata = self._next_event()
                    snapshot = await self._snapshot()
                    self._pending_events.append(StateResetEvent(**metadata, data=StateResponse.model_validate(snapshot)))
                logger.info("Demo reset committed; prior simulation tasks cancelled")
                return snapshot
            except Exception:
                # A rollback preserves the trips. Resume them if the DB is reachable;
                # a continuing DB outage is logged and startup recovery can repair it.
                if self.simulator:
                    try:
                        async with self._transaction(write=False):
                            snapshot = await self.repository.snapshot()
                            self._pending_tasks.extend(item.id for item in snapshot['dispatches']
                                                       if item.status in ('created', 'en_route'))
                    except Exception:
                        logger.exception("Could not resume trips after failed reset; restart after database recovery")
                raise

    async def shutdown(self) -> None:
        async with self.lock:
            if self.simulator:
                await self.simulator.cancel_all()
        await self.connections.shutdown()
