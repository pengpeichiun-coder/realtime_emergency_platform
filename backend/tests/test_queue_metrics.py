from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import delete, text, update

from app.db.models import JobRecord, OutboxEvent
from app.observability import queue_metrics
from app.workers.heartbeat import PublisherHealth
from test_auth import auth_case, database_run
from test_postgres import pg_url

pytestmark = pytest.mark.postgres


def values(families):
    return {sample.name: sample.value for family in families for sample in family.samples}


def test_durable_age_scope_backoff_terminal_and_recovery(auth_case, pg_url):
    _, client, first_user, headers, register, _ = auth_case
    second_user, _, _ = register()
    now = datetime.now(UTC)
    identifiers = []

    async def seed(session):
        for user, state, seconds in [
            (first_user, "QUEUED", 120), (second_user, "RUNNING", 600),
            (first_user, "QUEUED", -3600), (first_user, "SUCCEEDED", 9000),
            (second_user, "FAILED", 9000),
        ]:
            identifier = uuid4().hex
            identifiers.append(identifier)
            session.add(JobRecord(
                id=identifier, organization_id=user["organization_id"],
                created_by_user_id=user["id"], job_type="INCIDENT_REPORT", status=state,
                input_payload={}, celery_task_id=identifier,
                created_at=now - timedelta(days=7),
                next_attempt_at=now - timedelta(seconds=seconds),
            ))
        for user, processed in [(first_user, False), (second_user, False), (first_user, True)]:
            session.add(OutboxEvent(
                id=uuid4().hex, organization_id=user["organization_id"],
                event_type="incident.created", payload={}, subscription_ids=[],
                created_at=now - timedelta(seconds=900 if not processed else 9000),
                processed_at=now if processed else None,
            ))

    async def recover(session):
        await session.execute(update(JobRecord).where(JobRecord.id.in_(identifiers[:2])).values(status="SUCCEEDED"))
        await session.execute(update(OutboxEvent).where(
            OutboxEvent.organization_id.in_([first_user["organization_id"], second_user["organization_id"]])
        ).values(processed_at=now))

    async def scenario(session):
        # These metrics aggregate the whole deployment. Earlier integration
        # fixtures can leave demo outbox rows. Isolate this test's global state
        # inside a rollback-only transaction in the guarded disposable test DB.
        try:
            await session.execute(delete(JobRecord))
            await session.execute(delete(OutboxEvent))
            await seed(session)
            await session.flush()
            observed = values(await queue_metrics(session))
            assert 600 <= observed["background_job_oldest_pending_age_seconds"] < 615
            assert 900 <= observed["outbox_oldest_pending_age_seconds"] < 915
            assert observed["outbox_pending_events"] == 2
            await recover(session)
            assert values(await queue_metrics(session)) == {
                "background_job_oldest_pending_age_seconds": 0,
                "outbox_oldest_pending_age_seconds": 0, "outbox_pending_events": 0,
            }
        finally:
            await session.rollback()

    database_run(pg_url, scenario)


def test_metrics_endpoint_aggregates_without_leaking_tenant_data(auth_case, monkeypatch):
    _, client, user, headers, _, _ = auth_case
    monkeypatch.setenv("METRICS_TOKEN", "test-metrics-only")
    monkeypatch.setattr("app.observability.read_heartbeat", AsyncMock(
        return_value=PublisherHealth(False, False, 0)
    ))
    assert client.get("/metrics", headers=headers).status_code == 404
    response = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-only"})
    assert response.status_code == 200  # Redis failure is visible without breaking DB metrics.
    assert user["organization_id"] not in response.text and user["email"] not in response.text
    families = list(text_string_to_metric_families(response.text))
    names = {"background_job_oldest_pending_age_seconds", "outbox_oldest_pending_age_seconds",
             "outbox_pending_events", "outbox_publisher_heartbeat_age_seconds",
             "outbox_publisher_heartbeat_present", "publisher_redis_available"}
    samples = [sample for family in families for sample in family.samples if sample.name in names]
    assert {sample.name for sample in samples} == names
    assert all(not sample.labels for sample in samples)
    assert values(families)["publisher_redis_available"] == 0


def test_queue_indexes_cover_due_lookups_and_pending_outbox(pg_url):
    async def inspect(session):
        definitions = dict((await session.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('jobs','outbox_events')"
        ))).all())
        assert "(status, next_attempt_at)" in definitions["ix_jobs_pending"]
        assert "(created_at) WHERE (processed_at IS NULL)" in definitions["ix_outbox_pending_created"]
        # Tiny fixtures may rightly favor a sequential scan. Check that the
        # real SQL predicates have a bounded indexed path, without asserting
        # a cost or which equivalent index wins for the current statistics.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        for query, indexes in [
            ("SELECT min(next_attempt_at) FROM jobs WHERE status='QUEUED' AND next_attempt_at <= now()", {"ix_jobs_pending"}),
            ("SELECT count(*), min(created_at) FROM outbox_events WHERE processed_at IS NULL",
             {"ix_outbox_pending_created", "ix_outbox_events_processed_at"}),
        ]:
            plan = "\n".join((await session.scalars(text("EXPLAIN " + query))).all())
            assert any(index in plan for index in indexes), plan
            assert "Seq Scan" not in plan, plan
            if "ix_outbox_events_processed_at" in plan:
                # On a small pending set the existing processed_at index can
                # be cheaper. It must still restrict the scan to pending rows.
                assert "Index Cond: (processed_at IS NULL)" in plan, plan
    database_run(pg_url, inspect)
