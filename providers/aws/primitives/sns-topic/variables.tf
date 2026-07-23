variable "topic_name" {
  type        = string
  description = "(Required) SNS topic name. Must match pattern ^[A-Za-z0-9_-]+$."

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]+$", var.topic_name))
    error_message = "topic_name must contain only alphanumeric characters, underscores, or hyphens."
  }
}

variable "kms_key_id" {
  type        = string
  description = "(Required) CMK id/ARN or alias for server-side encryption on the SNS topic. Must be a KMS ARN (arn:aws:kms:...) or alias (alias/...)."

  validation {
    condition     = can(regex("^(arn:aws:kms:|alias/)", var.kms_key_id))
    error_message = "kms_key_id must be a KMS ARN starting with 'arn:aws:kms:' or an alias starting with 'alias/'."
  }
}

variable "subscribers" {
  type = list(object({
    protocol = string
    endpoint = string
  }))
  description = "(Optional) List of topic subscriptions. Each entry requires a protocol (one of email, email-json, https, sqs, lambda, sms) and a non-empty endpoint."
  default     = []

  validation {
    condition = alltrue([
      for s in var.subscribers : contains(["email", "email-json", "https", "sqs", "lambda", "sms"], s.protocol)
    ])
    error_message = "Each subscriber protocol must be one of: email, email-json, https, sqs, lambda, sms."
  }

  validation {
    condition = alltrue([
      for s in var.subscribers : length(s.endpoint) > 0
    ])
    error_message = "Each subscriber endpoint must be non-empty."
  }
}

variable "policy_json" {
  type        = string
  description = "(Optional) Optional SNS topic access policy as a JSON string (e.g. to allow CloudWatch or Cost Explorer to publish). When null, no explicit policy is applied."
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
  default     = "sns-topic"
}
