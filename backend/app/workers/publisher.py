"""Durable polling publisher. Run separately; no request-scoped objects or Beat DB."""

import asyncio
import logging
import signal
from app.observability import configure_logging
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, or_
from app.db.models import JobRecord
from app.db.session import Database
from app.workers.outbox import materialize_outbox, publish_deliveries
from app.workers.celery_app import celery_app
from app.workers.heartbeat import heartbeat_client, write_heartbeat

logger = logging.getLogger(__name__)


async def publish_jobs(database, send=None):
    send = send or (
        lambda identifier: celery_app.send_task(
            "emergency.execute_job", args=[identifier], task_id=identifier
        )
    )
    async with database.sessions.begin() as session:
        now = datetime.now(UTC)
        rows = list(
            await session.scalars(
                select(JobRecord)
                .where(
                    JobRecord.status.in_(["QUEUED", "RUNNING"]),
                    JobRecord.next_attempt_at <= now,
                    or_(
                        JobRecord.last_enqueued_at.is_(None),
                        JobRecord.last_enqueued_at < now - timedelta(seconds=30),
                    ),
                )
                .order_by(JobRecord.created_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            await asyncio.to_thread(send, row.id)
            row.last_enqueued_at = now
        return len(rows)


async def publish_cycle(database, heartbeat):
    await publish_jobs(database)
    await materialize_outbox(database)
    await publish_deliveries(database)
    await write_heartbeat(heartbeat)


async def main():
    database = Database()
    heartbeat = heartbeat_client()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)
    try:
        while not stop.is_set():
            try:
                await publish_cycle(database, heartbeat)
            except Exception:
                logger.warning(
                    "Publication unavailable; durable rows retained for retry"
                )
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass
    finally:
        try:
            await heartbeat.aclose()
        finally:
            await database.close()


if __name__ == "__main__":
    configure_logging()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
