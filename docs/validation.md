# Validation scope

This public snapshot contains the current application with an independent Git
history. Historical release/PR identifiers and private artifact links are not
part of its evidence. Reproduce the checks below and inspect the public Actions
tab for runs against the public tree.

## Snapshot validation

The curated public tree passed these local checks before publication:

| Check | Result |
| --- | --- |
| Backend | **286 passed** |
| PostgreSQL/PostGIS subset | **95 passed, 191 deselected, 0 skipped** |
| Frontend | **42 passed, 0 skipped** |
| Oxlint and TypeScript/Vite production build | Passed |
| Python dependency consistency | `pip check` passed |
| OpenAPI snapshot | Current |
| Workflow lint | Passed |
| Prometheus/Alertmanager | Nine normal alert rules and configuration checks passed |
| Terraform | Format/validate passed; one mock architecture test passed |

PostgreSQL tests used a newly created disposable database, removed after both
suites completed. These local results are separate from hosted CI; public run
results and browser/container artifacts are available through this repository's
Actions tab after a workflow run.

## Baseline and current checks

Before export, the application baseline passed **297 backend tests**, including
**95 PostgreSQL/PostGIS tests with zero skips**, **42 frontend tests** and **5
Chromium scenarios**. Frontend lint/build, pip check, workflow lint and offline
Terraform validation also passed. These are historical engineering facts, not
claims about a newly published CI run.

The public export omits 13 tests dedicated solely to removed private release-image
pins and private rehearsal reports, and adds two commit-email privacy regression
cases. Application behavior, tenant isolation,
authentication, RBAC, database transactions, workers and integration tests remain.
Public workflow policy tests enforce the retained five-job contract. Test totals
for this curated tree are recorded by its own pytest/Vitest/Playwright runs and
must not be inferred from the pre-export count.

## Reproduce locally

```bash
make setup
make lint
make build
make test
make test-postgres
make api-docs-check
make public-readiness
```

Supply a dedicated disposable `TEST_DATABASE_URL` ending in `_test`; the
PostgreSQL fixtures guard this suffix and reset test tenants. They need PostGIS
and the current Alembic migrations. The [CI workflow](ci.md) provisions isolated
PostgreSQL/Redis services and rejects skipped PostgreSQL tests.

## Real-service coverage

```bash
make up-distributed
make seed
make smoke
make system-smoke
make distributed-smoke
make webhook-smoke
make e2e
```

The Chromium suite covers the command workflow, ADMIN/DISPATCHER/RESPONDER
boundaries, second-tenant isolation, realtime/restart recovery and responsive login.
Smoke checks exercise real Redis→Celery task delivery, persisted reports and AI
artifacts, and a real signed HTTP webhook with controlled 500→200 retry. The
provider is deterministic FakeLLMProvider; no external LLM quality or credential
validation is implied. These tests need Docker and install Chromium locally.

The source application's earlier bounded load checks used 5 HTTP VUs for
15 seconds (241/241 checks) and 3 WebSocket VUs for 12 seconds (61/61 checks),
plus five concurrent report jobs. These are smoke/load samples, not capacity,
latency SLO or production reliability claims. [Realtime observability](realtime-observability.md)
describes the measurement scope.

## Recovery, security and infrastructure

[Operations](operations.md) documents isolated dependency loss, worker/publisher
recovery, backup/restore and local alert routing. `make alert-rehearsal` runs the
monitoring drill in its own disposable Compose project. Normal Compose volumes
are preserved. `make monitoring-validate` checks production-threshold alert rules
and Prometheus/Alertmanager configuration.

`make release-check` builds local images, runs app/browser gates and scans them.
Gitleaks checks tracked content and all local Git refs; Trivy provides an
independent secret scan for publication. Narrow scanner exceptions are documented
in [privacy checks](public-readiness.md); real findings are not suppressed.

Terraform format, backend-disabled initialization, validation and one mocked
architecture test establish code consistency only. No AWS account plan, apply,
resource creation, hosted deployment or live cloud recovery test has run. No
external LLM call has run. There is no public release/tag qualification claim.
