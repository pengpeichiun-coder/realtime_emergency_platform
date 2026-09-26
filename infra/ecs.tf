resource "aws_ecr_repository" "image" {
  for_each             = toset(["backend", "frontend"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_ecr_lifecycle_policy" "image" {
  for_each   = aws_ecr_repository.image
  repository = each.value.name
  policy     = jsonencode({ rules = [{ rulePriority = 1, description = "Expire untagged build layers after seven days", selection = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 7 }, action = { type = "expire" } }] })
}
resource "aws_ecs_cluster" "main" { name = var.name }
locals {
  tasks = {
    frontend  = { image = "frontend", command = null, port = 8080 }
    api       = { image = "backend", command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"], port = 8000 }
    worker    = { image = "backend", command = ["celery", "-A", "app.workers.celery_app:celery_app", "worker", "--concurrency=2", "--loglevel=INFO"], port = null }
    publisher = { image = "backend", command = ["python", "-m", "app.workers.publisher"], port = null }
    migrate   = { image = "backend", command = ["alembic", "upgrade", "head"], port = null }
    bootstrap = { image = "backend", command = ["python", "-m", "app.scripts.bootstrap_database", "--initialize-role"], port = null }
  }
  task_secret_sources = {
    api = {
      for key in ["DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "JWT_SECRET", "WEBHOOK_ENCRYPTION_KEY", "METRICS_TOKEN"] : key => key
    }
    worker = {
      for key in ["DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "WEBHOOK_ENCRYPTION_KEY", "OPENAI_API_KEY"] : key => key
    }
    publisher = {
      for key in ["DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL"] : key => key
    }
    migrate = { DATABASE_URL = "MIGRATION_DATABASE_URL" }
    bootstrap = {
      DATABASE_URL           = "DATABASE_URL"
      MIGRATION_DATABASE_URL = "MIGRATION_DATABASE_URL"
      RDS_USERNAME           = "RDS_USERNAME"
      RDS_PASSWORD           = "RDS_PASSWORD"
    }
  }
  environment = [
    { name = "APP_ENV", value = "production" },
    { name = "LOG_FORMAT", value = "json" },
    { name = "ALLOWED_ORIGINS", value = "https://${var.domain_name}" },
    { name = "ALLOWED_HOSTS", value = var.domain_name },
    { name = "TRUSTED_PROXY_CIDRS", value = aws_vpc.main.cidr_block },
    { name = "ENABLE_HSTS", value = "true" },
    { name = "LLM_PROVIDER", value = "fake" },
    { name = "LLM_MODEL", value = "gpt-4o-mini" },
    { name = "DB_SSL_CA_FILE", value = "/app/app/db/rds-ca-bundle.pem" },
  ]
}
resource "aws_ecs_task_definition" "service" {
  for_each                 = local.tasks
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = each.key == "bootstrap" ? aws_iam_role.bootstrap_execution.arn : aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([merge({
    name            = each.key
    image           = "${aws_ecr_repository.image[each.value.image].repository_url}:${var.image_tag}"
    essential       = true
    user            = each.key == "frontend" ? "101" : "10001"
    command         = each.value.command
    stopTimeout     = 120
    linuxParameters = { initProcessEnabled = true }
    portMappings    = each.value.port == null ? [] : [{ containerPort = each.value.port, protocol = "tcp" }]
    environment     = each.key == "frontend" ? [] : local.environment
    secrets = each.key == "frontend" ? [] : [
      for name, source in local.task_secret_sources[each.key] : {
        name      = name
        valueFrom = contains(["RDS_USERNAME", "RDS_PASSWORD"], source) ? "${aws_db_instance.main.master_user_secret[0].secret_arn}:${lower(trimprefix(source, "RDS_"))}::" : "${aws_secretsmanager_secret.runtime.arn}:${source}::"
      }
    ]
    logConfiguration = { logDriver = "awslogs", options = {
      awslogs-group  = aws_cloudwatch_log_group.service[each.key].name,
      awslogs-region = var.region, awslogs-stream-prefix = "ecs"
    } }
    }, each.key == "api" ? { healthCheck = {
      command  = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"],
      interval = 30, timeout = 5, retries = 3, startPeriod = 30
  } } : {})])
}
resource "aws_ecs_service" "main" {
  for_each                           = toset(["frontend", "api", "worker", "publisher"])
  name                               = "${var.name}-${each.key}"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.service[each.key].arn
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  desired_count                      = 0 # Bootstrap secrets/schema/images explicitly before deployment.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = contains(["api", "frontend"], each.key) ? 60 : null
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.application[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = contains(["api", "frontend"], each.key) ? [each.key] : []
    content {
      target_group_arn = aws_lb_target_group.service[each.key].arn
      container_name   = each.key
      container_port   = local.tasks[each.key].port
    }
  }
  lifecycle { ignore_changes = [task_definition, desired_count] }
  depends_on = [aws_lb_listener_rule.api, aws_iam_role_policy.runtime_secret]
}
