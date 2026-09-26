"""First-frame authentication keeps access credentials out of URL/access logs."""

import asyncio
import anyio
import logging
from time import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from app.auth.service import context_for
from app.observability import WS_AUTH_REJECTIONS

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/events")
async def events(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and origin not in websocket.app.state.allowed_origins:
        WS_AUTH_REJECTIONS.labels("origin").inc()
        await websocket.close(code=1008)
        return
    await websocket.accept()
    context = websocket.app.state.trusted_context
    token = None
    expiry = None
    if context is None:
        try:
            message = await asyncio.wait_for(websocket.receive_json(), 5)
            if (
                not isinstance(message, dict)
                or set(message) != {"type", "access_token"}
                or message["type"] != "authenticate"
            ):
                raise ValueError()
            token = message["access_token"]
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError()
            user, value = await websocket.app.state.auth.identity(token)
            context = context_for(user)
            expiry = value["exp"]
        except (
            ValueError,
            KeyError,
            TypeError,
            HTTPException,
            TimeoutError,
            WebSocketDisconnect,
        ):
            logger.info("WebSocket authentication rejected")
            WS_AUTH_REJECTIONS.labels("credentials").inc()
            await websocket.close(code=1008)
            return
    service = websocket.app.state.service
    if service is None:
        service = await websocket.app.state.tenants.get(context.organization_id)
    manager = service.connections
    async with service.lock, service._transaction(write=False):
        connection = manager.connect(websocket, service.connection_ready())
        connection.user_id = context.user_id

    async def check_identity():
        # Expiry is enforced exactly; external DB deactivation is noticed within 5s.
        while True:
            await asyncio.sleep(max(0, min(5, expiry - time())))
            try:
                user, _ = await websocket.app.state.auth.identity(token)
                if context_for(user) != context:
                    raise HTTPException(401)
            except HTTPException:
                connection.close_code = 1008
                connection.close_requested.set()
                return
            except Exception:
                logger.warning("WebSocket identity verification unavailable")
                connection.close_code = 1011
                connection.close_requested.set()
                return

    guard = asyncio.create_task(check_identity()) if token else None
    try:
        await manager.run(connection)
    finally:
        if guard:
            with anyio.CancelScope(shield=True):
                guard.cancel()
                await asyncio.gather(guard, return_exceptions=True)
