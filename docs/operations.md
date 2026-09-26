# Operational recovery

This runbook applies to the local Compose stack and its fictional/demo data. It is
not an AWS incident-response policy or a production availability commitment.
Run commands from the repository root. Select the Docker context for the intended
Compose project with `DOCKER_CONTEXT` if it differs from your configured context.

## Database backup and restore

The backup utility uses PostgreSQL's custom archive format and the local Compose
PostgreSQL tools. It stores the archive outside the repository with mode `0600`
and writes a neighboring SHA-256/version manifest. It never overwrites either
path. The Compose bootstrap database role is used because the narrower runtime
role cannot read PostGIS-owned support tables. Do not copy the archive into Git
or an unencrypted public location.

```bash
mkdir -p "$HOME/emergency-platform-backups"
python3 scripts/backup_database.py \
  "$HOME/emergency-platform-backups/emergency-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Restore always targets a database name that does not exist. The script verifies
the manifest, archive checksum and archive index before creating it; on a restore
error it leaves the partial target intact for inspection and never drops it.

```bash
python3 scripts/restore_database.py \
  "$HOME/emergency-platform-backups/emergency-YYYYMMDDTHHMMSSZ.dump" \
  emergency_restore_check

# Apply the existing role split to the restored database. This does not migrate
# or change the source database.
docker compose run --rm --no-deps \
  --entrypoint python db-roles -m app.scripts.bootstrap_database \
  --initialize-role --database emergency_restore_check

# Verify using the API image's runtime DATABASE_URL and SQLAlchemy models.
docker compose exec -T api \
  python -m app.scripts.verify_restore emergency_restore_check
