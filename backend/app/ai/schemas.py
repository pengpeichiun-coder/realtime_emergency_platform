from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=4000)]
Items = Annotated[list[Text], Field(max_length=50)]


class IncidentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: Text
    key_events: Items
    current_status: Text
    open_items: Items


class ShiftHandover(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    overview: Text
    active_incidents: Items
    units_in_service: Items
    priority_items: Items
