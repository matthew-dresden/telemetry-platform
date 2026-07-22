# ---------------------------------------------------------------------------
# Module source variables (const = true -- enables source = var.<name>_source)
# Each defaults to the in-repo relative path; can be overridden to a pinned
# git URL at deploy time via use_pinned_module_sources (spec S4.1, G2).
# ---------------------------------------------------------------------------

variable "sns_topic_source" {
  type        = string
  const       = true
  description = "Source path for the sns-topic primitive. Defaults to the in-repo relative path from providers/aws/references/observability/."
  default     = "../../primitives/sns-topic"
}

variable "cloudwatch_source" {
  type        = string
  const       = true
  description = "Source path for the cloudwatch primitive. Defaults to the in-repo relative path from providers/aws/references/observability/."
  default     = "../../primitives/cloudwatch"
}

variable "cost_anomaly_source" {
  type        = string
  const       = true
  description = "Source path for the cost-anomaly primitive. Defaults to the in-repo relative path from providers/aws/references/observability/."
  default     = "../../primitives/cost-anomaly"
}

# ---------------------------------------------------------------------------
# SNS topic inputs (D41 -- single notification sink for alarms and cost-anomaly)
# ---------------------------------------------------------------------------

variable "topic_name" {
  type        = string
  description = "(Required) SNS topic name passed to the sns-topic primitive. Must match ^[A-Za-z0-9_-]+$."

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.topic_name))
    error_message = "topic_name must contain only alphanumeric characters, underscores, or hyphens."
  }
}

variable "sns_kms_key_id" {
  type        = string
  description = "(Required) CMK id/ARN or alias for server-side encryption on the SNS topic (passed to sns-topic kms_key_id). Must be a KMS ARN (arn:aws:kms:...) or alias (alias/...)."

  validation {
    condition     = can(regex("^(arn:aws:kms:|alias/)", var.sns_kms_key_id))
    error_message = "sns_kms_key_id must be a KMS ARN starting with 'arn:aws:kms:' or an alias starting with 'alias/'."
  }
}

variable "sns_subscribers" {
  type = list(object({
    protocol = string
    endpoint = string
  }))
  description = "(Optional) SNS topic subscriptions (e.g. operator email). Each entry requires a protocol and a non-empty endpoint. Passed to the sns-topic primitive."
  default     = []

  validation {
    condition = alltrue([
      for s in var.sns_subscribers : contains(["email", "email-json", "https", "sqs", "lambda", "sms"], s.protocol)
    ])
    error_message = "Each sns_subscribers entry protocol must be one of: email, email-json, https, sqs, lambda, sms."
  }

  validation {
    condition = alltrue([
      for s in var.sns_subscribers : length(s.endpoint) > 0
    ])
    error_message = "Each sns_subscribers entry endpoint must be non-empty."
  }
}

variable "alarm_notification_email" {
  type        = string
  description = "(Optional) Email address subscribed to the SNS alarm/anomaly topic so CloudWatch alarms and cost-anomaly notifications reach a human (D41 -- closes the no-human-delivery gap where the topic had zero subscribers). Defaults to null (no email subscription is composed from this input). When set, the module composes an email subscription to this address and merges it with any sns_subscribers entries, deduplicated by endpoint (so setting both this and a matching sns_subscribers entry yields ONE subscription). NOTE: an SNS email subscription is created in PendingConfirmation state -- the recipient MUST click the confirmation link AWS emails after apply, or no notifications are delivered. AWS auto-deletes an unconfirmed subscription after ~3 days, and Terraform does not recreate a still-pending subscription on re-apply, so the link must be confirmed promptly after the apply that creates it."
  default     = null

  validation {
    condition     = var.alarm_notification_email == null || can(regex("^[^@]+@[^@]+\\.[^@]+$", var.alarm_notification_email))
    error_message = "alarm_notification_email must be a valid email address when provided."
  }
}

variable "sns_policy_json" {
  type        = string
  description = "(Optional) Operator override for the SNS topic access policy as a JSON string. When null (default), the module applies a constructed policy that retains owner-account control and grants costalerts.amazonaws.com SNS:Publish (scoped by aws:SourceAccount) so AWS Cost Anomaly Detection can publish to the CMK-encrypted topic (D41). Set this only to fully replace that default; the supplied policy is then the SOLE topic policy."
  default     = null

  validation {
    condition     = var.sns_policy_json == null || can(jsondecode(var.sns_policy_json))
    error_message = "sns_policy_json must be a valid JSON string when provided."
  }
}

