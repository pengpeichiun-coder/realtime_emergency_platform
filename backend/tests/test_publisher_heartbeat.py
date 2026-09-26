import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError

from app.observability import publisher_metrics
from app.workers import heartbeat, publisher


@pytest.mark.parametrize("result,expected", [
    ([1, 2], heartbeat.PublisherHealth(True, True, 2)),
    ([1, 80], heartbeat.PublisherHealth(True, True, 80)),
    ([0, 0], heartbeat.PublisherHealth(True, False, 0)),
    (ConnectionError("private Redis address"), heartbeat.PublisherHealth(False, False, 0)),
])
def test_read_heartbeat_distinguishes_missing_stale_and_unavailable(monkeypatch, result, expected):
    client = AsyncMock()
    if isinstance(result, Exception):
        client.eval.side_effect = result
    else:
        client.eval.return_value = result
    monkeypatch.setattr(heartbeat, "heartbeat_client", lambda: client)
    assert asyncio.run(heartbeat.read_heartbeat()) == expected
    client.aclose.assert_awaited_once()


def test_heartbeat_deadline_and_client_cleanup(monkeypatch):
    client = AsyncMock()

    async def blocked(*args):
        await asyncio.sleep(30)

    client.eval.side_effect = blocked
    monkeypatch.setattr(heartbeat, "heartbeat_client", lambda: client)
    assert asyncio.run(heartbeat.read_heartbeat()) == heartbeat.PublisherHealth(False, False, 0)
    client.aclose.assert_awaited_once()


@pytest.mark.parametrize("failure", [None, "publish_jobs", "materialize_outbox", "publish_deliveries"])
def test_only_completed_publisher_cycles_refresh_heartbeat(monkeypatch, failure):
    calls = []

    def step(name):
        async def run(*args):
            calls.append(name)
            if name == failure:
                raise ConnectionError("test-only failure")
        return run

    for name in ("publish_jobs", "materialize_outbox", "publish_deliveries", "write_heartbeat"):
        monkeypatch.setattr(publisher, name, step(name))
    if failure:
        with pytest.raises(ConnectionError):
            asyncio.run(publisher.publish_cycle(object(), object()))
        assert "write_heartbeat" not in calls
    else:
        asyncio.run(publisher.publish_cycle(object(), object()))
        assert calls == ["publish_jobs", "materialize_outbox", "publish_deliveries", "write_heartbeat"]


def test_real_redis_heartbeat_clock_expiry_and_malformed_value(monkeypatch):
    # A private key in the existing test Redis; never touch the publisher's key.
    key = "test:publisher:heartbeat:" + uuid4().hex
    monkeypatch.setattr(heartbeat, "HEARTBEAT_KEY", key)

    async def scenario():
        client = heartbeat.heartbeat_client()
        try:
            assert await heartbeat.read_heartbeat() == heartbeat.PublisherHealth(True, False, 0)
            await heartbeat.write_heartbeat(client)
            result = await heartbeat.read_heartbeat()
            assert result.redis_available and result.present and 0 <= result.age_seconds < 5
            assert 3500 < await client.ttl(key) <= 3600
            now = (await client.time())[0]
            await client.set(key, str(now - 120))
            assert (await heartbeat.read_heartbeat()).age_seconds >= 120
            for invalid in ("not-a-timestamp", str(now + 3600)):
                await client.set(key, invalid)
                assert await heartbeat.read_heartbeat() == heartbeat.PublisherHealth(True, False, 0)
        finally:
            await client.delete(key)
            await client.aclose()

    asyncio.run(scenario())


def test_publisher_metrics_have_no_identity_labels(monkeypatch):
    monkeypatch.setattr(
        "app.observability.read_heartbeat",
        AsyncMock(return_value=heartbeat.PublisherHealth(True, False, 0)),
    )
    values = {sample.name: sample.value for family in asyncio.run(publisher_metrics())
              for sample in family.samples if not sample.labels}
    assert values == {"publisher_redis_available": 1,
                      "outbox_publisher_heartbeat_present": 0,
                      "outbox_publisher_heartbeat_age_seconds": 0}
