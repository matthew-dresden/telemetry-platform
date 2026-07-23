variable "name" {
  type        = string
  description = "Name of the IAM role."
}

variable "assume_role_policy_json" {
  type        = string
  description = "A valid JSON trust policy document."
}

variable "description" {
  type        = string
  description = "Description of the IAM role."
  default     = "Firehose delivery role -- with-inline example"
}

variable "path" {
  type        = string
  description = "Path under which the role is created."
  default     = "/"
}

variable "max_session_duration" {
  type        = number
  description = "Maximum session duration in seconds."
  default     = 3600
}

variable "permissions_boundary" {
  type        = string
  description = "ARN of the permissions boundary policy."
  default     = null
}

variable "managed_policy_arns" {
  type        = list(string)
  description = "List of managed policy ARNs to attach."
  default     = []
}

variable "inline_policies" {
  type        = map(string)
  description = "Map of inline policy name to JSON document."
  default     = {}
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