```

`verify_restore` expects a representative demo database. It checks organizations,
users, incidents, units, dispatches, activity, jobs, webhook subscriptions,
outbox rows, deliveries and AI artifacts, then executes a nearby-unit
`ST_DWithin` query through the runtime role. It also verifies geography columns,
GiST indexes, the PostGIS extension, migration revision and that the runtime role
cannot create objects in `public`.

For a fresh checkout, run a successful backup at least daily if you want a local
RPO assumption of at most one day; the repository does not schedule or monitor
backups, so this is an operator target, not a guarantee. The RTO assumption is
the observed time to create a fresh database, restore the archive and validate
the application data. The drill timing is recorded in `docs/validation.md`.
There is no WAL archiving, point-in-time recovery, offsite copy, backup encryption
or cloud snapshot automation in this phase.

## API availability and HTTP errors

Check `docker compose ps` and `/health/live`, then `/health/ready` on each API.
Readiness checks PostgreSQL, Redis and the realtime subscription. `/metrics` also
needs PostgreSQL; a failed scrape can mean a stopped API, a database outage or an
incorrect metrics token, not a uniquely identified database failure. Confirm the
scrape credential from its secret file without printing it, and inspect bounded,
correlated API logs (`docker compose logs --tail=200 api api2`). Do not use
`curl -v` with a bearer token. Continue with the [database](#database-unavailable)
or [Redis](#redis-unavailable) runbook when dependency checks identify the cause.
For sustained `/api/*` 5xx, compare the affected route templates and request IDs
against the last deployment and dependency status; avoid blind service restarts.

## Database unavailable

```bash
docker compose ps
docker compose logs --tail=100 postgres api
docker compose exec -T postgres pg_isready -h 127.0.0.1 -U emergency -d emergency_platform
```

Keep the application stopped if the database reports recovery or storage errors;
do not delete its volume. `/health/live` is process health, while `/health/ready`
checks PostgreSQL and Redis. During a database outage, operational commands must
fail with a server error rather than report a committed result. After PostgreSQL
is healthy, verify API readiness and read a known record before resuming operator
work. The API pool reconnects on subsequent requests; a restart is a fallback
only after the database is healthy.

## Redis unavailable

```bash
docker compose logs --tail=100 redis api worker publisher
docker compose exec -T redis redis-cli ping
docker compose start redis
```

Expected behavior: API liveness remains healthy, readiness becomes unhealthy,
authenticated realtime subscribers reconnect after Redis returns, and REST
snapshots repair missed Pub/Sub messages. PostgreSQL remains authoritative for
jobs, webhook intents and operational data. Authentication rate limiting falls
back to a bounded per-process limiter and exposes a degradation metric. No
PostgreSQL state should be reset to fix a Redis failure.

## Celery backlog or worker unavailable

```bash
docker compose ps worker publisher redis
docker compose logs --tail=200 worker publisher
docker compose exec -T worker celery -A app.workers.celery_app:celery_app inspect ping --timeout=5
```

Inspect durable job state through the ADMIN/dispatcher Jobs view or
`GET /api/jobs`; Redis task results are not the source of truth. The publisher
republishes due `QUEUED`/stranded `RUNNING` jobs and pending webhook deliveries.
Restart a failed worker with `docker compose up -d worker`. A permanently failed
job or webhook delivery can be requeued by an ADMIN after correcting the cause:

```text
POST /api/jobs/{job_id}/retry
POST /api/webhook-deliveries/{delivery_id}/retry
GET  /api/operation-retries
```

Manual retry is limited to three attempts per operation and is recorded with
tenant, operator, operation ID, retry generation and request time. Repeating a
request after it has moved to a queued/running state is idempotent. Succeeded
operations cannot be replayed through this API. A webhook receiver should still
deduplicate the stable delivery ID: network delivery is at-least-once.

## Outbound webhook failure

Review the subscription health and `GET /api/webhook-deliveries` as an ADMIN.
The delivery record contains safe status/attempt information, not response bodies,
credentials or raw exceptions. Automatic retry is bounded to three attempts for
timeouts, connection failures and HTTP 408/429/5xx; ordinary 4xx and unsafe
destinations are terminal. Restore receiver availability/configuration, then
requeue a failed delivery with the ADMIN endpoint above. Do not edit an old
delivery payload or reset its event ID.

## Publisher and outbox recovery

Check `publisher_redis_available` first. With value `1`, a missing heartbeat or
age over 30s means no recent successful publisher cycle. Inspect
`docker compose ps publisher` and `docker compose logs --tail=100 publisher`,
then check PostgreSQL, broker connectivity and migration state before restarting
the publisher. With Redis availability `0`, use [Redis recovery](#redis-unavailable);
heartbeat age is unknown, not evidence that the publisher process is dead.

`outbox_pending_events` counts committed events that have not yet become delivery
records. When the publisher recovers, this count and its oldest age should fall;
check delivery history separately for HTTP success. Do not delete outbox rows or
manually emit replacement domain events. If the publisher heartbeat is healthy
but job age grows, follow [worker recovery](#celery-backlog-or-worker-unavailable).

## Migration failure and rollback decision

Read the migration container logs and inspect `alembic current`/`alembic heads`
before retrying. Take a backup before any manual database repair. Do not blindly
run a downgrade against operational data. Prefer rolling the application image
back only when the current schema remains compatible with that image; otherwise
fix forward with an additive migration.

Use expand/contract for future changes:

1. Add nullable columns/tables/indexes without removing the old contract.
2. Deploy code that can read/write both old and new forms.
3. Backfill in bounded, restartable batches where required.
4. Switch reads/writes, observe and validate.
5. Remove obsolete schema only in a later deployment after old code is gone.

### ECS application rollback

For a failed AWS rollout, first let the ECS deployment circuit breaker restore
the previous stable task definition. Confirm every service's running task
definition and desired/running counts, then inspect API, migration, worker and
publisher CloudWatch logs before retrying. The release image tag is immutable
(`sha-<full commit>`), so identify the last known-good commit tag and task
definition revisions from the deployment record. If the circuit breaker did not
recover the prior revision, update each affected service to its recorded previous
task-definition ARN and wait for stability:

```bash
aws ecs update-service --cluster <cluster> --service <service> \
  --task-definition <previous-task-definition-arn>
aws ecs wait services-stable --cluster <cluster> --services <service>
```

Repeat for frontend, API, worker and publisher using the matching prior task
definitions, then verify health, job processing and tenant-scoped WebSockets.
Do not substitute an arbitrary older image tag while leaving newer task
definitions/configuration in place. The regular deployment always runs
`alembic upgrade head` before updating services; it does not downgrade schema.
If a migration removed or changed a contract needed by old code, application
rollback is unsafe: take/verify a backup, follow the expand/contract recovery
plan and fix forward or restore only under the approved data-recovery procedure.

No schema change is made solely to demonstrate this strategy.

## Secret exposure or rotation

Limit access to logs and backup archives. Revoke or rotate the exposed credential
at its issuer, update the ignored local `.env`/secret store, and restart only the
affected service. A JWT signing-key change invalidates outstanding access tokens.
Preserve the Fernet webhook encryption key: changing it without re-encrypting
stored subscription secrets makes those integrations unusable. There is no
automated Fernet-key rotation tool yet. Rotate PostgreSQL role passwords with the
explicit database-role bootstrap, then update the matching deployment secret
before replacing service tasks. Redis has no local AUTH password.

## Project SLIs

These are observable project indicators, not production SLOs or contractual SLAs:

| Indicator | Existing evidence | Interpretation |
| --- | --- | --- |
| HTTP availability | `http_requests_total{route,status}` | 5xx rate by route template; exclude 4xx client errors. |
| HTTP latency | `http_request_duration_seconds{method,route}` | Histogram supports latency percentiles per route. |
| Background work completion | `background_jobs{job_type,status}` | Retained PostgreSQL job count by current state, not a time-window success rate. |
| Webhook completion | `webhook_deliveries{status}` and `webhook_retry_attempts{status}` | Retained delivery state and retry counts; history retention affects totals. |
| WebSocket load | `websocket_connections_active` | Active connections per API process; aggregate across replicas externally. |
| Publisher/worker health | Durable job/outbox ages and Redis publisher heartbeat | Detect overdue work and missing successful publication cycles; inspect process/broker health for the cause. |

Metrics are aggregate and intentionally omit user, tenant, incident, raw URL and
token labels. HTTP routes are templates. The repository supplies alert rules but
has no production Alertmanager, durable SLI history or external telemetry backend.
The optional local profile below rehearses routing only.

## Operational alerts (Phases 25 and 28)

`monitoring/alerts.yml` contains nine **repository-ready** definitions. They have
not been deployed to production. The optional Prometheus/Alertmanager profile uses
only a local test receiver; normal application startup is unchanged. A deployment
operator must supply a scraper, evaluation,
retention, credential and notification routing appropriate to the environment.

| Alert | Condition / meaning | First action and runbook |
| --- | --- | --- |
| `EmergencyAPIScrapeUnavailable` | Any configured API target has `up == 0` for 2m. | Verify process, metrics credential and database via [API availability](#api-availability-and-http-errors). |
| `EmergencyAPIReadinessFailure` | `/health/ready` 503 counter keeps increasing over 2m windows for 3m. | Check PostgreSQL, Redis and subscription connection via [API availability](#api-availability-and-http-errors). |
| `EmergencyAPIHigh5xxRate` | `/api/*` 5xx ratio above 5%, with at least 20 requests in 5m, sustained 5m. | Inspect request-correlated logs and dependencies via [API availability](#api-availability-and-http-errors). |
| `EmergencyRedisRateLimitDegraded` | `auth_rate_limit_fallback_total` continues increasing over 2m windows for 3m. | Restore shared Redis protection using [Redis unavailable](#redis-unavailable). |
| `EmergencyBackgroundJobFailures` | Retained `background_jobs{status="FAILED"}` stays positive for 5m, by bounded job type. | Inspect safe job error; repair cause before ADMIN retry via [Celery recovery](#celery-backlog-or-worker-unavailable). Includes report, incident summary and shift handover jobs. |
| `EmergencyWebhookDeliveryFailures` | Retained `webhook_deliveries{status="FAILED"}` stays positive for 5m. | Review delivery error and receiver, then use [webhook recovery](#outbound-webhook-failure). |
| `EmergencyBackgroundJobQueueStalled` | Oldest due QUEUED/RUNNING job is over 300s old for 2m. | Check worker/publisher/broker using [Celery recovery](#celery-backlog-or-worker-unavailable). |
| `EmergencyOutboxBacklogStalled` | Oldest unprocessed outbox event is over 60s old for 2m. | Inspect [publisher and outbox recovery](#publisher-and-outbox-recovery). |
| `EmergencyOutboxPublisherUnavailable` | Redis is reachable on the same scrape target, but heartbeat is absent or over 30s old for 2m. | Inspect process, database and broker using [publisher recovery](#publisher-and-outbox-recovery). |

The HTTP 5%/20-request threshold is an initial noise guard, **not a measured SLO**.
Tune it with actual traffic/error history before paging. Readiness and scrape
checks cover low-traffic outages that a ratio gate intentionally does not. A
single transient clears before the hold duration. Failed business records remain
visible until corrected/retried; accepting an unrecoverable historical failure
requires an operator silence with context, not deleting audit history.

Scrape each API process once with `job="emergency-api"` and a bounded `cluster`
label (one value per deployment). Use a 15–30s scrape interval, a 5s timeout,
30s evaluation, and a protected bearer credential file containing `METRICS_TOKEN`.
The application `/metrics` route requires the token and must remain private.
Example for an operator-managed scraper on the Compose network:

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s
rule_files: [/etc/prometheus/alerts.yml]
scrape_configs:
  - job_name: emergency-api
    metrics_path: /metrics
    scrape_timeout: 5s
    authorization:
      type: Bearer
      credentials_file: /run/secrets/emergency_metrics_token
    static_configs:
      - targets: [api:8000, api2:8000]
        labels:
          cluster: local
```

Remove `api2` when not using the distributed profile. Prometheus attaches the
`instance` target label, but rules aggregate it out; alert labels contain only
cluster, scrape job, bounded job type, severity and service. Do not add IDs,
emails, IPs, tokens or arbitrary URLs as alert dimensions. If a target is removed
from discovery entirely, `up` cannot detect it; supervise expected target inventory
and the monitoring process itself externally.

Readiness requires active `/health/ready` probes at least every 30s on **each** API
instance, including unhealthy instances. Existing Compose health checks and ALB
target checks provide this; scraping `/metrics` alone does not issue a readiness
probe. An idle service without probes cannot produce readiness failures. An auth
fallback alert similarly needs auth traffic. When PostgreSQL is unavailable,
metrics may return 503 too; the scrape-unavailable rule covers that blind spot.

The actual metrics inventory and limits are:

- HTTP counters/histogram: `http_requests_total`,
  `http_request_duration_seconds`; readiness is the existing HTTP counter's
  `/health/ready` route, not an invented gauge.
- WebSocket counters/gauge: `websocket_connections_total`,
  `websocket_disconnects_total`, `websocket_connections_active`,
  `realtime_subscription_reconnects_total`. Reconnects count **recoveries**;
  disconnects alone cannot distinguish normal client departures from failures.
- Jobs: `background_jobs{job_type,status}` and
  `background_job_duration_seconds` are retained-state gauges, the latter a mean
  last-attempt duration. Job types cover deterministic reports and both AI
  workflows; no AI provider input/output enters labels.
- Webhooks: `webhook_deliveries`, `webhook_retry_attempts`,
  `webhook_delivery_latency_seconds` are retained-state gauges, not counters or
  windowed success rates.
- Auth: `auth_login_attempts_total`, `auth_rate_limit_rejections_total`,
  `auth_rate_limit_fallback_total`, `auth_refresh_failures_total`,
  `websocket_auth_rejections_total` use bounded outcome/endpoint/reason labels.
- Queue: `background_job_oldest_pending_age_seconds` is PostgreSQL time minus the
  earliest **due `next_attempt_at`** among QUEUED/RUNNING jobs. Terminal jobs and
  future backoff are excluded. An ADMIN retry resets that due time, so an old
  creation date cannot cause an immediate false stall. Empty queues emit zero.
  Reuse `background_jobs{status=~"QUEUED|RUNNING"}` for depth; it includes future
  retries, unlike the age metric. Deduplicate each type/status with `max` before
  summing across those bounded dimensions.
- Outbox: `outbox_pending_events` counts `processed_at IS NULL`;
  `outbox_oldest_pending_age_seconds` measures age from `created_at`, zero when
  empty. Processing means delivery records were durably materialized, **not**
  that receivers accepted HTTP requests. Existing delivery metrics cover that.
- Publisher: after jobs, outbox and delivery publication all succeed, the process
  writes Redis key `emergency:publisher:heartbeat:v1`, with a one-hour TTL. Both
  timestamp and age use Redis TIME, avoiding host clock skew. A shared key means
  at least one publisher completed a cycle; it is not per-process supervision.
  `publisher_redis_available` is a bounded Redis probe, and
  `outbox_publisher_heartbeat_present` distinguishes a valid timestamp from a
  missing/expired/malformed one. `outbox_publisher_heartbeat_age_seconds` is zero
  when absent/unavailable; interpret it with both health gauges. Redis errors
  yield availability zero within one second and do not fail otherwise healthy
  database metrics. No tenant, destination, job, event or process IDs are labels.

Database-derived gauges are shared across replicas: use `max`, not `sum`, to
deduplicate them, and never `rate`/`increase` on these gauges. Counter rates are
computed before aggregating independent API processes. Time-windowed AI outcome
metrics and production alert delivery remain separate work.

The publisher polls roughly once per second, republishes due records after 30s,
and processes bounded batches (50 jobs, 100 events, 50 deliveries). The job
threshold allows the 300s broker visibility interval before declaring a sustained
stall; outbox and heartbeat thresholds allow many normal polling cycles. These
are initial operational noise guards, not throughput promises or production SLOs.

Job-age queries perform two indexed MIN lookups using `ix_jobs_pending`, not a
scan of completed history. Migration `0008_queue_observability` adds partial
`ix_outbox_pending_created` for unprocessed events, serving both polling order
and aggregate count/min queries. Count cost scales with the **current pending**
backlog. Existing retained job/delivery count and duration gauges still aggregate
retained history; measure their cost and establish retention before large-scale
deployment. No additional cache or summary table was introduced without evidence
that its complexity is warranted. `/metrics` retains its overall three-second
deadline and private bearer-token requirement.

Validation (promtool 3.15.0; official checksum verified in CI):

```bash
make alerts-check  # Or PROMTOOL=/path/to/promtool
backend/.venv/bin/python scripts/alert_failure_smoke.py
backend/.venv/bin/python scripts/queue_observability_smoke.py
```

Promtool tests cover healthy, sustained, transient, recovery, replica deduplication
and low-traffic cases. The smoke uses a migrated local `_test` database from
`TEST_DATABASE_URL`, starts its own Redis/API on free loopback ports with fresh
ephemeral credentials and disables movement simulation. It stops only its Redis
child, verifies readiness 503 and a +3 auth fallback counter, restores readiness
200 and stable counters, then stops both children. It never resets the database
or stops a shared service. Do not run it concurrently with tests resetting that
same test database. Alert hold durations are validated by promtool's synthetic
timeline, not by claiming a live alert fired during the short smoke.

The queue smoke creates a new randomly named local `_test` database using the
connection credentials from `TEST_DATABASE_URL` (requires local CREATE DATABASE),
migrates it, and starts only its own Redis/API/publisher/worker/HTTP receiver. It
registers a fictional tenant, stops the publisher until its real heartbeat is
stale, creates an incident/outbox event and report without a worker, then verifies
heartbeat recovery, materialization, a real signed delivery and report completion.
It drops only that generated database and stops only its own children in `finally`;
the configured test and development databases are never changed.

The existing `infrastructure` required check downloads a fixed, SHA-256-verified
promtool/amtool and runs syntax, rule tests and monitoring configuration checks.
No sixth branch check is required.
See [Prometheus rule testing](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
for the fixture format.

## Optional local monitoring and alert routing

**No production alert receiver is configured.** `compose.monitoring.yaml` is an
optional override with the `monitoring` profile. `docker compose up` continues to
use only `compose.yaml`. The monitoring profile starts the second API, Prometheus
3.15.0, Alertmanager 0.34.1 and a development-only Python webhook receiver. It
allows the internal `api`/`api2` Host names explicitly, preserving other configured
allowed hosts. It does not add a wildcard host or expose an unauthenticated
application metrics endpoint.

```bash
make monitoring-up
# Equivalent: docker compose -f compose.yaml -f compose.monitoring.yaml --profile monitoring up --build -d
make monitoring-validate  # Requires promtool 3.15.0 and amtool 0.34.1 on PATH.
make alert-rehearsal
```

Override `COMPOSE` to select an existing Docker context, for example
`make alert-rehearsal COMPOSE='docker --context colima-emergency-platform compose'`.
Use `PROMTOOL=/path/to/promtool AMTOOL=/path/to/amtool` for separately installed
tools. Monitoring tooling versions/checksums are manually reviewed with the
maintenance pins; Dependabot currently only updates the allowed Redis Compose
service. The official [Prometheus config](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
and [Alertmanager webhook config](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config)
describe the supported file-backed credentials and firing/resolved protocol.

Default local endpoints (all host bindings explicitly use `127.0.0.1`):

| Endpoint | Inspection |
| --- | --- |
| `http://127.0.0.1:9090` | Prometheus UI; `/targets` should show **two** healthy API processes; `/rules` should show nine normal rules. |
| `http://127.0.0.1:9093` | Alertmanager UI; `/api/v2/alerts` shows routed active alerts. |
| `http://127.0.0.1:9095/events` | Last 100 allowlisted alert name/status/labels/annotations held in receiver memory. `/health` verifies connectivity. |

`PROMETHEUS_PORT`, `ALERTMANAGER_PORT`, `ALERT_RECEIVER_PORT` are optional local
port overrides. No Slack, PagerDuty, SMTP or other external integration is
configured. Alerts carry only the existing bounded deployment/job/severity labels;
the receiver discards unapproved metadata and never stores request headers or
raw bodies. It is a test tool, not an authenticated production receiver.

A one-shot `monitoring-token` container copies the existing `METRICS_TOKEN` to a
dedicated Docker volume. Only that initializer and Prometheus mount it; Prometheus
mounts it read-only. The token is never interpolated into Prometheus YAML, URLs,
labels or command arguments. The file is readable by Prometheus's unprivileged
UID inside this isolated volume; it is not a public host file. Docker daemon
access already grants access to application environment secrets. Recreate the
initializer and Prometheus after rotating the local token.

Prometheus scrapes **each API once**. API process counters remain independent;
database/Redis gauges appear twice and alert expressions use `max` to deduplicate.
The ordinary profile loads the original 30s/2m publisher thresholds and all nine
rules, with 15s scrapes. Local Prometheus history retains one day; receiver history
is bounded and disappears on restart. Neither is a production SLI archive.

`make alert-rehearsal` uses a fresh, non-overridable UUID project name, random
loopback ports, temporary generated credentials and its own PostGIS/Redis volumes.
It builds the current backend/PostGIS, starts two APIs and a real publisher, then
checks both scrape targets, loaded rules and Alertmanager health. A **test-only**
rule uses age >10s for 10s with 5s scraping and 2s evaluation. Stopping the publisher must
produce a Prometheus firing alert, an Alertmanager active alert and a receiver
firing notification; restarting must clear the alert and produce a resolved
notification. The normal rules are not edited. The script uses bounded waits and
removes only its own Compose project/volumes in `finally`. Fake LLM mode is forced
and no provider request or paid external service is involved. Both initial health
and recovery require two successful scrape targets and fresh heartbeats sustained
for ten seconds; disappearing metrics alone cannot pass the recovery check.

Temporary configuration lives under the ignored `.local/monitoring-rehearsal/`
directory so Colima can bind-mount it from the shared checkout. Each run's private
directory and credentials are removed on exit. Cold amd64 PostGIS initialization
and Python probes can take several minutes on a small Apple Silicon Docker VM;
the disposable rehearsal allows up to ten minutes for startup and gives those
probes a longer process budget. It keeps the actual readiness checks and normal
application health-check settings unchanged. Startup failures include redacted
logs before cleanup; a timeout is a failed rehearsal, not an alert-delivery pass.

To stop monitoring in a manually started development stack while keeping the app:

```bash
docker compose -f compose.yaml -f compose.monitoring.yaml --profile monitoring stop prometheus alertmanager alert-receiver
```

Do not run `down --volumes` against an application project to stop monitoring; that
would also remove its PostgreSQL volume. The automated rehearsal's volume cleanup
is safe because it creates a separate project exclusively for that invocation.

CI validates the Prometheus config, normal/rehearsal rules, Alertmanager config
and receiver/policy tests under existing checks. It uses a temporary **validation
fixture** token, never a live credential. The longer actual alert-delivery
rehearsal stays an explicit local/manual command.
