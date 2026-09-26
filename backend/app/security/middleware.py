"""HTTP host, payload-size and browser security boundaries."""

import json
import os
from urllib.parse import urlsplit


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "style-src-attr 'unsafe-inline'; img-src 'self' data: blob: "
    "https://*.tile.openstreetmap.org; font-src 'self' data:; "
    "connect-src 'self' ws: wss:; base-uri 'self'; object-src 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
MAX_REQUEST_BODY_BYTES = 256 * 1024


def allowed_hostnames(raw: str | None = None) -> frozenset[str]:
    value = raw if raw is not None else os.environ.get("ALLOWED_HOSTS")
    if value is None:
        if os.environ.get("APP_ENV", "production") == "production":
            raise ValueError("ALLOWED_HOSTS must be configured in production")
        value = "localhost,127.0.0.1,testserver"
    result = frozenset(part.strip().lower().rstrip(".") for part in value.split(",") if part.strip())
    if not result or "*" in result:
        raise ValueError("ALLOWED_HOSTS must list explicit hostnames")
    return result


def trusted_proxy_networks(raw: str | None = None):
    import ipaddress

    value = raw if raw is not None else os.environ.get("TRUSTED_PROXY_CIDRS", "")
    try:
        return tuple(
            ipaddress.ip_network(part.strip(), strict=False)
            for part in value.split(",")
            if part.strip()
        )
    except ValueError as exc:
        raise ValueError("TRUSTED_PROXY_CIDRS contains an invalid network") from exc


def _host_allowed(value: bytes, allowlist: frozenset[str]) -> bool:
    try:
        host = urlsplit("//" + value.decode("latin-1")).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host in allowlist or any(
        pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:]
        for pattern in allowlist
    )


class TrustedHostPolicy:
    """Validate Host on application routes; permit only ALB health probes by path."""

    def __init__(self, app, allowlist: frozenset[str]):
        self.app = app
        self.allowlist = allowlist

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path not in {"/health", "/health/live", "/health/ready"}:
            host = next((v for k, v in scope.get("headers", []) if k == b"host"), b"")
            if not _host_allowed(host, self.allowlist):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    body = json.dumps(
                        {"error": {"code": "INVALID_HOST", "message": "Invalid host."}}
                    ).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 400,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class RequestBodyLimit:
    """Reject oversized declared or streaming bodies before route processing."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send, status=400, code="INVALID_CONTENT_LENGTH")
                return

        consumed = 0

        async def guarded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise RequestTooLarge()
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except RequestTooLarge:
            await self._reject(send)

    async def _reject(self, send, status=413, code="REQUEST_TOO_LARGE"):
        body = json.dumps(
            {"error": {"code": code, "message": "Request body is too large."}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeaders:
    def __init__(self, app, *, hsts: bool = False):
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                names = {key.lower() for key, _ in message.get("headers", [])}
                additions = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
                    (b"x-frame-options", b"DENY"),
                ]
                if self.hsts:
                    additions.append(
                        (b"strict-transport-security", b"max-age=31536000")
                    )
                message["headers"] = list(message.get("headers", [])) + [
                    (key, value) for key, value in additions if key not in names
                ]
            await send(message)

        await self.app(scope, receive, secure_send)


class RequestTooLarge(Exception):
    pass
