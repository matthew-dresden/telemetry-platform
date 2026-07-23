variable "name" {
  type        = string
  description = "(Required) The fully qualified name of the SSM parameter, including the path prefix (e.g. /telemetry/prod/ingest/adot-config)."
}

variable "type" {
  type        = string
  description = "(Required) The type of the SSM parameter. Must be one of: String, StringList, SecureString."

  validation {
    condition     = contains(["String", "StringList", "SecureString"], var.type)
    error_message = "type must be one of: String, StringList, SecureString."
  }
}

variable "value" {
  type        = string
  description = "(Required) The value of the SSM parameter. Marked sensitive -- this value is never echoed in outputs."
  sensitive   = true
}

variable "description" {
  type        = string
  description = "(Optional) Description of the SSM parameter."
  default     = ""
}

variable "tier" {
  type        = string
  description = "(Optional) The tier of the parameter. Valid values: Standard, Advanced, Intelligent-Tiering."
  default     = "Standard"

  validation {
    condition     = contains(["Standard", "Advanced", "Intelligent-Tiering"], var.tier)
    error_message = "tier must be one of: Standard, Advanced, Intelligent-Tiering."
  }
}

variable "kms_key_id" {
  type        = string
  description = "(Optional) The KMS key ID or ARN used to encrypt a SecureString parameter. Required when type is SecureString."
  default     = null

  validation {
    condition     = var.type != "SecureString" || var.kms_key_id != null
    error_message = "kms_key_id must be provided when type is SecureString."
  }
}

variable "overwrite" {
  type        = bool
  description = "(Optional) Whether to overwrite an existing parameter value."
  default     = false
}

variable "allowed_pattern" {
  type        = string
  description = "(Optional) A regular expression used to validate the parameter value."
  default     = null
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
  default     = "ssm-parameter"
}
