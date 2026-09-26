"""Validated request and response contracts; internal state uses dataclasses."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.models import (
    DispatchStatus,
    IncidentStatus,
    IncidentType,
    Severity,
    UnitStatus,
    UnitType,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentCreate(RequestModel):
    type: IncidentType
    severity: Severity
    description: Description
    latitude: Latitude
    longitude: Longitude
    title: ShortText = "Reported incident"
    area: ShortText = "Reported location"


class IncidentPatch(RequestModel):
    type: IncidentType | None = None
    severity: Severity | None = None
    description: Description | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    title: ShortText | None = None
    area: ShortText | None = None

    @model_validator(mode="after")
    def reject_null_or_empty(self) -> "IncidentPatch":
        if not self.model_fields_set:
            raise ValueError("Provide at least one incident field to update.")
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Incident fields cannot be null.")
        return self


class DispatchCreate(RequestModel):
    incident_id: Identifier
    unit_id: Identifier


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class IncidentResponse(ResponseModel):
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
    assigned_unit_id: str | None
    response_time_seconds: int | None


class UnitResponse(ResponseModel):
    id: str
    name: str
    type: UnitType
    status: UnitStatus
    area: str
    latitude: float
    longitude: float
    last_updated_at: datetime
    assigned_incident_id: str | None


class DispatchResponse(ResponseModel):
    id: str
    incident_id: str
    unit_id: str
    status: DispatchStatus
    created_at: datetime
    origin_latitude: float
    origin_longitude: float
    started_at: datetime | None
    arrived_at: datetime | None
    completed_at: datetime | None


class ActivityResponse(ResponseModel):
    id: str
    event_type: str
    entity_type: str
    entity_id: str
    message: str
    timestamp: datetime
    metadata: dict[str, str | int | float | None]


class StateResponse(ResponseModel):
    stream_id: str
    sequence: int
    incidents: list[IncidentResponse]
    units: list[UnitResponse]
    dispatches: list[DispatchResponse]
    activity: list[ActivityResponse]


class NearbyUnitResponse(BaseModel):
    unit_id: str
    distance_meters: float = Field(ge=0, allow_inf_nan=False)
