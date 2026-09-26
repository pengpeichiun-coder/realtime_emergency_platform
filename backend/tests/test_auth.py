"""Real PostgreSQL identity, cookie, authorization and socket security checks."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import select, func, delete, update, text
from redis.asyncio import Redis
from app.auth.config import AuthSettings
from app.auth.crypto import token_hash, verify_password
from app.db.models import (
    JobRecord,
    Organization,
    UserRecord,
    RefreshTokenRecord,
    ActivityRecord,
    DispatchRecord,
    UnitRecord,
    IncidentRecord,
)
from app.db.session import Database
from app.main import create_app
from test_postgres import pg_url

pytestmark = pytest.mark.postgres
PASSWORD = "Test-Only-Long-Password!"


@pytest.fixture
def auth_case(pg_url):
    settings = AuthSettings("test-only-signing-key-" + "x" * 40, environment="test")
    app = create_app(
        database_url=pg_url, simulation_enabled=False, auth_settings=settings
    )
    organizations = []
    with TestClient(app) as client:
        app.state.auth_rate_limiter.prefix = "test-auth-case:" + uuid4().hex

        def register():
            email = uuid4().hex + "@example.com"
            response = client.post(
                "/api/auth/register",
                json={
                    "organization_name": "Auth test organization",
                    "display_name": "Test administrator",
                    "email": email,
                    "password": PASSWORD,
                },
            )
            assert response.status_code == 201, response.text
            headers = {"Authorization": "Bearer " + response.json()["access_token"]}
            user = client.get("/api/auth/me", headers=headers).json()
            organizations.append(user["organization_id"])
            return user, headers, response

        user, headers, response = register()
        yield app, client, user, headers, register, settings

    async def cleanup():
        database = Database(pg_url)
        async with database.sessions.begin() as session:
            await session.execute(delete(JobRecord).where(JobRecord.organization_id.in_(organizations)))
            ids = select(UserRecord.id).where(
                UserRecord.organization_id.in_(organizations)
            )
            await session.execute(
                delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id.in_(ids))
            )
            await session.execute(
                delete(UserRecord).where(UserRecord.organization_id.in_(organizations))
            )
            for model in [ActivityRecord, DispatchRecord, UnitRecord, IncidentRecord]:
                await session.execute(
                    delete(model).where(model.organization_id.in_(organizations))
                )
            await session.execute(
                delete(Organization).where(Organization.id.in_(organizations))
            )
        await database.close()

    asyncio.run(cleanup())


def database_run(url, operation):
    async def run():
        db = Database(url)
        try:
            async with db.sessions.begin() as session:
                return await operation(session)
        finally:
            await db.close()

    return asyncio.run(run())


def create_role(client, headers, role):
    email = uuid4().hex + "@example.com"
    response = client.post(
        "/api/users",
        headers=headers,
        json={"email": email, "display_name": role, "role": role, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return response.json(), {"Authorization": "Bearer " + login.json()["access_token"]}


def test_registration_password_hash_and_duplicate_rollback(auth_case, pg_url):
    _, client, user, headers, _, _ = auth_case
    assert user["role"] == "ADMIN" and user["is_active"]
    assert "password_hash" not in user

    async def inspect(session):
        row = await session.get(UserRecord, user["id"])
        assert row.password_hash.startswith("$argon2id$")
        assert verify_password(row.password_hash, PASSWORD)
        assert row.password_hash != PASSWORD
        return await session.scalar(select(func.count()).select_from(Organization))

    before = database_run(pg_url, inspect)
    duplicate = client.post(
        "/api/auth/register",
        json={
            "organization_name": "Must roll back",
            "display_name": "Duplicate",
            "email": " " + user["email"].upper() + " ",
            "password": PASSWORD,
        },
    )
    assert duplicate.status_code == 409
    assert database_run(pg_url, inspect) == before
    assert client.get("/api/auth/me", headers=headers).json()["id"] == user["id"]


def test_login_generic_failure_and_normalization(auth_case):
    _, client, user, _, _, _ = auth_case
    bad = client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "Wrong-Password-Long!"},
    )
    unknown = client.post(
        "/api/auth/login",
        json={"email": "unknown@example.com", "password": "Wrong-Password-Long!"},
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    result = client.post(
        "/api/auth/login",
        json={"email": " " + user["email"].upper() + " ", "password": PASSWORD},
    )
    assert result.status_code == 200
    cookie = result.headers["set-cookie"]
    assert (
        "HttpOnly" in cookie
        and "SameSite=strict" in cookie
        and "Path=/api/auth" in cookie
        and "Max-Age=" in cookie
        and "Secure" not in cookie
    )
    assert result.headers["cache-control"] == "no-store"


def test_auth_rate_limit_uses_redis_and_keeps_identity_failures_indistinguishable(
    auth_case, monkeypatch
):
    app, client, user, _, _, _ = auth_case
    limiter = app.state.auth_rate_limiter
    limiter.prefix = "test-auth-limit:" + uuid4().hex
    # Keep the Redis fixed-window key stable even if this test starts just
    # before a minute boundary and spends several seconds verifying passwords.
    monkeypatch.setattr(limiter, "clock", lambda: 1_700_000_000.0)

    for email in (user["email"], "unregistered@example.com"):
        for _ in range(10):
            response = client.post(
                "/api/auth/login",
                json={"email": email, "password": "Wrong-Password-Long!"},
            )
            assert response.status_code == 401
            assert response.json() == {"error": {"code": "HTTP_ERROR", "message": "Invalid credentials."}}
        limited = client.post(
            "/api/auth/login",
            json={"email": email, "password": "Wrong-Password-Long!"},
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.json()["error"]["message"] == "Too many authentication requests. Please try again later."

    redis = Redis.from_url(limiter.redis_url)

    async def redis_keys():
        try:
            return await redis.keys(limiter.prefix + "*")
        finally:
            await redis.aclose()

    keys = asyncio.run(redis_keys())
    assert keys
    assert all(user["email"].encode() not in key for key in keys)


def test_production_refresh_cookie_is_secure_and_development_cookie_is_not(
    auth_case, monkeypatch, pg_url
):
    _, _, user, _, _, settings = auth_case
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://testserver")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("ENABLE_HSTS", "true")
    production = create_app(
        database_url=pg_url,
        simulation_enabled=False,
        auth_settings=AuthSettings(settings.secret, environment="production"),
    )
    with TestClient(production, base_url="https://testserver") as client:
        response = client.post(
            "/api/auth/login", json={"email": user["email"], "password": PASSWORD}
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie
        assert "SameSite=strict" in cookie and "Path=/api/auth" in cookie
        assert "Max-Age=604800" in cookie
        assert response.headers["strict-transport-security"] == "max-age=31536000"
        logout = client.post("/api/auth/logout")
        assert logout.status_code == 204
        assert "Secure" in logout.headers["set-cookie"]


def test_refresh_hash_rotation_reuse_and_logout(auth_case, pg_url):
    _, client, user, _, _, _ = auth_case
    old = client.cookies.get("emergency_refresh")

    async def inspect(session):
        rows = list(
            await session.scalars(
                select(RefreshTokenRecord).where(
                    RefreshTokenRecord.user_id == user["id"]
                )
            )
        )
        assert all(row.token_hash != old and len(row.token_hash) == 64 for row in rows)
        return rows

    assert inspect
    assert any(
        row.token_hash == token_hash(old) for row in database_run(pg_url, inspect)
    )
    response = client.post("/api/auth/refresh")
    assert response.status_code == 200
    current = client.cookies.get("emergency_refresh")
    assert current != old
    rows = database_run(pg_url, inspect)
    previous = next(row for row in rows if row.token_hash == token_hash(old))
    assert previous.revoked_at and previous.replaced_by
    assert (
        client.post(
            "/api/auth/refresh", headers={"Cookie": "emergency_refresh=" + old}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/logout", headers={"Cookie": "emergency_refresh=" + current}
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/auth/refresh", headers={"Cookie": "emergency_refresh=" + current}
        ).status_code
        == 401
    )


def test_expired_refresh_rejected(auth_case, pg_url):
    _, client, user, _, _, _ = auth_case

    async def expire(session):
        await session.execute(
            update(RefreshTokenRecord)
            .where(RefreshTokenRecord.user_id == user["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    database_run(pg_url, expire)
    assert client.post("/api/auth/refresh").status_code == 401


@pytest.mark.parametrize(
    "kind",
    [
        "malformed",
        "expired",
        "wrong-type",
        "missing-exp",
        "wrong-signature",
        "none-algorithm",
    ],
)
def test_access_token_validation(auth_case, kind):
    _, client, user, _, _, settings = auth_case
    payload = {
        "sub": user["id"],
        "type": "access",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=1),
        "jti": uuid4().hex,
        "iss": "emergency-platform",
    }
    if kind == "expired":
        payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    if kind == "wrong-type":
        payload["type"] = "refresh"
    if kind == "missing-exp":
        payload.pop("exp")
    token = (
        "broken"
        if kind == "malformed"
        else jwt.encode(
            payload,
            (
                ""
                if kind == "none-algorithm"
                else ("wrong-" * 10 if kind == "wrong-signature" else settings.secret)
            ),
            algorithm="none" if kind == "none-algorithm" else "HS256",
        )
    )
    assert (
        client.get(
            "/api/incidents", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/state",
        "/api/incidents",
        "/api/units",
        "/api/dispatches",
        "/api/activity",
        "/api/users",
        "/api/auth/me",
        "/api/incidents/INC-001/nearby-units",
    ],
)
def test_anonymous_api_rejected(auth_case, path):
    _, client, _, _, _, _ = auth_case
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("role", ["ADMIN", "DISPATCHER", "RESPONDER"])
def test_role_matrix(auth_case, role):
    _, client, _, admin, _, _ = auth_case
    _, headers = create_role(client, admin, role)
    assert client.post("/api/demo/reset", headers=admin).status_code == 200
    for path in [
        "/api/incidents",
        "/api/units",
        "/api/activity",
        "/api/dispatches",
        "/api/incidents/INC-001/nearby-units",
    ]:
        assert client.get(path, headers=headers).status_code == 200
    write = role != "RESPONDER"
    assert client.post(
        "/api/incidents",
        headers=headers,
        json={
            "type": "medical",
            "severity": "high",
            "description": "Authorization check",
            "latitude": 1.3,
            "longitude": 103.85,
        },
    ).status_code == (201 if write else 403)
    assert client.patch(
        "/api/incidents/INC-001", headers=headers, json={"title": "Updated"}
    ).status_code == (200 if write else 403)
    assert client.post(
        "/api/dispatches",
        headers=headers,
        json={"incident_id": "INC-001", "unit_id": "FIRE-001"},
    ).status_code == (201 if write else 403)
    assert client.post(
        "/api/incidents/INC-005/resolve", headers=headers
    ).status_code == (200 if write else 403)
    assert client.get("/api/users", headers=headers).status_code == (
        200 if role == "ADMIN" else 403
    )
    assert client.post("/api/demo/reset", headers=headers).status_code == (
        200 if role == "ADMIN" else 403
    )


def test_current_database_role_and_deactivation(auth_case):
    _, client, _, admin, _, _ = auth_case
    user, headers = create_role(client, admin, "DISPATCHER")
    raw = client.cookies.get("emergency_refresh")
    assert (
        client.patch(
            "/api/users/" + user["id"] + "/role",
            headers=admin,
            json={"role": "RESPONDER"},
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "RESPONDER"
    assert (
        client.post(
            "/api/incidents",
            headers=headers,
            json={
                "type": "medical",
                "severity": "high",
                "description": "Now forbidden",
                "latitude": 1.3,
                "longitude": 103.85,
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/users/" + user["id"] + "/active",
            headers=admin,
            json={"is_active": False},
        ).status_code
        == 200
    )
    assert client.get("/api/state", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": user["email"], "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/refresh", headers={"Cookie": "emergency_refresh=" + raw}
        ).status_code
        == 401
    )


def test_last_admin_and_cross_tenant_user_management(auth_case):
    _, client, user, headers, register, _ = auth_case
    for suffix, body in [
        ("role", {"role": "RESPONDER"}),
        ("active", {"is_active": False}),
    ]:
        assert (
            client.patch(
                "/api/users/" + user["id"] + "/" + suffix, headers=headers, json=body
            ).status_code
            == 409
        )
    other, other_headers, _ = register()
    assert (
        client.patch(
            "/api/users/" + other["id"] + "/active",
            headers=headers,
            json={"is_active": False},
        ).status_code
        == 404
    )
    assert {u["id"] for u in client.get("/api/users", headers=headers).json()} == {
        user["id"]
    }
    assert (
        client.post(
            "/api/users",
            headers=headers,
            json={
                "email": "invalid@example.com",
                "password": PASSWORD,
                "display_name": "Invalid",
                "role": "SUPERADMIN",
            },
        ).status_code
        == 422
    )


def test_authenticated_cross_tenant_operations_and_socket(auth_case):
    _, client, user, a, register, _ = auth_case
    other, b, _ = register()
    assert client.post("/api/demo/reset", headers=a).status_code == 200
    assert client.post("/api/demo/reset", headers=b).status_code == 200
    created = client.post(
        "/api/incidents",
        headers=b,
        json={
            "type": "medical",
            "severity": "high",
            "description": "Private organization B",
            "latitude": 1.3,
            "longitude": 103.85,
        },
    ).json()
    dispatched = client.post(
        "/api/dispatches",
        headers=b,
        json={"incident_id": created["id"], "unit_id": "FIRE-001"},
    ).json()
    for path in [
        "/api/incidents/" + created["id"],
        "/api/incidents/" + created["id"] + "/nearby-units",
        "/api/dispatches/" + dispatched["id"],
    ]:
        assert client.get(path, headers=a).status_code == 404
    assert created["id"] not in {
        i["id"] for i in client.get("/api/incidents", headers=a).json()
    }
    assert created["id"] not in {
        i["entity_id"] for i in client.get("/api/activity", headers=a).json()
    }
    assert (
        client.post(
            "/api/dispatches",
            headers=a,
            json={"incident_id": created["id"], "unit_id": "AMB-001"},
        ).status_code
        == 404
    )
    forged = dict(a, **{"X-Organization-ID": other["organization_id"]})
    assert (
        client.get(
            "/api/auth/me?organization_id=" + other["organization_id"], headers=forged
        ).json()["organization_id"]
        == user["organization_id"]
    )
    with (
        client.websocket_connect("/ws/events") as wa,
        client.websocket_connect("/ws/events") as wb,
    ):
        wa.send_json({"type": "authenticate", "access_token": a["Authorization"][7:]})
        wb.send_json({"type": "authenticate", "access_token": b["Authorization"][7:]})
        assert wa.receive_json()["stream_id"] != wb.receive_json()["stream_id"]
        assert (
            client.patch(
                "/api/incidents/" + created["id"], headers=b, json={"title": "B event"}
            ).status_code
            == 200
        )
        assert wb.receive_json()["data"]["id"] == created["id"]
        assert (
            client.patch(
                "/api/incidents/INC-001", headers=a, json={"title": "A event"}
            ).status_code
            == 200
        )
        assert wa.receive_json()["data"]["id"] == "INC-001"


@pytest.mark.parametrize("kind", ["invalid", "expired", "missing"])
def test_socket_auth_rejections(auth_case, kind):
    _, client, user, _, _, settings = auth_case
    token = "invalid"
    if kind == "expired":
        token = jwt.encode(
            {
                "sub": user["id"],
                "type": "access",
                "iat": 0,
                "exp": 1,
                "jti": "expired",
                "iss": "emergency-platform",
            },
            settings.secret,
            algorithm="HS256",
        )
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/ws/events") as socket:
            socket.send_json(
                {"type": "authenticate", "access_token": token}
                if kind != "missing"
                else {"type": "hello"}
            )
            socket.receive_json()
    assert caught.value.code == 1008


def test_socket_deactivation_closes_existing_connection(auth_case):
    _, client, _, admin, _, _ = auth_case
    user, headers = create_role(client, admin, "RESPONDER")
    with client.websocket_connect("/ws/events") as socket:
        socket.send_json(
            {"type": "authenticate", "access_token": headers["Authorization"][7:]}
        )
        assert socket.receive_json()["type"] == "connection.ready"
        assert (
            client.patch(
                "/api/users/" + user["id"] + "/active",
                headers=admin,
                json={"is_active": False},
            ).status_code
            == 200
        )
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_production_reset_and_cookie_policy(auth_case):
    app, client, _, headers, _, settings = auth_case
    app.state.auth_settings = AuthSettings(settings.secret, environment="production")
    assert client.post("/api/demo/reset", headers=headers).status_code == 403
    assert client.get("/api/auth/me", headers=headers).json()["can_reset_demo"] is False
    assert "Secure" in client.post("/api/auth/refresh").headers["set-cookie"]


def test_cookie_endpoints_reject_foreign_origins(auth_case):
    _, client, _, _, _, _ = auth_case
    for path in ["login", "register", "refresh", "logout"]:
        response = client.post(
            "/api/auth/" + path, headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403


def test_concurrent_refresh_only_one_rotation(auth_case):
    _, client, _, _, _, _ = auth_case
    cookie = client.cookies.get("emergency_refresh")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post(
                    "/api/auth/refresh",
                    headers={"Cookie": "emergency_refresh=" + cookie},
                ).status_code,
                range(2),
            )
        )
    assert sorted(results) == [200, 401]


def test_concurrent_admin_demotion_preserves_an_admin(auth_case, pg_url):
    _, client, user, a, _, _ = auth_case
    second, b = create_role(client, a, "ADMIN")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                client.patch,
                "/api/users/" + target["id"] + "/role",
                headers=headers,
                json={"role": "RESPONDER"},
            )
            for target, headers in [(user, a), (second, b)]
        ]
        codes = [future.result().status_code for future in futures]
    assert sorted(codes) == [200, 409]

    async def count(session):
        return await session.scalar(
            select(func.count())
            .select_from(UserRecord)
            .where(
                UserRecord.organization_id == user["organization_id"],
                UserRecord.role == "ADMIN",
                UserRecord.is_active.is_(True),
            )
        )

    assert database_run(pg_url, count) == 1


def test_auth_schema_foreign_keys_and_indexes(auth_case, pg_url):
    async def inspect(session):
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0008_queue_observability"
        )
        names = set(
            (
                await session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            ).scalars()
        )
        assert {"users", "refresh_tokens"} <= names
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE contype='f' AND conrelid IN ('users'::regclass,'refresh_tokens'::regclass)"
                )
            )
            == 3
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE tablename IN ('users','refresh_tokens')"
                )
            )
            >= 7
        )

    database_run(pg_url, inspect)


def test_security_logs_do_not_contain_credentials(auth_case, caplog):
    _, client, user, headers, _, settings = auth_case
    import logging

    caplog.set_level(logging.INFO)
    response = client.post(
        "/api/auth/login", json={"email": user["email"], "password": PASSWORD}
    )
    raw = client.cookies.get("emergency_refresh")
    token = response.json()["access_token"]
    client.post(
        "/api/auth/login",
        json={"email": user["email"], "password": "Incorrect-Password!"},
    )
    client.post(
        "/api/auth/refresh",
        headers={"Cookie": "emergency_refresh=invalid-sensitive-refresh"},
    )
    logs = caplog.text
    assert (
        "Login succeeded" in logs
        and "Login failed" in logs
        and "Refresh failed" in logs
    )
    for secret in [
        PASSWORD,
        "Incorrect-Password!",
        raw,
        token,
        settings.secret,
        "invalid-sensitive-refresh",
        "Authorization",
    ]:
        assert secret not in logs


def test_socket_expiry_closes_an_established_connection(auth_case):
    _, client, user, _, _, settings = auth_case
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": user["id"],
            "type": "access",
            "iat": now,
            "exp": now + timedelta(seconds=2),
            "jti": uuid4().hex,
            "iss": "emergency-platform",
        },
        settings.secret,
        algorithm="HS256",
    )
    with client.websocket_connect("/ws/events") as socket:
        socket.send_json({"type": "authenticate", "access_token": token})
        assert socket.receive_json()["type"] == "connection.ready"
        with pytest.raises(WebSocketDisconnect) as caught:
            socket.receive_json()
        assert caught.value.code == 1008


def test_public_registration_cannot_join_existing_tenant(auth_case):
    _, client, user, _, _, _ = auth_case
    response = client.post(
        "/api/auth/register",
        json={
            "organization_name": "Forbidden joining",
            "organization_id": user["organization_id"],
            "display_name": "Join attempt",
            "email": "join@example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 422
