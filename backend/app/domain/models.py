"""Operational state, independent of the HTTP API and persistence mechanism."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

IncidentType = Literal["fire", "medical", "traffic", "security"]
Severity = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal["active", "dispatched", "on_scene", "resolved"]
UnitType = Literal["ambulance", "fire_truck", "police"]
UnitStatus = Literal["available", "dispatched", "en_route", "on_scene", "offline"]
DispatchStatus = Literal["created", "en_route", "arrived", "completed"]


@dataclass
class Incident:
    id: str
    type: IncidentType
    title: str
    area: str
    severity: Severity
    status: IncidentStatus
    latitude: float
    longitude: float
    description: str
    created_at: datetime
    assigned_unit_id: str | None = None
    response_time_seconds: int | None = None


@dataclass
class EmergencyUnit:
    id: str
    name: str
    type: UnitType
    status: UnitStatus
    area: str
    latitude: float
    longitude: float
    last_updated_at: datetime
    assigned_incident_id: str | None = None


@dataclass
class Dispatch:
    id: str
    incident_id: str
    unit_id: str
    status: DispatchStatus
    created_at: datetime
    origin_latitude: float
    origin_longitude: float
    started_at: datetime | None = None
    arrived_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class ActivityEvent:
    id: str
    event_type: str
    entity_type: str
    entity_id: str
    message: str
    timestamp: datetime
    metadata: dict[str, str | int | float | None] = field(default_factory=dict)
