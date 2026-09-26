# Values are initialized explicitly outside Terraform after cost approval.
resource "aws_secretsmanager_secret" "runtime" {
  name                    = "${var.name}/runtime"
  recovery_window_in_days = 7
}
resource "aws_secretsmanager_secret" "redis" {
  name                    = "${var.name}/redis-auth"
  recovery_window_in_days = 7
}
resource "aws_secretsmanager_secret_version" "redis" {
  secret_id     = aws_secretsmanager_secret.redis.id
  secret_string = random_password.redis.result
}
