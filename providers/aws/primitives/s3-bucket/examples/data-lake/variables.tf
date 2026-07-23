variable "bucket_name" {
  type        = string
  description = "The name of the S3 bucket. Overridden at test time via TF_VAR_bucket_name."
  # A concrete default lets the static security scan resolve the self-logging
  # access_log_target_bucket reference (var.bucket_name) so it can confirm S3
  # access logging is enabled. Terratest overrides this via TF_VAR_bucket_name.
  default = "telemetry-data-lake-example-bucket"
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

variable "force_destroy" {
  type        = bool
  description = "Whether to allow Terraform to destroy the bucket even if it contains objects."
  default     = true
}

variable "transition_days" {
  type        = number
  description = "Number of days before transitioning objects to the cold storage class."
  default     = 365
}

variable "transition_storage_class" {
  type        = string
  description = "The cold storage class for lifecycle transitions. One of GLACIER, GLACIER_IR, or DEEP_ARCHIVE."
  default     = "GLACIER"
}

variable "expiration_days" {
  type        = number
  description = "Number of days before expiring objects permanently."
  default     = 730
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
