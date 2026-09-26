# Portfolio skill summary

A concise map from job requirements to evidence in this project. Use the implementation and validation documents as evidence; this is not a personal résumé.

| Skill | Project evidence |
| --- | --- |
| React and TypeScript | Authenticated command dashboard, protected routes, typed domain/event decoding, reconnect-safe state management, responsive incident/unit/job/webhook screens. |
| Python and FastAPI | Modular REST API, Pydantic request/response contracts, centralized policy dependencies, request context, health/readiness, structured errors and OpenAPI export. |
| PostgreSQL and data design | SQLAlchemy 2 async repositories, Alembic migrations, transaction boundaries, composite tenant keys/FKs, unique partial indexes, background-job/outbox persistence. |
| PostGIS | Geography columns, GiST indexes, nearest available-unit filtering/order in SQL, real PostgreSQL tests and restore verification. |
| REST and WebSocket | Durable commands/snapshots plus authenticated tenant event streams, shared cursors across API instances, client gap detection and REST recovery. |
| Authentication and authorization | Argon2id, short-lived JWT access, hashed rotating refresh credentials, HttpOnly cookie, current-user authorization, ADMIN/DISPATCHER/RESPONDER enforcement. |
| Multi-tenant SaaS | Database-derived tenant scope, no client-selected tenant, composite relational constraints, cross-tenant 404s, worker-context isolation. No RLS is claimed. |
| Background jobs | Redis/Celery reports and AI, standalone PostgreSQL outbox publisher, bounded retry classification, idempotency fences, persisted status/result/audit. |
| Third-party integration | Durable outbound webhook deliveries, Fernet-encrypted show-once signing secret, HMAC-SHA256, timeout/retry, DNS-pinned SSRF-aware delivery. |
| AI integration | Provider interface, deterministic fake and optional OpenAI implementation, structured validation, prompt versions, human review, no operational mutation/tool boundary. Real external provider was not smoke-tested. |
| Containers and testing | Multi-service Compose, isolated roles/migrations, backend unit/API and PostgreSQL suites, frontend tests, real Redis/Celery/webhook/outage/restore/browser workflow evidence. |
| CI/CD | Five PR checks cover application, PostgreSQL/PostGIS, browser, container, workflow and infrastructure validation. CI builds local images, produces SBOMs and needs no production credentials. No registry publication or cloud deployment is configured. |
| AWS | Terraform describes an ECS/ALB/RDS/cache/logging target and local mock tests pass. No AWS resource has been provisioned. |

See [system design](system-design.md), [validation](validation.md), and [CI configuration](ci.md) for architecture, validation counts, and limitations.
