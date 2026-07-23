# ---------------------------------------------------------------------------
# Child module source variables (const = true; default to in-repo relative paths).
# Override with a pinned git URL at deploy time via use_pinned_module_sources.
# ---------------------------------------------------------------------------

variable "zone_source" {
  type        = string
  const       = true
  description = "Source path for the route53-zone primitive used by the zone child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/route53-zone"
}

variable "kms_source" {
  type        = string
  const       = true
  description = "Source path for the kms-key primitive used by the kms child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/kms-key"
}

variable "ssm_source" {
  type        = string
  const       = true
  description = "Source path for the ssm-parameter primitive used by the ssm child module. Defaults to the in-repo relative path; override with a pinned git URL at deploy time via use_pinned_module_sources."
  default     = "../../primitives/ssm-parameter"
}

variable "zone_name" {
  type        = string
  description = "(Required) DNS name for the prod hosted zone. Passed to the route53-zone primitive. Must match pattern ^[a-z0-9.-]+$."

  validation {
    condition     = can(regex("^[a-z0-9.-]+$", var.zone_name))
    error_message = "zone_name must contain only lowercase alphanumeric characters, hyphens, and dots."
  }
}

variable "kms_alias" {
  type        = string
  description = "(Required) Alias suffix for the single prod DNS/cert customer-managed KMS key (docs/terragrunt-concepts.md). The kms-key primitive prefixes 'alias/' automatically. This reference creates alias/telemetry-config (docs/terragrunt-concepts.md). Must match pattern [A-Za-z0-9/_-]+."

  validation {
    condition     = can(regex("^[A-Za-z0-9/_-]+$", var.kms_alias))
    error_message = "kms_alias must contain only alphanumeric characters, slashes, underscores, or hyphens and must not include the 'alias/' prefix."
  }
}

variable "ssm_parameters" {
  type = map(object({
    type       = string
    value      = string
    kms_key_id = optional(string)
  }))
  description = "(Required) Map of non-secret zone metadata to publish to SSM Parameter Store. Each key is the fully-qualified parameter name; each value provides type (String/StringList/SecureString), value, and optional kms_key_id. The module iterates this map with for_each, instantiating one ssm-parameter per entry (docs/terragrunt-concepts.md)."

  validation {
    condition     = length(var.ssm_parameters) > 0
    error_message = "ssm_parameters must contain at least one entry."
  }

  validation {
    condition     = alltrue([for k, v in var.ssm_parameters : contains(["String", "StringList", "SecureString"], v.type)])
    error_message = "Every ssm_parameters entry type must be one of: String, StringList, SecureString."
  }
}

variable "kms_key_principals" {
  type        = list(string)
  description = "(Required) List of IAM principal ARNs (users, roles, accounts) that are granted kms:Decrypt and kms:GenerateDataKey on the prod DNS/cert CMK. No wildcard principals are permitted (docs/terragrunt-concepts.md)."

  validation {
    condition     = length(var.kms_key_principals) > 0
    error_message = "kms_key_principals must contain at least one principal ARN."
  }

  validation {
    condition     = alltrue([for p in var.kms_key_principals : !contains(["*"], p)])
    error_message = "kms_key_principals must not contain wildcard ('*') principals."
  }
}

variable "region" {
  type        = string
  description = "(Required) AWS region in which the KMS key ViaService condition is scoped (e.g. 'us-east-1'). Used to construct 'ssm.<region>.amazonaws.com' in the KMS key policy. Must be a valid AWS region identifier."

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be a valid AWS region identifier (e.g. 'us-east-1', 'eu-west-2')."
  }
}

variable "force_destroy" {
  type        = bool
  description = "(Optional) Whether to destroy all records in the zone before zone deletion. Set to true only for ephemeral test zones. Defaults to false."
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
  default     = "dns-prod-zone"
}

# ---------------------------------------------------------------------------
# Cross-account remote-state read (foundation-tier only; gated, default OFF).
#
# When this reference is deployed as the FOUNDATION-tier dns-prod-zone unit
# (terragrunt/live/.../bootstrap/<role>/dns-prod-zone), a cross-account terragrunt
# plan -- notably the dns-owner dns-delegation unit, which reads this foundation
# zone's name_servers from the env-account remote state -- must be able to decrypt
# and read this unit's own remote-state object across accounts. terragrunt 1.x
# treats a 403 reading dependency state as FATAL (its dependency mock_outputs only
# rescue an ABSENT state, not an access-denied one), so the cross-account plan role
# needs BOTH a kms:Decrypt grant on the remote-state CMK AND a read statement on the
# remote-state bucket policy. These two resources supply exactly that, gated behind
# the inputs below so the standalone module (and its terratest) creates neither
# (count 0 / empty for_each) and performs no plan-time AWS read.
# ---------------------------------------------------------------------------

variable "tfstate_cmk_alias" {
  type        = string
  description = "(Optional) Alias name of this unit's remote-state CMK (e.g. 'alias/222222222222-tfstate'), resolved to a key id for the cross-account kms:Decrypt grants. Only consulted when tfstate_cmk_decrypt_grantee_arns is non-empty; the empty default keeps the standalone module free of any KMS grant and any plan-time KMS read."
  default     = ""
}

variable "tfstate_cmk_decrypt_grantee_arns" {
  type        = list(string)
  description = "(Optional) Cross-account IAM principal ARNs granted kms:Decrypt on the remote-state CMK via an ADDITIVE aws_kms_grant. A grant can never remove the key's root access, so it cannot lock out the key. Used so cross-account terragrunt plans (the dns-owner dns-delegation unit; the prod cross-env plan reading sandbox state) can decrypt this foundation zone's remote state. Empty default => no grant (empty for_each)."
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
  description = "(Optional) Cross-account IAM principal ARNs granted read-only access to this unit's remote-state bucket (s3:GetObject/GetObjectVersion on objects; s3:ListBucket/GetBucketVersioning on the bucket) via the bucket policy, so a cross-account terragrunt plan (the dns-owner dns-delegation unit) can load this foundation zone's state. Empty default => no bucket policy (count 0)."
  default     = []

  validation {
    condition     = alltrue([for a in var.tfstate_bucket_read_grantee_arns : can(regex("^arn:aws:iam::[0-9]{12}:", a))])
    error_message = "Every tfstate_bucket_read_grantee_arns entry must be a full IAM principal ARN (arn:aws:iam::<account>:...)."
  }
}
