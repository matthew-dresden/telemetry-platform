variable "bucket_name" {
  type        = string
  description = "(Required) The name of the S3 bucket. Must be globally unique and follow S3 naming conventions."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be 3-63 characters, start and end with a lowercase letter or digit, and contain only lowercase letters, digits, hyphens, and dots."
  }
}

variable "force_destroy" {
  type        = bool
  description = "(Optional) Whether to allow Terraform to destroy the bucket even if it contains objects. Set true only for test environments."
  default     = false
}

variable "versioning_enabled" {
  type        = bool
  description = "(Optional) Whether to enable S3 versioning on the bucket."
  default     = true
}

variable "kms_key_arn" {
  type        = string
  description = "(Required) ARN of the KMS CMK used for server-side encryption. Must match ^arn:aws:kms:."

  validation {
    condition     = can(regex("^arn:aws:kms:", var.kms_key_arn))
    error_message = "kms_key_arn must be a valid KMS key ARN matching ^arn:aws:kms:."
  }
}

variable "bucket_key_enabled" {
  type        = bool
  description = "(Optional) Whether to use an S3 bucket key to reduce KMS request costs."
  default     = true
}

variable "block_public_access" {
  type = object({
    block_public_acls       = bool
    block_public_policy     = bool
    ignore_public_acls      = bool
    restrict_public_buckets = bool
  })
  description = "(Optional) Block public access settings. All four must be true to satisfy D22 hardening and the trivy security gate."
  default = {
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
  }

  validation {
    condition = (
      var.block_public_access.block_public_acls &&
      var.block_public_access.block_public_policy &&
      var.block_public_access.ignore_public_acls &&
      var.block_public_access.restrict_public_buckets
    )
    error_message = "All four block_public_access settings must be true to satisfy D22 hardening requirements."
  }
}

variable "object_ownership" {
  type        = string
  description = "(Optional) Object ownership setting. Valid values: BucketOwnerEnforced, BucketOwnerPreferred, ObjectWriter."
  default     = "BucketOwnerEnforced"

  validation {
    condition     = contains(["BucketOwnerEnforced", "BucketOwnerPreferred", "ObjectWriter"], var.object_ownership)
    error_message = "object_ownership must be one of BucketOwnerEnforced, BucketOwnerPreferred, or ObjectWriter."
  }
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
  description = "(Optional) List of lifecycle rules. Each rule may specify a transition (with an input-driven cold storage class from GLACIER, GLACIER_IR, or DEEP_ARCHIVE) and an expiration. Expiration must be greater than transition when both are specified."
  default     = []

  validation {
    condition = alltrue([
      for rule in var.lifecycle_rules :
      rule.transition_storage_class == null || contains(["GLACIER", "GLACIER_IR", "DEEP_ARCHIVE"], rule.transition_storage_class)
    ])
    error_message = "Each lifecycle rule's transition_storage_class must be one of GLACIER, GLACIER_IR, or DEEP_ARCHIVE."
  }

  validation {
    condition = alltrue([
      for rule in var.lifecycle_rules :
      rule.expiration_days == null || rule.expiration_days > 0
    ])
    error_message = "Each lifecycle rule's expiration_days must be a positive integer when specified."
  }

  validation {
    condition = alltrue([
      for rule in var.lifecycle_rules :
      (rule.transition_days == null || rule.expiration_days == null) || rule.expiration_days > rule.transition_days
    ])
    error_message = "Each lifecycle rule's expiration_days must be greater than transition_days when both are specified."
  }
}

variable "access_log_target_bucket" {
  type        = string
  description = "(Optional) Name of the S3 bucket to receive access logs. When null, access logging is disabled."
  default     = null
}

variable "bucket_policy_json" {
  type        = string
  description = "(Optional) A valid JSON bucket resource policy. When null, no bucket policy is attached."
  default     = null

  validation {
    condition     = var.bucket_policy_json == null || can(jsondecode(var.bucket_policy_json))
    error_message = "bucket_policy_json must be a valid JSON string when provided."
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
  default     = "s3-bucket"
}
