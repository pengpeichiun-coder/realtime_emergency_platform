# AWS deployment prerequisites

**Not deployed.** Terraform describes a proposed target and is validated offline.
There is no account-specific plan, live AWS verification or cloud resource creation.
The public repository contains no AWS deployment workflow or production secrets.

## Required decisions before deployment

An operator would need an authorized AWS account/profile and region, an owned
domain and validated ACM certificate, an encrypted/versioned remote state bucket,
reviewed network sizing and budget, immutable application images in ECR, and a
production secrets/bootstrap plan. Example files are templates, not configured
production inputs. No fake values should be presented as a valid account plan.

If GitHub deployment automation is introduced later, configure an explicitly
reviewed production environment and exact repository/environment OIDC trust.
Terraform contains the IAM architecture, while the public CI has read-only
repository permissions and never requests AWS credentials.

## What offline checks establish

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra validate
terraform -chdir=infra test
```

The mock test checks private/encrypted data services, private Fargate tasks,
HTTPS/readiness routing, immutable ECR tags, initially stopped services and
separation of runtime/bootstrap secrets. It is not a live integration test.

The architecture proposes RDS PostgreSQL/PostGIS, Valkey with TLS/AUTH, ALB,
private Fargate tasks, Secrets Manager and CloudWatch. HTTPS egress to public
IPv4 destinations remains a broad boundary reported by the IaC scanner;
application webhook URL validation does not replace network policy.

## Cost and secrets

Standing costs can include NAT/public IPv4, ALB, RDS and Valkey even when ECS
service counts are zero. Task runtime, logs, backups, storage, ECR and secret API
usage add variable costs. No current regional price estimate or cost approval
is claimed. Any future billable plan/apply requires separate authorization.

Runtime credentials include database and Redis URLs, JWT secret, webhook
encryption key and metrics token. Migration/bootstrap privileges are separate.
An OpenAI credential is optional and only used if explicitly configured; the
validated provider is fake. Secret values and Terraform state must stay private.

See [AWS architecture](aws-deployment.md) and the
[production environment contract](production-environment.md). Deployment helpers
are retained as offline-tested design evidence, not as proof of live AWS behavior.
