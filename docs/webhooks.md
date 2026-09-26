# Reliable outbound webhooks

ADMIN alone may create/list/patch/delete subscriptions and view delivery history. All queries derive organization ID from the authenticated RequestContext. Other roles receive 403; foreign IDs receive 404. Deletes disable and soft-delete subscriptions, preserving delivery history. Lists return the latest 100 records.

## Event and outbox semantics

The operations service persists outbox rows in the **same PostgreSQL transaction** as incident/dispatch/unit changes. Rollback removes both. The repository selects active matching subscriptions at event time and records their IDs; newly created subscriptions do not receive historical events. Supported events: `incident.created`, `incident.updated`, `incident.resolved`, `unit.dispatched`, `unit.arrived`, `dispatch.completed`. Unit events map from committed status transitions; simulator location frames are excluded. Incident updates include dispatch/on-scene transitions. Demo reset is not an integration event.

The standalone `python -m app.workers.publisher` process now handles jobs, materializes outbox events, then publishes pending deliveries once per second. Materialization locks rows with SKIP LOCKED, inserts one delivery per `(event_id, subscription_id)`, and marks the event processed in one transaction. Publication timestamps commit after broker acceptance; crashes may republish but cannot create duplicate delivery records. A broker failure leaves durable rows recoverable. Delivery URL is snapshotted at materialization; disabling/deleting the subscription prevents subsequent attempts (an already in-flight HTTP call cannot be recalled).

Run the same Redis, worker and publisher commands as [background jobs](background-jobs.md). Celery discovers `emergency.deliver_webhook`; only delivery ID travels through Redis. Fresh asyncio loop/engine per worker invocation, short business transactions and an independent PostgreSQL advisory lock fence concurrent duplicate tasks. Unfinished rows are republished after 30 seconds. Terminal rows are no-ops.

## Secrets and signature contract

Set `WEBHOOK_ENCRYPTION_KEY` to a generated Fernet key in ignored backend/.env. For example, use Python's `Fernet.generate_key()` and save it directly to your secret configuration. Do not replace this key without migrating encrypted secrets. Subscription creation generates a random 32-byte URL-safe signing secret, returns it once, and stores only authenticated ciphertext. GET/PATCH responses exclude both raw and encrypted secrets. The UI never stores it in browser storage and provides explicit dismissal.

POST body is UTF-8 JSON using sorted keys, compact separators and unescaped Unicode:

```json
{"created_at":"...","data":{},"id":"evt_...","type":"incident.created"}
```

Headers: `X-Emergency-Event`, `X-Emergency-Delivery`, `X-Emergency-Timestamp` (Unix seconds), `X-Emergency-Signature` (`sha256=` followed by lowercase hex). Signature is HMAC-SHA256 with the raw signing secret over **timestamp + period + exact request body bytes**. Receivers should compare in constant time, reject stale timestamps (e.g. ±5 minutes), and deduplicate the stable delivery ID. Timestamp/signature refresh each attempt; event ID and body remain stable.

External delivery is **at least once**, not exactly once: a receiver may accept a request immediately before the sender crashes prior to recording success. Receiver-side deduplication is required for durable external side effects.

## HTTP and destination policy

Three persisted attempts maximum, exponential 2/4-second retry delays. Retry connection failure/timeout and HTTP 408/429/5xx. Ordinary 4xx, redirects, invalid configuration, encryption errors and unsafe destinations fail permanently. Five-second total HTTP timeout, three-second connection/DNS deadlines. No response body, raw exception, signing secret or URL credentials are logged or exposed in delivery errors. History persists status, attempt count, last HTTP status/error, next-attempt and delivery timestamps. The API exposes ADMIN-only POST /api/webhook-deliveries/{delivery_id}/retry for
terminal failures. It reuses the same durable delivery ID and payload, resets the
current attempt generation, and records the actor in operation_retry_audits.
Queued/running requests are idempotent, successful deliveries cannot be replayed,
and manual requeues are capped at three per delivery. Receivers must still
deduplicate the stable delivery ID because a crash after HTTP acceptance can
repeat a delivery. GET /api/operation-retries returns tenant-scoped retry audit
history to ADMIN users.

HTTPS is required. Every attempt resolves DNS, rejects non-public/private/link-local/loopback/multicast/reserved/unspecified addresses (including mapped IPv6), then pins the HTTP connector to that validated address set, retaining hostname TLS verification. Redirects and environment proxies are disabled. For development/test only, `WEBHOOK_ALLOWED_LOOPBACK_ORIGINS` can allow exact origins such as `http://127.0.0.1:8765`; private LAN addresses are still rejected. Production ignores this exception. Network egress controls remain a deployment-layer defense for a later phase.

## Validation

Real Redis/Celery plus standalone publisher delivered to a temporary aiohttp loopback receiver: 200 succeeded in one attempt; 500 then 200 succeeded in two; 400 failed permanently in one. Receiver verified HMAC on all four actual HTTP requests. Temporary subscriptions were soft-deleted and receiver stopped after validation. Browser verified ADMIN create/show-once/dismiss/toggle/delete/history and DISPATCHER/RESPONDER direct-route denial, with no JS errors. Tests cover atomic rollback, subscription timing/filtering, replay-safe outbox, concurrent delivery fencing, broker recovery, safe errors, timeout/408/429/500 retries, 400 permanence, tenant isolation, RBAC, encryption and SSRF/redirect rejection.

Implementation references: [Fernet](https://cryptography.io/en/stable/fernet/) and [aiohttp client/connector](https://docs.aiohttp.org/en/stable/client_reference.html).

## Current limitations

Polling is a separate required process; no scheduler HA automation or operational alerting yet. History is latest-100 without pagination; outbox/history retention and encryption-key rotation tooling are future work. Subscription deletion is soft; a request already in flight can finish. Retry-After is not honored; retries are intentionally bounded. HTTP delivery ordering across distinct events is not guaranteed. Data is demo operational data; subscribers must be trusted organization integrations.
