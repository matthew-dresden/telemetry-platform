variable "name" {
  type        = string
  description = "ECS service name."
}

variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "subnet_cidr_a" {
  type        = string
  description = "CIDR block for the first fixture subnet."
  default     = "10.0.1.0/24"
}

variable "subnet_cidr_b" {
  type        = string
  description = "CIDR block for the second fixture subnet."
  default     = "10.0.2.0/24"
}

variable "override_subnets" {
  type        = list(string)
  description = "Override subnet IDs. When non-empty, the fixture subnets are not used. Used in validation error tests."
  default     = []
}

variable "task_cpu" {
  type        = number
  description = "CPU units for the task."
  default     = 512
}

variable "task_memory" {
  type        = number
  description = "Memory (MiB) for the task."
  default     = 1024
}

variable "desired_count" {
  type        = number
  description = "Desired number of tasks."
  default     = 1
}

variable "enable_autoscaling" {
  type        = bool
  description = "Whether to enable autoscaling."
  default     = false
}

variable "create_log_group" {
  type        = bool
  description = "Whether to create a CloudWatch log group."
  default     = true
}

variable "log_group_retention_days" {
  type        = number
  description = "Log group retention in days."
  default     = 30
}

variable "adot_image" {
  type        = string
  description = "ADOT collector container image URI. Must reference a real ECR or public image."
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
}

variable "adot_config_value" {
  type        = string
  description = "ADOT config content to store in SSM. Referenced by the container at runtime."
  default     = "receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 0.0.0.0:4318\nexporters:\n  logging:\n    loglevel: info\nservice:\n  pipelines:\n    traces:\n      receivers: [otlp]\n      exporters: [logging]\n"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "ecs-service-module-testing"
  }
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
