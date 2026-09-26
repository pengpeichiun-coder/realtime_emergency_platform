# AWS deployment preparation

**Implementation is prepared; no AWS resources have been provisioned.** No AWS
credential/profile was configured during validation. No real account plan, apply,
ECR push, secret write or ECS task launch was performed. Provider-mocked Terraform
tests are offline checks, not evidence of a deployed AWS system.

See the current [AWS deployment gate record](aws-deployment-gate.md) for the
verified 2026-09-23 credentials, state, domain/TLS, GitHub environment, and
production-secret readiness inventory.

See the [production environment contract](production-environment.md) for the
canonical runtime variables/secrets, hostname/TLS inputs, demo security profile,
bootstrap sequence and cost drivers.

## Target and tradeoffs

`infra/` is a flat Terraform root with networking, load balancer, database, Redis,
ECS, IAM, secrets, monitoring, variables and outputs. Provider versions/checksums
are locked. It defines a VPC with two AZs and separate public, application and data
subnets. Only the ALB has public ingress (HTTP redirects to HTTPS). Fargate tasks
have no public IP; RDS and ElastiCache accept traffic only from task security groups.
One NAT gateway provides outbound HTTPS for ECR, Secrets Manager, logs and external
webhook/LLM services. Task egress is restricted to port 443; public destinations
remain open so independently configured HTTPS webhook partners can be reached.
The Trivy IaC report intentionally flags that outbound boundary (AWS-0104). Data subnets have no internet default route.

An existing validated ACM certificate and an **owned DNS name** are required.
Point that name at `alb_dns_name` after provisioning. ALB routes `/api/*`, `/ws/*`
and `/health/*` to API tasks; other requests serve static React from frontend tasks.
It supports WebSocket upgrades directly, with 120-second idle timeout; Uvicorn's
WebSocket pings keep quiet sessions alive. Reconnect still performs REST resync.
CloudFront is deliberately omitted: one HTTPS entry is sufficient for this scope.
Production HttpOnly refresh cookies remain Secure/SameSite=Strict. Metrics are
private: ALB does not route `/metrics` to the API, and the API also requires a token.

Four ECS services reuse two images: frontend; API; Celery worker; publisher. Each
has its own command/log group. One Uvicorn process per task preserves process
metric semantics; API target count defaults to two after deployment. Worker
concurrency is two. Fargate CPU/memory variables default to 0.5 vCPU/1 GiB per task;
initial desired counts are **zero** until secrets, images and schema are ready.
Rollouts use a circuit breaker with rollback; the deployment script checks the
final revision so a stable rollback cannot be mistaken for successful deployment.

RDS PostgreSQL 17 uses encrypted gp3 storage, seven-day backups, private networking,
minor upgrades and deletion protection. Terraform never creates application tables.
The explicit bootstrap task installs PostGIS using RDS master credentials and
creates separate `emergency_app` runtime and `emergency_migrator` DDL roles. The
runtime role has application DML privileges but cannot create schema objects; Alembic
runs as the migration role. Normal services never receive RDS master credentials.

ElastiCache Valkey 7.2 uses TLS, AUTH, encryption at rest and private access. Celery
and Redis clients use verified `rediss://` URLs, including `ssl_cert_reqs=required`.
RDS URLs use `ssl=verify-full` and a SQLAlchemy `SSLContext` built from `DB_SSL_CA_FILE=/app/app/db/rds-ca-bundle.pem`, which
points to the committed **public** AWS RDS CA bundle retrieved from AWS's trust
store on 2026-09-22. No private key is in that file. Database business pools,
advisory-lock connections, Alembic, and the privileged bootstrap pass that
certificate as a verified SSL context, rather than relying on libpq-only
environment variables. Review CA rotation/bundle updates before deployment;
expired/missing trust must fail rather than disable verification.

Portfolio defaults are deliberately not highly available: one NAT gateway, one RDS
instance and one Redis node. Multiple API replicas remove local socket fanout
isolation, but do not make every dependency HA. RDS, Redis, ALB, NAT, public IPv4,
Fargate, logs, secrets, storage and network transfer can all incur charges even
with zero ECS tasks. Review regional prices and approve costs before applying.
No Kubernetes, service mesh or microservice decomposition is introduced.

## Secrets and Terraform state

The runtime Secrets Manager container starts **empty**. The explicit operator
script `scripts/aws_init_secrets.py` generates JWT/Fernet/metrics/database secrets,
constructs verified-TLS DB/Redis URLs and writes using stdin. It refuses an existing
secret version and never prints values. FakeLLM remains configured and
`OPENAI_API_KEY` is empty. No account, credits or provider key is created.

RDS manages its master password in its own secret. Terraform generates Redis AUTH
and stores it in a separate Secrets Manager secret; **that value is also sensitive
Terraform state**. Use an existing encrypted, versioned S3 state bucket with tightly
restricted IAM and lockfile support before apply. `backend.hcl.example` documents
configuration; actual state, plans, tfvars and backend inputs are gitignored. A
saved plan may contain sensitive values: never publish it as an artifact. Secret
rotation requires an explicit coordinated change/restart; no silent overwrite.

