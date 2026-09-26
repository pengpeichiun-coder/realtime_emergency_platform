# Interview guide

Ten concise discussion topics grounded in this repository. See the [system design](system-design.md), [security model](security.md), [operations runbooks](operations.md) and generated [OpenAPI snapshot](api/openapi-v1.0.0.json) for supporting detail.

### How do REST and WebSockets work together?

REST authenticates commands, commits durable changes and returns job IDs or a consistent operational snapshot. WebSockets distribute committed events with per-tenant sequence numbers. If a client sees a gap, a server restart or a reconnect, it refreshes state through REST instead of treating event delivery as durable history.

### How do you prevent two dispatchers from assigning the same unit?

The service locks the organization and affected rows, checks availability inside the transaction, and writes the incident, unit, dispatch and activity together. Partial unique indexes independently reject two active assignments for the same organization/unit or incident.

### How is tenant isolation enforced?

Authentication reloads the active user from PostgreSQL and builds an immutable RequestContext; clients cannot select an organization. Services and repositories scope reads/writes, composite tenant foreign keys constrain relationships, and foreign resource IDs return 404. PostgreSQL RLS is not enabled, so application scoping remains a meaningful residual risk.

### Why PostGIS for nearby-unit search?

Units and incidents use `GEOGRAPHY(Point, 4326)` with GiST indexes. SQL `ST_DWithin` filters a radius and `ST_Distance` orders available units in meters. It is straight-line distance, not a route ETA.

### Why Celery for reports and AI?

These tasks can take longer than an HTTP request and may fail transiently. The API persists a tenant-scoped PostgreSQL job and a publisher sends JSON-only task messages through Redis. The worker creates its own database lifecycle; job rows, not the Celery result backend, are the business-visible state.

### Why a transactional outbox?

A database commit followed by `.delay()` can lose work if the process dies between those operations. The domain change and outbox intent commit together; the publisher can recover and send after restart. Publication is at-least-once, so stable event/delivery IDs and idempotency fences handle duplicates.

### How are webhooks signed and SSRF-aware?

The worker sends timestamped HMAC-SHA256 signatures over a canonical JSON envelope. It resolves and pins DNS, rejects non-public addresses, disables redirects/proxy environment settings, requires HTTPS in production, and uses timeouts plus bounded retries. Delivery is still at-least-once; receivers should deduplicate by delivery ID.

### How does refresh-token rotation work?

The short-lived access JWT stays in memory. A high-entropy opaque refresh value is sent only as an HttpOnly SameSite=Strict cookie; PostgreSQL stores its hash. Rotation locks the user/token and replaces the old credential transactionally. Origin checks protect cookie endpoints. Cross-tab refresh coordination and family-wide replay revocation are not implemented.

### What happens when Redis, PostgreSQL or a worker fails?

Liveness is process-only; readiness checks PostgreSQL and Redis. PostgreSQL owns operational and job state, while Redis loss interrupts realtime fanout/queueing and readiness fails. Isolated stop/restart drills covered Redis, PostgreSQL, API replica loss and Celery child loss/redelivery. WebSocket history is not durable replay, so reconnect uses REST.

### How would you scale this system, and why not Kubernetes?

The project already tests two API replicas behind shared PostgreSQL/Redis. Next, measure per-tenant lock wait, database latency, reconnect rates and queue age; the coarse organization write lock and absent queue-lag alert are likely early limits. ECS is the prepared target for a few containers. Kubernetes would add an operator surface without a demonstrated need or workload measurement.
