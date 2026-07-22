# live/telemetry/us-east-1/bootstrap/dns_owner_role/state-bootstrap/000/terragrunt.hcl
#
# Root DNS-owner account (444444444444) state-bootstrap unit.
# Creates the remote-state foundation for the root DNS-owner account via the state-bootstrap module:
#   - state CMK (KMS customer managed key, alias/444444444444-tfstate)
#   - access-log bucket (444444444444-tfstate-access-logs)
#   - artifact bucket / S3 state bucket (444444444444-tfstate)
#   - artifact_bucket_name output: published to PORTAL_ARTIFACT_BUCKET repo variable (AC-7)
#
# S3-NATIVE LOCKING (spec section 0.3 / D5, E8-F6-S1-T1):
# The DynamoDB lock table is removed. S3-native conditional-write locking via
# remote_state { use_lockfile = true } (set at the root in E8-F3-S1-T2) is the single
# locking mechanism (spec section 4.9, AC-14). Terraform/OpenTofu >= 1.10 is required
# (satisfied by the pinned 1.15.5 version). The lock_table_name input was removed from
# the references/state-bootstrap module in E8-F6-S1-T1; do NOT pass it here.
#
# OWN-STATE LOCAL-BACKEND OVERRIDE (D-15, D40, spec 02 Section 4.6, spec section 4.11):
# The generate "backend" block below is conditional on local.bootstrap_local_backend
# (default false via BOOTSTRAP_LOCAL_BACKEND env var). When true, it writes an empty
# backend.tf to override the root remote_state S3 config, forcing the initial apply
# to use a local backend. When false (default), it writes to backend_disabled.tf.disabled
# (a file Terraform ignores), leaving root remote_state backend.tf in place for
# normal S3 backend operation -- no operator edit required after migration (D-15).
#
# IN-REPO MODULE SOURCE (D-16, spec section 4.11):
# Bootstrap units always source the in-repo module (get_repo_root()) regardless of
# use_pinned_module_sources. The pin toggle governs service units only. This makes the
# bootstrap unit applyable pre-push before the module git tag exists in the remote.
#
# BOOTSTRAP APPLY SEQUENCE (spec 02 Section 4.6, docs/bootstrap-ordering.md):
#   Step 1 -- Operator creates GitHub OIDC provider in prod account (pre-req, D40)
#   Step 2 -- First apply (local backend, BOOTSTRAP_LOCAL_BACKEND=true):
#     BOOTSTRAP_LOCAL_BACKEND=true AWS_PROFILE=prod terragrunt apply
#     State stored locally in terraform.tfstate in the unit directory.
#   Step 3 -- State migration (once):
#     terraform init -migrate-state
#     Moves the local terraform.tfstate into the newly-created S3 backend.
#   Step 4 -- Apply oidc-bootstrap (depends on this state backend being present)
#   Step 5 -- Publish role ARNs and artifact_bucket_name to repo variables
#
# ACCOUNT GUARD (D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The prod account id is 444444444444. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# Applied once with root admin credentials.
# spec 02 Section 4.6, ledger D-15, D-16, D40, D5, E8-F6-S1-T1.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Bootstrap units always source the in-repo module regardless of use_pinned_module_sources
  # (D-16, spec section 4.11). The pin toggle governs service units only. Using get_repo_root()
  # unconditionally makes this unit applyable pre-push before the module git tag exists.
  source = "${get_repo_root()}//providers/aws/references/state-bootstrap"
}

# ---------------------------------------------------------------------------
# Own-state local-backend override (D-15, D40, spec 02 Section 4.6, spec 4.11)
#
# This generate block is conditional on local.bootstrap_local_backend (default false).
# When true:  path = "backend.tf" (overrides root remote_state -> local backend).
# When false: path = "backend_disabled.tf.disabled" (Terraform ignores .disabled files;
#             root remote_state generates backend.tf with S3 config normally).
#
# This design satisfies D-15 without requiring a Terragrunt version that supports
# the generate "if" attribute. The generate block always runs but harmlessly writes
# to a non-Terraform path when bootstrap_local_backend is false.
#
# Operator runbook (docs/bootstrap-ordering.md):
#   1. First apply: BOOTSTRAP_LOCAL_BACKEND=true terragrunt apply  (uses local backend)
#   2. Migrate state: terraform init -migrate-state  (moves state to S3)
#   3. Apply oidc-bootstrap (uses the now-present S3 backend normally)
# ---------------------------------------------------------------------------
generate "backend" {
  path      = local.bootstrap_local_backend ? "backend.tf" : "backend_disabled.tf.disabled"
  if_exists = "overwrite_terragrunt"
  contents  = local.bootstrap_local_backend ? "terraform {\n  backend \"local\" {}\n}\n" : ""
}

