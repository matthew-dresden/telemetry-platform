# live/telemetry/us-east-1/<env>/000/data-lake/000/terragrunt.hcl
#
# Service account data-lake unit.
# Creates the S3 data lake bucket, Glue catalog database, Kinesis Firehose
# delivery stream, and the telemetry-data KMS CMK via the references/data-lake
# reference module (AC-3, E1-F5-S2-T1). Sources exactly ONE module per AC-3:
# no primitive is sourced directly from this leaf.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# DEPENDENCY WIRING (D37/AC-3):
# - dependency.dns_prod_zone: consumes kms_key_arn from the sandbox dns-prod-zone
#   unit (E6-F1-S1-T1). The dns-prod-zone KMS key ARN is included as a principal
#   in the lake KMS key policy so the platform key admin can manage the lake CMK.
# - dependency.identity: consumes analyst_role_arn and admin_role_arn from the
#   sandbox identity unit (E6-F3-S2-T1). These roles are listed as principals in
#   the lake KMS key policy for Athena/QuickSight data access.
#
# RECORDS RETENTION (AC-11):
# The S3 bucket lifecycle implements 1 year hot storage (365 days), 1 year cold
# storage (GLACIER for another 365 days), then expiry at 730 days total.
# transition_days and expiration_days are deployment-unique values sourced from
# terraform.tfvars (spec section 4.5, AC-FUNC-002) rather than inlined here.
# The references/data-lake module propagates these values to the composed s3-bucket
# primitive lifecycle_rules (AC-11).
#
# SINGLE MODULE CONTRACT (AC-3):
# This leaf sources ONLY references/data-lake. No primitive is sourced directly.
# Grep the rendered config for a single source = to verify at refactor time.
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight before plan/apply to confirm the sandbox state backend
# exists in the resolved service account. The root remote_state block will fail closed
# if the bucket does not exist.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D4, D37, D40, D45, D47, AC-3, AC-11.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/data-lake.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/data-lake?ref=providers/aws/references/data-lake/v2.7.0" : "${get_repo_root()}//providers/aws/references/data-lake"
}

# ---------------------------------------------------------------------------
# dependencies: upstream unit outputs consumed as inputs (D37/AC-3)
# ---------------------------------------------------------------------------

dependency "dns_prod_zone" {
  config_path = "../../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"

  # mock_outputs are provided so that plan can run before dns-prod-zone is applied,
  # while still failing closed on apply (mock_outputs_allowed_terraform_commands
  # restricts mocks to plan and validate only -- never apply or destroy).
  # KMS ARN derived from local.region and local.aws_account_id (AC-13).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    kms_key_arn = "arn:aws:kms:${local.region}:${local.aws_account_id}:key/mock-dns-zone-kms-key-id"
  }
}

dependency "identity" {
  config_path = "../../identity/${local.svc_instance}"

  # mock_outputs are provided so that plan can run before identity is applied.
  # IAM ARNs derived from local.aws_account_id (IAM is global -- no region) (AC-13).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    analyst_role_arn = "arn:aws:iam::${local.aws_account_id}:role/mock-TelemetryAnalyst"
    admin_role_arn   = "arn:aws:iam::${local.aws_account_id}:role/mock-TelemetryAdmin"
  }
}

# ---------------------------------------------------------------------------
# locals: account identity, ARN derivation, trust and key policies (D31, D37)
# ---------------------------------------------------------------------------

