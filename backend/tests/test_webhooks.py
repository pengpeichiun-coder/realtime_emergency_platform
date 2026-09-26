import asyncio
import hashlib
import hmac
import socket
from datetime import UTC, datetime, timedelta
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update, func
from app.db.models import OutboxEvent, WebhookSubscription, WebhookDelivery
from app.db.session import Database
from app.integrations.webhooks import (
    cipher,
    canonical,
    signed_headers,
    validate_url,
    UnsafeURL,
)
from app.workers.outbox import materialize_outbox, publish_deliveries
from app.workers.tasks.webhooks import deliver
from test_auth import auth_case, create_role, database_run
from test_postgres import pg_url


@pytest.fixture(autouse=True)
def webhook_settings(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WEBHOOK_ALLOWED_LOOPBACK_ORIGINS", "http://127.0.0.1:8765")


def subscription(client, headers, events=None):
    response = client.post(
        "/api/webhooks",
        headers=headers,
        json={
            "name": "Receiver",
            "url": "http://127.0.0.1:8765/events",
            "event_types": events or ["incident.updated"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def emit(client, headers):
    assert client.post("/api/demo/reset", headers=headers).status_code == 200
    assert (
        client.patch(
            "/api/incidents/INC-001", headers=headers, json={"title": "Webhook test"}
        ).status_code
        == 200
    )


def process(url):
    async def run():
        db = Database(url)
        try:
            await materialize_outbox(db)
            sent = []
            await publish_deliveries(db, sent.append)
            return sent
        finally:
            await db.close()

    return asyncio.run(run())


@pytest.mark.postgres
def test_crud_secret_filter_outbox_idempotency_and_isolation(auth_case, pg_url):
    _, client, user, headers, register, _ = auth_case
    # A subscription cannot receive events that happened before it existed.
    emit(client, headers)
    sub = subscription(client, headers)
    subscription(client, headers, ["incident.resolved"])
    listed = client.get("/api/webhooks", headers=headers).json()
    assert all(
        "signing_secret" not in row and "encrypted_signing_secret" not in row
        for row in listed
    )

    async def stored(session):
        row = await session.get(WebhookSubscription, sub["id"])
        assert row.encrypted_signing_secret != sub["signing_secret"]
        assert (
            cipher().decrypt(row.encrypted_signing_secret.encode()).decode()
            == sub["signing_secret"]
        )

    database_run(pg_url, stored)
    emit(client, headers)
    process(pg_url)
    process(pg_url)
    deliveries = client.get("/api/webhook-deliveries", headers=headers).json()
    assert len(deliveries) == 1
    item = deliveries[0]
    assert (
        item["subscription_id"] == sub["id"]
        and item["payload"]["type"] == "incident.updated"
    )

    async def replay(session):
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.organization_id == user["organization_id"])
            .values(processed_at=None)
        )

    database_run(pg_url, replay)
    process(pg_url)
    assert len(client.get("/api/webhook-deliveries", headers=headers).json()) == 1
    _, other, _ = register()
    assert client.get("/api/webhooks", headers=other).json() == []
    assert client.get("/api/webhook-deliveries", headers=other).json() == []
    assert (
        client.get("/api/webhook-deliveries/" + item["id"], headers=other).status_code
        == 404
    )
    assert (
        client.patch(
            "/api/webhooks/" + sub["id"], headers=other, json={"name": "Forbidden"}
        ).status_code
        == 404
    )
    assert client.delete("/api/webhooks/" + sub["id"], headers=other).status_code == 404
    assert (
        client.patch(
            "/api/webhooks/" + sub["id"], headers=headers, json={"is_active": False}
        ).json()["is_active"]
        is False
    )
    assert (
        client.patch(
            "/api/webhooks/" + sub["id"], headers=headers, json={"name": None}
        ).status_code
        == 422
    )
    assert (
        client.delete("/api/webhooks/" + sub["id"], headers=headers).status_code == 204
    )
    assert len(client.get("/api/webhooks", headers=headers).json()) == 1
    assert len(client.get("/api/webhook-deliveries", headers=headers).json()) == 1


@pytest.mark.postgres
@pytest.mark.parametrize("role", ["ADMIN", "DISPATCHER", "RESPONDER"])
def test_webhook_rbac(auth_case, role):
    _, client, _, admin, _, _ = auth_case
    sub = subscription(client, admin)
    _, headers = create_role(client, admin, role)
    expected = 200 if role == "ADMIN" else 403
    assert client.get("/api/webhooks", headers=headers).status_code == expected
    assert (
        client.get("/api/webhook-deliveries", headers=headers).status_code == expected
    )
    assert (
        client.patch(
            "/api/webhooks/" + sub["id"], headers=headers, json={"name": "Renamed"}
        ).status_code
        == expected
    )
    assert client.post(
        "/api/webhooks",
        headers=headers,
        json={"name": "Other", "url": sub["url"], "event_types": ["incident.updated"]},
    ).status_code == (201 if role == "ADMIN" else 403)
    assert client.delete("/api/webhooks/" + sub["id"], headers=headers).status_code == (
        204 if role == "ADMIN" else 403
    )
    assert client.get("/api/webhooks").status_code == 401


@pytest.mark.postgres
@pytest.mark.parametrize("outcome", [200, 400, 408, 429, 500, "timeout", "connection"])
def test_delivery_classification_signature_retry_and_duplicate(
    auth_case, pg_url, outcome
):
    _, client, _, headers, _, _ = auth_case
    sub = subscription(client, headers)
    emit(client, headers)
    process(pg_url)
    identifier = client.get("/api/webhook-deliveries", headers=headers).json()[0]["id"]
    calls = []

    async def sender(url, payload, hdr):
        calls.append(hdr)
        expected = hmac.new(
            sub["signing_secret"].encode(),
            hdr["X-Emergency-Timestamp"].encode() + b"." + canonical(payload),
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(hdr["X-Emergency-Signature"], "sha256=" + expected)
        assert hdr["X-Emergency-Delivery"] == identifier
        if outcome == "timeout":
            raise TimeoutError("private secret")
        if outcome == "connection":
            raise ConnectionError("private secret")
        return outcome

    transient = outcome in [408, 429, 500, "timeout", "connection"]
    for attempt in range(1, 4 if transient else 2):
        delay = asyncio.run(deliver(identifier, pg_url, sender))
        saved = client.get(
            "/api/webhook-deliveries/" + identifier, headers=headers
        ).json()
        assert saved["attempt_count"] == attempt
        assert "private secret" not in str(saved)
        if transient and attempt < 3:
            assert saved["status"] == "PENDING" and delay == 2**attempt

            async def due(session):
                await session.execute(
                    update(WebhookDelivery)
                    .where(WebhookDelivery.id == identifier)
                    .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
                )

            database_run(pg_url, due)
        else:
            assert saved["status"] == ("DELIVERED" if outcome == 200 else "FAILED")
    before = len(calls)
    asyncio.run(deliver(identifier, pg_url, sender))
    assert len(calls) == before


def test_admin_delivery_retry_is_scoped_audited_idempotent_and_redeliverable(
    auth_case, pg_url
):
    _, client, user, admin, register, _ = auth_case
    sub = subscription(client, admin)
    emit(client, admin)
    process(pg_url)
    identifier = client.get("/api/webhook-deliveries", headers=admin).json()[0]["id"]

    async def rejected(url, payload, headers):
        return 400

    asyncio.run(deliver(identifier, pg_url, rejected))
    assert (
        client.get("/api/webhook-deliveries/" + identifier, headers=admin).json()[
            "status"
        ]
        == "FAILED"
    )

    _, dispatcher = create_role(client, admin, "DISPATCHER")
    _, foreign_admin, _ = register()
    path = f"/api/webhook-deliveries/{identifier}/retry"
    assert client.post(path, headers=dispatcher).status_code == 403
    assert client.post(path, headers=foreign_admin).status_code == 404

    queued = client.post(path, headers=admin)
    assert queued.status_code == 202
    assert queued.json()["status"] == "PENDING"
    assert queued.json()["attempt_count"] == 0
    assert client.post(path, headers=admin).status_code == 202
    audits = client.get("/api/operation-retries", headers=admin).json()
    rows = [row for row in audits if row["operation_id"] == identifier]
    assert len(rows) == 1
    assert rows[0]["operation_type"] == "WEBHOOK_DELIVERY"
    assert rows[0]["retry_count"] == 1
    assert rows[0]["requested_by_user_id"] == user["id"]

    delivered_ids = []

    async def accepted(url, payload, headers):
        delivered_ids.append(headers["X-Emergency-Delivery"])
        return 200

    asyncio.run(deliver(identifier, pg_url, accepted))
    final = client.get(
        "/api/webhook-deliveries/" + identifier, headers=admin
    ).json()
    assert final["status"] == "DELIVERED" and final["attempt_count"] == 1
    assert delivered_ids == [identifier]
    assert client.post(path, headers=admin).status_code == 409


@pytest.mark.postgres
def test_outbox_atomic_rollback(auth_case, pg_url):
    from app.repositories.postgres import PostgresRepository
    from app.services.operations import OperationsService
    from app.services.connections import ConnectionManager
    from app.schemas.operations import IncidentPatch

    _, _, user, _, _, _ = auth_case

    async def run():
        repo = PostgresRepository(pg_url, organization_id=user["organization_id"])
        await repo.open()
        service = OperationsService(repo, ConnectionManager())
        try:
            await service.reset()
            before = await service.get_incident("INC-001")
            original = repo.persist_events

            async def failing(events):
                await original(events)
                raise RuntimeError("rollback")

            repo.persist_events = failing
            with pytest.raises(RuntimeError):
                await service.update_incident(
                    "INC-001", IncidentPatch(title="Uncommitted")
                )
            assert (await service.get_incident("INC-001")).title == before.title
            async with repo.database.sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.organization_id == user["organization_id"])
                    )
                    == 0
                )
        finally:
            await repo.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x",
        "file:///etc/passwd",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://169.254.169.254",
        "https://224.0.0.1",
        "https://0.0.0.0",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
        "https://user:pass@example.com",
        "https://example.com/#fragment",
        "http://127.0.0.1:9999",
        "https://192.168.1.1",
    ],
)
def test_ssrf_rejection(url):
    with pytest.raises(UnsafeURL):
        asyncio.run(validate_url(url))


