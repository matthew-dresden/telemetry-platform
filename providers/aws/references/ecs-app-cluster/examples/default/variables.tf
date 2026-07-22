variable "cluster_name" {
  type        = string
  description = "(Required) Name of the ECS Fargate cluster."
  default     = "telemetry-ecs-app-cluster-test"
}

variable "capacity_providers" {
  type        = list(string)
  description = "(Optional) List of capacity providers to associate with the cluster."
  default     = ["FARGATE", "FARGATE_SPOT"]
}

variable "enable_container_insights" {
  type        = bool
  description = "(Optional) Whether to enable Container Insights."
  default     = true
}

variable "execution_role_name" {
  type        = string
  description = "(Required) Name of the shared ECS task execution IAM role."
  default     = "telemetry-ecs-app-cluster-exec-role-test"
}

variable "dashboard_name" {
  type        = string
  description = "(Required) Name for the cluster CloudWatch dashboard."
  default     = "telemetry-ecs-app-cluster-test"
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "ecs-app-cluster-module-testing"
  }
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
