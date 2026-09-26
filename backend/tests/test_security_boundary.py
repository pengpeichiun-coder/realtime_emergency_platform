"""Security boundary tests that do not require PostgreSQL or external accounts."""

import asyncio
import ipaddress
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from starlette.requests import Request
from fastapi.testclient import TestClient

from app.main import create_app
from app.observability import (
    AUTH_LOGIN_ATTEMPTS,
    AUTH_RATE_LIMIT_DEGRADED,
    AUTH_REFRESH_FAILURES,
    WS_AUTH_REJECTIONS,
    SafeTextFormatter,
    metrics,
)
from app.repositories.memory import MemoryRepository
from app.security.middleware import RequestBodyLimit
from app.security.rate_limit import AuthRateLimiter, WindowLimit, trusted_client_ip


def test_host_allowlist_and_local_security_headers(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENABLE_HSTS", "true")
    app = create_app(repository=MemoryRepository(), simulation_enabled=False)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["x-frame-options"] == "DENY"
        csp = response.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "script-src 'unsafe-inline'" not in csp
        assert "style-src-attr 'unsafe-inline'" in csp
        assert "strict-transport-security" not in response.headers
        assert client.get("/health", headers={"Host": "attacker.invalid"}).status_code == 200
        assert client.get("/api/state", headers={"Host": "attacker.invalid"}).status_code == 400


def test_declared_oversize_request_is_rejected_before_validation():
    app = create_app(repository=MemoryRepository(), simulation_enabled=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/incidents",
            json={"description": "x" * (256 * 1024 + 1)},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_streaming_request_without_content_length_is_bounded():
    sent = []
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def downstream(scope, receive, send):
        await receive()
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": [(b"host", b"testserver")],
    }
    asyncio.run(RequestBodyLimit(downstream, max_bytes=5)(scope, receive, send))
    assert sent[0]["status"] == 413


def test_forwarded_ip_is_ignored_unless_peer_is_a_trusted_proxy():
    networks = (ipaddress.ip_network("10.0.0.0/8"),)

    def request(peer, forwarded):
        app = SimpleNamespace(state=SimpleNamespace(trusted_proxy_networks=networks))
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"x-forwarded-for", forwarded.encode())],
                "client": (peer, 1234),
                "app": app,
            }
        )

    assert trusted_client_ip(request("198.51.100.8", "203.0.113.99")) == "198.51.100.8"
    assert trusted_client_ip(
        request("10.0.0.8", "203.0.113.99, 198.51.100.7")
    ) == "198.51.100.7"
    assert trusted_client_ip(request("10.0.0.8", "not-an-ip")) == "10.0.0.8"


def test_redis_failure_uses_bounded_temporary_fallback_then_window_recovers():
    class FailedRedis:
        async def eval(self, *_args):
            raise OSError("intentionally unavailable")

    clock = [1000.0]
    limiter = AuthRateLimiter(
        "redis://127.0.0.1:1/0",
        pepper="test-pepper",
        prefix="test-rate-limit",
        policies={"login": (WindowLimit("identity", 2, 60),)},
        clock=lambda: clock[0],
    )
    limiter.redis = FailedRedis()

    async def exercise():
        await limiter.check("login", "127.0.0.1", "User@example.test")
        await limiter.check("login", "127.0.0.1", "User@example.test")
        with pytest.raises(HTTPException) as rejected:
            await limiter.check("login", "127.0.0.1", "User@example.test")
        assert rejected.value.status_code == 429
        assert int(rejected.value.headers["Retry-After"]) >= 1
        clock[0] += 61
        await limiter.check("login", "127.0.0.1", "User@example.test")

    asyncio.run(exercise())


def test_production_https_gets_hsts_but_development_http_does_not(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENABLE_HSTS", "true")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://testserver")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    production = create_app(repository=MemoryRepository(), simulation_enabled=False)
    with TestClient(production, base_url="https://testserver") as client:
        assert client.get("/health").headers["strict-transport-security"] == "max-age=31536000"

    monkeypatch.setenv("APP_ENV", "development")
    local = create_app(repository=MemoryRepository(), simulation_enabled=False)
    with TestClient(local, base_url="http://testserver") as client:
        assert "strict-transport-security" not in client.get("/health").headers


def test_auth_and_socket_metrics_have_only_low_cardinality_labels():
    AUTH_LOGIN_ATTEMPTS.labels("success").inc()
    AUTH_LOGIN_ATTEMPTS.labels("failure").inc()
    AUTH_RATE_LIMIT_DEGRADED.inc()
    AUTH_REFRESH_FAILURES.inc()
    WS_AUTH_REJECTIONS.labels("credentials").inc()
    output = asyncio.run(metrics(None)).decode()
    for name in (
        "auth_login_attempts_total",
        "auth_rate_limit_rejections_total",
        "auth_rate_limit_fallback_total",
        "auth_refresh_failures_total",
        "websocket_auth_rejections_total",
    ):
        assert name in output
    assert "email=" not in output and "ip=" not in output and "token=" not in output


def test_text_logs_keep_exception_type_but_not_exception_message():
    formatter = SafeTextFormatter("%(levelname)s %(message)s")
    try:
        raise RuntimeError("password=never-log-this")
    except RuntimeError:
        import logging

        record = logging.LogRecord("test", logging.ERROR, "", 1, "operation failed", (), __import__("sys").exc_info())
    output = formatter.format(record)
    assert "error_type=RuntimeError" in output
    assert "never-log-this" not in output
