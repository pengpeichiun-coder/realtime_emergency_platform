output "alb_dns_name" { value = aws_lb.main.dns_name }
output "application_url" { value = "https://${var.domain_name}" }
output "github_deploy_role_arn" { value = aws_iam_role.deploy.arn }
output "deployment_config" {
  description = "Non-secret deployment metadata; export to ignored aws-deployment.json after approved provisioning."
  value = {
    region             = var.region
    cluster            = aws_ecs_cluster.main.name
    ecr                = { for name, image in aws_ecr_repository.image : name => image.repository_url }
    services           = { for name, service in aws_ecs_service.main : name => service.name }
    task_families      = { for name, task in aws_ecs_task_definition.service : name => task.family }
    subnets            = aws_subnet.application[*].id
    security_groups    = [aws_security_group.tasks.id]
    api_desired_count  = var.api_desired_count
    runtime_secret_arn = aws_secretsmanager_secret.runtime.arn
    redis_secret_arn   = aws_secretsmanager_secret.redis.arn
    database_host      = aws_db_instance.main.address
    redis_host         = aws_elasticache_replication_group.main.primary_endpoint_address
  }
}
