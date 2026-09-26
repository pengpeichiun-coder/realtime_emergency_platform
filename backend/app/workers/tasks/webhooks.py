"""At-least-once network delivery; receivers deduplicate the stable delivery ID."""

import asyncio
from app.observability import request_id_var, identity_var
import logging
from datetime import UTC, datetime, timedelta
import aiohttp
from sqlalchemy import text
from app.db.models import WebhookDelivery, WebhookSubscription
from app.db.session import Database
from app.integrations.webhooks import cipher, post_webhook, signed_headers
from app.workers.celery_app import celery_app
from app.workers.config import MAX_ATTEMPTS, backoff

logger = logging.getLogger(__name__)


async def deliver(identifier, url=None, sender=None):
    database = Database(url)
    try:
        async with database.engine.connect() as lock:
            acquired = await lock.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:id, 902))"),
                {"id": identifier},
            )
            await lock.commit()
            if not acquired:
                return
            try:
                async with database.sessions.begin() as session:
                    row = await session.get(
                        WebhookDelivery, identifier, with_for_update=True
                    )
                    if (
                        not row
                        or row.status in {"DELIVERED", "FAILED"}
                        or row.next_attempt_at > datetime.now(UTC)
                    ):
                        return
                    sub = await session.get(WebhookSubscription, row.subscription_id)
                    if (
                        not sub
                        or sub.organization_id != row.organization_id
                        or not sub.is_active
                        or sub.deleted_at
                        or row.attempt_count >= MAX_ATTEMPTS
                    ):
                        row.status, row.last_error = (
                            "FAILED",
                            "Subscription disabled or attempt limit reached.",
                        )
                        return
                    row.status = "RUNNING"
                    row.attempt_count += 1
                request_id_var.set(row.request_id)
                identity_var.set({"organization_id":row.organization_id})
                status = None
                transient = False
                error = None
                try:
                    secret = (
                        cipher().decrypt(sub.encrypted_signing_secret.encode()).decode()
                    )
                    headers = signed_headers(secret, row.payload, row.id)
                    status = await (sender or post_webhook)(
                        row.url, row.payload, headers
                    )
                    transient = status in {408, 429} or 500 <= status < 600
                    if not 200 <= status < 300:
                        error = "Receiver returned an unsuccessful HTTP status."
                except (
                    TimeoutError,
                    ConnectionError,
                    aiohttp.ClientConnectionError,
                    OSError,
                ):
                    transient, error = True, "Receiver connection failed or timed out."
                except Exception:
                    error = "Delivery configuration or destination is invalid."
                retry = transient and row.attempt_count < MAX_ATTEMPTS
                delay = backoff(row.attempt_count)
                async with database.sessions.begin() as session:
                    saved = await session.get(
                        WebhookDelivery, identifier, with_for_update=True
                    )
                    saved.last_http_status = status
                    saved.last_error = error
                    saved.status = (
                        "DELIVERED"
                        if error is None
                        else ("PENDING" if retry else "FAILED")
                    )
                    saved.delivered_at = datetime.now(UTC) if error is None else None
                    saved.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
                logger.info(
                    "Webhook delivery id=%s attempt=%s status=%s retry=%s",
                    identifier,
                    row.attempt_count,
                    status,
                    retry, extra={"delivery_id":identifier,"event_type":row.event_type,"attempt":row.attempt_count,"status":saved.status}
                )
                return delay if retry else None
            finally:
                await lock.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:id, 902))"),
                    {"id": identifier},
                )
                await lock.commit()
    finally:
        await database.close()


@celery_app.task(bind=True, name="emergency.deliver_webhook", max_retries=2)
def execute(self, identifier):
    try:
        delay = asyncio.run(deliver(identifier))
    except Exception:
        logger.warning(
            "Delivery database unavailable id=%s; publisher will recover", identifier
        )
        return
    if delay is not None:
        raise self.retry(countdown=delay)
