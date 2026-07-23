variable "name" {
  type        = string
  description = "(Required) Name of the IAM role. Must be 64 characters or fewer."

  validation {
    condition     = length(var.name) <= 64
    error_message = "name must be 64 characters or fewer to satisfy the IAM role name limit."
  }
}

variable "assume_role_policy_json" {
  type        = string
  description = "(Required) A valid JSON trust policy document granting principals permission to assume this role."

  validation {
    condition     = can(jsondecode(var.assume_role_policy_json))
    error_message = "assume_role_policy_json must be a valid JSON string."
  }
}

variable "description" {
  type        = string
  description = "(Optional) Description of the IAM role."
  default     = ""
}

variable "path" {
  type        = string
  description = "(Optional) Path under which the role is created."
  default     = "/"
}

variable "max_session_duration" {
  type        = number
  description = "(Optional) Maximum session duration in seconds. Must be between 3600 and 43200."
  default     = 3600

  validation {
    condition     = var.max_session_duration >= 3600 && var.max_session_duration <= 43200
    error_message = "max_session_duration must be between 3600 and 43200 seconds."
  }
}

variable "permissions_boundary" {
  type        = string
  description = "(Optional) ARN of the policy that is used to set the permissions boundary for the role."
  default     = null
}

variable "managed_policy_arns" {
  type        = list(string)
  description = "(Optional) List of managed policy ARNs to attach to the role."
  default     = []
}

variable "inline_policies" {
  type        = map(string)
  description = "(Optional) Map of inline policy name to JSON policy document. Each value must be valid JSON."
  default     = {}

  validation {
    condition     = alltrue([for v in values(var.inline_policies) : can(jsondecode(v))])
    error_message = "Every value in inline_policies must be a valid JSON string."
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
  default     = "iam-role"
}
