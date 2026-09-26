# Infrastructure preparation only

See [AWS deployment guide](../docs/aws-deployment.md) for configuration, cost
boundary, secrets/bootstrap/migration workflow, validation and exact commands.
Do not apply or launch tasks without explicit authorization for billable resources.

For local checks without credentials:

```bash
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

Commit `.terraform.lock.hcl`; never commit state, plans, actual backend config or
operator tfvars. The `.pem` in the backend is a public AWS CA bundle, not a secret.
