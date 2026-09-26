"""Atomic event intent and administrator-owned subscriptions."""

import secrets
from app.observability import request_id_var
from datetime import UTC, datetime
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select
from app.db.models import OutboxEvent, WebhookSubscription, WebhookDelivery
from app.integrations.webhooks import EVENT_TYPES, cipher, validate_url, UnsafeURL
from app.services.operation_retries import record_manual_retry


async def persist_events(session, organization_id, events):
    subscriptions = None
    for event in events:
        event_type = event.type
        if event_type == "unit.status.updated":
            event_type = {
                "dispatched": "unit.dispatched",
                "on_scene": "unit.arrived",
            }.get(event.data.status)
        if event_type not in EVENT_TYPES:
            continue
        if subscriptions is None:
            subscriptions = list(
                await session.scalars(
                    select(WebhookSubscription).where(
                        WebhookSubscription.organization_id == organization_id,
                        WebhookSubscription.is_active.is_(True),
                        WebhookSubscription.deleted_at.is_(None),
                    )
                )
            )
        identifier = "evt_" + uuid4().hex
        session.add(
            OutboxEvent(
                id=identifier,
                request_id=request_id_var.get(),
                organization_id=organization_id,
                event_type=event_type,
                created_at=event.timestamp,
                subscription_ids=[
                    s.id for s in subscriptions if event_type in s.event_types
                ],
                payload={
                    "id": identifier,
                    "type": event_type,
                    "created_at": event.timestamp.isoformat(),
                    "data": event.data.model_dump(mode="json"),
                },
            )
        )


class WebhookService:
    def __init__(self, database):
        self.database = database

    async def checked_url(self, url):
        try:
            await validate_url(url)
        except (UnsafeURL, OSError, TimeoutError):
            raise HTTPException(422, "Webhook URL is invalid or disallowed.") from None

    async def create(self, context, body):
        await self.checked_url(body.url)
        secret = secrets.token_urlsafe(32)
        try:
            encrypted = cipher().encrypt(secret.encode()).decode()
        except ValueError:
            raise HTTPException(503, "Webhook encryption is not configured.") from None
        async with self.database.sessions.begin() as session:
            row = WebhookSubscription(
                id="WH-" + uuid4().hex,
                organization_id=context.organization_id,
                created_by_user_id=context.user_id,
                encrypted_signing_secret=encrypted,
                **body.model_dump(),
            )
            session.add(row)
            await session.flush()
        return row, secret

    async def list(self, context, model):
        async with self.database.sessions() as session:
            query = select(model).where(
                model.organization_id == context.organization_id
            )
            if model is WebhookSubscription:
                query = query.where(model.deleted_at.is_(None))
            return list(
                await session.scalars(
                    query.order_by(model.created_at.desc(), model.id).limit(100)
                )
            )

    async def get(self, session, context, model, identifier):
        query = select(model).where(
            model.organization_id == context.organization_id, model.id == identifier
        )
        if model is WebhookSubscription:
            query = query.where(model.deleted_at.is_(None))
        row = await session.scalar(query.with_for_update())
        if row is None:
            raise HTTPException(404, "Integration record not found.")
        return row

    async def update(self, context, identifier, body=None):
        if body and body.url is not None:
            await self.checked_url(body.url)
        async with self.database.sessions.begin() as session:
            row = await self.get(session, context, WebhookSubscription, identifier)
            if body is None:
                row.is_active = False
                row.deleted_at = datetime.now(UTC)
            else:
                for key, value in body.model_dump(exclude_unset=True).items():
                    setattr(row, key, value)
            await session.flush()
        return row

    async def delivery(self, context, identifier):
        async with self.database.sessions() as session:
            return await self.get(session, context, WebhookDelivery, identifier)

    async def retry_failed_delivery(self, context, identifier):
        async with self.database.sessions.begin() as session:
            row = await self.get(session, context, WebhookDelivery, identifier)
            if row.status in {"PENDING", "RUNNING"}:
                return row
            if row.status != "FAILED":
                raise HTTPException(409, "Only failed deliveries can be retried.")

            subscription = await session.get(WebhookSubscription, row.subscription_id)
            if (
                subscription is None
                or subscription.organization_id != context.organization_id
                or not subscription.is_active
                or subscription.deleted_at is not None
            ):
                raise HTTPException(409, "The webhook subscription is inactive.")

            await record_manual_retry(session, context, "WEBHOOK_DELIVERY", row.id)
            now = datetime.now(UTC)
            row.status = "PENDING"
            row.attempt_count = 0
            row.last_http_status = None
            row.last_error = None
            row.next_attempt_at = now
            row.last_enqueued_at = None
            row.delivered_at = None
            row.request_id = request_id_var.get()
            row.updated_at = now
            await session.flush()
            return row
