# Advisory AI workflows

## Product and permissions

ADMIN and DISPATCHER can queue incident summaries (`POST /api/incidents/{id}/ai-summary`) and tenant shift handovers (`POST /api/ai/shift-handover`). Both return 202 with job ID without contacting an LLM. All authenticated roles, including RESPONDER, can read tenant jobs and completed artifacts (`GET /api/ai/artifacts`, optional incident_id filter; `GET /api/ai/artifacts/{id}`). Foreign IDs return 404. The UI exposes incident generation on Overview and shift generation on Jobs and reports; it displays job states, structured sections, provider/model/version and **AI-generated · Human review required**. Fake output is additionally labeled as deterministic demo output.

## Execution and persistence

The job service writes a durable QUEUED job. The independent publisher sends its ID through Redis; a real Celery worker claims it, commits RUNNING, gathers a repeatable-read, read-only tenant snapshot, calls the provider, validates output and atomically inserts an `ai_artifacts` row together with SUCCEEDED/result payload. A unique job_id and the existing advisory lock prevent duplicate durable artifacts. Each task owns a new asyncio loop/engine; no request session crosses into a worker. Job result embeds the validated artifact for convenient polling, while the artifacts table provides explicit retrieval and metadata.

The artifact records organization, actor, source incident (optional), job, type, provider, model, prompt version and JSONB content. Incident ID is a historical logical reference, not a foreign key, so demo resets do not delete reports. Organization, user and job references are enforced; deleting a job cascades its artifact. No artifact update/delete API is exposed.

## Providers and prompts

`LLMProvider` is a small async protocol. `FakeLLMProvider` is deterministic, performs no network calls and identifies itself as `fake / deterministic-v1`. `OpenAIProvider` uses the fixed HTTPS Responses API endpoint through the existing aiohttp dependency; business code contains no provider-specific HTTP logic. It uses `store=false`, no tools, separate instructions and untrusted JSON operational input, strict JSON Schema and at most 1,500 output tokens. Provider response reads are capped at 1 MB. Pydantic validates required fields, strict types, no extra fields, text length and array limits before any artifact is saved.

Prompts are centralized and persisted as `incident_summary_v1` and `shift_handover_v1`. Incident summaries include summary/key_events/current_status/open_items; handovers include overview/active_incidents/units_in_service/priority_items. Context is built solely from server-owned tenant records, never caller-supplied organization/context. Incident context includes incident, dispatches, involved units, full matching activity and timestamps. Shift context includes active incidents, current dispatches, units and the latest 200 activity records within 24 hours. Inputs exceeding 100 KB fail explicitly; output lists contain at most 50 items. No silent claim of a complete unbounded historical report is made.

## Configuration

| Variable | Behavior |
| --- | --- |
| LLM_PROVIDER | `fake` default; `openai` enables external calls |
| LLM_MODEL | OpenAI model, default `gpt-4o-mini`; ignored by fake |
| OPENAI_API_KEY | Required only for openai; backend environment only |
| LLM_TIMEOUT_SECONDS | Total provider timeout, default 30, allowed 1–120 |

Restart worker after changing provider configuration. Missing/invalid configuration fails safely; it never silently falls back from OpenAI to fake. The local configuration is fake. **Real external LLM smoke test was not run because no provider credential was configured.** No account/credit purchase or external inference cost was incurred. Fake-provider integration ran through the actual Redis-backed worker.

## Retry and safety

The job layer permits three attempts, with 2/4-second exponential delays. Provider timeout, connection/payload transport errors, 408, 429 and 5xx are transient. Bad credentials, other 4xx, invalid config/request, refusal, incomplete response and invalid structured results are permanent. Errors are fixed safe messages; provider response bodies, API keys and raw stack traces are not exposed through jobs or application worker logs. A worker crash before commit can cause a repeated external provider request/cost, but cannot create multiple durable artifacts for one job.

The provider receives **only a plain data snapshot and prompt**, no database/session, operational service, function tools or executable capability. Incident text/activity is explicitly untrusted. LLM content cannot alter incidents, dispatches, resources, users or permissions. React renders text without raw HTML. This boundary prevents action execution; it cannot guarantee factual correctness or eliminate misleading generated text, so human review is mandatory. This is a fictional portfolio system, not emergency command advice.

## Validation and limits

Tests cover provider selection and fake determinism, both job types, structured validation, prompt metadata, artifact persistence and duplicate execution, tenant isolation, all roles, transient recovery/permanent failure, safe logging and unchanged operational snapshots. Mocked provider HTTP tests validate the actual Responses request contract, timeout/status classification, refusals and incomplete output without using credentials. Browser/system validation and exact counts are recorded in [validation](validation.md).

No per-tenant AI quotas, cost dashboard, prompt-quality evaluation corpus, artifact retention policy, cancellation or manual retry UI yet. Generated summaries are snapshots, may become stale, and do not auto-refresh. Default model availability/account access requires a real credential smoke test before external-provider use.

Protocol reference: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
