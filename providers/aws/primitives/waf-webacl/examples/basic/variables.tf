variable "name" {
  type        = string
  description = "Name for the WAFv2 web ACL."
}

variable "scope" {
  type        = string
  description = "Scope of the web ACL. Valid values: CLOUDFRONT, REGIONAL."
  default     = "CLOUDFRONT"
}

variable "default_action" {
  type        = string
  description = "Default action. Valid values: allow, block."
  default     = "allow"
}

variable "rate_limit_per_ip" {
  type        = number
  description = "Rate-based rule limit: requests per 5-minute window per IP."
  default     = 2000
}

variable "logging_enabled" {
  type        = bool
  description = "Whether to attach a logging configuration."
  default     = false
}

variable "log_destination_arns" {
  type        = list(string)
  description = "External log destination ARNs (Kinesis, S3, or CloudWatch Logs). Used only when create_log_group is false."
  default     = []
}

variable "create_log_group" {
  type        = bool
  description = "When true, the module creates its own aws-waf-logs-* CloudWatch log group as the logging destination (docs/terragrunt-concepts.md)."
  default     = false
}

variable "log_retention_in_days" {
  type        = number
  description = "Retention for the module-owned CloudWatch log group when create_log_group is true."
  default     = 365
}

variable "log_kms_key_arn" {
  type        = string
  description = "KMS key ARN for encrypting WAF logs. Required when logging_enabled is true (per D2)."
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