locals {
  # Instance-relative local: resolves to the basename of this service-instance directory
  # (e.g. "000") so that every same-tier sibling dependency config_path interpolates
  # the owning instance index rather than a hardcoded literal (spec section 4.4,
  # AC-FUNC-001, AC-FUNC-002). Terragrunt evaluates locals before dependency blocks,
  # so local.svc_instance is valid inside config_path expressions (spec section 4.4).
  # Copying this folder to index "001" causes svc_instance to resolve to "001",
  # wiring all sibling deps at the new index with zero edits (spec section G5, AC-FUNC-003).
  svc_instance = basename(get_terragrunt_dir())

  # Foundation-tier dns-prod-zone lives at bootstrap/<role>/ (a sibling of the env
  # subtree), OUT of the disposable 000 instance sets. bootstrap_role is derived from
  # environment.hcl (never hardcoded, D31). The foundation active set is read from its
  # own active.hcl so a copied service instance still resolves the foundation's active set.
  bootstrap_role       = "${read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment}_role"
  dns_prod_zone_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../../bootstrap/${local.bootstrap_role}/dns-prod-zone/active.hcl").locals.active

  # Account-level locals -- used for KMS policy principals and ARN construction (D2/D45).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  service_vars   = read_terragrunt_config(find_in_parent_folders("service.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # Region from root -- used for kms:ViaService and ARN construction (D47).
  region = include.root.locals.region

  # Namespace from root -- used to derive resource ARNs (D37/D45).
  # namespace is the canonical "_"-in-field form; namespace_dns is the flattened "_"->"-"
  # form for the "_"-hostile S3 bucket name (S3 rejects "_").
  namespace     = include.root.locals.namespace
  namespace_dns = include.root.locals.namespace_dns

  # ---------------------------------------------------------------------------
  # Firehose delivery role trust policy (iac/04 section 3.3).
  # Trusts the firehose.amazonaws.com service principal with an ExternalId
  # condition scoped to the sandbox account id (D31 -- no hardcoded account id
  # literal; local.aws_account_id flows from account.hcl basename derivation).
  # ---------------------------------------------------------------------------
  firehose_assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowFirehoseAssumption"
        Effect = "Allow"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = local.aws_account_id
          }
        }
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # ARN inputs for the Firehose delivery role inline policy (B1, B4, S7).
  # All ARNs are input-driven from the namespace (D31, D45, D47).
  # The Firehose log group ARN uses the :* suffix to scope logs:PutLogEvents
  # to all log streams within the group.
  # ---------------------------------------------------------------------------
  bucket_name          = "${local.namespace_dns}-data-lake"
  glue_database_name   = replace("${local.namespace}_telemetry", "-", "_")
  glue_table_name      = replace("${local.namespace}_events", "-", "_")
  firehose_stream_name = "${local.namespace}-telemetry-events"

  bucket_arn             = "arn:aws:s3:::${local.bucket_name}"
  glue_table_arn         = "arn:aws:glue:${local.region}:${local.aws_account_id}:table/${local.glue_database_name}/${local.glue_table_name}"
  firehose_log_group_arn = "arn:aws:logs:${local.region}:${local.aws_account_id}:log-group:/aws/firehose/${local.firehose_stream_name}:*"
}

# ---------------------------------------------------------------------------
# inputs: data-lake reference module (AC-3, D37, D45, D47)
#
# Naming inputs (bucket_name, glue_database_name, glue_table_name,
# firehose_stream_name, firehose_role_name, transition_storage_class)
# are merged from _envcommon/data-lake.hcl (via the "envcommon" include above).
# Leaf-level inputs here extend the shared template with sandbox-specific values:
# the Firehose delivery role trust policy, ARN-scoped policy inputs, KMS policy, region.
#
# S3 lifecycle retention policy (AC-11):
#   - transition_storage_class = GLACIER (from _envcommon)
#   - transition_days: deployment-unique; sourced from terraform.tfvars (spec section 4.5)
#   - expiration_days: deployment-unique; sourced from terraform.tfvars (spec section 4.5)
#     The references/data-lake module propagates these values to the s3-bucket primitive
#     lifecycle_rules (AC-11). Retuning retention = edit terraform.tfvars only.
#
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true (spec 02).
# ---------------------------------------------------------------------------

