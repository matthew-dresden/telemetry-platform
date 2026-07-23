# live/telemetry/us-east-1/bootstrap/qa_role/oidc-bootstrap/000/terragrunt.hcl
#
# QA account (333333333333) oidc-bootstrap unit.
# Creates exactly the telemetry-platform-gha-terratest IAM role via references/oidc-bootstrap.
#
# OPERATOR PREREQUISITE (D40):
# The GitHub OIDC provider must pre-exist in the QA account before applying this unit.
# This unit CONSUMES the provider ARN via github_oidc_provider_arn; it does NOT create
# the provider (aws_iam_openid_connect_provider is not declared here). A missing or
# invalid ARN causes the oidc-bootstrap module variables.tf validation to fail fast
# with a clear error -- this is intentional (AC-14, D40).
#
# ACCOUNT GUARD (D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The QA account id is 333333333333. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# ROLES MAP FROM common/ (E8-F6-S2-T1, spec section 4.9, AC-16):
# The roles map is sourced from common/oidc-roles.json keyed by the derived account id.
# This leaf carries no inline roles literal, so copying this unit under a new account
# directory and adding a row to common/oidc-roles.json produces working roles with
# zero edits to this file (spec section 4.9, AC-16).
#
# OWN-STATE BOOTSTRAP (D-15, spec 4.12):
# The QA account has no state-bootstrap unit (ledger D30). On the first apply, the
# S3 state backend bucket does not yet exist, so this leaf uses the same local-backend
# conditional generate pattern as state-bootstrap (D-15): set BOOTSTRAP_LOCAL_BACKEND=true
# on the first apply to use a local backend, then run terraform init -migrate-state to
# move state into the auto-created S3 backend, then leave unset for subsequent applies.
# Terragrunt --backend-bootstrap can also create the bucket automatically when the env
# var is not set and the bucket is absent, but the D-15 local approach is preferred for
# reproducibility (no interactive prompt, copy-safe).
#
# Applied once with QA admin credentials.
# spec 02 Section 4.6, ledger D40, AC-14, D-15.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (QA): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/oidc-bootstrap?ref=providers/aws/references/oidc-bootstrap/v1.0.1" : "${get_repo_root()}//providers/aws/references/oidc-bootstrap"
}

# ---------------------------------------------------------------------------
# Own-state local-backend override (D-15, D40, spec 4.12)
#
# generate "backend": when BOOTSTRAP_LOCAL_BACKEND=true, writes backend.tf
# with a local backend config; when false (default), writes to a .disabled
# path that Terraform ignores, so the remote_state.generate from root.hcl
# writes backend.tf normally.
#
# This single generate block matches the proven state-bootstrap leaf pattern
# (D-15). After the one-time first apply with BOOTSTRAP_LOCAL_BACKEND=true,
# run terraform init -migrate-state to move state into the S3 backend, then
# leave BOOTSTRAP_LOCAL_BACKEND unset for all subsequent applies.
#
# First-apply sequence:
#   1. First apply:   BOOTSTRAP_LOCAL_BACKEND=true terragrunt apply
#   2. Migrate state: terraform init -migrate-state
#   3. Subsequent:    unset BOOTSTRAP_LOCAL_BACKEND (defaults to false)
# ---------------------------------------------------------------------------

generate "backend" {
  path      = local.bootstrap_local_backend ? "backend.tf" : "backend_disabled.tf.disabled"
  if_exists = "overwrite_terragrunt"
  contents  = local.bootstrap_local_backend ? "terraform {\n  backend \"local\" {}\n}\n" : ""
}

# ---------------------------------------------------------------------------
# locals: QA account identity and roles map from common/ (D2/D4, E8-F6-S2-T1)
# ---------------------------------------------------------------------------

locals {
  # QA account id sourced from account.hcl via the D2 basename idiom.
  account_vars  = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  qa_account_id = local.account_vars.locals.aws_account_id

  # common_tags sourced from root include for tagging all resources.
  common_tags = include.root.locals.common_tags

  # D-15: bootstrap_local_backend toggle (default false).
  # Set BOOTSTRAP_LOCAL_BACKEND=true only on the one-time first apply, then
  # leave unset for all subsequent applies (spec section 4.12, D-15).
  bootstrap_local_backend = tobool(get_env("BOOTSTRAP_LOCAL_BACKEND", "false"))

  # ---------------------------------------------------------------------------
  # Roles map resolution from common/oidc-roles.json (E8-F6-S2-T1, spec 4.9)
  #
  # The per-account roles map is loaded from common/ (outside the copy boundary,
  # spec D3) keyed by the derived QA account id. A missing account row fails fast
  # at Terragrunt parse time naming the account id and file before any AWS call
  # (spec section 4.1, S3.5).
  #
  # The QA entry in common/oidc-roles.json contains exactly one role:
  #   telemetry-platform-gha-terratest
  # Any additional role in the common/ entry would over-provision the QA account (AC-14).
  #
  # Fail-fast idiom: lookup(map, key, null) -> tobool("ERROR: ...")
  # ---------------------------------------------------------------------------

  _oidc_roles_all = jsondecode(file("${get_repo_root()}/terragrunt/common/oidc-roles.json"))

  _account_roles_entry = lookup(local._oidc_roles_all, local.qa_account_id, null)

  roles = local._account_roles_entry != null ? local._account_roles_entry.roles : tobool(
    "ERROR: account id '${local.qa_account_id}' not found in common/oidc-roles.json -- add a roles row for this account id before deploying oidc-bootstrap for this account (spec section 4.1, E8-F6-S2-T1)."
  )
}

# ---------------------------------------------------------------------------
# inputs: QA subset of the roles map from common/ (AC-14, D40, E8-F6-S2-T1)
# ---------------------------------------------------------------------------
#
# The roles map is resolved entirely from common/oidc-roles.json (see locals above).
# This leaf carries no inline roles literal -- only the common/ resolver.
#
# github_oidc_provider_arn: the pre-existing operator-created OIDC provider ARN
# in the QA account (D40). This input is REQUIRED -- passing null or omitting it
# causes the oidc-bootstrap module validation to fail fast with a clear error.
# The provider is NOT created by this unit (aws_iam_openid_connect_provider absent).

inputs = {
  # OPERATOR: the GitHub OIDC provider ARN for the QA account (333333333333).
  # This provider must be created as an operator prerequisite before applying this unit (D40).
  # ARN format: arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com
  # Derived from the basename account id (copy-safe, spec section 4.9).
  github_oidc_provider_arn = "arn:aws:iam::${local.qa_account_id}:oidc-provider/token.actions.githubusercontent.com"

  # Roles map: resolved from common/oidc-roles.json keyed by the derived QA account id.
  # No inline roles literal -- the common/ entry for 333333333333 contains exactly
  # telemetry-platform-gha-terratest (AC-14, D40, E8-F6-S2-T1).
  roles = local.roles

  # ---------------------------------------------------------------------------
  # Child module source override (AC-7, AC-8, spec Section 4.3, Section 5).
  # When use_pinned_module_sources=true (prod): oidc_role_source is set to its
  # pinned git URL so the composed module tree is fully pinned.
  # When use_pinned_module_sources=false (QA): explicit in-repo path is passed.
  # Passing null for a const=true variable causes a Terraform crash (panic: value is
  # null); the explicit in-repo path is functionally equivalent to the module default
  # and is always safe to pass (D-16, spec Section 4.3).
  # ---------------------------------------------------------------------------
  oidc_role_source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1" : "${get_repo_root()}//providers/aws/primitives/iam-role"

  tags = local.common_tags
}
