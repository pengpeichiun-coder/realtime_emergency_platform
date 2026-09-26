"""Low-cardinality metrics and allowlisted correlation logging; never log payloads."""

import contextvars
from copy import copy
from datetime import UTC, datetime
import json
import logging
import os
import re
import time
from uuid import uuid4
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    CollectorRegistry,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import select, func, extract
from app.db.models import JobRecord, OutboxEvent, WebhookDelivery
from app.workers.heartbeat import read_heartbeat

request_id_var = contextvars.ContextVar("request_id", default=None)
identity_var = contextvars.ContextVar("identity", default={})
registry = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Completed HTTP requests",
    ["method", "route", "status"],
    registry=registry,
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP response latency",
    ["method", "route"],
    registry=registry,
)
WS_ACTIVE = Gauge(
    "websocket_connections_active",
    "Active local WebSocket connections",
    registry=registry,
)
WS_OPENED = Counter(
    "websocket_connections_total",
    "Accepted authenticated WebSocket connections",
    registry=registry,
)
WS_CLOSED = Counter(
    "websocket_disconnects_total",
    "Disconnected WebSocket connections",
    registry=registry,
)
BUS_RECONNECTS = Counter(
    "realtime_subscription_reconnects_total",
    "Redis subscription recoveries",
    registry=registry,
)
AUTH_LOGIN_ATTEMPTS = Counter(
    "auth_login_attempts_total",
    "Completed password login attempts",
    ["result"],
    registry=registry,
)
AUTH_RATE_LIMIT_REJECTIONS = Counter(
    "auth_rate_limit_rejections_total",
    "Rejected authentication requests",
    ["endpoint"],
    registry=registry,
)
AUTH_RATE_LIMIT_DEGRADED = Counter(
    "auth_rate_limit_fallback_total",
    "Authentication checks handled by the process-local fallback during Redis outages",
    registry=registry,
)
AUTH_REFRESH_FAILURES = Counter(
    "auth_refresh_failures_total",
    "Refresh-cookie rotations rejected as invalid or expired",
    registry=registry,
)
WS_AUTH_REJECTIONS = Counter(
    "websocket_auth_rejections_total",
    "Rejected WebSocket authentication handshakes",
    ["reason"],
    registry=registry,
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = {**identity_var.get(), "request_id": request_id_var.get()}
        for key in [
            "request_id",
            "organization_id",
            "user_id",
            "job_id",
            "job_type",
            "delivery_id",
            "event_type",
            "attempt",
            "status",
        ]:
            value = getattr(record, key, fields.get(key))
            if value is not None:
                result[key] = value
        # Exception bodies/SQL parameters may contain credentials; retain only class.
        if record.exc_info:
            result["error_type"] = record.exc_info[0].__name__
        return json.dumps(result, ensure_ascii=False)


class SafeTextFormatter(logging.Formatter):
    """Keep local readable logs from formatting exception messages or SQL URLs."""

    def format(self, record):
        error_type = record.exc_info[0].__name__ if record.exc_info else None
        safe_record = copy(record)
        safe_record.exc_info = None
        safe_record.exc_text = None
        result = super().format(safe_record)
        return f"{result} error_type={error_type}" if error_type else result


def configure_logging():
    logging.basicConfig(level=logging.INFO)
    handler = logging.StreamHandler()
    root = logging.getLogger()
    if os.environ.get("LOG_FORMAT", "text") == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(SafeTextFormatter("%(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ["uvicorn", "uvicorn.error", "celery", "celery.task"]:
        child = logging.getLogger(name)
        child.handlers = []
        child.propagate = True
    # Middleware logs route templates, not raw URLs/query strings.
    logging.getLogger("uvicorn.access").disabled = True


class RequestObservability:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = (
            dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("latin-1")
        )
        identifier = (
            incoming if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", incoming) else uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = identifier
        token = request_id_var.set(identifier)
        identity_token = identity_var.set({})
        started = time.monotonic()
        status = 500

        async def respond(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", identifier.encode())
                ]
            await send(message)

        try:
            await self.app(scope, receive, respond)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope.get("method", "OTHER")
            if method not in {
                "GET",
                "POST",
                "PATCH",
                "DELETE",
                "PUT",
                "OPTIONS",
                "HEAD",
            }:
                method = "OTHER"
            HTTP_REQUESTS.labels(method, route, str(status)).inc()
            HTTP_LATENCY.labels(method, route).observe(time.monotonic() - started)
            logging.getLogger("app.http").info(
                "HTTP request completed", extra={"status": status}
            )
            request_id_var.reset(token)
            identity_var.reset(identity_token)


async def queue_metrics(session):
    # Two bounded MIN lookups use (status, next_attempt_at), even when completed
    # history is large. Future backoff and terminal jobs are not actionable.
    oldest = func.least(*(
        select(func.min(JobRecord.next_attempt_at)).where(
            JobRecord.status == status, JobRecord.next_attempt_at <= func.now()
        ).scalar_subquery()
        for status in ("QUEUED", "RUNNING")
    ))
    job_age = await session.scalar(select(
        func.greatest(0, extract("epoch", func.now() - oldest))
    ))
    count, outbox_age = (await session.execute(select(
        func.count(),
        func.greatest(0, extract("epoch", func.now() - func.min(OutboxEvent.created_at))),
    ).where(OutboxEvent.processed_at.is_(None)))).one()
    return [
        GaugeMetricFamily(
            "background_job_oldest_pending_age_seconds",
            "Age since the oldest QUEUED/RUNNING job became due; zero when empty",
            value=float(job_age),
        ),
        GaugeMetricFamily(
            "outbox_oldest_pending_age_seconds",
            "Age of the oldest event not yet materialized into deliveries; zero when empty",
            value=float(outbox_age),
        ),
        GaugeMetricFamily(
            "outbox_pending_events", "Events not yet materialized into deliveries", value=count,
        ),
    ]


async def publisher_metrics():
    health = await read_heartbeat()
    return [
        GaugeMetricFamily(
            "publisher_redis_available", "Redis heartbeat store is reachable", value=health.redis_available,
        ),
        GaugeMetricFamily(
            "outbox_publisher_heartbeat_present",
            "A valid heartbeat exists; meaningful only when publisher_redis_available is one",
            value=health.present,
        ),
        GaugeMetricFamily(
            "outbox_publisher_heartbeat_age_seconds",
            "Seconds since the last successful cycle; zero if missing or Redis unavailable",
            value=health.age_seconds,
        ),
    ]


async def metrics(database):
    output = generate_latest(registry)
    if database is None:
        return output
    # Durable business metrics work across Celery processes/restarts. These are
    # current retained-record gauges, not falsely process-local task counters.
    durable = CollectorRegistry()
    families = []
    async with database.sessions() as session:
        families.extend(await queue_metrics(session))
        jobs = await session.execute(
            select(
                JobRecord.job_type,
                JobRecord.status,
                func.count(),
                func.avg(
                    extract("epoch", JobRecord.completed_at - JobRecord.started_at)
                ),
            ).group_by(JobRecord.job_type, JobRecord.status)
        )
        counts = GaugeMetricFamily(
            "background_jobs",
            "Retained jobs by type/state",
            labels=["job_type", "status"],
        )
        duration = GaugeMetricFamily(
            "background_job_duration_seconds",
            "Mean retained last-attempt duration",
            labels=["job_type", "status"],
        )
        for kind, status, count, average in jobs:
            kind = (
                kind
                if kind in {"INCIDENT_REPORT", "INCIDENT_SUMMARY", "SHIFT_HANDOVER"}
                else "OTHER"
            )
            counts.add_metric([kind, status], count)
            if average is not None:
                duration.add_metric([kind, status], float(average))
        families.extend([counts, duration])
        deliveries = await session.execute(
            select(
                WebhookDelivery.status,
                func.count(),
                func.sum(func.greatest(WebhookDelivery.attempt_count - 1, 0)),
                func.avg(
                    extract(
                        "epoch",
                        WebhookDelivery.delivered_at - WebhookDelivery.created_at,
                    )
                ),
            ).group_by(WebhookDelivery.status)
        )
        counts = GaugeMetricFamily(
            "webhook_deliveries", "Retained deliveries by state", labels=["status"]
        )
        retries = GaugeMetricFamily(
            "webhook_retry_attempts",
            "Retained attempts after first delivery attempt",
            labels=["status"],
        )
        latency = GaugeMetricFamily(
            "webhook_delivery_latency_seconds",
            "Mean end-to-end successful delivery latency",
            labels=["status"],
        )
        for status, count, attempts, average in deliveries:
            counts.add_metric([status], count)
            retries.add_metric([status], attempts or 0)
            if average is not None:
                latency.add_metric([status], float(average))
        families.extend([counts, retries, latency])

    families.extend(await publisher_metrics())

    class Collector:
        def collect(self):
            return iter(families)

    durable.register(Collector())
    return output + generate_latest(durable)
