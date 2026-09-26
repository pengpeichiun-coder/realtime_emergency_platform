# System design

This document describes the implemented portfolio system and its real operational guarantees. The [architecture diagram](architecture.md) is the single component diagram; this page focuses on decisions, consistency, and failure behavior. The application uses fictional data and is not an emergency dispatch service.

## Requirements and product boundaries

The system supports organizations that manage incidents, response units, dispatch assignments, operational activity, and a live command view. Operators need a consistent view of current server state, nearby-unit lookup, durable reports, signed third-party notifications, and advisory summaries. Administrators manage users and webhook subscriptions; dispatchers can operate incidents; responders are read-only.

The current implementation is a modular monolith with separate API, publisher, and Celery worker processes. It is not decomposed into microservices. There is no claim of a production SLO, automatic disaster failover, regulated-data readiness, or real-world dispatch safety.

## Domain and persistence model

An `Organization` owns users, incidents, units, dispatches, activity, background jobs, webhook subscriptions/deliveries, outbox events, AI artifacts, and retry audits. Composite tenant keys and foreign keys keep operational relationships inside the organization. The API derives organization scope from the active database user and never accepts a tenant selector from request JSON or headers.

PostgreSQL is the business source of truth. It stores UTC-aware timestamps, JSONB job/event payloads, and the lifecycle for durable workflows. PostGIS stores incident/unit positions as `GEOGRAPHY(Point, 4326)` with GiST indexes. Nearby lookup is a bounded SQL `ST_DWithin`/`ST_Distance` query, so distance is measured in meters and sorted in the database; it is straight-line distance, not road ETA.

## REST, WebSocket, and consistency

REST handles authentication, commands, reads, snapshots, and job status/results. Mutating endpoints validate the transition and commit related entity/activity changes in one database transaction. A successful HTTP response represents durable PostgreSQL state.

Authenticated WebSockets carry typed events for low-latency UI updates. Each organization has a PostgreSQL-backed stream ID and monotonically increasing sequence. Redis Pub/Sub fans events between API processes but is intentionally best-effort; it is not an event store. The React client buffers events while loading a REST snapshot, rejects duplicates/older events, and requests a fresh snapshot when it detects a sequence gap or stream restart. This trades durable event replay for a simple recoverable view of current state.

## Transactions and duplicate dispatch prevention

A write locks the tenant's organization row first, then affected records, and commits incident/unit/dispatch/activity together. A partial unique index prevents more than one active dispatch for an organization/unit or organization/incident, even if an application path bypasses service-level validation. Composite foreign keys prevent cross-tenant assignments. Independent API instances therefore cannot successfully claim the same unit for competing active dispatches.

The organization row lock is deliberately coarse: it makes reset, ordering, and concurrent dispatch behavior easier to reason about but serializes writes within one tenant. It is a known throughput limit. Read snapshots use short read-only transactions. No database row-level security is configured; tenant isolation depends on the shared request context and scoped repositories plus relational constraints.

## Authentication and authorization

Passwords use Argon2id. HS256 access JWTs are short-lived and contain identity, not authoritative tenant/role claims; each HTTP request and WebSocket handshake reloads the current active user and role from PostgreSQL. Refresh credentials are opaque, rotated and hashed at rest, and delivered in an HttpOnly SameSite=Strict cookie (Secure in production); the access token stays in browser memory. Cookie endpoints enforce Origin/fetch metadata. Central API policies enforce ADMIN, DISPATCHER, and RESPONDER permissions; frontend controls are an additional usability layer, not the security boundary.

Redis-backed authentication limits use HMAC-derived dimensions. If Redis is unavailable, each API process uses a bounded local fallback and reports a degradation metric; the fallback does not coordinate replicas. Refresh coordination is per tab, not cross-tab. See [security](security.md) for details and limits.

## Background jobs and worker lifecycle

A route delegates to a service that commits a PostgreSQL job before returning `202 Accepted`. A standalone publisher polls due job and outbox state and sends JSON tasks through Redis to Celery. PostgreSQL remains authoritative; a lost Redis message can be republished. Tasks use fresh async SQLAlchemy engines/sessions per invocation, close them on every path, and do not reuse request sessions or cross event loops.

Celery uses late acknowledgements and worker-lost rejection. A PostgreSQL advisory lock keyed by durable job ID fences concurrent/duplicate execution. The worker conditionally transitions the row, increments attempts, stores bounded safe errors, and persists results/artifacts transactionally. Automatic retries are bounded and classify transient failures; permanent validation/configuration errors are not blindly retried. An ADMIN may manually requeue a terminal job up to three times, with tenant, actor, operation, generation, request ID and timestamp written to `operation_retry_audits`.

These are at-least-once task semantics, not exactly once. A provider or receiver may complete an external request before a worker crash; a retry can repeat that call. PostgreSQL uniqueness, job locks, stable delivery IDs, and receiver-side idempotency contain duplicate durable effects where possible.

## Outbox and webhooks

