variable "alarms" {
  type = map(object({
    comparison_operator = string
    evaluation_periods  = number
    metric_name         = string
    namespace           = string
    period              = number
    statistic           = optional(string)
    extended_statistic  = optional(string)
    threshold           = number
    alarm_description   = optional(string, "")
    dimensions          = optional(map(string), {})
    alarm_actions       = list(string)
    ok_actions          = list(string)
    treat_missing_data  = optional(string, "missing")
  }))
  description = "(Optional) Map of alarm name to metric alarm configuration. Each alarm must have non-empty alarm_actions and ok_actions wired to an SNS topic ARN (D41 fail-fast contract). Each alarm must set EXACTLY ONE of statistic (one of SampleCount, Average, Sum, Minimum, Maximum) or extended_statistic (a percentile such as p95) -- aws_cloudwatch_metric_alarm rejects setting both or neither."
  default     = {}

  validation {
    condition = alltrue([
      for k, v in var.alarms : v.evaluation_periods >= 1
    ])
    error_message = "Every alarm must have evaluation_periods >= 1."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : (v.statistic != null) != (v.extended_statistic != null)
    ])
    error_message = "Every alarm must set EXACTLY ONE of statistic or extended_statistic. aws_cloudwatch_metric_alarm requires exactly one: use statistic for SampleCount/Average/Sum/Minimum/Maximum and extended_statistic for percentiles such as p95."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : v.statistic == null || contains(
        ["SampleCount", "Average", "Sum", "Minimum", "Maximum"],
        v.statistic
      )
    ])
    error_message = "When statistic is set it must be one of: SampleCount, Average, Sum, Minimum, Maximum. Use extended_statistic for percentile statistics such as p95."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : v.period >= 10
    ])
    error_message = "Every alarm must have period >= 10 seconds."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : length(v.alarm_actions) > 0
    ])
    error_message = "Every alarm must have at least one alarm_actions entry. An empty alarm_actions list is a D41 contract violation -- alarms must route to a real SNS sink."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : length(v.ok_actions) > 0
    ])
    error_message = "Every alarm must have at least one ok_actions entry. An empty ok_actions list is a D41 contract violation -- alarms must route to a real SNS sink."
  }
}

variable "log_groups" {
  type = map(object({
    name              = string
    retention_in_days = number
    kms_key_id        = string
  }))
  description = "(Optional) Map of logical name to log group configuration. Each log group requires a kms_key_id for encryption at rest and a retention_in_days from the valid CloudWatch set."
  default     = {}

  validation {
    condition = alltrue([
      for k, v in var.log_groups : contains(
        [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653],
        v.retention_in_days
      )
    ])
    error_message = "Every log_groups entry must have a retention_in_days value from the valid CloudWatch set: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653."
  }

  validation {
    condition = alltrue([
      for k, v in var.log_groups : length(v.kms_key_id) > 0
    ])
    error_message = "Every log_groups entry must specify a kms_key_id. Log groups without KMS encryption are a security violation."
  }
}

variable "create_dashboard" {
  type        = bool
  description = "(Optional) Whether to create a CloudWatch dashboard. When true, dashboard_name must be non-null."
  default     = false
}

variable "dashboard_name" {
  type        = string
  description = "(Optional) Name for the CloudWatch dashboard. Required (non-null) when create_dashboard is true."
  default     = null

  validation {
    condition     = var.create_dashboard == false || var.dashboard_name != null
    error_message = "dashboard_name must be non-null when create_dashboard is true."
  }
}

variable "dashboard_body" {
  type        = string
  description = "(Optional) JSON body for the CloudWatch dashboard. Must be valid JSON when provided."
  default     = null

  validation {
    condition     = var.dashboard_body == null || can(jsondecode(var.dashboard_body))
    error_message = "dashboard_body must be valid JSON when provided."
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
  default     = "cloudwatch"
}
