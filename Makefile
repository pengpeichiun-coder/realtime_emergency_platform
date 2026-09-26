PYTHON ?= $(shell command -v python3.14 2>/dev/null || command -v python3)
COMPOSE ?= docker compose
BACKEND_PYTHON := backend/.venv/bin/python
TRIVY ?= trivy
GITLEAKS ?= gitleaks
PROMTOOL ?= promtool
AMTOOL ?= amtool
RELEASE_SECURITY_DIR ?= /tmp/emergency-platform-release-security
E2E_PROJECT ?= emergency-platform-e2e
E2E_BUILD_FLAG ?= --build
E2E_HTTP_PORT ?= 18082
E2E_API2_PORT ?= 18004
E2E_ADMIN_EMAIL ?= admin@demo.example.com
E2E_DEMO_PASSWORD ?= Demo-Only-Response-2026!
E2E_COMPOSE = COMPOSE_PROJECT_NAME=$(E2E_PROJECT) HTTP_PORT=$(E2E_HTTP_PORT) API2_PORT=$(E2E_API2_PORT) ALLOWED_ORIGINS=http://127.0.0.1:$(E2E_HTTP_PORT),http://localhost:$(E2E_HTTP_PORT) WEBHOOK_ALLOWED_LOOPBACK_ORIGINS=http://127.0.0.1:8765 $(COMPOSE) -f compose.yaml -f compose.e2e.yaml
E2E_ENV = E2E_BASE_URL=http://127.0.0.1:$(E2E_HTTP_PORT) E2E_API2_URL=http://127.0.0.1:$(E2E_API2_PORT) E2E_COMPOSE_ROOT=$(CURDIR) E2E_COMPOSE_PROJECT=$(E2E_PROJECT) E2E_ADMIN_EMAIL=$(E2E_ADMIN_EMAIL) E2E_DEMO_PASSWORD=$(E2E_DEMO_PASSWORD)

.PHONY: help env setup install-dev up up-distributed down status seed migrate smoke system-smoke distributed-smoke webhook-smoke test test-backend test-postgres test-frontend lint build validate e2e-stack e2e e2e-repeat e2e-cross-browser release-check api-docs api-docs-check metrics backup restore-test

help:
	@printf '%s\n' \
	  'setup              Create private Compose env and install local dev dependencies' \
	  'up                 Build/start the default local Compose stack' \
	  'up-distributed     Build/start the stack with the second API replica' \
	  'down               Stop this Compose project; keep its database volume' \
	  'seed               Explicitly seed fictional demo data/users if absent' \
	  'smoke              Check Compose health, DB roles, PostGIS, worker, and UI' \
	  'system-smoke       Exercise real HTTP, WebSocket, dispatch, reports, and AI jobs' \
	  'distributed-smoke  Check two-API fanout and tenant isolation' \
	  'webhook-smoke      Run a real signed local delivery and 500-to-200 retry' \
	  'test               Run backend and frontend suites' \
	  'e2e                Build isolated two-API stack and run Chromium browser journey' \
	  'e2e-repeat         Run the release-critical Chromium journey three times' \
	  'e2e-cross-browser  Run login smoke in Chromium, Firefox, and WebKit' \
	  'release-check      Run local tests, Compose/browser smoke, security, and Terraform gates' \
	  'test-postgres      Run PostgreSQL-marked tests (requires TEST_DATABASE_URL)' \
	  'lint / build       Run frontend lint / production build' \
	  'api-docs           Regenerate the committed OpenAPI snapshot' \
	  'api-docs-check     Verify the OpenAPI snapshot is current' \
	  'public-readiness   Check tracked files, Markdown links and Git-history secrets' \
	  'metrics            Print protected aggregate Prometheus metrics' \
	  'monitoring-up      Start the optional local Prometheus/Alertmanager profile' \
	  'monitoring-validate  Validate alert rules and Prometheus/Alertmanager config' \
	  'alert-rehearsal    Prove firing/resolved routing in a fresh isolated Compose stack' \
	  'backup             BACKUP_FILE=/path/to/file.dump make backup' \
	  'restore-test       BACKUP_FILE=... RESTORE_DATABASE=... make restore-test'

