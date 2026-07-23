variable "alias_name" {
  type        = string
  description = "Alias suffix for the KMS key."
}

variable "account_id" {
  type        = string
  description = "AWS account ID used to construct the KMS key resource policy principal."
}

variable "description" {
  type        = string
  description = "Description of the KMS key."
  default     = "Customer managed KMS key - with-policy example"
}

variable "deletion_window_in_days" {
  type        = number
  description = "Waiting period in days before key deletion."
  default     = 7
}

variable "enable_key_rotation" {
  type        = bool
  description = "Whether to enable annual automatic key rotation."
  default     = true
}

variable "key_usage" {
  type        = string
  description = "Intended use of the key."
  default     = "ENCRYPT_DECRYPT"
}

variable "customer_master_key_spec" {
  type        = string
  description = "Specifies whether the key contains a symmetric key or an asymmetric key pair."
  default     = "SYMMETRIC_DEFAULT"
}

variable "multi_region" {
  type        = bool
  description = "Whether the key is a multi-region key."
  default     = true
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
