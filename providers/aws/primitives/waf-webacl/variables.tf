variable "name" {
  type        = string
  description = "(Required) Name for the WAFv2 web ACL."
}

variable "scope" {
  type        = string
  description = "(Required) Scope of the WAFv2 web ACL. Valid values: CLOUDFRONT, REGIONAL."

  validation {
    condition     = contains(["CLOUDFRONT", "REGIONAL"], var.scope)
    error_message = "scope must be one of CLOUDFRONT or REGIONAL."
  }
}

variable "default_action" {
  type        = string
  description = "(Required) Default action for the WAFv2 web ACL. Valid values: allow, block."

  validation {
    condition     = contains(["allow", "block"], var.default_action)
    error_message = "default_action must be one of allow or block."
  }
}

variable "managed_rule_groups" {
  type = list(object({
    name            = string
    vendor_name     = string
    priority        = number
    override_action = string
    rule_action_overrides = optional(list(object({
      name          = string
      action_to_use = string
    })), [])
  }))
  description = "(Required) List of AWS managed rule groups to attach. Each entry specifies name, vendor_name, priority, override_action (none or count), and an optional rule_action_overrides list. The group-level override_action governs the whole group; each rule_action_overrides entry instead retargets a SINGLE named sub-rule of the managed group to a specific action (allow, block, count, captcha, or challenge) WITHOUT changing the rest of the group. This is the AWS-recommended way to neutralize one noisy sub-rule while every other rule in the group stays enforced -- for example overriding only AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY sub-rule to count so request bodies larger than the WAF default body-inspection limit are not blocked, while SQLi/XSS/etc. remain in effect. Defaults to an empty list (no sub-rule overrides)."

  validation {
    condition = alltrue([
      for rg in var.managed_rule_groups :
      contains(["none", "count"], rg.override_action)
    ])
    error_message = "Each managed_rule_groups entry's override_action must be one of none or count."
  }

  validation {
    condition = alltrue(flatten([
      for rg in var.managed_rule_groups : [
        for o in rg.rule_action_overrides :
        contains(["allow", "block", "count", "captcha", "challenge"], o.action_to_use)
      ]
    ]))
    error_message = "Each rule_action_overrides entry's action_to_use must be one of allow, block, count, captcha, or challenge."
  }
}

variable "rate_limit_per_ip" {
  type        = number
  description = "(Required) Rate-based rule limit: maximum requests per 5-minute window per IP. Must be a positive integer."

  validation {
    condition     = var.rate_limit_per_ip > 0
    error_message = "rate_limit_per_ip must be a positive integer."
  }
}

variable "logging_enabled" {
  type        = bool
  description = "(Required) Whether to attach a WAFv2 logging configuration. When true, log_kms_key_arn must be supplied (decision D2), and the log destination must be available either by setting create_log_group = true (the module owns its own CloudWatch log group per docs/terragrunt-concepts.md) or by supplying at least one external log_destination_arns entry."

  validation {
    condition     = !var.logging_enabled || var.log_kms_key_arn != null
    error_message = "logging_enabled = true requires a non-null log_kms_key_arn (decision D2: CMK encryption must be enforced on the log destination)."
  }

  validation {
    condition     = !var.logging_enabled || var.create_log_group || length(var.log_destination_arns) > 0
    error_message = "logging_enabled = true requires a log destination: set create_log_group = true to let the module own its CloudWatch log group (docs/terragrunt-concepts.md), or supply at least one log_destination_arns entry."
  }
}

variable "create_log_group" {
  type        = bool
  description = "(Optional) When true, the module creates its OWN CloudWatch log group as the WAFv2 logging destination (docs/terragrunt-concepts.md). AWS requires the log group name to start with the 'aws-waf-logs-' prefix, so the module names it 'aws-waf-logs-<name>'. The group is encrypted with log_kms_key_arn (telemetry-data CMK, docs/terragrunt-concepts.md) and retained for log_retention_in_days. When false, the caller must supply log_destination_arns. Defaults to false."
  default     = false
}

variable "log_retention_in_days" {
  type        = number
  description = "(Optional) Retention in days for the module-owned CloudWatch log group when create_log_group = true. Must be a value from the valid CloudWatch retention set. Defaults to 365 (docs/terragrunt-concepts.md)."
  default     = 365

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.log_retention_in_days)
    error_message = "log_retention_in_days must be one of the valid CloudWatch retention values: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653."
  }
}

variable "log_kms_key_arn" {
  type        = string
  description = "(Optional) ARN of the KMS key used to enforce CMK encryption on the log destination. Required when logging_enabled is true (decision D2). Must match ^arn:aws:kms: when provided. When create_log_group = true, this CMK encrypts the module-owned CloudWatch log group at rest (docs/terragrunt-concepts.md telemetry-data CMK). When supplying an external log_destination_arns, encryption is enforced on that destination resource (e.g., Kinesis Firehose) by its own configuration. The WAFv2 resources have no native KMS field."
  default     = null

  validation {
    condition     = var.log_kms_key_arn == null || can(regex("^arn:aws:kms:", var.log_kms_key_arn))
    error_message = "log_kms_key_arn must be a valid KMS key ARN matching ^arn:aws:kms: when provided."
  }
}

variable "log_destination_arns" {
  type        = list(string)
  description = "(Optional) List of EXTERNAL CloudWatch Logs, Kinesis Data Firehose, or S3 log destination ARNs for the WAFv2 logging configuration. Used only when create_log_group = false. When create_log_group = true the module owns its CloudWatch log group and this must be left empty (the module-created group is the sole destination, docs/terragrunt-concepts.md)."
  default     = []

  validation {
    condition     = length(var.log_destination_arns) == 0 || alltrue([for arn in var.log_destination_arns : can(regex("^arn:aws:", arn))])
    error_message = "Each log_destination_arns entry must be a valid AWS ARN matching ^arn:aws:."
  }

  validation {
    condition     = !var.create_log_group || length(var.log_destination_arns) == 0
    error_message = "log_destination_arns must be empty when create_log_group = true: the module-owned CloudWatch log group is the sole logging destination (docs/terragrunt-concepts.md)."
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
  default     = "waf-webacl"
}