def test_loopback_exception_development_only(monkeypatch):
    assert asyncio.run(validate_url("http://127.0.0.1:8765/events"))
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(UnsafeURL):
        asyncio.run(validate_url("http://127.0.0.1:8765/events"))


def test_dns_mixed_private_rejected(monkeypatch):
    async def run():
        async def resolve(*args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
        with pytest.raises(UnsafeURL):
            await validate_url("https://public.example.com")

    asyncio.run(run())


def test_signature_canonical_and_timestamp():
    a = {"id": "evt_1", "type": "incident.created", "data": {"z": 1, "a": "緊急"}}
    b = {"data": {"a": "緊急", "z": 1}, "type": "incident.created", "id": "evt_1"}
    assert signed_headers("secret", a, "delivery", 123) == signed_headers(
        "secret", b, "delivery", 123
    )
    assert signed_headers("secret", a, "delivery", 124) != signed_headers(
        "secret", a, "delivery", 123
    )


@pytest.mark.postgres
def test_concurrent_delivery_fenced_and_broker_failure_recovers(auth_case, pg_url):
    _, client, _, headers, _, _ = auth_case
    subscription(client, headers)
    emit(client, headers)
    async def scenario():
        db = Database(pg_url)
        try:
            await materialize_outbox(db)
            def fail(identifier):
                raise ConnectionError('broker unavailable')
            with pytest.raises(ConnectionError):
                await publish_deliveries(db, fail)
            sent = []
            await publish_deliveries(db, sent.append)
            identifier = client.get('/api/webhook-deliveries', headers=headers).json()[0]['id']
            assert identifier in sent
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def sender(*args):
                calls.append(1)
                entered.set()
                await release.wait()
                return 200
            task = asyncio.create_task(deliver(identifier, pg_url, sender))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                await deliver(identifier, pg_url, sender)
                assert len(calls) == 1
            finally:
                release.set()
                await task
        finally:
            await db.close()
    asyncio.run(scenario())


def test_http_does_not_follow_redirects():
    from aiohttp import web
    from app.integrations.webhooks import post_webhook
    async def scenario():
        contacted = []
        async def redirect(request):
            return web.Response(status=302, headers={'Location':'http://127.0.0.1:8765/private'})
        async def private(request):
            contacted.append(True)
            return web.Response()
        app = web.Application()
        app.router.add_post('/redirect', redirect)
        app.router.add_route('*', '/private', private)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, '127.0.0.1', 8765).start()
            assert await post_webhook('http://127.0.0.1:8765/redirect', {}, {}) == 302
            assert contacted == []
        finally:
            await runner.cleanup()
    asyncio.run(scenario())
