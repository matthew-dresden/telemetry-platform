variable "aws_region" {
  type        = string
  description = "AWS region to deploy resources."
  default     = "us-east-1"
}

variable "override_alarm_actions" {
  type        = bool
  description = "When true, alarm_actions and ok_actions are set to empty lists to trigger the D41 validation error. For testing only."
  default     = false
}

variable "override_retention_days" {
  type        = number
  description = "When non-null, overrides retention_in_days for all log groups to trigger the CloudWatch validation error. For testing only."
  default     = null
}

variable "override_use_extended_statistic" {
  type        = bool
  description = "When true, the high_cpu alarm uses a percentile extended_statistic (p99) instead of the base statistic. Exercises the extended_statistic pass-through (percentile SLO alarms). For testing only."
  default     = false
}

variable "override_both_statistics" {
  type        = bool
  description = "When true, the high_cpu alarm sets BOTH statistic and extended_statistic to trigger the mutual-exclusion validation error. For testing only."
  default     = false
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
