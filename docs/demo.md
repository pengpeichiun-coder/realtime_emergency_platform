# Recruiter demo walkthrough

Estimated time: 3–5 minutes. All seeded incidents, people, and units are fictional. Use the local Compose stack only.

## Start the demo

```bash
make setup
make up
make seed
make smoke
```

Open <http://127.0.0.1:8080/login> and sign in:

| Email | Role | Development-only password |
| --- | --- | --- |
| `admin@demo.example.com` | ADMIN | `Demo-Only-Response-2026!` |
| `dispatcher@demo.example.com` | DISPATCHER | `Demo-Only-Response-2026!` |
| `responder@demo.example.com` | RESPONDER | `Demo-Only-Response-2026!` |

The seed is idempotent and does not replace existing passwords/roles or overwrite operational data. Use **Reset demo** in the UI when you want the fictional incident/unit fixtures restored for the current ADMIN organization.

## Five-minute path

1. **See live state.** On Operations overview, note the Realtime connected indicator, incident/unit counts, Singapore map, and activity feed. Select `INC-001` (Building fire). The nearby-unit query returns available units ordered by PostGIS distance.
2. **Dispatch.** Select `FIRE-001` and dispatch. The backend commits the assignment before emitting events. The map shows server-authored movement; wait for the incident to reach `on_scene` (the local simulator normally takes about 20 seconds), then resolve it.
3. **Background jobs.** Generate the incident's operational report and AI summary. Open **Jobs and reports** to see queued/running/succeeded status and the persisted result. Generate a shift handover from the same page. AI results are labeled **AI-generated · Human review required**; the default FakeLLMProvider is deterministic and does not call an external model.
4. **Permissions.** Sign out, then sign in as DISPATCHER. Dispatch operations remain available; user and webhook administration are hidden and denied by the API. Sign in as RESPONDER to show read-only operations and completed results.
5. **Webhook delivery and metrics.** As ADMIN, the Webhooks page manages HTTPS subscriptions and delivery history. To exercise a real local signed receiver and a 500→200 retry without configuring a third-party account, run `make webhook-smoke`. That smoke uses a separate fictional tenant and temporary receiver inside the worker; it does not alter the seeded workspace. Run `make metrics` to inspect token-protected low-cardinality Prometheus metrics.

## Show two API replicas

In a local setup with port 8002 available:

```bash
make up-distributed
make distributed-smoke
```

The smoke opens authenticated sockets to both API instances, checks Redis fanout, shared stream cursors, duplicate suppression, REST resynchronization, and two-way tenant isolation. If 8002 is occupied, choose a port and set both values consistently, for example:

```bash
COMPOSE_PROJECT_NAME=emergency-platform-demo HTTP_PORT=18080 API2_PORT=18082 \
ALLOWED_ORIGINS=http://127.0.0.1:18080,http://localhost:18080 make up-distributed
BASE_URL=http://127.0.0.1:18080 SECOND_API_URL=http://127.0.0.1:18082 make distributed-smoke
```

## Reset and stop

Use **Reset demo** to restore fixtures in the current development/test organization. It changes only that organization. `make down` stops containers but deliberately retains the Compose database volume. Do not use `docker compose down -v` unless you intentionally want to destroy that project's database.
