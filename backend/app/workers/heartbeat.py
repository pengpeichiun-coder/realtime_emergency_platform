"""One deployment-wide heartbeat, written only after a successful publisher cycle."""

import asyncio
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.workers.config import REDIS_URL

HEARTBEAT_KEY = "emergency:publisher:heartbeat:v1"
# Expiration prevents a removed deployment leaving a permanent key. Missing and
# stale both indicate publisher trouble when Redis is reachable.
HEARTBEAT_TTL_SECONDS = 3600
WRITE_HEARTBEAT = """
local now = redis.call('TIME')[1]
redis.call('SET', KEYS[1], now, 'EX', ARGV[1])
return 1
"""
READ_HEARTBEAT = """
local now = tonumber(redis.call('TIME')[1])
local last = tonumber(redis.call('GET', KEYS[1]))
if not last or last > now then return {0, 0} end
return {1, now - last}
"""


@dataclass(frozen=True)
class PublisherHealth:
    redis_available: bool
    present: bool
    age_seconds: float


def heartbeat_client():
    return Redis.from_url(
        REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5,
        retry_on_timeout=False,
    )


async def write_heartbeat(client):
    # Redis supplies both write/read clocks, avoiding skew between API/publisher.
    await client.eval(WRITE_HEARTBEAT, 1, HEARTBEAT_KEY, HEARTBEAT_TTL_SECONDS)


async def read_heartbeat():
    client = heartbeat_client()
    try:
        try:
            async with asyncio.timeout(1):
                present, age = await client.eval(READ_HEARTBEAT, 1, HEARTBEAT_KEY)
        except (RedisError, OSError, TimeoutError):
            # Unknown heartbeat age must not be mistaken for a dead publisher.
            return PublisherHealth(False, False, 0)
        return PublisherHealth(True, bool(present), float(age))
    finally:
        await client.aclose()