A domain mutation writes eligible recipient-scoped outbox rows in the same PostgreSQL transaction as the operational change. The publisher materializes stable, unique delivery records and publishes due deliveries. If the publisher is stopped, the outbox remains pending and is picked up after restart. Publication may be duplicated; delivery IDs and uniqueness constraints prevent uncontrolled duplicate records.

Outbound webhooks use a stable event envelope and HMAC-SHA256 over the documented timestamp plus exact canonical JSON body. Subscriptions return a newly generated signing secret once and store a Fernet-encrypted copy. Requests have explicit timeouts, do not follow redirects or proxy environment settings, and validate/pin resolved IP addresses. Production requires public HTTPS destinations; loopback exceptions are exact-origin and development/test-only. Connection/timeout, 408, 429, and 5xx failures retry with bounded exponential backoff; ordinary 4xx and unsafe destinations are terminal. Network acceptance and local delivery-history commit cannot be atomic, so receivers must deduplicate the delivery ID.

## Advisory AI boundary

Incident summaries and shift handovers are asynchronous jobs. The server builds context from the job's tenant-owned data; clients cannot submit arbitrary operational context. `LLMProvider` separates the workflow from `FakeLLMProvider` and the optional OpenAI provider. Structured outputs are schema-validated and persisted with provider/model/prompt version provenance. Operational text is untrusted input and is separated from system instructions. The provider has no database session, arbitrary tools, or operational mutation path: AI cannot dispatch units, change incidents, alter roles, execute code, or write arbitrary database state. UI output is labeled AI-generated and requires human review.

The deterministic fake provider was exercised through a real Redis/Celery worker. No real external LLM credential was configured, so a real provider smoke call was not run.

## Horizontal realtime and deployment

Each API instance owns local sockets and per-tenant service objects. Redis Pub/Sub carries cross-instance events; PostgreSQL owns the common stream cursor. A deployment can add API replicas behind a proxy/load balancer, but existing sockets are process-local and reconnect after an instance loss. Browser REST resynchronization repairs missed messages. Movement is simulated by tracked asyncio tasks per API process with database advisory ownership for dispatch recovery; it is not a separate fleet-tracking microservice.

Docker Compose is the reproducible local stack. Terraform describes an AWS target using an Application Load Balancer, ECS tasks, RDS PostgreSQL/PostGIS, ElastiCache-compatible Redis, and CloudWatch. Terraform format/validate/mock tests ran locally; no AWS resources or task definitions were applied. ECS is a lower-operations fit for this small set of containers than Kubernetes. The target is not a claim of cloud deployment, measured HA, or production capacity.

## Observability

The application emits JSON logs with request IDs and allowlisted low-cardinality entity identifiers; it does not log request bodies, credentials, raw exception messages, webhook secrets, or provider response bodies. Prometheus metrics use bounded route/status, role-independent, job-type/status, delivery-status, and WebSocket labels. `/metrics` requires a separate bearer token and is not exposed by the public frontend proxy. Metrics show retained state/counts and request histograms; there is no alert manager, queue-depth/lag metric, or durable SLI history. The project defines operational indicators, not production SLOs, in [operations](operations.md).

## Failure modes

| Failure | Expected behavior | Recovery/evidence |
| --- | --- | --- |
| PostgreSQL unavailable | Readiness fails; writes return a server error and do not claim success; process liveness stays up. | Restore PostgreSQL, wait for readiness, verify durable state; tested with a real isolated Compose outage. |
| Redis unavailable | Readiness fails; liveness stays up; realtime sockets resynchronize after recovery; auth limits use a bounded per-process fallback. | Restore Redis; PostgreSQL state remains authoritative; tested with Redis stop/start. |
| API replica stops | Its sockets close; another replica can continue commands against PostgreSQL. | Restarted replica shares the durable stream cursor; browser reconnects and snapshots; tested with two replicas. |
| Celery child is lost | Late-acked task is requeued; duplicate execution is fenced by the PostgreSQL job lock. | Replacement child resumes the same job; real SIGKILL/redelivery drill passed. Total worker/container loss additionally depends on broker visibility timeout/reconnect behavior. |
| Publisher stops | Committed jobs/outbox rows remain pending in PostgreSQL. | Restart publisher; it scans and republishes due intents. Real webhook publisher-outage delivery passed. |
| Webhook receiver fails | History records bounded attempt state; transient failures retry, ordinary 4xx terminates. | ADMIN can manually retry after fixing the destination; signed local 500→200 drill passed. |
| AI provider unavailable | Job records a safe failure or bounded transient retry; no operational state is mutated. | Inspect tenant job; fake provider remains available for local validation. |

## Trade-offs and scale considerations

The design favors a clear relational source of truth, simple deployment, and testable transaction boundaries over speculative infrastructure. It accepts per-tenant write serialization, best-effort realtime fanout, bounded retained history, no RLS, and at-least-once external calls. At higher observed load, first measure lock wait time, query latency, queue age, event reconnect rate, and tenant skew. Likely next work is narrowing organization locks, paginating/retaining history, adding queue lag/alerts and key rotation, and ensuring movement ownership across multiple scheduler instances. Only split services or add a streaming platform if measured workload or team boundaries justify the extra consistency and operations cost.
