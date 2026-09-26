# Distributed realtime and observability

PostgreSQL remains authoritative. A tenant's stream ID and sequence are stored on
its organization row (migration `0006_realtime`). Existing organization transaction
locks serialize domain mutations and cursor changes atomically. A repeatable-read
REST snapshot includes the cursor for precisely that state, across API restarts.
After commit, the API publishes one internal envelope containing the trusted
organization ID and validated events to Redis `emergency:realtime:v1`. Every API,
including the origin, fans out only through its subscriber to its local tenant
connections. This avoids duplicate origin delivery. Browsers cannot publish.

Redis Pub/Sub is **not durable**. A process can die between commit and publish;
concurrent processes can publish out of commit order. Sequence gaps trigger the
existing REST snapshot resync, and reconnect always resyncs. Subscriber failure
closes sockets with 1013; recovery invalidates old connections too. This provides
best-effort low latency, not guaranteed delivery or an event archive. Durable
webhooks continue using the separate PostgreSQL outbox. A final lost event with
no subsequent traffic requires reconnect/manual refresh to become visible.

Each active demo dispatch holds a dedicated PostgreSQL advisory-lock connection
using NullPool, separate from the business pool, so two API processes do not both
own the same simulation. A five-second recovery scan resumes unfinished dispatches
after owner shutdown. This is a demonstration simulator, not a fleet telemetry
service; its dedicated connections must be included in the database connection
budget. The Redis test seam is explicit in `create_app(realtime_enabled=False)`;
the deployed module always enables the backplane.

## Run and validate

```bash
# From the repository root; select your Docker context with DOCKER_CONTEXT if needed.
docker compose --profile distributed up -d --build
backend/.venv/bin/python scripts/distributed_smoke.py
backend/.venv/bin/python scripts/distributed_smoke.py --outage
```

The optional second API listens only on localhost:8002; the primary is behind
localhost:8080. The outage test stops **this Compose project's Redis**, checks
live=200/ready=503, commits a fictional incident during the gap, restores Redis in
a finally block, reconnects, and proves REST recovers the missing durable state.
Do not run outage validation against shared/production environments.

## Health, logs, correlation and metrics

`/health` stays compatible; `/health/live` tests the event loop. `/health/ready`
checks PostgreSQL, Redis PING and the subscriber under bounded deadlines. Optional
LLM availability is not a readiness dependency. Container mode `LOG_FORMAT=json`
emits timestamp/level/logger/message and available request, organization, user,
job, delivery, event, attempt and state identifiers. Request IDs accept only 1–64
ASCII alphanumeric/underscore/hyphen characters; invalid values get a UUID. The
`X-Request-ID` response header also covers error responses. Jobs/outbox/deliveries
persist correlation through worker execution. No headers or payloads are logged;
JSON exception logging retains only the exception class, not SQL values/tracebacks.

`GET /metrics` requires `Authorization: Bearer <METRICS_TOKEN>`, otherwise 404.
Keep it private; it exposes cross-tenant aggregate operational statistics. There
is no public reverse-proxy metrics route. HTTP count/status/latency use route
templates; socket connection/disconnection and subscription recovery counters are
per API process. Durable job counts (including both AI types), mean last-attempt
job durations, delivery counts/retries and mean end-to-end delivery latency are
queried from PostgreSQL. These retained-record **gauges** survive workers but
change on retention/deletion; do not sum them across API replicas. They are not
attempt histograms or a queue depth estimate. No user/tenant/incident/URL labels.
Use one Uvicorn process per container; scale containers for correct process metrics.

## Bounded local load measurements — 2026-09-22

Measured on this Apple Silicon workstation with a dedicated Colima VM: 2 CPUs,
4 GiB RAM; application images ARM64, PostGIS image AMD64 emulated. k6 scripts use
the explicit demo tenant and its public demo credentials. Override `BASE_URL`,
`LOAD_EMAIL`, `LOAD_PASSWORD` for an isolated test environment. Never target live
emergency data. Install k6, explicitly seed demo accounts, then:

```bash
k6 run load/http.js
k6 run load/websocket.js
k6 run load/jobs.js
```

| Scenario | Concurrency / duration | Volume | Median / p90 / p95 HTTP latency | Errors |
| --- | --- | --- | --- | --- |
| Read-heavy | 5 VUs, 15 s | 306 requests, 61 iterations | 21.34 / 149.92 / 218.80 ms | 0% |
| Realtime/reconnect | 3 VUs, 12 s | 15 sessions, 105 messages, 16 HTTP calls | 55.50 / 83.74 / 129.68 ms | 0%; all 61 checks pass |
| Concurrent reports | 5 VUs, one report each, 3.7 s elapsed | 5 completed jobs, 31 HTTP calls | 54.19 / 116.36 / 139.16 ms | 0%; all 21 checks pass |

WebSocket connection p95 was 12.68 ms. Every session authenticated, received an
actual domain update, and reconnected; updates touched only INC-001. Each report
had one durable execution and a matching job ID/result. Separate two-tenant smoke
and PostgreSQL tests enforce isolation. These are local benchmark measurements,
**not production capacity guarantees**. Results are not a sustained soak test.

The k6 socket timeout API follows the [official k6 documentation](https://grafana.com/docs/k6/latest/using-k6/protocols/websockets/).
