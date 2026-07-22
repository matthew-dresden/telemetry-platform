# Caller account id is required to scope the CloudWatch Logs kms:EncryptionContext
# condition on the telemetry-config CMK policy (locals.tf AllowCloudWatchLogs). Derived
# from the active AWS provider identity so the account id is never hardcoded (D45/D47).
data "aws_caller_identity" "current" {}

module "zone" {
  source = var.zone_source

  zone_name     = var.zone_name
  force_destroy = var.force_destroy

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "route53-zone"
}

module "kms" {
  source = var.kms_source

  alias_name          = var.kms_alias
  description         = "Prod DNS and certificate customer-managed key (telemetry-config) for zone ${var.zone_name}"
  enable_key_rotation = true
  policy_json         = local.kms_policy_json

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "kms-key"
}

module "ssm" {
  source   = var.ssm_source
  for_each = var.ssm_parameters

  name       = each.key
  type       = each.value.type
  value      = each.value.value
  kms_key_id = each.value.kms_key_id

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "ssm-parameter"
}

# ---------------------------------------------------------------------------
# Cross-account remote-state read (foundation-tier only; gated, default OFF).
#
# These resources let a cross-account terragrunt plan -- notably the dns-owner
# dns-delegation unit, which reads this foundation zone's name_servers from the
# env-account remote state -- decrypt and read this unit's own remote-state object
# across accounts. terragrunt treats a 403 reading dependency state as FATAL (its
# dependency mock_outputs rescue only an ABSENT state, not access-denied), so the
# cross-account plan role needs both a kms:Decrypt grant on the remote-state CMK
# and a read statement on the remote-state bucket policy. Both are gated: with the
# default empty inputs the data source, the grant, and the bucket policy are all
# absent, so the standalone module (and its terratest) performs zero plan-time AWS
# reads and creates zero extra resources.
# ---------------------------------------------------------------------------

# Resolve the remote-state CMK alias to a key id for the grant. Gated so it is read
# only when a cross-account decrypt grantee is configured (the env account that owns
# the CMK performs this read at apply -- never the standalone terratest).
data "aws_kms_key" "tfstate" {
  count  = local.enable_tfstate_cmk_grant ? 1 : 0
  key_id = var.tfstate_cmk_alias
}

# Additive cross-account kms:Decrypt grant on the remote-state CMK. A KMS grant is
# additive and cannot remove the key's root access, so it is lockout-safe (unlike a
# key-policy edit). One grant per grantee principal.
resource "aws_kms_grant" "tfstate_cross_account_read" {
  for_each = local.enable_tfstate_cmk_grant ? toset(var.tfstate_cmk_decrypt_grantee_arns) : toset([])

  name              = "tfstate-cross-account-read-${substr(md5(each.value), 0, 12)}"
  key_id            = data.aws_kms_key.tfstate[0].id
  grantee_principal = each.value
  operations        = ["Decrypt"]

  lifecycle {
    precondition {
      condition     = var.tfstate_cmk_alias != ""
      error_message = "tfstate_cmk_alias must be set when tfstate_cmk_decrypt_grantee_arns is non-empty (it resolves the remote-state CMK for the cross-account grant)."
    }
  }
}

# Cross-account read on this unit's own remote-state bucket. The policy replicates
# terragrunt's EnforcedTLS + RootAccess statements (so terragrunt's additive backend
# policy management detects no drift) and adds the cross-account read statements.
resource "aws_s3_bucket_policy" "tfstate_cross_account_read" {
  count = local.enable_tfstate_bucket_policy ? 1 : 0

  bucket = var.tfstate_bucket_name
  policy = local.tfstate_bucket_policy_json

  lifecycle {
    precondition {
      condition     = var.tfstate_bucket_name != "" && var.tfstate_bucket_account_id != ""
      error_message = "tfstate_bucket_name and tfstate_bucket_account_id must both be set when tfstate_bucket_read_grantee_arns is non-empty (the RootAccess statement replicates terragrunt's account-root grant)."
    }
  }
}
