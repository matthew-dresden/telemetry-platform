# live/telemetry/us-east-1/bootstrap/sandbox_role/state-bootstrap/000/terragrunt.hcl
#
# Per-account state-bootstrap unit.
# Creates the remote-state foundation for the sandbox account via references/state-bootstrap:
#   - state CMK (KMS customer managed key, alias/<account_id>-tfstate)
#   - access-log bucket (<account_id>-tfstate-access-logs)
#   - artifact bucket / S3 state bucket (<account_id>-tfstate)
#
# COPYABLE DESIGN (spec section 4.9, AC-16):
# This unit contains no hardcoded account-id literal. The account id is derived from
# basename(get_terragrunt_dir()) of the account directory via account.hcl (D2/AC-16).
# Copying this unit under a new 12-digit account directory and adding a row to
# common/accounts.json produces a working new-account state foundation with zero edits
# to this file (spec section 4.9, AC-16).
#
# S3-NATIVE LOCKING (spec section 0.3 / D5, AC-14):
# The DynamoDB lock table is removed. S3-native conditional-write locking via
# remote_state { use_lockfile = true } (set at the root in E8-F3-S1-T2) is the single
# locking mechanism (spec section 4.9, AC-14). Terraform/OpenTofu >= 1.10 is required
# (satisfied by the pinned 1.15.5 version).
#
# OWN-STATE LOCAL-BACKEND OVERRIDE (D-15, D40, spec 02 Section 4.6, spec section 4.11):
# The generate "backend" block below is conditional on local.bootstrap_local_backend
# (default false via BOOTSTRAP_LOCAL_BACKEND env var). When true, it writes an empty
# backend.tf to override the root remote_state S3 config, forcing the initial apply
# to use a local backend. When false (default), it writes to backend_disabled.tf.disabled
# (a file Terraform ignores), leaving root remote_state backend.tf in place for
# normal S3 backend operation -- no operator edit required after migration (D-15).
#
# BOOTSTRAP APPLY SEQUENCE (spec 02 Section 4.6):
#   Step 1 -- First apply (local backend, BOOTSTRAP_LOCAL_BACKEND=true):
#     BOOTSTRAP_LOCAL_BACKEND=true AWS_PROFILE=sandbox terragrunt apply
#     This creates the CMK, access-log bucket, and artifact bucket.
#     State is stored locally in terraform.tfstate in the unit directory.
#
#   Step 2 -- State migration (once):
#     terraform init -migrate-state
#     Moves the local terraform.tfstate into the newly-created S3 backend.
#     After this step, subsequent applies use the hardened S3 backend normally.
#
#   Step 3 -- Subsequent applies (S3 backend, default BOOTSTRAP_LOCAL_BACKEND unset):
#     AWS_PROFILE=sandbox terragrunt apply
#     local.bootstrap_local_backend is false so the generate block writes to
#     backend_disabled.tf.disabled (ignored by Terraform). Root remote_state
#     generates backend.tf with S3 config. No backend.tf conflict, no manual
#     operator edit required (D-15, spec section 4.11).
#
# ACCOUNT GUARD (D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# A wrong-profile apply will fail fast because the active caller identity will not
# match the allowed account (D4).
#
# Applied once with AWS_PROFILE=sandbox admin credentials.
# spec 02 Section 4.6, ledger D-15, D40, D5, AC-14, AC-16.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/state-bootstrap?ref=providers/aws/references/state-bootstrap/v1.0.1" : "${get_repo_root()}//providers/aws/references/state-bootstrap"
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
# Operator runbook:
#   1. First apply: BOOTSTRAP_LOCAL_BACKEND=true terragrunt apply
#   2. Migrate state: terraform init -migrate-state  (moves state to S3)
#   3. Subsequent applies: omit BOOTSTRAP_LOCAL_BACKEND (defaults to false)
# ---------------------------------------------------------------------------
generate "backend" {
  path      = local.bootstrap_local_backend ? "backend.tf" : "backend_disabled.tf.disabled"
  if_exists = "overwrite_terragrunt"
  contents  = local.bootstrap_local_backend ? "terraform {\n  backend \"local\" {}\n}\n" : ""
}

# ---------------------------------------------------------------------------
# locals: account identity derived from account.hcl basename (D2, AC-16)
#
# The account id is sourced from account.hcl via find_in_parent_folders (D2).
# account.hcl declares: aws_account_id = basename(get_terragrunt_dir())
# This leaf carries no hardcoded account-id literal -- the id resolves purely
# from the directory path, so copying this unit under a new account directory
# produces a working deployment with zero edits (spec section 4.9, AC-16).
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
  #   BOOTSTRAP_LOCAL_BACKEND=true AWS_PROFILE=sandbox terragrunt apply
  bootstrap_local_backend = tobool(get_env("BOOTSTRAP_LOCAL_BACKEND", "false"))
}

# ---------------------------------------------------------------------------
# inputs: state-bootstrap reference module (spec section 4.9, AC-14, AC-16)
#
# bucket_prefix: derived from the basename account id (no literal).
# kms_alias: matches the state_kms_key_alias derived in root terragrunt.hcl (D10/B19).
#   The alias format is "${account_id}-tfstate" without the "alias/" prefix (variables.tf).
#
# DynamoDB lock-table inputs are absent (spec section 0.3 / D5, AC-14):
#   S3-native conditional-write locking (use_lockfile = true) set at the root in
#   E8-F3-S1-T2 is the single locking mechanism (spec section 4.9, AC-14).
#   No DynamoDB lock table is created by this module.
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