# ---------------------------------------------------------------------------
# locals: account identity derived from account.hcl basename (D2)
#
# The account id is sourced from account.hcl via find_in_parent_folders (D2).
# account.hcl declares: aws_account_id = basename(get_terragrunt_dir())
# This mirrors the sandbox leaf treatment -- no hardcoded account-id literal
# in the inputs block (E8-F6-S1-T1).
#
# D-15 (spec section 4.11): bootstrap_local_backend toggle (default false).
# Set BOOTSTRAP_LOCAL_BACKEND=true on the first apply (local backend). After
# terraform init -migrate-state, leave the env var unset so subsequent applies
# use the S3 backend normally with no operator edit needed (D-15).
# ---------------------------------------------------------------------------
locals {
  account_vars              = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  account_id                = local.account_vars.locals.aws_account_id
  use_pinned_module_sources = local.account_vars.locals.use_pinned_module_sources

  # D-15 (spec section 4.11): conditional local-backend override toggle.
  # Default false so subsequent applies (after terraform init -migrate-state) do not
  # override root remote_state with an empty backend.tf. Set via env var on first apply:
  #   BOOTSTRAP_LOCAL_BACKEND=true AWS_PROFILE=prod terragrunt apply
  bootstrap_local_backend = tobool(get_env("BOOTSTRAP_LOCAL_BACKEND", "false"))
}

# ---------------------------------------------------------------------------
# inputs: state-bootstrap reference module (spec section 4.9, AC-14)
#
# bucket_prefix: derived from the account id resolved via account.hcl.
# kms_alias: matches the state_kms_key_alias derived in root terragrunt.hcl (D10/B19).
#   The alias format is "${account_id}-tfstate" without the "alias/" prefix (variables.tf).
#
# DynamoDB lock-table input is absent (spec section 0.3 / D5, E8-F6-S1-T1):
#   The references/state-bootstrap module removed lock_table_name from variables.tf
#   (E8-F6-S1-T1, AC-FUNC-002). S3-native conditional-write locking (use_lockfile = true)
#   set at the root in E8-F3-S1-T2 is the single locking mechanism. Do NOT pass
#   lock_table_name here -- it is an undeclared variable in the current module version.
#
# artifact_bucket_name output: the state-bootstrap module exports this output
# which the operator publishes to PORTAL_ARTIFACT_BUCKET repo variable (AC-7).
# ---------------------------------------------------------------------------
inputs = {
  bucket_prefix = "${local.account_id}-tfstate"
  kms_alias     = "${local.account_id}-tfstate"

  # ---------------------------------------------------------------------------
  # Child module source overrides (AC-7, AC-8, spec Section 4.3, Section 5).
  # When use_pinned_module_sources=true (prod): every in-repo child source is set to
  # its pinned git URL so the composed module tree is fully pinned.
  # When use_pinned_module_sources=false (sandbox/QA/root): the in-repo relative path
  # defaults from variables.tf are passed explicitly. Passing null crashes Terraform
  # 1.15.5 when the receiving variable has const=true -- the null is not treated as
  # "use the const default" but as a literal null value for the module source.
  # ---------------------------------------------------------------------------
  state_kms_key_source     = local.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/kms-key?ref=providers/aws/primitives/kms-key/v1.0.1" : "../../primitives/kms-key"
  access_log_bucket_source = local.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/s3-bucket?ref=providers/aws/primitives/s3-bucket/v1.0.1" : "../../primitives/s3-bucket"
  artifact_bucket_source   = local.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/s3-bucket?ref=providers/aws/primitives/s3-bucket/v1.0.1" : "../../primitives/s3-bucket"

  tags = include.root.locals.common_tags
}
