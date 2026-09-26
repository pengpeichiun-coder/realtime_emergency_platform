resource "aws_cloudwatch_log_group" "service" {
  for_each          = toset(["frontend", "api", "worker", "publisher", "migrate", "bootstrap"])
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "database" {
  for_each          = toset(["postgresql", "upgrade"])
  name              = "/aws/rds/instance/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}
