# Project facts

Measured implementation facts and bounded validation, not production scale claims.
See [validation](validation.md) for reproducible checks and evidence boundaries.

## Architecture

- React, TypeScript, Vite, Tailwind CSS, React-Leaflet and Playwright frontend.
- Python 3.14, FastAPI, async SQLAlchemy 2, PostgreSQL/PostGIS, Alembic,
  Redis and Celery backend.
- Modular monolith with separate API, worker and outbox-publisher processes.
  Distributed Compose validation uses two API replicas.
- **Eight Alembic revisions**, one head: `0008_queue_observability`.
- PostgreSQL owns operational, job and outbox state. Redis carries messages,
  rate limits and best-effort realtime fanout.
- ADMIN, DISPATCHER and RESPONDER policies derive tenant scope from authenticated
  database identity. Composite constraints reinforce application query scope.
  PostgreSQL row-level security is not implemented.

## Evidence

The curated snapshot passed **286 backend tests**, including **95 real
PostgreSQL/PostGIS tests with zero skips**, and **42 frontend tests** locally.
Lint, production build, pip check, OpenAPI export, workflow lint, monitoring
configuration and one Terraform mock test passed. The earlier application baseline
also passed five Chromium scenarios; public CI runs the real-service suite again.
See [CI](ci.md) and [validation](validation.md) for the exact scope and the removal
of private-release-specific test contracts from the export.

The real-service workflow includes Redis-backed Celery tasks, a durable outbox
publisher and an HMAC-validating HTTP receiver with retry. Reports and AI
artifacts are persisted and tenant-isolated. FakeLLMProvider exercises the same
asynchronous product path; a real external LLM provider was not smoke-tested.

Seven screenshots use fictional demo data. Public OpenStreetMap tiles were
unavailable during capture; Leaflet markers, routes and live movement were
exercised. No live public demo URL is configured.

## Deployment and supply chain

- Public CI builds local container images and produces CycloneDX SBOMs. It does
  not depend on private registries or publish packages/releases.
- Dependabot covers the six configured dependency ecosystems. A weekly audit
  reports dependency vulnerabilities; results are time-dependent.
- Terraform describes ECS/Fargate, ALB, RDS PostgreSQL/PostGIS, ElastiCache Valkey,
  Secrets Manager, ECR and CloudWatch. Offline validation and a mocked
  architecture test run in CI; **no AWS plan/apply or deployment has run**.
- No software license has been selected. Visibility alone does not grant an
  open-source license.

## Deliberate limits

There is no durable WebSocket replay, RLS, cross-tab refresh lock, automatic
retention/key rotation or production notification receiver. Queue-age and
publisher-heartbeat metrics have local alert rules; thresholds have not been
tuned to a production workload. HTTP external calls are at-least-once and receivers
must deduplicate stable delivery IDs. No real LLM evaluation corpus, quota,
production uptime target or user-volume claim is provided.
