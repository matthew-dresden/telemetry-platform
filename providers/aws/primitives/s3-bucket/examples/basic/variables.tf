variable "bucket_name" {
  type        = string
  description = "The name of the S3 bucket. Overridden at test time via TF_VAR_bucket_name."
  # A concrete default lets the static security scan resolve the self-logging
  # access_log_target_bucket reference (var.bucket_name) so it can confirm S3
  # access logging is enabled. Terratest overrides this via TF_VAR_bucket_name.
  default = "telemetry-basic-example-bucket"
}

variable "versioning_enabled" {
  type        = bool
  description = "Whether to enable S3 versioning."
  default     = true
}

variable "bucket_key_enabled" {
  type        = bool
  description = "Whether to use an S3 bucket key to reduce KMS request costs."
  default     = true
}

variable "lifecycle_rules" {
  type = list(object({
    id                       = string
    enabled                  = bool
    prefix                   = optional(string, "")
    transition_days          = optional(number, null)
    transition_storage_class = optional(string, "GLACIER")
    expiration_days          = optional(number, null)
  }))
  description = "List of lifecycle rules."
  default     = []
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
