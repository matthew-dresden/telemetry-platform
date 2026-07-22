variable "name" {
  type        = string
  description = "(Required) Base name used to derive all resource names in this fixture."
  default     = "telemetry-obs-test"
}

variable "budget_amount" {
  type        = number
  description = "(Optional) Monthly budget limit amount in USD for the observability unit."
  default     = 500

  validation {
    condition     = var.budget_amount > 0
    error_message = "budget_amount must be greater than 0."
  }
}

variable "budget_subscriber_email_addresses" {
  type        = list(string)
  description = "(Required) Direct email addresses for budget notifications. Read by Terratest to assert direct-subscriber preservation (D41)."

  validation {
    condition     = length(var.budget_subscriber_email_addresses) > 0
    error_message = "budget_subscriber_email_addresses must be non-empty."
  }
}

variable "budget_notification_thresholds" {
  type = list(object({
    threshold         = number
    notification_type = optional(string, "ACTUAL")
    comparison        = optional(string, "GREATER_THAN")
  }))
  description = "(Optional) Percentage increments of budget_amount at which the budget alerts."
  default = [
    { threshold = 80, notification_type = "ACTUAL", comparison = "GREATER_THAN" },
    { threshold = 100, notification_type = "ACTUAL", comparison = "GREATER_THAN" },
  ]

  validation {
    condition     = length(var.budget_notification_thresholds) > 0
    error_message = "budget_notification_thresholds must be non-empty."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "observability-module-testing"
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
