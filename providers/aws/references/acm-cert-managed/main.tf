# ---------------------------------------------------------------------------
# acm-cert-managed reference module
#
# Wraps the acm-certificate primitive (a pure TLS certificate REQUEST per D24 --
# aws_acm_certificate_validation is owned by the consuming unit) and adds the SAME
# gated, default-OFF cross-account remote-state read pattern shipped on the
# dns-prod-zone reference (#135): an additive (lockout-safe) aws_kms_grant on this
# unit's remote-state CMK plus an aws_s3_bucket_policy on this unit's own remote-state
# bucket.
#
# WHY: when this reference is deployed as the prod acm-collector / acm-portal unit, a
# cross-account terragrunt plan -- notably the dns-owner _singletons/pretty
# validate-collector / validate-portal units, which read this unit's
# domain_validation_options (the pretty-SAN ACM validation CNAME) from the env-account
# remote state -- must be able to decrypt and read this unit's own remote-state object
# across accounts. terragrunt treats a 403 reading dependency state as FATAL (its
# dependency mock_outputs rescue only an ABSENT state, not an access-denied one), so the
# cross-account plan role needs read on the bucket and decrypt on the CMK. Both are
# gated: with the default empty inputs the data source, the grant, and the bucket policy
# are all absent, so the standalone module (and its terratest) performs zero plan-time
# AWS reads and creates zero extra resources. In sandbox/qa the pretty/validate units run
# IN-account, so the gate stays OFF (the in-account deploy role already has root access).
# ---------------------------------------------------------------------------

# State migration: the acm-collector / acm-portal leaves previously sourced the
# acm-certificate PRIMITIVE directly, so the certificate lived at the root state address
# aws_acm_certificate.this. This reference wraps that primitive as module.certificate, so
# the address becomes module.certificate.aws_acm_certificate.this. The moved block
# refactors the existing state in place -- no destroy/recreate of the live certificate.
# On a fresh deploy (terratest, examples, a never-applied env) the `from` address is
# absent from state, so the moved block is a documented no-op.
moved {
  from = aws_acm_certificate.this
  to   = module.certificate.aws_acm_certificate.this
}

module "certificate" {
  source = var.certificate_source

  domain_name               = var.domain_name
  subject_alternative_names = var.subject_alternative_names
  validation_method         = var.validation_method
  key_algorithm             = var.key_algorithm
  wait_for_validation       = var.wait_for_validation

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  # The wrapped certificate keeps the primitive's Module tag so the resource's tag value
  # is byte-identical to the pre-wrap primitive-sourced unit (no tag drift on migration).
  module_tag = "acm-certificate"
}

# ---------------------------------------------------------------------------
# Cross-account remote-state read (gated, default OFF). Mirrors the shipped
# dns-prod-zone (#135) pattern exactly.
# ---------------------------------------------------------------------------

# Resolve the remote-state CMK alias to a key id for the grant. Gated so it is read only
# when a cross-account decrypt grantee is configured (the account that owns the CMK
# performs this read at apply -- never the standalone terratest).
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
