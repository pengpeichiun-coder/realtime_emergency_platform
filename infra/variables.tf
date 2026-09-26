variable "region" {
  type    = string
  default = "ap-southeast-1"
}
variable "name" {
  type    = string
  default = "emergency-platform"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name))
    error_message = "Use 3–24 lowercase letters, digits or hyphens, starting with a letter."
  }
}
variable "domain_name" {
  description = "An owned DNS name pointing to the ALB; no https prefix."
  type        = string
  validation {
    condition     = can(regex("^[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.domain_name))
    error_message = "Provide an owned fully qualified DNS name."
  }
}
variable "certificate_arn" {
  description = "Existing validated ACM certificate in this AWS region for domain_name."
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:", var.certificate_arn))
    error_message = "Provide an ACM certificate ARN."
  }
}
variable "github_repository" {
  description = "Exact owner/repository allowed to deploy via the production environment."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use the real GitHub owner/repository."
  }
}
variable "github_oidc_provider_arn" {
  description = "Reuse an existing account-wide GitHub OIDC provider if present; otherwise null creates one."
  type        = string
  default     = null
}
variable "image_tag" {
  description = "Initial reviewed immutable commit image tag; services bootstrap at desired_count=0."
  type        = string
  validation {
    condition     = can(regex("^sha-[a-f0-9]{40}$", var.image_tag))
    error_message = "Use sha- followed by the full reviewed Git commit."
  }
}
variable "api_desired_count" {
  type    = number
  default = 2
  validation {
    condition     = var.api_desired_count >= 1 && var.api_desired_count <= 4 && floor(var.api_desired_count) == var.api_desired_count
    error_message = "Portfolio API capacity must be an integer from 1 to 4."
  }
}
variable "task_cpu" {
  type    = number
  default = 512
  validation {
    condition     = contains([256, 512, 1024], var.task_cpu)
    error_message = "Choose a conservative Fargate CPU value: 256, 512 or 1024."
  }
}
variable "task_memory" {
  type    = number
  default = 1024
  validation {
    condition     = contains([512, 1024, 2048, 3072, 4096], var.task_memory) && var.task_memory >= var.task_cpu * 2 && (var.task_cpu != 256 || var.task_memory <= 2048)
    error_message = "Choose a supported Fargate memory/CPU pair."
  }
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}
variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}
variable "log_retention_days" {
  type    = number
  default = 14
}
variable "deletion_protection" {
  type    = bool
  default = true
}