env:
	$(PYTHON) scripts/init_local_env.py

setup: env install-dev

install-dev:
	@$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 14), f"Python 3.14+ is required; got {sys.version.split()[0]} (set PYTHON=/path/to/python3.14 to select an interpreter)"'
	@test -x backend/.venv/bin/python || $(PYTHON) -m venv backend/.venv
	@backend/.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 14), f"backend/.venv uses Python {sys.version.split()[0]}; recreate it with Python 3.14+"'
	backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
	npm --prefix frontend ci

up: env
	$(COMPOSE) up --build -d

up-distributed: env
	$(COMPOSE) --profile distributed up --build -d

down:
	$(COMPOSE) down

status:
	$(COMPOSE) ps

seed:
	$(COMPOSE) --profile tools run --rm seed

migrate:
	$(COMPOSE) run --rm migrate

smoke:
	BASE_URL=$${BASE_URL:-http://127.0.0.1:$${HTTP_PORT:-8080}} $(PYTHON) scripts/compose_smoke.py

system-smoke:
	BASE_URL=$${BASE_URL:-http://127.0.0.1:$${HTTP_PORT:-8080}} $(BACKEND_PYTHON) scripts/system_smoke.py

distributed-smoke:
	BASE_URL=$${BASE_URL:-http://127.0.0.1:$${HTTP_PORT:-8080}} SECOND_API_URL=$${SECOND_API_URL:-http://127.0.0.1:$${API2_PORT:-8002}} $(BACKEND_PYTHON) scripts/distributed_smoke.py

webhook-smoke:
	WEBHOOK_ALLOWED_LOOPBACK_ORIGINS=$${WEBHOOK_ALLOWED_LOOPBACK_ORIGINS:-http://127.0.0.1:8765} $(COMPOSE) up -d --no-deps api worker publisher
	BASE_URL=$${BASE_URL:-http://127.0.0.1:$${HTTP_PORT:-8080}} $(PYTHON) scripts/compose_smoke.py
	WEBHOOK_ALLOWED_LOOPBACK_ORIGINS=$${WEBHOOK_ALLOWED_LOOPBACK_ORIGINS:-http://127.0.0.1:8765} BASE_URL=$${BASE_URL:-http://127.0.0.1:$${HTTP_PORT:-8080}} $(BACKEND_PYTHON) scripts/container_webhook_smoke.py --publisher-outage --retry-receiver

e2e-stack: env
	$(E2E_COMPOSE) --profile distributed up $(E2E_BUILD_FLAG) -d
	$(E2E_COMPOSE) --profile tools run --rm seed

e2e: e2e-stack
	npm --prefix frontend ci
	cd frontend && npx playwright install chromium
	$(E2E_ENV) E2E_CAPTURE_PORTFOLIO=1 npm --prefix frontend run test:e2e

e2e-repeat: e2e-stack
	npm --prefix frontend ci
	cd frontend && npx playwright install chromium
	@set -e; for run in 1 2 3; do \
	  echo "Release E2E pass $$run/3"; \
	  $(E2E_ENV) E2E_CAPTURE_PORTFOLIO=1 npm --prefix frontend run test:e2e; \
	done

e2e-cross-browser: e2e-stack
	npm --prefix frontend ci
	cd frontend && npx playwright install chromium firefox webkit
	$(E2E_ENV) E2E_CROSS_BROWSER=1 npm --prefix frontend run test:e2e -- --grep @browser-smoke

release-check:
	$(MAKE) validate
	$(MAKE) api-docs-check
	$(E2E_COMPOSE) build postgres
	$(E2E_COMPOSE) build api
	$(E2E_COMPOSE) build frontend
	$(MAKE) e2e E2E_BUILD_FLAG=
	COMPOSE_PROJECT_NAME=$(E2E_PROJECT) HTTP_PORT=$(E2E_HTTP_PORT) API2_PORT=$(E2E_API2_PORT) BASE_URL=http://127.0.0.1:$(E2E_HTTP_PORT) $(PYTHON) scripts/compose_smoke.py
	@command -v "$(TRIVY)" >/dev/null 2>&1 || { echo 'Trivy is required; install it with brew install trivy or set TRIVY=/path/to/trivy.'; exit 2; }
	@mkdir -p "$(RELEASE_SECURITY_DIR)"
	TRIVY_BIN="$(TRIVY)" SECURITY_REPORT_DIR="$(RELEASE_SECURITY_DIR)" bash scripts/scan_images.sh
	"$(TRIVY)" fs --scanners secret --timeout 10m --exit-code 1 --format json --output "$(RELEASE_SECURITY_DIR)/source-secrets.json" .
	actionlint
	shellcheck scripts/scan_images.sh
	terraform -chdir=infra fmt -check -recursive
	terraform -chdir=infra init -backend=false -input=false
	terraform -chdir=infra validate
	terraform -chdir=infra test

test: test-backend test-frontend

test-backend:
	cd backend && APP_ENV=test ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 .venv/bin/python -m pytest -q

test-postgres:
	cd backend && APP_ENV=test ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173 .venv/bin/python -m pytest -q -m postgres

test-frontend:
	npm --prefix frontend test

lint:
	npm --prefix frontend run lint
	$(BACKEND_PYTHON) -m pip check

build:
	npm --prefix frontend run build

validate: lint build test

api-docs:
	cd backend && .venv/bin/python -m app.scripts.export_openapi

api-docs-check:
	cd backend && .venv/bin/python -m app.scripts.export_openapi --check

.PHONY: public-readiness
public-readiness:
	$(PYTHON) scripts/public_readiness.py --gitleaks "$(GITLEAKS)"

.PHONY: alerts-check queue-observability-smoke monitoring-validate monitoring-up alert-rehearsal
alerts-check:
	$(PROMTOOL) check rules monitoring/alerts.yml
	$(PROMTOOL) test rules monitoring/alerts.test.yml

queue-observability-smoke:
	$(BACKEND_PYTHON) scripts/queue_observability_smoke.py

monitoring-validate: alerts-check
	$(PYTHON) scripts/validate_monitoring.py --promtool "$(PROMTOOL)" --amtool "$(AMTOOL)"

monitoring-up: env
	$(COMPOSE) -f compose.yaml -f compose.monitoring.yaml --profile monitoring up --build -d

alert-rehearsal:
	COMPOSE="$(COMPOSE)" $(BACKEND_PYTHON) scripts/alert_rehearsal.py

metrics:
	$(COMPOSE) exec -T api python -c 'import os,urllib.request; request=urllib.request.Request("http://127.0.0.1:8000/metrics", headers={"Authorization":"Bearer " + os.environ["METRICS_TOKEN"]}); print(urllib.request.urlopen(request, timeout=5).read().decode(), end="")'

backup:
	@test -n "$(BACKUP_FILE)" || { echo 'Set BACKUP_FILE=/path/to/database.dump'; exit 2; }
	$(PYTHON) scripts/backup_database.py "$(BACKUP_FILE)"

restore-test:
	@test -n "$(BACKUP_FILE)" || { echo 'Set BACKUP_FILE=/path/to/database.dump'; exit 2; }
	@test -n "$(RESTORE_DATABASE)" || { echo 'Set RESTORE_DATABASE to a new, unused database name'; exit 2; }
	$(PYTHON) scripts/restore_database.py "$(BACKUP_FILE)" "$(RESTORE_DATABASE)"
	$(COMPOSE) run --rm --no-deps --entrypoint python db-roles -m app.scripts.bootstrap_database --initialize-role --database "$(RESTORE_DATABASE)"
	$(COMPOSE) exec -T api python -m app.scripts.verify_restore "$(RESTORE_DATABASE)"
