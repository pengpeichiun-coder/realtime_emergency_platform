"""Redis is best-effort fanout only. PostgreSQL owns state and tenant cursors."""

import asyncio
from app.observability import BUS_RECONNECTS
import json
import logging
from pydantic import TypeAdapter
from redis.asyncio import Redis
from app.schemas.events import RealtimeEvent

logger = logging.getLogger(__name__)
CHANNEL = "emergency:realtime:v1"
adapter = TypeAdapter(RealtimeEvent)


class RedisEventBus:
    def __init__(self, url, services):
        self.redis = Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=3
        )
        self.services = services
        self.task = None
        self.connected = asyncio.Event()
        self.stopping = False

    async def start(self):
        self.task = asyncio.create_task(self.listen(), name="redis-realtime-subscriber")
        await asyncio.wait_for(self.connected.wait(), 5)

    def invalidate_clients(self):
        # Clients reconnect and REST-resync after a gap, including silent outages.
        for service in tuple(self.services.values()):
            for connection in tuple(service.connections.connections.values()):
                connection.close_code = 1013
                connection.close_requested.set()

    async def listen(self):
        while not self.stopping:
            try:
                async with self.redis.pubsub() as subscription:
                    await subscription.subscribe(CHANNEL)
                    # Await actual subscription acknowledgement before readiness.
                    while not self.stopping:
                        message = await subscription.get_message(
                            ignore_subscribe_messages=False, timeout=1
                        )
                        if message and message["type"] == "subscribe":
                            self.connected.set()
                            break
                    while not self.stopping:
                        message = await subscription.get_message(
                            ignore_subscribe_messages=False, timeout=1
                        )
                        if not message:
                            continue
                        if message["type"] == "subscribe":
                            self.invalidate_clients()
                            BUS_RECONNECTS.inc()
                            continue
                        if message["type"] != "message":
                            continue
                        try:
                            payload = json.loads(message["data"])
                            organization_id = payload["organization_id"]
                            if not isinstance(organization_id, str):
                                raise ValueError()
                            events = [
                                adapter.validate_python(item)
                                for item in payload["events"]
                            ]
                            service = self.services.get(organization_id)
                            if service:
                                for event in events:
                                    service.connections.broadcast(event)
                        except (ValueError, KeyError, TypeError):
                            logger.warning(
                                "Invalid internal realtime envelope discarded"
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected.clear()
                self.invalidate_clients()
                logger.warning(
                    "Realtime subscription unavailable; clients must resynchronize"
                )
                await asyncio.sleep(1)

    async def publish(self, organization_id, events):
        if not events:
            return
        try:
            await self.redis.publish(
                CHANNEL,
                json.dumps(
                    {
                        "organization_id": organization_id,
                        "events": [event.model_dump(mode="json") for event in events],
                    }
                ),
            )
        except Exception:
            # The domain commit already succeeded; never tell the caller it failed.
            self.invalidate_clients()
            logger.warning(
                "Realtime publication unavailable after durable commit",
                extra={"organization_id": organization_id},
            )

    async def close(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.connected.clear()
        await self.redis.aclose()
