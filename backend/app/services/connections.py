"""Ordered, bounded queues isolate operational commands from slow sockets."""

import asyncio
from app.observability import WS_ACTIVE, WS_OPENED, WS_CLOSED
from dataclasses import dataclass, field
import logging
from uuid import uuid4

import anyio
from fastapi import WebSocket, WebSocketDisconnect

from app.schemas.events import ConnectionReadyEvent, RealtimeEvent

logger = logging.getLogger(__name__)


@dataclass
class Connection:
    websocket: WebSocket
    queue: asyncio.Queue[RealtimeEvent]
    id: str = field(default_factory=lambda: uuid4().hex)
    close_requested: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    close_code: int = 1000
    user_id: str | None = None


class ConnectionManager:
    def __init__(self, queue_size: int = 256):
        self.queue_size = queue_size
        self.connections: dict[str, Connection] = {}
        self.tasks: set[asyncio.Task] = set()

    def connect(self, websocket: WebSocket, ready: ConnectionReadyEvent) -> Connection:
        """Called under the service lock after accepting the socket."""
        connection = Connection(websocket, asyncio.Queue(maxsize=self.queue_size))
        self.connections[connection.id] = connection
        WS_ACTIVE.inc(); WS_OPENED.inc()
        connection.queue.put_nowait(ready)
        logger.info("WebSocket connected: %s", connection.id)
        return connection

    def disconnect(self, connection: Connection) -> None:
        if self.connections.pop(connection.id, None) is not None:
            WS_ACTIVE.dec(); WS_CLOSED.inc()
        logger.info("WebSocket disconnected: %s", connection.id)

    def broadcast(self, event: RealtimeEvent) -> None:
        """No network awaits: called while the service owns its mutation lock."""
        for connection in tuple(self.connections.values()):
            if connection.close_requested.is_set():
                continue
            try:
                connection.queue.put_nowait(event)
            except asyncio.QueueFull:
                connection.close_code = 1013
                connection.close_requested.set()
                logger.warning("Closing slow WebSocket subscriber: %s", connection.id)

    async def _send(self, connection: Connection) -> None:
        while True:
            event = await connection.queue.get()
            await connection.websocket.send_json(event.model_dump(mode="json"))

    async def _receive(self, connection: Connection) -> None:
        # The stream is server-to-client; reading only detects peer disconnects.
        while True:
            message = await connection.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    async def run(self, connection: Connection) -> None:
        workers = {
            asyncio.create_task(self._send(connection), name=f"ws-send-{connection.id}"),
            asyncio.create_task(self._receive(connection), name=f"ws-receive-{connection.id}"),
            asyncio.create_task(connection.close_requested.wait(), name=f"ws-close-{connection.id}"),
        }
        self.tasks.update(workers)
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    task.result()
                except (WebSocketDisconnect, OSError, RuntimeError) as exc:
                    logger.info("WebSocket transport closed: %s", connection.id)
                except Exception:
                    logger.exception("WebSocket worker failed: %s", connection.id)
        finally:
            # ASGI servers/test clients may cancel the request as soon as the peer
            # leaves. Shield cleanup so owned workers and shutdown waiters finish.
            with anyio.CancelScope(shield=True):
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                self.tasks.difference_update(workers)
                self.disconnect(connection)
                try:
                    await asyncio.wait_for(connection.websocket.close(code=connection.close_code), timeout=1)
                except (WebSocketDisconnect, OSError, RuntimeError, TimeoutError):
                    logger.debug("WebSocket already closed: %s", connection.id)
                finally:
                    connection.closed.set()

    def close_user(self, user_id):
        for connection in self.connections.values():
            if connection.user_id == user_id:
                connection.close_code = 1008
                connection.close_requested.set()

    async def shutdown(self) -> None:
        connections = tuple(self.connections.values())
        for connection in connections:
            connection.close_code = 1001
            connection.close_requested.set()
        if connections:
            await asyncio.gather(*(connection.closed.wait() for connection in connections))
