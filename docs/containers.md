# Local container stack

Host development remains supported. For a fresh checkout:

```bash
python3 scripts/init_local_env.py
docker compose up --build -d
# Optional fictional accounts/data; explicitly invoked, not automatic:
docker compose --profile tools run --rm seed
```

Open http://127.0.0.1:8080. The root ignored `.env` configures Compose; backend/.env remains for host development. The initializer creates mode-0600 random PostgreSQL/JWT/Fernet credentials once and never overwrites existing configuration. Do not commit either env. APP_ENV=development enables local HTTP cookies/demo seed; real production requires HTTPS and APP_ENV=production. ALLOWED_ORIGINS must exactly match browser origins.

The application backend image uses Python 3.14.7 on Alpine 3.24, pinned runtime dependencies, UID 10001, narrow allowlisted context and no tests/env/virtualenv. API, worker, publisher and migration reuse it with different commands. Frontend uses Node 24 npm ci + TypeScript/Vite build followed by a non-root Nginx 1.30 Alpine runtime. Same-origin `/api/` and `/ws/` proxying retains WebSocket upgrade headers; `/docs` and `/openapi.json` proxy the live API contract without publishing a separate API port. Swagger uses a per-response CSP nonce for its bootstrap script, while ordinary application pages retain the strict same-origin CSP. SPA navigation falls back to index.html. Production assets derive API origin from the current page; host Vite still targets port 8000. No backend hostname or secret is embedded in the JavaScript bundle.

Compose has frontend, api, worker, publisher, postgres, redis, one-shot database-role setup and migration services; seed is an explicit tools profile. The bootstrap creates separate `emergency_app` runtime and `emergency_migrator` DDL roles; API/worker/publisher run without schema creation privileges and Alembic connects as the migrator. Only frontend port 8080 is bound to host loopback. `infra/postgis/Dockerfile` derives from digest-pinned `postgis/postgis:17-3.6-alpine`, applies Alpine package security updates, and rebuilds the upstream gosu source for the target architecture. The PostGIS image currently targets linux/amd64 and requires emulation on ARM64 hosts; update and rescan the pinned base images during maintenance. PostgreSQL data uses a named volume. Redis 8.2 Alpine has no public port and no persistence: committed PostgreSQL job/delivery intents recover lost broker messages. Redis is not the business result authority. Backup/restore commands use the Compose Postgres tools and are documented in [operational recovery](operations.md); restore drills use a new database name and never overwrite the application database.

Authentication rate limits use Redis fixed-window counters keyed by HMAC-derived
identity/IP values. During a Redis outage, each API process uses a bounded local
fallback and exposes a low-cardinality degradation counter; readiness reports
unhealthy until Redis returns. See [security boundaries](security.md#edge-and-credential-hardening).

PostgreSQL/Redis healthchecks gate migration/application startup; one successful `alembic upgrade head` gates API/worker/publisher. API readiness checks PostgreSQL and Redis under deadlines. Frontend health checks an actual HTTP response. Worker ping and publisher process state are verified by the smoke script. API never migrates implicitly. SIGTERM propagates via init to Python; publisher exits its loop and disposes its engine; API lifespan cancels simulation and closes sockets/database; Celery gets a warm shutdown window.

```bash
python3 scripts/compose_smoke.py
backend/.venv/bin/python scripts/system_smoke.py
# Explicit local test exception required on API/worker: WEBHOOK_ALLOWED_LOOPBACK_ORIGINS=http://127.0.0.1:8765
python3 scripts/container_webhook_smoke.py
docker compose down      # retains the project database volume
docker compose up -d
```

Smoke scripts register isolated fictional organizations and retain their operational history for inspection. Webhook receiver runs temporarily inside the worker, verifies HMAC, exits, and its subscription is soft-deleted. Never use `down -v` on a volume containing wanted data. Avoid running the loopback receiver script concurrently with itself.

On a fresh checkout, use a running Docker engine and the intended `DOCKER_CONTEXT`. The PostGIS image targets linux/amd64; native AMD64 CI avoids emulation startup overhead. ARM64 hosts need compatible emulation and sufficient resources.

Reference: [Compose startup ordering](https://docs.docker.com/compose/how-tos/startup-order/), [upstream PostGIS images](https://github.com/postgis/docker-postgis).
