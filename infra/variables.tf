variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "dd-log-analyzer"
}

variable "schedule_rate" {
  description = "EventBridge schedule rate"
  type        = string
  default     = "rate(5 minutes)"
}

variable "lambda_memory" {
  description = "Lambda memory in MB"
  type        = number
  default     = 512
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 300
}

variable "secret_name" {
  description = "Secrets Manager secret name for API credentials"
  type        = string
  default     = "dd-log-analyzer/secrets"
}

variable "ssm_config_path" {
  description = "SSM Parameter Store path for analysis config"
  type        = string
  default     = "/dd-log-analyzer/config"
}

variable "report_retention_days" {
  description = "Days to keep reports in S3"
  type        = number
  default     = 30
}

variable "alert_ttl_hours" {
  description = "Hours to keep alert dedup records in DynamoDB"
  type        = number
  default     = 24
}
