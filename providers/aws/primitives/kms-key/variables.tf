variable "alias_name" {
  type        = string
  description = "(Required) Alias suffix for the KMS key. The module prefixes 'alias/' automatically. Must match pattern [A-Za-z0-9/_-]+."

  validation {
    condition     = can(regex("^[A-Za-z0-9/_-]+$", var.alias_name))
    error_message = "alias_name must contain only alphanumeric characters, slashes, underscores, or hyphens and must not include the 'alias/' prefix."
  }
}

variable "description" {
  type        = string
  description = "(Optional) Description of the KMS key."
  default     = "Customer managed KMS key"
}

variable "deletion_window_in_days" {
  type        = number
  description = "(Optional) Waiting period in days before key deletion. Must be between 7 and 30 inclusive."
  default     = 30

  validation {
    condition     = var.deletion_window_in_days >= 7 && var.deletion_window_in_days <= 30
    error_message = "deletion_window_in_days must be between 7 and 30 inclusive."
  }
}

variable "enable_key_rotation" {
  type        = bool
  description = "(Optional) Whether to enable annual automatic key rotation."
  default     = true
}

variable "key_usage" {
  type        = string
  description = "(Optional) Intended use of the key. Valid values: ENCRYPT_DECRYPT, SIGN_VERIFY, GENERATE_VERIFY_MAC."
  default     = "ENCRYPT_DECRYPT"

  validation {
    condition     = contains(["ENCRYPT_DECRYPT", "SIGN_VERIFY", "GENERATE_VERIFY_MAC"], var.key_usage)
    error_message = "key_usage must be one of ENCRYPT_DECRYPT, SIGN_VERIFY, or GENERATE_VERIFY_MAC."
  }
}

variable "customer_master_key_spec" {
  type        = string
  description = "(Optional) Specifies whether the key contains a symmetric key or an asymmetric key pair."
  default     = "SYMMETRIC_DEFAULT"
}

variable "multi_region" {
  type        = bool
  description = "(Optional) Whether the key is a multi-region key."
  default     = false
}

variable "policy_json" {
  type        = string
  description = "(Optional) A valid JSON key resource policy. When null, the default KMS key policy is used."
  default     = null

  validation {
    condition     = var.policy_json == null || can(jsondecode(var.policy_json))
    error_message = "policy_json must be a valid JSON string when provided."
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
  default     = "kms-key"
}
