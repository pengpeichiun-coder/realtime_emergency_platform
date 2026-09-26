# Provider mocks: this creates no resources and makes no AWS API requests.
mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-southeast-1a", "ap-southeast-1b"] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}
mock_provider "random" {}
variables {
  domain_name       = "test.example.com"
  certificate_arn   = "arn:aws:acm:ap-southeast-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  github_repository = "example/emergency-test"
  image_tag         = "sha-0000000000000000000000000000000000000000"
}
run "private_data_and_zero_task_bootstrap" {
  command = plan
  assert {
    condition     = !aws_db_instance.main.publicly_accessible && aws_db_instance.main.storage_encrypted && aws_db_instance.main.backup_retention_period == 7
    error_message = "RDS must remain private, encrypted and backed up."
  }
  assert {
    condition     = aws_elasticache_replication_group.main.transit_encryption_enabled && aws_elasticache_replication_group.main.at_rest_encryption_enabled
    error_message = "Redis requires TLS and encryption at rest."
  }
  assert {
    condition     = alltrue([for service in aws_ecs_service.main : service.desired_count == 0 && !service.network_configuration[0].assign_public_ip])
    error_message = "Initial services must remain stopped and private until bootstrap/deployment."
  }
  assert {
    condition     = length(aws_subnet.application) == 2 && length(aws_subnet.data) == 2 && length(aws_ecs_service.main) == 4
    error_message = "Use two application/data subnets and four application services."
  }
  assert {
    condition     = alltrue([for repository in aws_ecr_repository.image : repository.image_tag_mutability == "IMMUTABLE"])
    error_message = "ECR tags must be immutable."
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_target_group.service["api"].health_check[0].path == "/health/ready"
    error_message = "HTTPS and API dependency readiness must remain configured."
  }
  assert {
    condition     = length(local.task_secret_sources.bootstrap) == 4 && toset(keys(local.task_secret_sources.bootstrap)) == toset(["DATABASE_URL", "MIGRATION_DATABASE_URL", "RDS_USERNAME", "RDS_PASSWORD"])
    error_message = "Bootstrap must get the runtime/migration URLs and RDS master fields exactly once."
  }
  assert {
    condition     = local.task_secret_sources.bootstrap.DATABASE_URL == "DATABASE_URL" && local.task_secret_sources.bootstrap.MIGRATION_DATABASE_URL == "MIGRATION_DATABASE_URL" && local.task_secret_sources.migrate.DATABASE_URL == "MIGRATION_DATABASE_URL"
    error_message = "Runtime, bootstrap and migration task URL sources must be distinct and correctly named."
  }
  assert {
    condition     = !contains(keys(local.task_secret_sources.api), "OPENAI_API_KEY") && contains(keys(local.task_secret_sources.worker), "OPENAI_API_KEY") && !contains(keys(local.task_secret_sources.publisher), "OPENAI_API_KEY") && !contains(keys(local.task_secret_sources.migrate), "JWT_SECRET")
    error_message = "Provider and auth credentials must only be injected into workloads that require them."
  }
}
