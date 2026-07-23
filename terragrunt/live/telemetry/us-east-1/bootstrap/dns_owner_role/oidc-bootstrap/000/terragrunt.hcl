# live/telemetry/us-east-1/bootstrap/dns_owner_role/oidc-bootstrap/000/terragrunt.hcl
#
# Root account (444444444444) oidc-bootstrap unit.
# Creates exactly the telemetry-platform-dns-writer IAM role via references/oidc-bootstrap.
#
# The dns-writer role is assumed via sts:AssumeRole (role-chaining) from the prod
# telemetry-platform-gha-tg-apply role. Root needs NO GitHub OIDC provider (D40):
# the dns-writer role is not directly trusted by GitHub; it is chained from the
# prod apply role. Therefore github_oidc_provider_arn is NOT passed and
# aws_iam_openid_connect_provider is NOT declared here.
#
# ROLES MAP AND CHAINING PRINCIPAL FROM common/ (E8-F6-S2-T1, spec section 4.9):
# The roles map is sourced from common/oidc-roles.json keyed by the derived root account id.
# The prod account id (the chaining principal for the dns-writer trust policy) is resolved
# from common/accounts.json -- keyed by account_role='prod-infra' -- rather than a literal.
# This leaf carries no account-id literal, so copying this unit under a new account
# directory and adding rows to common/ produces working roles with zero edits to this
# file (spec section 4.9, AC-16).
#
# OWN-STATE BOOTSTRAP (D-15, spec 4.12):
# The root account has no state-bootstrap unit. On the first apply, the S3 state backend
# bucket does not yet exist. This leaf uses the same local-backend conditional generate
# pattern as state-bootstrap (D-15): set BOOTSTRAP_LOCAL_BACKEND=true on the first apply,
# then run terraform init -migrate-state to move state into the auto-created S3 backend.
#
# Applied once with root admin/SSO credentials. Bootstrap-only thereafter.
# spec 02 Section 4.6, ledger D40, D48, D-15.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (root/dns-owner): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/oidc-bootstrap?ref=providers/aws/references/oidc-bootstrap/v1.0.1" : "${get_repo_root()}//providers/aws/references/oidc-bootstrap"
}

# ---------------------------------------------------------------------------
# Own-state local-backend override (D-15, D40, spec 4.12)
#
# This generate block is conditional on local.bootstrap_local_backend (default false).
# When true:  path = "backend.tf" (overrides root remote_state -> local backend).
# When false: path = "backend_disabled.tf.disabled" (Terraform ignores .disabled files;
#             root remote_state generates backend.tf with S3 config normally).
#
# First-apply sequence:
#   1. First apply: BOOTSTRAP_LOCAL_BACKEND=true terragrunt apply
#   2. Migrate state: terraform init -migrate-state
#   3. Subsequent applies: omit BOOTSTRAP_LOCAL_BACKEND (defaults to false)
# ---------------------------------------------------------------------------

generate "backend" {
  path      = local.bootstrap_local_backend ? "backend.tf" : "backend_disabled.tf.disabled"
  if_exists = "overwrite_terragrunt"
  contents  = local.bootstrap_local_backend ? "terraform {\n  backend \"local\" {}\n}\n" : ""
}

# ---------------------------------------------------------------------------
# locals: root account identity, roles map, and chaining principal from common/
# (D2/D4, E8-F6-S2-T1)
# ---------------------------------------------------------------------------