# ---------------------------------------------------------------------------
# CloudWatch inputs
# ---------------------------------------------------------------------------

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
    treat_missing_data  = optional(string, "missing")
  }))
  description = "(Optional) Map of alarm name to metric alarm configuration. The observability reference wires every alarm's alarm_actions and ok_actions to the composed SNS topic_arn (D41). Do NOT pass alarm_actions or ok_actions here -- they are set by locals.tf. Each alarm must set EXACTLY ONE of statistic (SampleCount, Average, Sum, Minimum, Maximum) or extended_statistic (a percentile such as p95); the value is passed through to the cloudwatch primitive."
  default     = {}

  validation {
    condition = alltrue([
      for k, v in var.alarms : v.evaluation_periods >= 1
    ])
    error_message = "Every alarm must have evaluation_periods >= 1."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : v.period >= 10
    ])
    error_message = "Every alarm must have period >= 10 seconds."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : (v.statistic != null) != (v.extended_statistic != null)
    ])
    error_message = "Every alarm must set EXACTLY ONE of statistic or extended_statistic. Use statistic for SampleCount/Average/Sum/Minimum/Maximum and extended_statistic for percentiles such as p95."
  }

  validation {
    condition = alltrue([
      for k, v in var.alarms : contains(["missing", "ignore", "breaching", "notBreaching"], v.treat_missing_data)
    ])
    error_message = "Every alarm treat_missing_data must be one of: missing, ignore, breaching, notBreaching. Use breaching where absent data means trouble (e.g. a stopped task or a stalled stream that stops emitting datapoints) and notBreaching where absent data means healthy (e.g. error/drop counters that publish nothing when there is nothing to report)."
  }
}

variable "log_groups" {
  type = map(object({
    name              = string
    retention_in_days = number
    kms_key_id        = string
  }))
  description = "(Optional) Map of logical name to CloudWatch log group configuration. Each log group requires a kms_key_id for encryption at rest."
  default     = {}

  validation {
    condition = alltrue([
      for k, v in var.log_groups : contains(
        [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653],
        v.retention_in_days
      )
    ])
    error_message = "Every log_groups entry must have a retention_in_days value from the valid CloudWatch set."
  }

  validation {
    condition = alltrue([
      for k, v in var.log_groups : length(v.kms_key_id) > 0
    ])
    error_message = "Every log_groups entry must specify a kms_key_id."
  }
}

variable "create_dashboard" {
  type        = bool
  description = "(Optional) Whether to provision the CloudWatch dashboard resource. When true, dashboard_name must be non-empty. Passed to the cloudwatch primitive create_dashboard input."
  default     = false
}

