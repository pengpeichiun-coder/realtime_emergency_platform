import asyncio
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import select, update
from app.db.models import JobRecord, IncidentRecord
from app.db.session import Database
from app.workers.tasks.jobs import execute_job
from app.workers.publisher import publish_jobs
from app.workers.reports import PermanentJobError
from test_auth import auth_case, create_role, database_run
from test_postgres import pg_url

pytestmark = pytest.mark.postgres


def create(client, headers):
    assert client.post("/api/demo/reset", headers=headers).status_code == 200
    response = client.post("/api/incidents/INC-001/reports", headers=headers)
    assert response.status_code == 202
    return response.json()["job_id"]


def test_report_api_result_and_duplicate_execution(auth_case, pg_url):
    _, client, _, headers, _, _ = auth_case
    identifier = create(client, headers)
    assert (
        client.get("/api/jobs/" + identifier, headers=headers).json()["status"]
        == "QUEUED"
    )
    asyncio.run(execute_job(identifier, pg_url))
    first = client.get("/api/jobs/" + identifier, headers=headers).json()
    assert first["status"] == "SUCCEEDED" and first["attempt_count"] == 1
    assert first["result_payload"]["incident"]["id"] == "INC-001"
    assert {"incident", "dispatches", "units", "activity"} <= first[
        "result_payload"
    ].keys()
    asyncio.run(execute_job(identifier, pg_url))
    assert client.get("/api/jobs/" + identifier, headers=headers).json() == first
    assert identifier in {
        j["id"] for j in client.get("/api/jobs", headers=headers).json()
    }


@pytest.mark.parametrize("role", ["ADMIN", "DISPATCHER", "RESPONDER"])
def test_job_rbac(auth_case, role):
    _, client, _, admin, _, _ = auth_case
    identifier = create(client, admin)
    _, headers = create_role(client, admin, role)
    assert client.get("/api/jobs/" + identifier, headers=headers).status_code == 200
    assert client.post(
        "/api/incidents/INC-001/reports", headers=headers
    ).status_code == (403 if role == "RESPONDER" else 202)
    assert client.get("/api/jobs").status_code == 401


def test_job_tenant_isolation(auth_case):
    _, client, _, headers, register, _ = auth_case
    identifier = create(client, headers)
    _, other, _ = register()
    assert client.get("/api/jobs/" + identifier, headers=other).status_code == 404
    assert client.get("/api/jobs", headers=other).json() == []
    assert (
        client.post("/api/incidents/INC-001/reports", headers=other).status_code == 404
    )


@pytest.mark.parametrize("transient", [True, False])
def test_job_failure_retry_and_attempt_limit(auth_case, pg_url, transient):
    _, client, _, headers, _, _ = auth_case
    identifier = create(client, headers)

    async def fail(db, job):
        raise (
            TimeoutError("secret diagnostic")
            if transient
            else PermanentJobError("UNAVAILABLE", "Report input is unavailable.")
        )

    for attempt in range(1, 4 if transient else 2):
        delay = asyncio.run(execute_job(identifier, pg_url, handler=fail))
        row = client.get("/api/jobs/" + identifier, headers=headers).json()
        assert row["attempt_count"] == attempt
        assert "secret diagnostic" not in str(row)
        if transient and attempt < 3:
            assert row["status"] == "QUEUED" and delay == 2**attempt

            async def due(session):
                await session.execute(
                    update(JobRecord)
                    .where(JobRecord.id == identifier)
                    .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
                )

            database_run(pg_url, due)
        else:
            assert row["status"] == "FAILED" and delay is None