locals {
  # account_vars: sourced from account.hcl for toggle-driven source resolution (spec Section 4.3, AC-7).
  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))

  # common_tags sourced from root include for tagging all resources.
  common_tags = include.root.locals.common_tags

  # D-15: bootstrap_local_backend toggle (default false).
  # Set BOOTSTRAP_LOCAL_BACKEND=true only on the one-time first apply, then
  # leave unset for all subsequent applies (spec section 4.12, D-15).
  bootstrap_local_backend = tobool(get_env("BOOTSTRAP_LOCAL_BACKEND", "false"))

  # ---------------------------------------------------------------------------
  # Prod account id resolution from common/accounts.json (E8-F6-S2-T1, spec 4.9)
  #
  # The prod tg-apply role ARN is the chaining principal for the dns-writer trust
  # policy. The prod account id is resolved from common/accounts.json by locating
  # the entry with account_role='prod-infra', rather than hardcoding the literal.
  # This removes the last account-id literal from the oidc-bootstrap units and
  # makes the chaining principal copy-safe (spec section 4.8/4.9, AC-16).
  #
  # Fail-fast idiom: tobool("ERROR: ...") if no matching entry is found.
  # ---------------------------------------------------------------------------

  _accounts_all = jsondecode(file("${get_repo_root()}/terragrunt/common/accounts.json"))

  # Collect all account ids whose account_role is 'prod-infra'.
  _prod_infra_ids = [
    for acct_id, cfg in local._accounts_all :
    acct_id
    if lookup(cfg, "account_role", "") == "prod-infra"
  ]

  # Fail fast if no account with account_role='prod-infra' is found in accounts.json.
  prod_account_id = length(local._prod_infra_ids) > 0 ? local._prod_infra_ids[0] : tobool(
    "ERROR: no account with account_role='prod-infra' found in common/accounts.json -- add the prod-infra account row before deploying the root oidc-bootstrap unit (spec section 4.1, E8-F6-S2-T1)."
  )

  # Prod tg-apply role ARN: the chaining principal for the dns-writer trust policy.
  # Constructed from the common/-resolved prod account id and the conventional role name.
  prod_tg_apply_role = "arn:aws:iam::${local.prod_account_id}:role/telemetry-platform-gha-tg-apply"

  # Trust policy JSON: binds sts:AssumeRole to the prod tg-apply role ARN only.
  # No OIDC federation, no wildcard principals -- least-privilege (CLAUDE.md security).
  dns_writer_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowProdApplyRoleChaining"
        Effect = "Allow"
        Principal = {
          AWS = local.prod_tg_apply_role
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  # ---------------------------------------------------------------------------
  # Roles map resolution from common/oidc-roles.json (E8-F6-S2-T1, spec 4.9)
  #
  # The per-account roles map for the root account is loaded from common/oidc-roles.json
  # keyed by the root account id (derived from the directory basename via account.hcl).
  # A missing account row fails fast at Terragrunt parse time naming the account id
  # and file before any AWS call (spec section 4.1, S3.5).
  #
  # The root entry in common/oidc-roles.json contains one role: telemetry-platform-dns-writer.
  # The trust_policy_json field is NOT carried in common/oidc-roles.json because it
  # depends on the runtime-resolved prod_tg_apply_role ARN above. It is injected
  # as a top-level input override to the roles map entry here (AC-14, D40).
  #
  # Fail-fast idiom: lookup(map, key, null) -> tobool("ERROR: ...")
  # ---------------------------------------------------------------------------

  _root_account_id = include.root.locals.aws_account_id

  _oidc_roles_all = jsondecode(file("${get_repo_root()}/terragrunt/common/oidc-roles.json"))

  _account_roles_entry = lookup(local._oidc_roles_all, local._root_account_id, null)

  _base_roles = local._account_roles_entry != null ? local._account_roles_entry.roles : tobool(
    "ERROR: account id '${local._root_account_id}' not found in common/oidc-roles.json -- add a roles row for this account id before deploying oidc-bootstrap for this account (spec section 4.1, E8-F6-S2-T1)."
  )

  # Fail-fast guard: the dns-writer key must be present in the account's roles entry.
  # Using null as the default and a tobool ERROR guard ensures Terragrunt parse fails
  # immediately and loudly if the key is renamed or removed from common/oidc-roles.json
  # while the account row still exists -- rather than silently producing a role that
  # drops managed_policy_arns and max_session_duration (CLAUDE.md fail-fast, spec 4.1).
  _dns_writer_base = lookup(local._base_roles, "telemetry-platform-dns-writer", null)

  _dns_writer_base_validated = local._dns_writer_base != null ? local._dns_writer_base : tobool(
    "ERROR: role 'telemetry-platform-dns-writer' missing from common/oidc-roles.json for account '${local._root_account_id}' -- add the role row before deploying oidc-bootstrap for this account (spec section 4.1, E8-F6-S2-T1)."
  )

  # Plan role (multi-account CI, WALL 1): the dns-owner account hosts the prod _dns_owner
  # and _pretty units (apex asymmetry -> dns_pretty_apex served from the shared root zone).
  # A cross-env PR must plan those units under a dns-owner-account identity (the root provider
  # allowed_account_ids guard rejects the prod plan role for them). The dns-owner plan role is
  # an OIDC role (sub: repo:...:*) fully described by common/oidc-roles.json -- no leaf
  # override beyond what the base entry carries. It requires github_oidc_provider_arn, which
  # the dns-owner account HAS (the operator created it as a prerequisite, D40); the arn is
  # passed in the inputs block below. Unlike dns-writer (role-chaining), the plan role uses
  # direct GitHub OIDC.
  _plan_role_base = lookup(local._base_roles, "telemetry-platform-gha-tg-plan", null)

  _plan_role_validated = local._plan_role_base != null ? local._plan_role_base : tobool(
    "ERROR: role 'telemetry-platform-gha-tg-plan' missing from common/oidc-roles.json for account '${local._root_account_id}' -- add the plan role row before deploying oidc-bootstrap for this account (multi-account CI WALL 1)."
  )

  # Merge the runtime-resolved trust_policy_json into the dns-writer role entry.
  # The base entry from common/ omits trust_policy_json because it requires the
  # chaining principal ARN that is only available after the prod account id lookup.
  # The plan role is passed through from common/ unchanged (pure OIDC role).
  # Named _resolved_roles to avoid a bare 'roles = {' local that looks like an
  # inline definition; the actual roles input uses this via local._resolved_roles.
  _resolved_roles = {
    "telemetry-platform-dns-writer" = merge(
      local._dns_writer_base_validated,
      { trust_policy_json = local.dns_writer_trust_policy }
    )
    "telemetry-platform-gha-tg-plan" = local._plan_role_validated
  }
}

