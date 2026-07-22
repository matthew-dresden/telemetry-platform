variable "cluster_source" {
  type        = string
  const       = true
  description = "Source path for the ecs-cluster primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/ecs-cluster"
}

variable "execution_role_source" {
  type        = string
  const       = true
  description = "Source path for the iam-role primitive child module used as the ECS execution role. Defaults to the in-repo relative path."
  default     = "../../primitives/iam-role"
}

variable "cloudwatch_source" {
  type        = string
  const       = true
  description = "Source path for the cloudwatch primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/cloudwatch"
}

variable "cluster_name" {
  type        = string
  description = "(Required) Name of the ECS Fargate cluster. Must match ^[a-zA-Z0-9_-]+$ and be 255 characters or fewer."

  validation {
    condition     = length(var.cluster_name) >= 1 && length(var.cluster_name) <= 255 && can(regex("^[a-zA-Z0-9_-]+$", var.cluster_name))
    error_message = "cluster_name must be 1-255 characters and contain only alphanumeric characters, underscores, and hyphens."
  }
}

variable "capacity_providers" {
  type        = list(string)
  description = "(Optional) List of capacity providers to associate with the cluster. Must be a non-empty subset of [FARGATE, FARGATE_SPOT]."
  default     = ["FARGATE", "FARGATE_SPOT"]

  validation {
    condition     = length(var.capacity_providers) >= 1
    error_message = "capacity_providers must contain at least one provider."
  }

  validation {
    condition     = alltrue([for cp in var.capacity_providers : contains(["FARGATE", "FARGATE_SPOT"], cp)])
    error_message = "capacity_providers must be a subset of [FARGATE, FARGATE_SPOT]."
  }
}

variable "enable_container_insights" {
  type        = bool
  description = "(Optional) Whether to enable Container Insights for the cluster. Defaults to true per D11."
  default     = true
}

variable "execution_role_name" {
  type        = string
  description = "(Required) Name of the shared ECS task execution IAM role. Must be 64 characters or fewer."

  validation {
    condition     = length(var.execution_role_name) >= 1 && length(var.execution_role_name) <= 64
    error_message = "execution_role_name must be 1-64 characters to satisfy the IAM role name limit."
  }
}

variable "execution_role_managed_policy_arns" {
  type        = list(string)
  description = "(Optional) List of managed policy ARNs to attach to the shared execution role."
  default     = ["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"]
}

variable "execution_role_inline_policies" {
  type        = map(string)
  description = "(Optional) Map of inline policy name to JSON policy document for the shared execution role. Used to grant ssm:GetParameters on container-secret SSM parameters (the AmazonECSTaskExecutionRolePolicy managed policy does not cover custom SSM parameters). Each value must be valid JSON."
  default     = {}

  validation {
    condition     = alltrue([for v in values(var.execution_role_inline_policies) : can(jsondecode(v))])
    error_message = "Every value in execution_role_inline_policies must be a valid JSON string."
  }
}

variable "alarms" {
  type = map(object({
    comparison_operator = string
    evaluation_periods  = number
    metric_name         = string
    namespace           = string
    period              = number
    statistic           = string
    threshold           = number
    alarm_description   = optional(string, "")
    dimensions          = optional(map(string), {})
    alarm_actions       = list(string)
    ok_actions          = list(string)
    treat_missing_data  = optional(string, "missing")
  }))
  description = "(Optional) Map of alarm name to metric alarm configuration for the cluster. Each alarm must have non-empty alarm_actions and ok_actions."
  default     = {}
}

variable "log_groups" {
  type = map(object({
    name              = string
    retention_in_days = number
    kms_key_id        = string
  }))
  description = "(Optional) Map of logical name to log group configuration for the cluster. Each log group requires a kms_key_id and a valid retention_in_days."
  default     = {}
}

variable "dashboard_name" {
  type        = string
  description = "(Required) Name for the cluster CloudWatch dashboard."
}

variable "dashboard_body" {
  type        = string
  description = "(Optional) JSON body for the CloudWatch dashboard. Must be valid JSON when provided."
  default     = null

  validation {
    condition     = var.dashboard_body == null || can(jsondecode(var.dashboard_body))
    error_message = "dashboard_body must be valid JSON when provided."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source module."
  default     = "ecs-app-cluster"
}