ECS resolves selected JSON keys at task start. Runtime task roles have no AWS API
permissions; execution roles can pull images/write logs and read only the required
runtime secret. A separate bootstrap execution role alone can read RDS credentials.
ECS secret injection does not hot-reload values; roll tasks after planned rotation.

## OIDC and deployment design

The public repository includes **no AWS deployment workflow**. Terraform retains
an exact repository/production-environment OIDC role design for future review;
public validation never exchanges AWS credentials or applies resources. Reuse an
existing provider ARN if an approved account already has one. No permanent AWS
keys belong in GitHub.

A future deployment pipeline must verify CI for the selected commit, scan and
publish immutable ECR images, serialize rollouts and require a successful one-shot
migration before updating services. The retained `scripts/aws_deploy.py` helper
has offline tests for migration gating and rollback detection. It has not been
run against live Fargate. Initial database bootstrap uses a separate privileged
operator path; the normal deploy role cannot run it or apply Terraform.

Use backward-compatible expand/contract migrations because old tasks can continue
serving while the migration runs. Rollback changes application revisions, not the
database schema. Test recovery before using real operational data. Current tests
prove offline migration gating/rollback detection; actual Fargate/RDS/ALB/OIDC
integration still requires an authorized deployment and smoke validation.

For a public portfolio demo, use only fictional seed data and keep the fake AI
provider unless an owner intentionally configures an external provider. The
registration endpoint currently allows rate-limited creation of isolated new
organizations; there is no invitation-only environment switch. Decide whether
to constrain it at the edge before opening a public demo. `/docs` and
`/openapi.json` are public read-only schema routes through the frontend proxy;
that is a deliberate discoverability choice to revisit for private/customer
deployment. `/metrics` remains un-routed and bearer-protected. Do not run the
demo seed in production.

## Validations already executed without cloud writes

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra validate
terraform -chdir=infra test
```

The mock-provider plan checks private/encrypted data services, two AZ subnet pairs,
zero initial task counts, four services, immutable ECR tags and HTTPS/readiness.
It does not contact AWS. A real `terraform plan` was **not run** because account
credentials, owned domain/certificate and state bucket inputs are unavailable.

## Exact next steps after explicit cloud-cost approval

These commands are design documentation and have **not been executed against AWS**. First configure
an authorized AWS profile, existing encrypted/versioned state bucket, owned domain,
validated ACM certificate, and real GitHub repository. Fill the two copied example
files with those owned values; never put credentials into tfvars. Use a reviewed
commit for `image_tag` (the command-line value below overrides the example).
Run the commands from the repository root.

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
cp infra/backend.hcl.example infra/backend.hcl
# Fill real domain/certificate/repository/state-bucket values before continuing.
terraform -chdir=infra init -backend-config=backend.hcl
terraform -chdir=infra plan -var="image_tag=sha-$(git rev-parse HEAD)" -out=deployment.tfplan
# Review plan and costs. ONLY AFTER EXPLICIT BILLABLE PROVISIONING APPROVAL:
terraform -chdir=infra apply deployment.tfplan
terraform -chdir=infra output -json deployment_config > aws-deployment.json
```

Initial image publication and bootstrap (also billable; run as approved operator):

```bash
IMAGE_TAG="sha-$(git rev-parse HEAD)"
AWS_REGION=$(jq -r .region aws-deployment.json)
REGISTRY=$(jq -r '.ecr.backend | split("/")[0]' aws-deployment.json)
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/amd64 --load -t emergency-platform-backend:local backend
docker buildx build --platform linux/amd64 --load -t emergency-platform-frontend:local frontend
# Install Trivy if needed; the same fixable HIGH/CRITICAL gate must pass.
bash scripts/scan_images.sh
for component in backend frontend; do
  image=$(jq -r --arg name "$component" '.ecr[$name]' aws-deployment.json)
  docker tag "emergency-platform-$component:local" "$image:$IMAGE_TAG"
  docker push "$image:$IMAGE_TAG"
done
python3 scripts/aws_init_secrets.py --config aws-deployment.json --approve-billable
python3 scripts/aws_deploy.py --config aws-deployment.json --image-tag "$IMAGE_TAG" --bootstrap-database --approve-billable
python3 scripts/aws_deploy.py --config aws-deployment.json --image-tag "$IMAGE_TAG" --approve-billable
```

Then configure the DNS alias, validate HTTPS/login/cookies, readiness, two-instance
WebSockets, worker/outbox/AI, log redaction, private networking and backup restore.
Do not claim deployed readiness until these real-cloud checks pass. First image
push/bootstrap is not repeatable blindly: ECR tags are immutable, and the secret
initializer refuses overwrites. A future deployment workflow must be reviewed separately; none is included here.

References: [ECS secret JSON-key injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html),
[RDS extension privileges](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Extensions.html),
[ALB WebSocket support](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html),
[GitHub OIDC on AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws),
[Celery Redis TLS configuration](https://docs.celeryq.dev/en/main/userguide/configuration.html).
