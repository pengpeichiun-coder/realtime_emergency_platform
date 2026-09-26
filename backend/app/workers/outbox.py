from datetime import UTC, datetime, timedelta
import asyncio
from uuid import uuid4
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import insert
from app.db.models import OutboxEvent, WebhookSubscription, WebhookDelivery
from app.workers.celery_app import celery_app


async def materialize_outbox(database):
    async with database.sessions.begin() as session:
        events = list(
            await session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.processed_at.is_(None))
                .order_by(OutboxEvent.created_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for event in events:
            subscriptions = list(
                await session.scalars(
                    select(WebhookSubscription).where(
                        WebhookSubscription.organization_id == event.organization_id,
                        WebhookSubscription.id.in_(event.subscription_ids),
                        WebhookSubscription.is_active.is_(True),
                        WebhookSubscription.deleted_at.is_(None),
                    )
                )
            )
            for subscription in subscriptions:
                await session.execute(
                    insert(WebhookDelivery)
                    .values(
                        id="WHD-" + uuid4().hex,
                        organization_id=event.organization_id,
                        request_id=event.request_id,
                        subscription_id=subscription.id,
                        event_id=event.id,
                        event_type=event.event_type,
                        payload=event.payload,
                        url=subscription.url,
                        status="PENDING",
                        attempt_count=0,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["event_id", "subscription_id"]
                    )
                )
            event.processed_at = datetime.now(UTC)
        return len(events)


async def publish_deliveries(database, send=None):
    send = send or (
        lambda identifier: celery_app.send_task(
            "emergency.deliver_webhook", args=[identifier], task_id=identifier
        )
    )
    async with database.sessions.begin() as session:
        now = datetime.now(UTC)
        rows = list(
            await session.scalars(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status.in_(["PENDING", "RUNNING"]),
                    WebhookDelivery.next_attempt_at <= now,
                    or_(
                        WebhookDelivery.last_enqueued_at.is_(None),
                        WebhookDelivery.last_enqueued_at < now - timedelta(seconds=30),
                    ),
                )
                .order_by(WebhookDelivery.created_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            await asyncio.to_thread(send, row.id)
            row.last_enqueued_at = now
        return len(rows)