inputs = merge({
  # S3 lifecycle retention (AC-11): transition_days and expiration_days are deployment-unique
  # values sourced from terraform.tfvars (spec section 4.5, AC-FUNC-002). Terraform auto-loads
  # terraform.tfvars after Terragrunt copies the unit directory into the module working directory
  # (spec section 1.1). To retune retention for this deployment, edit terraform.tfvars only.

  # Trust policy for the composed Firehose delivery role. This single role is also
  # wired as the data_format_conversion schema_configuration.role_arn, so trusting
  # firehose.amazonaws.com is sufficient -- no separate Glue conversion role exists.
  firehose_assume_role_policy_json = local.firehose_assume_role_policy_json

  # ARN inputs for the Firehose delivery role inline policy (B1, B4, S7).
  firehose_role_s3_bucket_arn  = local.bucket_arn
  firehose_role_glue_table_arn = local.glue_table_arn
  firehose_role_log_group_arn  = local.firehose_log_group_arn

  # ---------------------------------------------------------------------------
  # telemetry-data CMK policy per iac/04 section 3.7 (no wildcard principals).
  # Built directly in inputs (not locals) because the TelemetryRoleDataAccess
  # statement references dependency.identity.outputs.{analyst,admin}_role_arn, and
  # terragrunt only resolves dependency.<name>.outputs at input-evaluation time --
  # a locals block cannot reference dependency outputs (terragrunt v1.0.7:
  # "dependency is not defined"). All other principals remain input-driven ARNs
  # derived from local.aws_account_id (D31, no wildcard principal). The policy grants:
  #   1. Root admin access to the sandbox account root (required by KMS; D45).
  #   2. TelemetryAnalyst and TelemetryAdmin roles (from identity dependency) for
  #      data access via Athena and QuickSight (D37).
  #   3. The Athena service principal (iac/04 section 3.7). Athena still queries the
  #      lake for BI cross-account reads and the e2e_verify path.
  #
  # QuickSight consumer surface retirement (portal/QuickSight retirement, Phase 2):
  # the telemetry-data CMK policy in service.hcl NO LONGER grants QuickSight. The
  # quicksight.amazonaws.com service principal was removed from the Athena statement
  # and the QuickSightServiceRoleDataAccess statement (which granted the
  # aws-quicksight-service-role-v0 role kms:Decrypt/DescribeKey) was removed entirely,
  # because that QuickSight-managed service role is deleted in a later retirement phase
  # and a KMS PutKeyPolicy referencing a deleted principal fails. This comment is
  # touched in sync with the service.hcl policy change so the change-detector maps the
  # service.hcl change to this data-lake unit (the service.hcl sits one dir above this
  # leaf's terragrunt.hcl, so without this touch it maps to zero units).
  # ---------------------------------------------------------------------------
  lake_kms_policy_json = local.service_vars.locals.lake_kms_policy_json

  # Cross-account BI read grant (portal/QuickSight retirement Phase 8). The external
  # read principals are declared in common/cross-account-read.json and exposed via
  # service.hcl (local.cross_account_read). The data-lake reference attaches the S3
  # bucket read policy (s3:GetObject/ListBucket) for each account root; the matching
  # telemetry-data CMK decrypt grant is merged into lake_kms_policy_json above
  # (service.hcl CrossAccountDataLakeKmsRead). {} => no grant (default preserved).
  cross_account_read_principals = local.service_vars.locals.cross_account_read

  # AWS region for kms:ViaService condition construction (iac/04 section 3.3).
  region = include.root.locals.region

  # Tags from the root include (expose=true pattern -- not local.common_tags).
  tags = include.root.locals.common_tags

  # CWL-split transform Lambda (data-lake v2.0.0, BUG-6-large): re-ingests each
  # CloudWatch Logs event as its own record so >500-event deliveries partition to
  # raw/ instead of errors/. Names are namespace-derived (collision-free, <=64 chars).
  cwl_transform_lambda_name      = "${local.namespace}-cwl-split"
  cwl_transform_lambda_role_name = "${local.namespace}-cwl-split-role"
  },
  # ---------------------------------------------------------------------------
  # Child module source overrides (AC-7, AC-8, spec Section 4.3, Section 5).
  # When use_pinned_module_sources=true (prod): every in-repo child source is set to
  # its pinned git URL so the composed module tree is fully pinned.
  # When use_pinned_module_sources=false (sandbox/QA/root): the *_source keys are
  # OMITTED entirely so the module's relative-path defaults apply (spec Section 4.3,
  # Leaf behavior toggle=false: "Does NOT set any *_source inputs"). They MUST be
  # omitted -- not set to null -- because a terraform child module `source` argument
  # is a const-typed variable; passing an explicit null overrides the relative-path
  # default and crashes `terraform init` ("panic: value is null" in module install).
  # A conditional merge adds the keys only in the pinned (prod) context.
  # ---------------------------------------------------------------------------
  local.account_vars.locals.use_pinned_module_sources ? {
    lake_kms_key_source  = "${include.root.locals.module_git_base}//providers/aws/primitives/kms-key?ref=providers/aws/primitives/kms-key/v1.0.1"
    lake_bucket_source   = "${include.root.locals.module_git_base}//providers/aws/primitives/s3-bucket?ref=providers/aws/primitives/s3-bucket/v1.0.1"
    glue_catalog_source  = "${include.root.locals.module_git_base}//providers/aws/primitives/glue-catalog-database?ref=providers/aws/primitives/glue-catalog-database/v1.1.0"
    firehose_role_source = "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1"
    firehose_source      = "${include.root.locals.module_git_base}//providers/aws/primitives/kinesis-firehose-delivery-stream?ref=providers/aws/primitives/kinesis-firehose-delivery-stream/v2.0.0"
  } : {}
)