variable "dashboard_name" {
  type        = string
  description = "(Required) Name for the CloudWatch observability dashboard. Must be non-empty."

  validation {
    condition     = length(var.dashboard_name) >= 1
    error_message = "dashboard_name must be non-empty."
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

# ---------------------------------------------------------------------------
# Budget inputs (docs/terragrunt-concepts.md)
# The budget keeps its direct subscriber_email_addresses (D41).
# ---------------------------------------------------------------------------

variable "budget_amount" {
  type        = number
  description = "(Required) Monthly budget limit amount in USD. Must be greater than 0. Passed to the reused budget primitive."

  validation {
    condition     = var.budget_amount > 0
    error_message = "budget_amount must be greater than 0."
  }
}

variable "budget_subscriber_email_addresses" {
  type        = list(string)
  description = "(Required) Direct email addresses for budget notifications. The budget keeps these direct subscribers (D41 -- budget does not route through the SNS topic). Must be non-empty."

  validation {
    condition     = length(var.budget_subscriber_email_addresses) > 0
    error_message = "budget_subscriber_email_addresses must be non-empty -- the budget must have at least one direct email subscriber."
  }

  validation {
    condition = alltrue([
      for addr in var.budget_subscriber_email_addresses : can(regex("^[^@]+@[^@]+\\.[^@]+$", addr))
    ])
    error_message = "Each budget_subscriber_email_addresses entry must be a valid email address."
  }
}

variable "budget_notification_thresholds" {
  type = list(object({
    threshold         = number
    notification_type = optional(string, "ACTUAL")
    comparison        = optional(string, "GREATER_THAN")
  }))
  description = "(Required) Percentage increments of budget_amount at which the budget alerts. Each entry creates one notification threshold. Must be non-empty. notification_type defaults to ACTUAL; comparison defaults to GREATER_THAN."

  validation {
    condition     = length(var.budget_notification_thresholds) > 0
    error_message = "budget_notification_thresholds must be non-empty -- at least one threshold percentage is required."
  }

  validation {
    condition = alltrue([
      for t in var.budget_notification_thresholds : t.threshold > 0 && t.threshold <= 1000
    ])
    error_message = "Each budget_notification_thresholds threshold must be between 0 and 1000 (exclusive lower bound, inclusive upper)."
  }

  validation {
    condition = alltrue([
      for t in var.budget_notification_thresholds : contains(["ACTUAL", "FORECASTED"], t.notification_type)
    ])
    error_message = "Each budget_notification_thresholds notification_type must be one of: ACTUAL, FORECASTED."
  }

  validation {
    condition = alltrue([
      for t in var.budget_notification_thresholds : contains(["GREATER_THAN", "LESS_THAN", "EQUAL_TO"], t.comparison)
    ])
    error_message = "Each budget_notification_thresholds comparison must be one of: GREATER_THAN, LESS_THAN, EQUAL_TO."
  }
}

# ---------------------------------------------------------------------------
# Cost anomaly inputs (D23)
# ---------------------------------------------------------------------------

variable "anomaly_monitor_name" {
  type        = string
  description = "(Required) Display name for the cost anomaly monitor. Must be non-empty."

  validation {
    condition     = length(var.anomaly_monitor_name) >= 1
    error_message = "anomaly_monitor_name must be non-empty."
  }
}

variable "anomaly_monitor_type" {
  type        = string
  description = "(Optional) Monitor type for cost anomaly detection. DIMENSIONAL monitors all AWS services; CUSTOM allows a cost-category filter. Defaults to DIMENSIONAL."
  default     = "DIMENSIONAL"

  validation {
    condition     = contains(["DIMENSIONAL", "CUSTOM"], var.anomaly_monitor_type)
    error_message = "anomaly_monitor_type must be one of: DIMENSIONAL, CUSTOM."
  }
}

variable "anomaly_subscription_name" {
  type        = string
  description = "(Required) Display name for the cost anomaly alert subscription. Must be non-empty."

  validation {
    condition     = length(var.anomaly_subscription_name) >= 1
    error_message = "anomaly_subscription_name must be non-empty."
  }
}

variable "anomaly_subscription_frequency" {
  type        = string
  description = "(Optional) How often anomaly alerts are delivered. Valid values: DAILY, IMMEDIATE, WEEKLY. Defaults to DAILY."
  default     = "DAILY"

  validation {
    condition     = contains(["DAILY", "IMMEDIATE", "WEEKLY"], var.anomaly_subscription_frequency)
    error_message = "anomaly_subscription_frequency must be one of: DAILY, IMMEDIATE, WEEKLY."
  }
}

variable "anomaly_threshold_expression" {
  type        = string
  description = "(Required) JSON cost-expression defining the spend anomaly threshold. Passed directly to the cost-anomaly primitive threshold_expression input."

  validation {
    condition     = can(jsondecode(var.anomaly_threshold_expression))
    error_message = "anomaly_threshold_expression must be a valid JSON string."
  }

  validation {
    condition     = can(jsondecode(var.anomaly_threshold_expression)["Dimensions"]["Key"])
    error_message = "anomaly_threshold_expression must contain a Dimensions.Key field."
  }

  validation {
    condition     = can(jsondecode(var.anomaly_threshold_expression)["Dimensions"]["Values"])
    error_message = "anomaly_threshold_expression must contain a Dimensions.Values field."
  }

  validation {
    condition     = can(jsondecode(var.anomaly_threshold_expression)["Dimensions"]["MatchOptions"])
    error_message = "anomaly_threshold_expression must contain a Dimensions.MatchOptions field."
  }
}

# ---------------------------------------------------------------------------
# Shared tagging inputs
# ---------------------------------------------------------------------------

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this reference module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source reference module."
  default     = "observability"
}
