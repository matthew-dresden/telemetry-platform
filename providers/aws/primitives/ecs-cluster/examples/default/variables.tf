variable "name" {
  type        = string
  description = "ECS cluster name."
}

variable "capacity_providers" {
  type        = list(string)
  description = "List of capacity providers to associate with the cluster."
  default     = ["FARGATE", "FARGATE_SPOT"]
}

variable "default_capacity_provider_strategy" {
  type = list(object({
    capacity_provider = string
    weight            = number
    base              = optional(number, 0)
  }))
  description = "Default capacity provider strategy. Defaults to [] so the module auto-derives a strategy from capacity_providers."
  default     = []
}

variable "enable_container_insights" {
  type        = bool
  description = "Whether to enable Container Insights."
  default     = true
}

variable "execute_command_logging" {
  type        = string
  description = "Logging configuration for ECS Exec."
  default     = "DEFAULT"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "ecs-cluster-module-testing"
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