# ---------------------------------------------------------------------------
# inputs: root subset of the roles map from common/ (D40, E8-F6-S2-T1)
# ---------------------------------------------------------------------------
#
# The roles map is resolved from common/oidc-roles.json with the trust_policy_json
# injected at this layer (see locals above). This leaf carries no inline roles
# literal and no account-id literal.

inputs = {
  # roles map: dns-owner account subset containing telemetry-platform-dns-writer (role-chaining)
  # and telemetry-platform-gha-tg-plan (direct GitHub OIDC, multi-account CI WALL 1).
  roles = local._resolved_roles

  # github_oidc_provider_arn: REQUIRED now that the dns-owner account hosts an OIDC-trust role
  # (telemetry-platform-gha-tg-plan, sub set). The dns-owner account has a GitHub OIDC provider
  # (operator prerequisite, D40). The dns-writer role is unaffected: its trust_policy_json takes
  # precedence over OIDC generation (oidc-bootstrap locals.tf), so it stays role-chaining-only.
  # The arn is derived from the basename account id (copy-safe, spec section 4.9); the module's
  # check block requires a non-empty arn whenever any role uses OIDC trust (FR-10).
  github_oidc_provider_arn = "arn:aws:iam::${local._root_account_id}:oidc-provider/token.actions.githubusercontent.com"

  # ---------------------------------------------------------------------------
  # Child module source override (AC-7, AC-8, spec Section 4.3, Section 5).
  # When use_pinned_module_sources=true (prod): oidc_role_source is set to its
  # pinned git URL so the composed module tree is fully pinned.
  # When use_pinned_module_sources=false (root): explicit in-repo path is passed.
  # Passing null for a const=true variable causes a Terraform crash (panic: value is
  # null); the explicit in-repo path is functionally equivalent to the module default
  # and is always safe to pass (D-16, spec Section 4.3).
  # ---------------------------------------------------------------------------
  oidc_role_source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1" : "${get_repo_root()}//providers/aws/primitives/iam-role"

  tags = local.common_tags
}
