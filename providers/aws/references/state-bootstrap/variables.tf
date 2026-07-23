# ---------------------------------------------------------------------------
# Child module source variables -- const=true defaults resolve to in-repo local
# relative paths so terraform init -backend=false succeeds without network access.
# Each variable is declared with const=true to prevent callers from overriding
# the canonical in-repo source at plan time. (E9-F1-S1-T4 const-source convention.)
# ---------------------------------------------------------------------------

variable "state_kms_key_source" {
  type        = string
  const       = true
  description = "Source path for the kms-key primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/kms-key"
}

variable "access_log_bucket_source" {
  type        = string
  const       = true
  description = "Source path for the access-log s3-bucket primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/s3-bucket"
}

variable "artifact_bucket_source" {
  type        = string
  const       = true
  description = "Source path for the artifact s3-bucket primitive child module. Defaults to the in-repo relative path."
  default     = "../../primitives/s3-bucket"
}

variable "bucket_prefix" {
  type        = string
  description = "(Required) Prefix used to name the access-log and artifact S3 buckets. Must be lowercase alphanumeric with hyphens, 3-28 chars."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,26}[a-z0-9]$", var.bucket_prefix))
    error_message = "bucket_prefix must be 3-28 characters, start and end with a lowercase letter or digit, and contain only lowercase letters, digits, and hyphens."
  }
}

variable "kms_alias" {
  type        = string
  description = "(Required) Alias suffix for the state KMS CMK (without the 'alias/' prefix). Must match [A-Za-z0-9/_-]+."

  validation {
    condition     = can(regex("^[A-Za-z0-9/_-]+$", var.kms_alias))
    error_message = "kms_alias must contain only alphanumeric characters, slashes, underscores, or hyphens and must not include the 'alias/' prefix."
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
  default     = "state-bootstrap"
}
