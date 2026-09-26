# Identity, tenancy and authorization

## Request flow

Credentials are checked using `argon2-cffi` Argon2id. Its maintained defaults use
64 MiB memory, time cost 3 and parallelism 4. Hash/verify run in the thread pool,
not on the async event loop. Login rehashes when library parameters change. Unknown
users perform a dummy hash verification; unknown, incorrect-password and inactive
accounts return the same invalid-credentials response. Password input is bounded
at 12–128 characters and wrapped in Pydantic SecretStr.

Emails are trimmed, case-folded, validated with EmailStr, and stored with a global
unique constraint and a lowercase check. Each user belongs to exactly one
organization. Registration creates the organization and first ADMIN in one
transaction; duplicate email errors roll back the organization too. Registration
cannot accept an existing organization ID.

JWT access tokens are signed with HS256 and a randomly generated local secret.
Only HS256 is accepted. `sub`, `type=access`, `iat`, `exp`, `jti` and issuer are
required and validated. JWTs contain no organization/role authority. Every
request loads the current active user from PostgreSQL before constructing the
immutable `RequestContext(user_id, organization_id, role)`.

The context selects an organization service, whose repository scopes every
operational read/write. The API never accepts an organization selector from the
client. Known cross-tenant IDs return 404. ADMIN/DISPATCHER/RESPONDER policies are
central dependencies, not UI-only restrictions. Last-admin changes lock the
organization and involved users, recheck the acting administrator, and count
active admins in the same transaction.

## Refresh sessions

Each refresh value contains 48 random bytes encoded using `secrets.token_urlsafe`.
Only its SHA-256 hash is stored; random high-entropy tokens do not need a slow
password KDF. The database stores the user FK, expiry, revocation time, creation
time and replacement FK, all timestamps timezone aware.

Refresh locks the user followed by the refresh row, checks active/expiry/revocation,
inserts a new hashed credential and revokes/links the old one in one transaction.
Only one concurrent rotation can succeed. A rotated credential is rejected; its
replacement remains usable (there is no family-wide replay revocation policy).
Logout revokes the presented refresh credential. Deactivation revokes all of that
user's refresh credentials and makes existing access JWTs fail database identity
checks. Rows retain history; pruning is future work.

Cookie: `emergency_refresh`, HttpOnly, SameSite=Strict, Path=`/api/auth`, Max-Age
matching configured lifetime, Secure in production. No domain attribute is set.
Cookie-based endpoints reject foreign Origin and cross-site fetch metadata.
CORS permits only the two local frontend origins with credentials. Non-browser
clients without Origin can authenticate normally. Production hosting requires
explicit trusted origins, HTTPS and same-site frontend/API configuration.

The React session keeps access tokens only in memory. Reload performs refresh and
`/auth/me`. Axios sends Bearer headers, shares one refresh promise across concurrent
401 responses, and retries each request once. Persistent 401/refresh failure clears
identity and protected routes return to login. Session revisions prevent old
requests from crossing logout/login boundaries. Logout waits for an in-flight
rotation before revoking the resulting cookie; network failure is reported honestly.
Refresh coordination is per browser tab, not across tabs.

## WebSocket

The browser first opens `/ws/events`, then sends a JSON authentication frame. The
access token never appears in the URL or access log. The server waits at most five
seconds, validates JWT and current DB user, then registers the socket only in its
organization's ConnectionManager. No ready/state events precede authentication.
Origins are checked before accepting the connection.

Each organization has separate stream IDs, sequence numbers, queues and tasks.
The browser refreshes identity before each new socket connection. The server closes
a socket at token expiry and checks current DB identity at most every five seconds;
API role/active changes request immediate closure of matching user sockets. The
next connection uses the current role. A database verification outage closes the
connection rather than retaining unverified access. Queues remain bounded; slow
subscribers close independently of operational transactions.

Logout clears the browser's operations provider and socket. As with access JWTs,
a separately retained socket credential is valid until expiry unless the user is
deactivated. No distributed revocation/broadcast service has been introduced.

## Explicit test seams

`create_app(repository=...)` and `trusted_context=...` are server-side test injection
facilities retained for isolated operational tests and tenant fixtures. They are
not exposed by HTTP, environment variables, or the normal `app.main:app` entry
point. Production requests derive context exclusively from a current database
identity. The public application does not have a global ORG-DEMO tenant.

