# Engineering decision summary

This index records the important choices behind the current implementation and the trade-offs they create. It complements the [system design](system-design.md) and [security model](security.md); none of the choices is presented as universally best.

| Decision | Why it fits this project | Cost / limit |
| --- | --- | --- |
| PostgreSQL as the source of truth | Transactions cover incidents, units, dispatches, activity, tenant state, jobs, and webhook intent; mature constraints and JSONB support structured workflow data. | Writes for one tenant are deliberately serialized; operators still need retention, backups, role management, and restore procedures. |
| PostGIS `GEOGRAPHY` + GiST | Nearby unit search executes in the database, uses meter-based distance, and avoids loading every location into Python. | Straight-line distance is not road travel or an ETA; extension/image maintenance is another operational responsibility. |
| Modular monolith, not microservices | One domain and transaction boundary keeps deployment and local development understandable while API/worker/publisher remain separate processes. | Some process boundaries share code and release as one image; independent scaling and fault domains are limited. |
| REST commands/snapshots + WebSocket events | REST is durable and easy to retry/read; WebSockets keep the operator view fresh without polling each collection. | WebSocket messages are not the event store; clients reconnect and resync from REST when cursors gap or Redis loses Pub/Sub. |
| Redis for realtime, rate limiting, Celery transport | The same lightweight service supports fanout and asynchronous delivery; PostgreSQL retains the business state. | Redis outage impacts realtime and queue publication; process-local auth-limit fallback does not coordinate replicas. |
| Celery for retryable work | Reports, signed webhooks, and advisory AI calls do not block API requests and have explicit retry policy. | Delivery is at-least-once; broker visibility/reconnect and task duration need operational tuning; result backend is not authoritative. |
| PostgreSQL job state + separate publisher | A committed job/outbox row survives a broker outage or lost enqueue; the publisher repairs publication gaps. | Polling adds database work and lag; queue-age and publisher-heartbeat alerts exist locally, with thresholds still needing production tuning. |
| Transactional outbox for webhooks | Operational change and delivery intent commit together, closing the DB-commit-to-queue crash gap. | Receiver HTTP and delivery-history commit cannot be one transaction; receivers deduplicate stable IDs. |
| HttpOnly rotating refresh cookie + in-memory access token | Avoids persistent browser storage for access JWTs; hashed refresh credentials are revocable and rotated server-side. | Refresh coordination is per tab, and cookie deployment requires same-site HTTPS/origin configuration. |
| Advisory-only provider abstraction | A small provider interface supports a deterministic fake and optional real provider while keeping AI output structured and non-authoritative. | Fake output is not evidence of real model quality; no quota, evaluation corpus, or real external provider smoke exists. |
| ECS target instead of Kubernetes | A small set of HTTP/background containers fits managed ECS tasks with fewer cluster components to operate. | Terraform only describes a target; it has not been applied, costed with an account, or load-tested in AWS. |
| Separate migration and runtime DB roles | API/worker/publisher can perform required DML without schema creation; Alembic runs with a distinct DDL role. | Role bootstrap is an explicit deployment step and requires operator credential hygiene. |

## Revisit triggers

- Revisit tenant write locking when lock wait/latency or noisy-neighbor measurements justify narrower locks.
- Revisit realtime transport only if reconnect/resync volume, delivery guarantees, or event throughput become a measured problem.
- Revisit service decomposition when teams need independent ownership or release cadence, not merely to add more infrastructure to a portfolio project.
- Revisit the AWS target only after an owner supplies account/network/secret choices and approves cloud costs; no resource has been provisioned by this project work.
