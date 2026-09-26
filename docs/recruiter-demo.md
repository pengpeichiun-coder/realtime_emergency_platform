# Recruiter demo

## 30-second introduction

This is a fictional, multi-tenant emergency operations command dashboard. A
dispatcher can inspect nearby units, assign one through a transaction-safe API,
and watch server-authored movement over WebSockets. Reports, signed webhooks and
advisory AI summaries run as persisted background jobs. The portfolio focus is
the correctness and failure behavior between React, FastAPI, PostgreSQL/PostGIS,
Redis and Celery; it is not an operational emergency service.

## Three-minute walkthrough

All people, units and incidents in this demo are invented.

1. **0:00–0:30 — Sign in and orient.** Sign in as the seeded ADMIN using the
   development-only credentials in [the local demo guide](demo.md). Point out
   active incidents, response units, activity and tenant role in the dashboard.
2. **0:30–1:00 — Locate and dispatch.** Select `INC-001` (Building fire). Show
   units ordered by PostGIS distance and dispatch `FIRE-001`. The API commits the
   assignment before publishing the event.
3. **1:00–1:35 — Follow the operation.** Show the unit marker move over the
   authenticated WebSocket stream and the incident's status advance. Reload the
   second browser/API path to show REST resynchronization, then resolve the
   incident and point to the activity timeline.
4. **1:35–2:10 — Inspect durable work.** Generate the incident report and AI
   summary. Open **Jobs and reports** to show worker state/result persistence.
   The FakeLLM result is visibly labeled **AI-generated · Human review
   required** and cannot dispatch units or change incident state.
5. **2:10–2:40 — Show integration and role boundaries.** Open **Webhooks** to
   show delivery history. The release test runs a real receiver that validates
   HMAC. Switch to DISPATCHER or RESPONDER and show that server permissions deny
   administrative operations.
6. **2:40–3:00 — Close on engineering evidence.** Mention the second-tenant
   isolation checks and isolated PostgreSQL/Redis/Celery browser run. Use the
   [architecture](architecture.md), [validation record](validation.md),
   [project facts](project-facts.md) and [interview guide](interview-guide.md)
   for detail.

## Technical highlights

- Atomic dispatch transaction with partial unique constraints to prevent a
  duplicate active assignment.
- PostGIS geography/GiST nearest-unit filtering and distance ordering.
- Tenant context derived from the authenticated database identity; REST
  snapshots repair best-effort WebSocket event gaps.
- PostgreSQL jobs and outbox as durable state, with Redis/Celery at-least-once
  delivery and idempotency fences.
- Timestamped HMAC-SHA256 webhook signatures with DNS-pinned SSRF checks,
  bounded timeout/retry and tenant-scoped delivery history.
- Advisory structured AI output with prompt versions, validated persistence and
  no operational mutation or tool execution path.

## Portfolio links

- [Public source repository](https://github.com/pengpeichiun-coder/realtime_emergency_platform)
- [Screenshots](screenshots/)
- [Validation scope](validation.md) and [CI configuration](ci.md)

There is no deployed public live demo. Run the local Compose walkthrough instead.

## Known limitations

- AWS Terraform is validated offline; no account plan or apply has run.
- AI uses FakeLLMProvider in the validated flow. No real external provider call
  or paid credits were used.
- Map operations use real Leaflet markers/routes; OSM tiles were unavailable in
  the capture environment. A deployment needs an appropriate tile provider policy.
- This demo is not intended for real incidents, people, patient data or dispatch
  decisions. No production capacity or uptime claim is made.
