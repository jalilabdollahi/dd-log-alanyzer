terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Using local backend for demo deployment
  backend "local" {
    path = "terraform.tfstate"
  }
}

provider "aws" {
  region  = var.aws_region
  profile = "default"

  default_tags {
    tags = {
      Project     = "dd-log-analyzer"
      ManagedBy   = "terraform"
      Environment = "prod"
    }
  }
}

# Current AWS account ID for naming
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
