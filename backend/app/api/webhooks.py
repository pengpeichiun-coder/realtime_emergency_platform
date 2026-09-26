from datetime import datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.auth.policies import admin
from app.context import RequestContext
from app.db.models import WebhookSubscription, WebhookDelivery
from app.services.webhooks import WebhookService

router = APIRouter(prefix="/api", tags=["webhooks"])
Admin = Annotated[RequestContext, Depends(admin)]
EventType = Literal[
    "incident.created",
    "incident.updated",
    "incident.resolved",
    "unit.dispatched",
    "unit.arrived",
    "dispatch.completed",
]


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    event_types: list[EventType] = Field(min_length=1, max_length=6)
    is_active: bool = True


class SubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    event_types: list[EventType] | None = Field(
        default=None, min_length=1, max_length=6
    )
    is_active: bool | None = None

    @model_validator(mode="after")
    def no_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Explicit null is not supported.")
        return self


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    url: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SubscriptionCreated(SubscriptionResponse):
    signing_secret: str


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    subscription_id: str
    event_id: str
    event_type: str
    payload: dict
    status: str
    attempt_count: int
    last_http_status: int | None
    last_error: str | None
    next_attempt_at: datetime
    created_at: datetime
    delivered_at: datetime | None


def service(request: Request):
    db = request.app.state.tenants.database
    if db is None:
        raise HTTPException(503, "Webhooks require PostgreSQL.")
    return WebhookService(db)


Service = Annotated[WebhookService, Depends(service)]


@router.post("/webhooks", status_code=201, response_model=SubscriptionCreated)
async def create(body: SubscriptionCreate, context: Admin, svc: Service):
    row, secret = await svc.create(context, body)
    return {
        **SubscriptionResponse.model_validate(row).model_dump(),
        "signing_secret": secret,
    }


@router.get("/webhooks", response_model=list[SubscriptionResponse])
async def subscriptions(context: Admin, svc: Service):
    return await svc.list(context, WebhookSubscription)


@router.patch("/webhooks/{identifier}", response_model=SubscriptionResponse)
async def patch(identifier: str, body: SubscriptionPatch, context: Admin, svc: Service):
    return await svc.update(context, identifier, body)


@router.delete("/webhooks/{identifier}", status_code=204)
async def delete(identifier: str, context: Admin, svc: Service):
    await svc.update(context, identifier)
    return Response(status_code=204)


@router.get("/webhook-deliveries", response_model=list[DeliveryResponse])
async def deliveries(context: Admin, svc: Service):
    return await svc.list(context, WebhookDelivery)


@router.get("/webhook-deliveries/{identifier}", response_model=DeliveryResponse)
async def delivery(identifier: str, context: Admin, svc: Service):
    return await svc.delivery(context, identifier)


@router.post(
    "/webhook-deliveries/{identifier}/retry",
    status_code=202,
    response_model=DeliveryResponse,
)
async def retry_delivery(identifier: str, context: Admin, svc: Service):
    return await svc.retry_failed_delivery(context, identifier)
