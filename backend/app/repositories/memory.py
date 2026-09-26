"""Process-local repository. The service lock owns access to these collections."""

from copy import deepcopy
from contextlib import asynccontextmanager

from app.domain.models import ActivityEvent, Dispatch, EmergencyUnit, Incident
from app.domain.transitions import DomainError
from app.repositories.seed import seed_activity, seed_dispatches, seed_incidents, seed_units


class MemoryRepository:
    def __init__(self) -> None:
        self._seed()

    def _seed(self) -> None:
        self.incidents = seed_incidents()
        self.units = seed_units()
        self.dispatches = seed_dispatches()
        self.activity = seed_activity()

    async def event_cursor(self):
        return None

    async def save_cursor(self, stream_id, sequence):
        pass

    async def persist_events(self, events):
        """Memory test double has no external integrations."""

    async def incident(self, identifier: str) -> Incident:
        if identifier not in self.incidents:
            raise DomainError("INCIDENT_NOT_FOUND", f"Incident {identifier} was not found.", 404)
        return self.incidents[identifier]

    async def unit(self, identifier: str) -> EmergencyUnit:
        if identifier not in self.units:
            raise DomainError("UNIT_NOT_FOUND", f"Unit {identifier} was not found.", 404)
        return self.units[identifier]

    async def dispatch(self, identifier: str) -> Dispatch:
        if identifier not in self.dispatches:
            raise DomainError("DISPATCH_NOT_FOUND", f"Dispatch {identifier} was not found.", 404)
        return self.dispatches[identifier]

    async def add_activity(self, event: ActivityEvent) -> None:
        self.activity.insert(0, event)
        del self.activity[100:]

    async def snapshot(self) -> dict:
        # Copies prevent callers from modifying repository state outside the lock.
        return deepcopy({
            "incidents": list(self.incidents.values()),
            "units": list(self.units.values()),
            "dispatches": list(self.dispatches.values()),
            "activity": self.activity,
        })

    @asynccontextmanager
    async def transaction(self, *, write=False):
        before = deepcopy((self.incidents, self.units, self.dispatches, self.activity)) if write else None
        try:
            yield
        except BaseException:
            if before is not None:
                self.incidents, self.units, self.dispatches, self.activity = before
            raise

    async def open(self):
        pass

    async def close(self):
        pass

    async def save_incident(self, value):
        self.incidents[value.id] = value

    async def save_unit(self, value):
        self.units[value.id] = value

    async def save_dispatch(self, value):
        self.dispatches[value.id] = value

    async def arrived_dispatch(self, incident_id):
        return next((item for item in self.dispatches.values()
                     if item.incident_id == incident_id and item.status == 'arrived'), None)

    async def reset(self):
        self._seed()

    async def nearby_units(self, incident_id, radius, limit):
        await self.incident(incident_id)
        raise DomainError('POSTGIS_REQUIRED', 'Nearby lookup requires PostgreSQL/PostGIS.', 503)
