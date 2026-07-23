# live/telemetry/us-east-1/<env>/_singletons/shared/athena/000/terragrunt.hcl
#
# Shared Athena workgroup unit.
# Provisions the cost-capped Athena workgroup that queries the telemetry data lake
# (Glue/Athena). Sources the athena-workgroup primitive directly. The workgroup name
# is namespace-derived and exported as workgroup_name for the observability unit's
# Athena scanned-bytes CloudWatch alarm dimension.
#
# This unit replaces the retired analytics unit as the owner of the Athena workgroup:
# it keeps ONLY the vendor-neutral, cost-capped workgroup (the QuickSight/SPICE
# surface the analytics reference module carried has been removed from the platform).
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from account.hcl (D2). A wrong-profile apply fails
# fast because the active caller identity will not match the allowed account (D4).
#
# DEPENDENCY WIRING (D37):
# - dependency.data_lake: consumes lake_kms_key_arn so Athena query results are
#   SSE-KMS encrypted with the same telemetry-data CMK that encrypts the lake objects
#   -- never a hardcoded ARN (D37).
#
# ATHENA RESULTS BUCKET:
# The Athena results S3 bucket (product-family + account-id scoped) must exist before
# the workgroup is created -- the athena-workgroup primitive references it, it does not
# create it. This is the same contract the retired analytics unit relied on, and its
# name shape matches the identity unit's analyst S3 read grant.
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight before plan/apply to confirm the state backend exists in the
# resolved account. The root remote_state block will fail closed if the bucket is absent.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/athena-workgroup?ref=providers/aws/primitives/athena-workgroup/v1.0.1" : "${get_repo_root()}//providers/aws/primitives/athena-workgroup"
}

# ---------------------------------------------------------------------------
# dependencies: upstream unit outputs consumed as inputs (D37)
# ---------------------------------------------------------------------------

dependency "data_lake" {
  config_path = "../../data-lake/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before data-lake is applied.
  # mock_outputs_allowed_terraform_commands restricts mocks to plan and validate
  # only -- apply and destroy always use real outputs (fail closed on apply).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    lake_kms_key_arn = "arn:aws:kms:${local.region}:${local.aws_account_id}:key/mock-lake-cmk"
  }
}

# ---------------------------------------------------------------------------
# locals: account identity, namespace, region (D31, D37, D45)
# ---------------------------------------------------------------------------

locals {
  # Instance-relative local: resolves to the basename of this service-instance directory
  # (e.g. "000") so every same-tier sibling dependency config_path interpolates the
  # owning instance index rather than a hardcoded literal (spec section 4.4).
  svc_instance = basename(get_terragrunt_dir())

  # Account-level locals -- used for ARN construction (D2/D45).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # Region + namespace from the root include (expose=true).
  region    = include.root.locals.region
  namespace = include.root.locals.namespace

  # Athena results S3 bucket: product-family + account-id scoped (D31, D45). This is
  # the same name shape the retired analytics unit used, so the identity unit's analyst
  # inline S3 policy (arn:aws:s3:::<product_family>-athena-results-<account_id>) still
  # matches. product_family resolves from product.hcl via the exposed root local (no
  # literal); the account-id suffix keeps the globally-unique bucket name distinct per
  # account and within the S3 63-char limit.
  athena_results_bucket = "${include.root.locals.product_family}-athena-results-${local.aws_account_id}"
}

# ---------------------------------------------------------------------------
# inputs: athena-workgroup primitive (D37, D45)
# ---------------------------------------------------------------------------

inputs = {
  # Namespace-derived workgroup name (never hardcoded, D45). Exported as workgroup_name
  # and consumed by the observability unit's Athena alarm dimension.
  workgroup_name = "${local.namespace}-workgroup"

  # Athena results bucket: product-family + account-id scoped (D31, D45).
  result_s3_bucket = local.athena_results_bucket

  # Athena results KMS key ARN: the telemetry-data CMK from the data-lake dependency
  # output so result CSVs are SSE-KMS encrypted with the same key as the lake objects
  # -- never a hardcoded ARN (D37).
  result_kms_key_arn = dependency.data_lake.outputs.lake_kms_key_arn

  # Bytes-scanned cost cap per query (100 GB) -- the same cost cap the analytics unit
  # enforced on the workgroup.
  bytes_scanned_cutoff_per_query = 107374182400

  # Tags from the root include (expose=true pattern -- not local.common_tags).
  tags = include.root.locals.common_tags
}
