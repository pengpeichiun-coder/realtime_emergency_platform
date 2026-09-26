# Background business jobs

The API returns 202 after committing a tenant-owned job. ADMIN/DISPATCHER may
request incident reports; all authenticated roles may read their organization's
jobs/results. Known foreign job/incident IDs return 404. The report deterministically
collects the incident, dispatches, related units and complete incident activity in
a repeatable-read transaction. It never changes operational state.

`jobs` stores QUEUED/RUNNING/SUCCEEDED/FAILED, creator, input/result JSONB, safe errors,
attempt count, Celery task ID, timestamps and publication/retry scheduling. PostgreSQL
is authoritative; Redis result storage is not used for business state.

From the repository root, run these alongside the existing backend/frontend:

```bash
cd backend
source .venv/bin/activate
python -m app.workers.publisher
# Separate terminal, same backend directory and virtual environment:
celery -A app.workers.celery_app:celery_app worker --pool=solo --concurrency=1 --loglevel=INFO
```

The standalone publisher polls every second and republishes unfinished due work
if it has not been sent for 30 seconds. A broker failure leaves the committed row
available for recovery; a crash after send can duplicate publication. A worker
holds a session-level PostgreSQL advisory lock for the job ID, commits RUNNING
before work, and ignores duplicate or terminal work. A process crash releases the
lock and the publisher recovers the stranded job. Three durable attempts bound
recovery even if Celery's own retry counter is reset by redelivery.

Only timeouts, connection failures and recognized transient PostgreSQL SQLSTATEs
retry, after 2 then 4 seconds. Permanent failures persist immediately; unexpected
failures use a generic safe message. Stack traces are not exposed. Each task uses
one fresh asyncio loop and database engine, disposes it in finally, and never
reuses request sessions or an engine across event loops. The advisory connection
is separate from short business transactions. No simulator work moved to Celery.

Celery 5.6.3 uses JSON only, UTC, late acknowledgement, worker-lost rejection,
prefetch 1 and the `emergency-business` queue. Redis 8.10.2 is a local Homebrew
service; REDIS_URL/CELERY_BROKER_URL use database 0 and CELERY_RESULT_BACKEND uses
1. Keep Redis local/private; these defaults have no authentication.

Validated with a real solo worker, publisher and Redis: 202/QUEUED → SUCCEEDED,
repeat delivery no-op, real PostgreSQL statement timeout → second-attempt success,
and missing input → FAILED. Unit/integration tests cover all states, RBAC, tenant
isolation, attempt limits, concurrency fencing and publication recovery. Celery
success only means task handling completed; inspect PostgreSQL for business status.

Single-process WebSockets remain unchanged. Publication is at-least-once, not
exactly-once. Jobs/history are not automatically pruned; API lists the latest 100.
The solo pool is the validated local macOS mode, not a multi-host deployment claim.

The API exposes ADMIN-only POST /api/jobs/{job_id}/retry for terminal failures. It
resets only that job's current attempt generation, lets the existing publisher
requeue the same durable job ID, and records the operator in
operation_retry_audits. Requests repeated while queued/running are idempotent;
succeeded jobs cannot be replayed. Manual requeues are capped at three per job.

References: [Celery task semantics](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
and [configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html).