def test_admin_job_retry_is_tenant_scoped_audited_bounded_and_idempotent(
    auth_case, pg_url
):
    _, client, user, admin, register, _ = auth_case
    identifier = create(client, admin)
    _, dispatcher = create_role(client, admin, "DISPATCHER")
    _, responder = create_role(client, admin, "RESPONDER")
    _, foreign_admin, _ = register()

    async def mark_failed(session):
        row = await session.get(JobRecord, identifier)
        row.status = "FAILED"
        row.attempt_count = 3
        row.error_code = "JOB_FAILED"
        row.error_message = "The background operation could not complete."
        row.completed_at = datetime.now(UTC)

    assert client.post(f"/api/jobs/{identifier}/retry", headers=dispatcher).status_code == 403
    assert client.post(f"/api/jobs/{identifier}/retry", headers=responder).status_code == 403
    assert client.post(
        f"/api/jobs/{identifier}/retry", headers=foreign_admin
    ).status_code == 404

    for generation in range(1, 4):
        database_run(pg_url, mark_failed)
        response = client.post(f"/api/jobs/{identifier}/retry", headers=admin)
        assert response.status_code == 202
        assert response.json()["status"] == "QUEUED"
        assert response.json()["attempt_count"] == 0
        # A repeated request after the transition is idempotent and adds no audit row.
        assert client.post(
            f"/api/jobs/{identifier}/retry", headers=admin
        ).status_code == 202
        audits = client.get("/api/operation-retries", headers=admin)
        assert audits.status_code == 200
        rows = [
            row
            for row in audits.json()
            if row["operation_type"] == "JOB" and row["operation_id"] == identifier
        ]
        assert sorted(row["retry_count"] for row in rows) == list(
            range(1, generation + 1)
        )
        assert all(row["requested_by_user_id"] == user["id"] for row in rows)

    database_run(pg_url, mark_failed)
    assert client.post(f"/api/jobs/{identifier}/retry", headers=admin).status_code == 409

    succeeded = create(client, admin)
    asyncio.run(execute_job(succeeded, pg_url))
    assert client.post(f"/api/jobs/{succeeded}/retry", headers=admin).status_code == 409


def test_job_transient_then_success_and_loop_lifecycle(auth_case, pg_url):
    _, client, _, headers, _, _ = auth_case
    identifier = create(client, headers)

    async def fail(db, job):
        raise ConnectionError()

    asyncio.run(execute_job(identifier, pg_url, handler=fail))

    async def due(session):
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == identifier)
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    database_run(pg_url, due)
    # Separate asyncio.run invocations prove no engine/session crosses loops.
    asyncio.run(execute_job(identifier, pg_url))
    result = client.get("/api/jobs/" + identifier, headers=headers).json()
    assert result["status"] == "SUCCEEDED" and result["attempt_count"] == 2


def test_job_running_visible_and_concurrent_duplicate_fenced(auth_case, pg_url):
    _, client, _, headers, _, _ = auth_case
    identifier = create(client, headers)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler(db, job):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return {"checked": True}

        task = asyncio.create_task(execute_job(identifier, pg_url, handler=handler))
        await entered.wait()
        db = Database(pg_url)
        try:
            async with db.sessions() as session:
                assert (await session.get(JobRecord, identifier)).status == "RUNNING"
            await execute_job(identifier, pg_url, handler=handler)
            assert calls == 1
        finally:
            release.set()
            await task
            await db.close()

    asyncio.run(scenario())


def test_durable_publication_recovers_broker_failure(auth_case, pg_url):
    _, client, _, headers, _, _ = auth_case
    identifier = create(client, headers)

    async def scenario():
        db = Database(pg_url)
        try:

            def unavailable(_):
                raise ConnectionError()

            with pytest.raises(ConnectionError):
                await publish_jobs(db, unavailable)
            async with db.sessions() as session:
                assert (
                    await session.get(JobRecord, identifier)
                ).last_enqueued_at is None
            sent = []
            await publish_jobs(db, sent.append)
            assert identifier in sent
            sent.clear()
            await publish_jobs(db, sent.append)
            assert identifier not in sent
        finally:
            await db.close()

    asyncio.run(scenario())