## edge and credential hardening

Login, registration and refresh use atomic fixed-window Redis limits. Login is
limited by both client IP and normalized email; registration uses stricter
per-email limits; refresh uses IP and refresh-token dimensions. Redis keys contain
HMAC-derived values, not raw email addresses, IPs or refresh tokens. If Redis is
unavailable, each API process switches to a bounded 10,000-entry in-memory
limiter and increments `auth_rate_limit_fallback_total`; this preserves a local
limit but does not coordinate replicas. The readiness endpoint remains unhealthy
while Redis is unavailable. Liveness remains independent of dependencies.

Forwarded client addresses are used only when the TCP peer belongs to an
explicitly configured `TRUSTED_PROXY_CIDRS` range. Uvicorn proxy-header parsing
is disabled. The Nginx edge replaces `X-Forwarded-For` with the socket address;
the AWS API security group accepts traffic only from the load balancer, and the
configured trusted range is the VPC CIDR. Direct production requests require an
explicit `ALLOWED_HOSTS` list and HTTPS `ALLOWED_ORIGINS`; wildcard hosts are
rejected. Bodies are capped at 256 KiB in Nginx and the ASGI request stream.
Responses include a restrictive CSP, frame denial, MIME sniffing protection,
referrer policy and permissions policy. HSTS is enabled only in production and
only when explicitly configured; the Nginx edge emits it for HTTPS traffic.

Database credentials now use separate roles. The explicit bootstrap principal
creates `emergency_migrator` and `emergency_app`; Alembic uses the migrator, which
owns the public schema and application objects, while API/worker/publisher use
the runtime role with DML and sequence rights but no schema `CREATE`, role or
database administration privileges. In AWS only the one-off bootstrap task's
execution role can read the RDS master credential. This bootstrap is explicit;
application startup never changes database roles.

Metrics require a separate bearer token and are not exposed by the frontend
proxy. Authentication counters have only fixed low-cardinality labels. Security
logs retain the failure class but omit exception values, which may contain SQL
or connection details.

## Logging and limitations

Logs record login success/failure, refresh failure, role/active changes and socket
authentication rejection using event names and internal IDs. They do not log
passwords, JWTs, refresh values, signing secrets, Authorization headers or credential
URLs. Tests assert sensitive values are absent from captured security logs.

No email verification, password reset, MFA, external identity provider, token-family-wide
reuse revocation, cross-tab locking, refresh retention job or key rotation service
is implemented. These are explicit limits, not claims of production security.

Implementation references: [argon2-cffi API](https://argon2-cffi.readthedocs.io/en/stable/api.html)
and [PyJWT validation API](https://pyjwt.readthedocs.io/en/stable/api.html).

## integration boundary

Only ADMIN can configure external destinations or inspect webhook payload/history. Secrets are generated once and authenticated-encrypted at rest. Production requires public HTTPS destinations; every delivery validates and pins DNS, disables redirects and ignores proxy environment settings. Explicit loopback exceptions work only in development/test. Receivers must verify timestamped HMAC and deduplicate delivery IDs. Full contract and remaining deployment-layer protections: [webhooks](webhooks.md).

## advisory AI boundary

ADMIN/DISPATCHER may enqueue summaries; RESPONDER only reads completed tenant
artifacts/jobs. Worker context comes from persisted job organization, not request
JSON. Providers get plain data without DB sessions or tools. Prompts separate
trusted instructions from untrusted operational input; structured output is
validated before persistence. AI has no operational mutation path. React renders
escaped text and always labels outputs for human review; fake demo output is
explicit. This prevents action execution but does not guarantee factual accuracy.
External provider calls use a fixed HTTPS endpoint, no redirects/proxy environment,
a timeout and response size cap. Keys and provider error bodies are not logged.
See [AI workflows](ai-workflows.md) for remaining quotas/evaluation limitations.

Metrics require a separate `METRICS_TOKEN` and are not proxied publicly.
Request IDs are validated; JSON logs omit payloads/headers/exception values. Redis
Pub/Sub is trusted private infrastructure and routes using server-owned tenant IDs.
