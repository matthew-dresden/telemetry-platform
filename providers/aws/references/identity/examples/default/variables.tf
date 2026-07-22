variable "viewer_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the viewer group."
  default     = "telemetry-viewers"

  validation {
    condition     = length(var.viewer_group_name) > 0
    error_message = "viewer_group_name must be non-empty."
  }
}

variable "author_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the author (analyst) group."
  default     = "telemetry-authors"

  validation {
    condition     = length(var.author_group_name) > 0
    error_message = "author_group_name must be non-empty."
  }
}

variable "admin_group_name" {
  type        = string
  description = "(Required) IAM Identity Center group name for the admin group."
  default     = "telemetry-admins"

  validation {
    condition     = length(var.admin_group_name) > 0
    error_message = "admin_group_name must be non-empty."
  }
}

variable "analyst_role_name" {
  type        = string
  description = "(Required) Name for the TelemetryAnalyst IAM role."
  default     = "telemetry-analyst-test"

  validation {
    condition     = length(var.analyst_role_name) > 0 && length(var.analyst_role_name) <= 64
    error_message = "analyst_role_name must be between 1 and 64 characters."
  }
}

variable "admin_role_name" {
  type        = string
  description = "(Required) Name for the TelemetryAdmin IAM role."
  default     = "telemetry-admin-test"

  validation {
    condition     = length(var.admin_role_name) > 0 && length(var.admin_role_name) <= 64
    error_message = "admin_role_name must be between 1 and 64 characters."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "identity-module-testing"
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
