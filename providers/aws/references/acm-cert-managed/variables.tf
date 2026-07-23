# ---------------------------------------------------------------------------
# Child module source variable (const = true; defaults to the in-repo relative path).
# Override with a pinned git URL at deploy time via use_pinned_module_sources.
# ---------------------------------------------------------------------------

variable "certificate_source" {
  type        = string
  const       = true
  description = "Source path for the acm-certificate primitive used by the certificate child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/acm-certificate"
}

# ---------------------------------------------------------------------------
# Certificate inputs (passthrough to the acm-certificate primitive).
# ---------------------------------------------------------------------------

variable "domain_name" {
  type        = string
  description = "(Required) Fully qualified domain name for the certificate. Must match pattern ^[a-z0-9.-]+$."

  validation {
    condition     = can(regex("^[a-z0-9.-]+$", var.domain_name))
    error_message = "domain_name must contain only lowercase alphanumeric characters, hyphens, and dots."
  }
}

variable "subject_alternative_names" {
  type        = list(string)
  description = "(Optional) List of domains that should be Subject Alternative Names in the issued certificate. The pretty-name SAN is MANDATORY on the collector/portal certs (D19). Each element must match pattern ^[a-z0-9.*-]+$."
  default     = []

  validation {
    condition     = alltrue([for san in var.subject_alternative_names : can(regex("^[a-z0-9.*-]+$", san))])
    error_message = "Each subject_alternative_names entry must contain only lowercase alphanumeric characters, dots, asterisks, and hyphens."
  }
}

variable "validation_method" {
  type        = string
  description = "(Optional) Method to use for domain validation. Valid values: DNS, EMAIL."
  default     = "DNS"

  validation {
    condition     = contains(["DNS", "EMAIL"], var.validation_method)
    error_message = "validation_method must be one of DNS or EMAIL."
  }
}

variable "key_algorithm" {
  type        = string
  description = "(Optional) Algorithm of the public and private key pair the issued certificate uses. Valid values: RSA_2048, EC_prime256v1, EC_secp384r1."
  default     = "RSA_2048"

  validation {
    condition     = contains(["RSA_2048", "EC_prime256v1", "EC_secp384r1"], var.key_algorithm)
    error_message = "key_algorithm must be one of RSA_2048, EC_prime256v1, or EC_secp384r1."
  }
}

variable "wait_for_validation" {
  type        = bool
  description = "(Optional) Reserved input for the consuming unit. This module does NOT create aws_acm_certificate_validation regardless of value (per decision D24). The collector/portal certs set this to false so the request returns immediately; validation is created downstream (D26)."
  default     = false
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
  description = "(Optional) Value for the Module tag identifying the source reference module."
  default     = "acm-cert-managed"
}

# ---------------------------------------------------------------------------
# Cross-account remote-state read (gated, default OFF).
#
# When this reference is deployed as the prod acm-collector / acm-portal unit, a
# cross-account terragrunt plan -- the dns-owner _singletons/pretty validate-collector /
# validate-portal units, which read this unit's domain_validation_options from the
# env-account remote state -- must be able to decrypt and read this unit's own
# remote-state object across accounts. terragrunt 1.x treats a 403 reading dependency
# state as FATAL (its dependency mock_outputs rescue only an ABSENT state, not an
# access-denied one), so the cross-account plan role needs read on the bucket and decrypt
# on the CMK. These resources supply exactly that, gated behind the inputs below so the
# standalone module (and its terratest) creates neither (count 0 / empty for_each) and
# performs no plan-time AWS read. Sandbox/qa leave the gate OFF (pretty/validate run
# in-account).
# ---------------------------------------------------------------------------

variable "tfstate_cmk_alias" {
  type        = string
  description = "(Optional) Alias name of this unit's remote-state CMK (e.g. 'alias/111111111111-tfstate'), resolved to a key id for the cross-account kms:Decrypt grants. Only consulted when tfstate_cmk_decrypt_grantee_arns is non-empty; the empty default keeps the standalone module free of any KMS grant and any plan-time KMS read."
  default     = ""
}

variable "tfstate_cmk_decrypt_grantee_arns" {
  type        = list(string)
  description = "(Optional) Cross-account IAM principal ARNs granted kms:Decrypt on the remote-state CMK via an ADDITIVE aws_kms_grant. A grant can never remove the key's root access, so it cannot lock out the key. Empty default => no grant (empty for_each). NOTE: the prod tfstate CMK is account-wide (one CMK per account) and the dns-owner plan role already holds an account-level Decrypt grant, so the acm leaves leave this empty and only enable the bucket policy."
  default     = []

  validation {
    condition     = alltrue([for a in var.tfstate_cmk_decrypt_grantee_arns : can(regex("^arn:aws:iam::[0-9]{12}:", a))])
    error_message = "Every tfstate_cmk_decrypt_grantee_arns entry must be a full IAM principal ARN (arn:aws:iam::<account>:...)."
  }
}

variable "tfstate_bucket_name" {
  type        = string
  description = "(Optional) Name of this unit's own remote-state S3 bucket. When tfstate_bucket_read_grantee_arns is non-empty, the module attaches an aws_s3_bucket_policy to this bucket that replicates terragrunt's EnforcedTLS + RootAccess statements (so terragrunt's additive backend policy management detects no drift) and adds the cross-account read statements. Empty default => no bucket policy (count 0)."
  default     = ""
}

variable "tfstate_bucket_account_id" {
  type        = string
  description = "(Optional) The 12-digit account id that owns the remote-state bucket; used as the principal of the replicated RootAccess statement (arn:aws:iam::<id>:root). Required (non-empty) when tfstate_bucket_read_grantee_arns is non-empty."
  default     = ""

  validation {
    condition     = var.tfstate_bucket_account_id == "" || can(regex("^[0-9]{12}$", var.tfstate_bucket_account_id))
    error_message = "tfstate_bucket_account_id must be empty or a 12-digit account id."
  }
}

variable "tfstate_bucket_read_grantee_arns" {
  type        = list(string)
  description = "(Optional) Cross-account IAM principal ARNs granted read-only access to this unit's remote-state bucket (s3:GetObject/GetObjectVersion on objects; s3:ListBucket/GetBucketVersioning on the bucket) via the bucket policy, so a cross-account terragrunt plan (the dns-owner pretty/validate units) can load this unit's state. Empty default => no bucket policy (count 0)."
  default     = []

  validation {
    condition     = alltrue([for a in var.tfstate_bucket_read_grantee_arns : can(regex("^arn:aws:iam::[0-9]{12}:", a))])
    error_message = "Every tfstate_bucket_read_grantee_arns entry must be a full IAM principal ARN (arn:aws:iam::<account>:...)."
  }
}
