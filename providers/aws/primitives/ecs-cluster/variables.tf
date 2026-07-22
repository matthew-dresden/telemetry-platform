variable "name" {
  type        = string
  description = "(Required) ECS cluster name. Must match ^[a-zA-Z0-9_-]+$ and be 255 characters or fewer."

  validation {
    condition     = length(var.name) >= 1 && length(var.name) <= 255 && can(regex("^[a-zA-Z0-9_-]+$", var.name))
    error_message = "name must be 1-255 characters and contain only alphanumeric characters, underscores, and hyphens."
  }
}

variable "capacity_providers" {
  type        = list(string)
  description = "(Optional) List of capacity providers to associate. Must be a non-empty subset of [FARGATE, FARGATE_SPOT]."
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

variable "default_capacity_provider_strategy" {
  type = list(object({
    capacity_provider = string
    weight            = number
    base              = optional(number, 0)
  }))
  description = "(Optional) Default capacity provider strategy for the cluster. Defaults to [] so the module auto-derives a strategy from capacity_providers (FARGATE base=1 weight=1, FARGATE_SPOT weight=4 when present). Each explicit entry must reference a provider present in capacity_providers."
  default     = []
}

variable "enable_container_insights" {
  type        = bool
  description = "(Optional) Whether to enable Container Insights for the cluster. Defaults to true per D11."
  default     = true
}

variable "execute_command_logging" {
  type        = string
  description = "(Optional) Logging configuration for ECS Exec. Must be one of NONE, DEFAULT, OVERRIDE."
  default     = "DEFAULT"

  validation {
    condition     = contains(["NONE", "DEFAULT", "OVERRIDE"], var.execute_command_logging)
    error_message = "execute_command_logging must be one of NONE, DEFAULT, OVERRIDE."
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
  default     = "ecs-cluster"
}
