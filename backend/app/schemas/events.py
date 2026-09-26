"""Discriminated server-event contract shared by all WebSocket messages."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.operations import (
    ActivityResponse, DispatchResponse, IncidentResponse, StateResponse, UnitResponse,
)

IncidentEventType = Literal["incident.created", "incident.updated", "incident.resolved"]
UnitEventType = Literal["unit.status.updated", "unit.location.updated"]
DispatchEventType = Literal["dispatch.created", "dispatch.updated", "dispatch.completed"]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: AwareDatetime
    stream_id: str
    sequence: int = Field(ge=0)


class IncidentEvent(EventEnvelope):
    type: IncidentEventType
    data: IncidentResponse


class UnitEvent(EventEnvelope):
    type: UnitEventType
    data: UnitResponse


class DispatchEvent(EventEnvelope):
    type: DispatchEventType
    data: DispatchResponse


class ActivityCreatedEvent(EventEnvelope):
    type: Literal["activity.created"] = "activity.created"
    data: ActivityResponse


class StateResetEvent(EventEnvelope):
    type: Literal["state.reset"] = "state.reset"
    data: StateResponse


class ConnectionReadyEvent(EventEnvelope):
    type: Literal["connection.ready"] = "connection.ready"
    data: dict = Field(default_factory=dict, max_length=0)


RealtimeEvent = Annotated[
    IncidentEvent | UnitEvent | DispatchEvent | ActivityCreatedEvent | StateResetEvent | ConnectionReadyEvent,
    Field(discriminator="type"),
]
