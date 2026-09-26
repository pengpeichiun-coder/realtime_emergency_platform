"""Redis-backed, privacy-preserving authentication request limits.

Redis is authoritative while reachable. On a Redis outage, a bounded process-local
fixed-window limiter remains active and emits a metric and rate-limited warning.
The fallback is deliberately documented as weaker across API replicas.
"""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import logging
import math
import os
import time

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.observability import AUTH_RATE_LIMIT_DEGRADED, AUTH_RATE_LIMIT_REJECTIONS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowLimit:
    dimension: str
    limit: int
    seconds: int


# The identity bucket stops distributed password guessing; the IP bucket bounds
# sprays across many identities. Registration is separately stricter per email.
POLICIES = {
    "login": (
        WindowLimit("ip", 60, 60),
        WindowLimit("identity", 10, 60),
    ),
    "register": (
        WindowLimit("ip", 20, 3600),
        WindowLimit("identity", 3, 3600),
    ),
    "refresh": (
        WindowLimit("ip", 60, 60),
        WindowLimit("credential", 20, 60),
    ),
}


_ATOMIC_INCREMENT = """
local rejected = 0
local retry_after = 0
for i = 1, #KEYS do
    local count = redis.call('INCR', KEYS[i])
    local window = tonumber(ARGV[(i - 1) * 2 + 2])
    if count == 1 then redis.call('EXPIRE', KEYS[i], window + 1) end
    if count > tonumber(ARGV[(i - 1) * 2 + 1]) then
        rejected = 1
        local ttl = redis.call('TTL', KEYS[i])
        if ttl > retry_after then retry_after = ttl end
    end
end
return {rejected, retry_after}
"""


class AuthRateLimiter:
    def __init__(
        self,
        redis_url: str | None = None,
        *,
        pepper: str,
        prefix: str = "emergency:auth-limit:v1",
        policies: dict[str, tuple[WindowLimit, ...]] | None = None,
        timeout_seconds: float = 0.3,
        clock=time.time,
    ):
        self.redis_url = redis_url or os.environ.get("REDIS_URL")
        self.pepper = pepper.encode("utf-8")
        self.prefix = prefix
        self.policies = policies or POLICIES
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.redis: Redis | None = None
        self._fallback: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._last_warning = 0.0

    def _key(self, endpoint: str, rule: WindowLimit, raw_value: str, now: float):
        window = int(now) // rule.seconds
        value = hmac.new(
            self.pepper,
            f"{endpoint}:{rule.dimension}:{raw_value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{self.prefix}:{endpoint}:{rule.dimension}:{window}:{value}"

    def _local_check(self, keys, rules, now):
        monotonic_now = time.monotonic()
        rejected = False
        retry_after = 1
        for key, rule in zip(keys, rules, strict=True):
            count, expires = self._fallback.get(key, (0, monotonic_now + rule.seconds))
            count += 1
            self._fallback[key] = (count, expires)
            self._fallback.move_to_end(key)
            if count > rule.limit:
                rejected = True
                retry_after = max(retry_after, math.ceil(expires - monotonic_now))

        # Counters are bounded even under a spray of unique email addresses.
        if len(self._fallback) > 10_000:
            for key, (_, expires) in list(self._fallback.items()):
                if expires <= monotonic_now:
                    self._fallback.pop(key, None)
            while len(self._fallback) > 10_000:
                self._fallback.popitem(last=False)
        return rejected, retry_after

    async def check(self, endpoint: str, client_ip: str, identity: str | None = None):
        try:
            rules = self.policies[endpoint]
        except KeyError as exc:
            raise ValueError("Unknown authentication rate-limit scope") from exc
        now = self.clock()
        keys = []
        applicable = []
        for rule in rules:
            value = client_ip if rule.dimension == "ip" else identity
            if not value:
                continue
            keys.append(self._key(endpoint, rule, value, now))
            applicable.append(rule)
        if not applicable:
            return

        try:
            if not self.redis_url:
                raise RedisError("Redis URL is not configured")
            if self.redis is None:
                self.redis = Redis.from_url(
                    self.redis_url,
                    socket_connect_timeout=self.timeout_seconds,
                    socket_timeout=self.timeout_seconds,
                    retry_on_timeout=False,
                )
            arguments = []
            for rule in applicable:
                arguments.extend((rule.limit, rule.seconds))
            result = await asyncio.wait_for(
                self.redis.eval(_ATOMIC_INCREMENT, len(keys), *keys, *arguments),
                timeout=self.timeout_seconds,
            )
            rejected, retry_after = bool(result[0]), max(1, int(result[1]))
        except (RedisError, OSError, TimeoutError, asyncio.TimeoutError):
            AUTH_RATE_LIMIT_DEGRADED.inc()
            self._warn_degraded()
            rejected, retry_after = self._local_check(keys, applicable, now)

        if rejected:
            AUTH_RATE_LIMIT_REJECTIONS.labels(endpoint).inc()
            raise HTTPException(
                status_code=429,
                detail="Too many authentication requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    def _warn_degraded(self):
        now = time.monotonic()
        if now - self._last_warning >= 30:
            logger.warning("Authentication rate limiting uses process-local fallback")
            self._last_warning = now

    async def close(self):
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None


def trusted_client_ip(request: Request) -> str:
    """Resolve client IP only through explicitly configured proxy CIDRs.

    The default is the transport peer. Forwarded headers from any other peer are
    ignored, preventing callers from selecting their own rate-limit identity.
    """
    peer = request.client.host if request.client else "unknown"
    networks = request.app.state.trusted_proxy_networks
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_address in network for network in networks):
        return peer_address.compressed

    forwarded = request.headers.get("x-forwarded-for", "")
    chain = []
    for part in forwarded.split(","):
        try:
            chain.append(ipaddress.ip_address(part.strip()))
        except ValueError:
            return peer_address.compressed
    # ALB appends the actual socket peer to a caller-supplied chain. Walk from
    # the trusted transport inward; the first untrusted hop is the client.
    for address in reversed([*chain, peer_address]):
        if not any(address in network for network in networks):
            return address.compressed
    return chain[0].compressed if chain else peer_address.compressed


async def enforce_auth_rate_limit(request: Request, endpoint: str, identity: str | None):
    limiter: AuthRateLimiter | None = request.app.state.auth_rate_limiter
    if limiter is None:
        raise HTTPException(503, "Authentication protection is unavailable.")
    await limiter.check(endpoint, trusted_client_ip(request), identity)
