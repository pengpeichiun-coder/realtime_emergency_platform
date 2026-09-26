terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

# State must live in an existing encrypted/versioned bucket before any apply.
# Static validation intentionally uses terraform init -backend=false.
terraform {
  backend "s3" {}
}
