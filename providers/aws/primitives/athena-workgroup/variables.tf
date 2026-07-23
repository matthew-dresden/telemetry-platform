variable "workgroup_name" {
  type        = string
  description = "(Required) The name of the Athena workgroup. Must contain only alphanumeric characters, hyphens, or underscores."

  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]+$", var.workgroup_name))
    error_message = "workgroup_name must contain only alphanumeric characters, hyphens, or underscores and must not be empty."
  }
}

variable "description" {
  type        = string
  description = "(Optional) Description for the Athena workgroup."
  default     = "Telemetry analytics Athena workgroup"
}

variable "enforce_workgroup_configuration" {
  type        = bool
  description = "(Optional) When true, enforces workgroup configuration over client-side settings. Prevents clients from overriding query result location, encryption, or byte cutoff."
  default     = true
}

variable "bytes_scanned_cutoff_per_query" {
  type        = number
  description = "(Required) Maximum number of bytes scanned per query. Queries exceeding this limit are cancelled. Minimum value is 10485760 (10 MB) per the AWS API contract."

  validation {
    condition     = var.bytes_scanned_cutoff_per_query >= 10485760
    error_message = "bytes_scanned_cutoff_per_query must be at least 10485760 (10 MB) to satisfy the AWS API minimum and the cost-cap fail-fast requirement."
  }
}

variable "result_s3_bucket" {
  type        = string
  description = "(Required) The S3 bucket name (without s3:// prefix) where Athena writes query results. Must exist before the workgroup is created."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.result_s3_bucket))
    error_message = "result_s3_bucket must be a valid S3 bucket name."
  }
}

variable "result_s3_key_prefix" {
  type        = string
  description = "(Optional) S3 key prefix within the result bucket where Athena writes query results."
  default     = "athena-results/"
}

variable "result_kms_key_arn" {
  type        = string
  description = "(Required) ARN of the KMS key used for SSE-KMS encryption of Athena query results. Must be a valid KMS key ARN."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:kms:", var.result_kms_key_arn))
    error_message = "result_kms_key_arn must be a valid KMS key ARN starting with arn:aws:kms: or arn:aws-cn:kms: etc."
  }
}

variable "publish_cloudwatch_metrics_enabled" {
  type        = bool
  description = "(Optional) When true, publishes query metrics to CloudWatch."
  default     = true
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
  default     = "athena-workgroup"
}
