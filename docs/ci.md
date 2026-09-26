# Public CI and dependency maintenance

The public repository validates the application without production credentials,
private container packages, an AWS account or an external LLM key. CI builds its
own images from this checkout. FakeLLMProvider exercises asynchronous AI flows.

## Triggers and merge policy

`.github/workflows/ci.yml` runs on pull requests targeting `main` and on manual
`workflow_dispatch`. Ordinary pushes to `main` and tags do not run it. Five checks
are intended to be required by branch protection with the branch up to date:

| Check | Validation |
| --- | --- |
| `frontend` | Node.js 24, locked npm install, Oxlint, Node's built-in test runner and TypeScript/Vite production build. |
| `backend` | Python 3.14, pip check, real PostgreSQL 17/PostGIS and Redis, migrations, full pytest and PostgreSQL subset with zero skips. A fresh JWT secret is generated and masked for each run. |
| `workflow-lint` | Pinned actionlint checks all retained Actions workflows. |
| `containers` | Builds local PostGIS/backend/frontend images, starts two API replicas plus worker/publisher, runs HTTP/WebSocket/tenant/job/webhook smoke checks and Chromium E2E, scans images and creates CycloneDX SBOMs. |
| `infrastructure` | Validates Prometheus/Alertmanager, tests alert rules, runs Terraform format/init-without-backend/validate/mock test and reports IaC findings. No AWS credentials or resource creation. |

Future changes use branch → PR → all five checks pass → merge. Keep commit
attribution on an owner-confirmed GitHub noreply identity. Do not copy personal
commit emails or use force pushes to rewrite published history. GitHub merge
attribution depends on account email settings and must be reviewed before merging.

The initial snapshot can be validated from the Actions **Validate** workflow via
**Run workflow**. Its run and artifacts belong to this repository. Current results
are visible in the repository's Actions tab; [validation](validation.md) explains
what the tests establish and their limits.

## Container and security gates

The container job builds and loads local images; it never logs in to GHCR/ECR or
publishes packages. The Compose setup generates ignored, disposable local secrets.
Browser tests run against two real APIs, PostgreSQL/PostGIS, Redis, Celery and the
outbox publisher. Signed webhook tests use a real local receiver.

Trivy blocks fixable HIGH/CRITICAL image vulnerabilities. Other findings remain
visible, and tool/network/report errors fail the gate rather than becoming a
clean result. Image security JSON and CycloneDX SBOMs are retained for 14 days;
never add runtime `.env`, logs with credentials or source secret matches to public
artifacts. Terraform findings are reported for review, including deliberate public
ALB/HTTPS-egress boundaries; the report is not a zero-finding security assertion.

All Actions are pinned to full commit IDs. Versions are explicit for Python,
Node, Terraform, Trivy, actionlint, promtool and amtool. These pins are dependency
identities, not development-history references.

## Dependency maintenance

`.github/dependabot.yml` schedules weekly pip, npm, GitHub Actions, Docker,
Docker Compose Redis and Terraform updates. Related minor/patch updates are
grouped; each ecosystem has at most two open PRs. Major upgrades remain reviewable
separately and must pass the same application checks. No automatic merge is enabled.

`.github/workflows/security-maintenance.yml` runs weekly and manually on `main`
with read-only repository permissions. Isolated audit tooling runs pip-audit,
npm audit and Trivy against dependency inventories. Scanner failures and missing
inventory fail closed; advisory severities and fix availability remain in the
report. It does not build/publish images or deploy infrastructure. Reports expire
after 30 days; the scheduled audit is separate from the five PR checks.

## Local equivalents

```bash
make validate
make test-postgres
make api-docs-check
make public-readiness
make monitoring-validate
make e2e
make release-check
```

`make release-check` is a local validation aggregate, not a tag, registry push or
cloud release. Install Gitleaks, Trivy, actionlint, shellcheck, Terraform, promtool
and amtool for the relevant targets. See [operations](operations.md) and
[container setup](containers.md).

No package-publication, private release reconciliation, native private-image
rehearsal or AWS deployment workflow is included. A future public release or
cloud deployment needs its own reviewed configuration and authorization.
