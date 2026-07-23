# live/telemetry/us-east-1/sandbox/000/data-lake/service.hcl
#
# Service layer for the sandbox data-lake unit.
# Basename resolves to "data-lake" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit creates the sandbox S3 data lake bucket, Glue catalog database,
# Kinesis Firehose delivery stream, and the telemetry-data KMS CMK via a single
# call to the references/data-lake reference module (AC-3). The reference module
# is the canonical composition point; no primitive is sourced directly from this unit.
#
# Module source: providers/aws/references/data-lake (spec 02 Section 3.5).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Dependencies:
#   - references/data-lake module (E1-F5-S2-T1) must be present in the repo
#     at providers/aws/references/data-lake before this unit can be applied.
#   - sandbox dns-prod-zone unit (E6-F1-S1-T1) must be applied and expose
#     kms_key_arn so the data-lake leaf can read it via terragrunt dependency.
#   - sandbox identity unit (E6-F3-S2-T1) must be applied and expose the
#     analyst_role_arn and admin_role_arn used in the lake KMS key policy.
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote
#     state backend and lock table exist (D40).
#   - _envcommon/data-lake.hcl must define bucket_name, glue_database_name,
#     glue_table_name, firehose_stream_name, and role names (D37).
#
# The _envcommon/data-lake.hcl shared template is included by the leaf terragrunt.hcl
# (not here at the service layer). No _envcommon include is used at this layer.

locals {
  # service resolves to the directory basename, i.e. "data-lake".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())

  # ---------------------------------------------------------------------------
  # telemetry-data CMK key policy (iac/04 section 3.7, D8/AC-FIX-05). Declared at the
  # service layer (not inlined in the leaf) so the policy is a reproducible, ARN-scoped
  # config value. Account + region resolved from the hierarchy; the TelemetryAnalyst/
  # TelemetryAdmin role ARNs are constructed from the account id (D45) -- the identity
  # unit creates those exact role names -- so the policy is self-contained here.
  # ---------------------------------------------------------------------------
  account_vars     = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id   = local.account_vars.locals.aws_account_id
  region           = read_terragrunt_config(find_in_parent_folders("region.hcl")).locals.aws_region
  analyst_role_arn = "arn:aws:iam::${local.aws_account_id}:role/TelemetryAnalyst"
  admin_role_arn   = "arn:aws:iam::${local.aws_account_id}:role/TelemetryAdmin"

  # ---------------------------------------------------------------------------
  # Cross-account BI read grant (portal/QuickSight retirement Phase 8). The external
  # AWS principals allowed to read the telemetry data lake are declared in
  # common/cross-account-read.json (the exempt common/ mapping layer, so external
  # account ids never appear as literals in the live tree -- tg-no-hardcoded-identity).
  # The data-lake reference attaches the S3 bucket read policy from the
  # cross_account_read_principals input (wired in the leaf terragrunt.hcl); the
  # telemetry-data CMK policy is owned here, so the CrossAccountDataLakeKmsRead
  # statement below grants each external account root kms:Decrypt/DescribeKey scoped by
  # kms:ViaService=s3.<region> -- mirroring the module's cross_account_kms_statements
  # output. Empty map => no statement (no-op default preserved).
  # ---------------------------------------------------------------------------
  cross_account_read           = jsondecode(file("${get_repo_root()}/terragrunt/common/cross-account-read.json")).principals
  cross_account_principal_arns = [for _, v in local.cross_account_read : "arn:aws:iam::${v.account_id}:root"]
  cross_account_kms_statements = length(local.cross_account_read) > 0 ? [
    {
      Sid       = "CrossAccountDataLakeKmsRead"
      Effect    = "Allow"
      Principal = { AWS = local.cross_account_principal_arns }
      Action    = ["kms:Decrypt", "kms:DescribeKey"]
      Resource  = "*"
      Condition = {
        StringEquals = {
          "kms:ViaService" = "s3.${local.region}.amazonaws.com"
        }
      }
    }
  ] : []

  # ---------------------------------------------------------------------------
  # telemetry-data CMK key policy. QuickSight consumer surface retirement
  # (portal/QuickSight retirement, Phase 2): this policy NO LONGER grants QuickSight.
  # The QuickSight service principal was removed from the Athena statement, and the
  # former QuickSightServiceRoleDataAccess statement (which granted the QuickSight-managed
  # service role kms:Decrypt/DescribeKey for dashboard DIRECT-QUERY source reads) was
  # removed entirely, because that role is deleted in a later retirement phase and a KMS
  # PutKeyPolicy referencing a deleted principal fails at apply. The telemetry-data CMK
  # itself is KEPT -- it still encrypts the data-lake objects the BI team reads via
  # Athena (the athena service principal + Athena/Admin/Analyst role grants below are
  # retained).
  # ---------------------------------------------------------------------------
  lake_kms_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid       = "RootAdminAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.aws_account_id}:root" }
        Action    = ["kms:*"]
        Resource  = "*"
      },
      {
        Sid       = "TelemetryRoleDataAccess"
        Effect    = "Allow"
        Principal = { AWS = [local.analyst_role_arn, local.admin_role_arn] }
        Action    = ["kms:GenerateDataKey", "kms:Decrypt", "kms:DescribeKey"]
        Resource  = "*"
      },
      {
        Sid       = "AthenaAccess"
        Effect    = "Allow"
        Principal = { Service = ["athena.amazonaws.com"] }
        Action    = ["kms:GenerateDataKey", "kms:Decrypt", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = ["athena.${local.region}.amazonaws.com"]
          }
        }
      },
      {
        Sid       = "CloudWatchLogsAccess"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.aws_account_id}:log-group:*"
          }
        }
      },
    ], local.cross_account_kms_statements)
  })

}
