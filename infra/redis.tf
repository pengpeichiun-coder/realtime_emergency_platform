resource "random_password" "redis" {
  length  = 40
  special = false
}
resource "aws_elasticache_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.data[*].id
}
resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = var.name
  description                = "Private Celery broker and best-effort realtime backplane"
  engine                     = "valkey"
  engine_version             = "7.2"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  auth_token                 = random_password.redis.result
  snapshot_retention_limit   = 1
  auto_minor_version_upgrade = true
}
