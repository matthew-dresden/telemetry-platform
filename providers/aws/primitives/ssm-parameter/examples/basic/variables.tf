variable "name" {
  type        = string
  description = "The fully qualified name of the SSM parameter."
}

variable "type" {
  type        = string
  description = "The type of the SSM parameter."
  default     = "String"
}

variable "value" {
  type        = string
  description = "The value of the SSM parameter."
  sensitive   = true
  default     = "100"
}

variable "description" {
  type        = string
  description = "Description of the SSM parameter."
  default     = "WAF rate limit config -- basic example"
}

variable "tier" {
  type        = string
  description = "The tier of the parameter."
  default     = "Standard"
}

variable "kms_key_id" {
  type        = string
  description = "The KMS key ID for SecureString parameters."
  default     = null
}

variable "overwrite" {
  type        = bool
  description = "Whether to overwrite an existing parameter value."
  default     = false
}

variable "allowed_pattern" {
  type        = string
  description = "A regular expression used to validate the parameter value."
  default     = null
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
