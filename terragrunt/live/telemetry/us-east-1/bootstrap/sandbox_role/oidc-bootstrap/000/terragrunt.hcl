# live/telemetry/us-east-1/bootstrap/sandbox_role/oidc-bootstrap/000/terragrunt.hcl
#
# Sandbox account (222222222222) oidc-bootstrap unit.
# Creates telemetry-platform-gha-tg-plan (read-only plan) and telemetry-platform-gha-tg-apply
# (on-demand ephemeral apply/destroy) via references/oidc-bootstrap, using the shared
# envcommon template for the roles map and the github_oidc_provider_arn input
# (iac/02 section 1.1 line 139, AC-2). Mirrors prod_role/oidc-bootstrap in structure; BOTH
# sandbox roles are fully described by common/oidc-roles.json (including the apply role's
# AdministratorAccess + teardown-sweep inline policy), so this leaf carries NO role override
# (unlike prod, whose leaf overrides its apply role to add the AdministratorAccess + dns-writer
# assume-role inline policy).
#
# WHY (multi-account CI, WALL 1 + on-demand sandbox lifecycle):
# The change-scoped terragrunt-pr plan runs one job per account so a cross-env PR plans each
# env's units under that env's own OIDC role (the root provider's allowed_account_ids guard,
# D4, rejects the prod plan role for sandbox units). This unit creates the sandbox plan role
# that the sandbox plan job assumes. Sandbox stays ci_deploy=false (D33) -- the normal push lane
# never auto-applies it -- but it now also declares ci_deploy_on_demand=true in accounts.json, so
# a workflow_dispatch run that sets TT_ON_DEMAND_APPLY can stand the sandbox stack up and tear it
# back down under the sandbox apply role (trust gated to the sandbox-apply GitHub environment).
#
# OPERATOR PREREQUISITE (D40):
# The GitHub OIDC provider must pre-exist in the sandbox account (222222222222) before
# applying this unit. It is created OUT-OF-BAND by the operator (NOT by this unit; D40). This
# unit CONSUMES the provider ARN via github_oidc_provider_arn (inherited from
# _envcommon/oidc-bootstrap.hcl); it does NOT create the provider.
#
# ACCOUNT GUARD (D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2). The
# sandbox account id is 222222222222. A wrong-profile apply will fail fast.
#
# ROLES MAP FROM common/ via _envcommon (E8-F6-S2-T1, spec section 4.9, AC-16):
# The roles map is inherited from _envcommon/oidc-bootstrap.hcl, which sources it from
# common/oidc-roles.json keyed by the derived account id (222222222222). The sandbox entry
# contains telemetry-platform-gha-tg-plan (sub: repo:...:*) and telemetry-platform-gha-tg-apply
# (sub: repo:...:environment:sandbox-apply). Both are fully described in common/oidc-roles.json,
# so no leaf override is needed.
#
# Applied once with sandbox admin credentials (AWS_PROFILE=sandbox).
# spec 02 Section 4.6, ledger D40, D48.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

# ---------------------------------------------------------------------------
# envcommon include (iac/02 section 1.1 line 139, AC-2)
#
# The _envcommon/oidc-bootstrap.hcl template provides:
#   - github_oidc_provider_arn: ARN of the operator-created OIDC provider derived from the
#     basename account id (copy-safe, spec section 4.9)
#   - roles: per-account roles map loaded from common/oidc-roles.json keyed by the derived
#     account id (E8-F6-S2-T1, spec section 4.9)
# ---------------------------------------------------------------------------
include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/oidc-bootstrap.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # D16: bootstrap units always use the in-repo local source, regardless of the account.hcl
  # use_pinned_module_sources toggle (the toggle governs service units only). Bootstrap must
  # be applyable pre-push. include.envcommon.locals.bootstrap_use_pinned_module_sources is
  # always false, which unconditionally resolves to the in-repo path (spec section 4.12, D16).
  source = include.envcommon.locals.bootstrap_use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/oidc-bootstrap?ref=providers/aws/references/oidc-bootstrap/v1.0.1" : "${get_repo_root()}//providers/aws/references/oidc-bootstrap"
}

# ---------------------------------------------------------------------------
# inputs: sandbox leaf inputs merged with the envcommon template (AC-1, AC-2, D40)
#
# The envcommon template provides github_oidc_provider_arn and roles (from
# common/oidc-roles.json keyed by the sandbox account id 222222222222). The sandbox entry
# contains the read-only plan role and the on-demand apply/destroy role, both fully described
# in common/oidc-roles.json -- no leaf override.
# ---------------------------------------------------------------------------
inputs = {
  # ---------------------------------------------------------------------------
  # Child module source override (AC-7, AC-8, spec Section 4.3, Section 5).
  # D16: bootstrap applies always use in-repo local sources (not pinned git URLs).
  # include.envcommon.locals.bootstrap_use_pinned_module_sources is always false, so
  # oidc_role_source always resolves to the in-repo path for bootstrap. Passing null for a
  # const=true variable causes a Terraform crash; the explicit in-repo path is always safe.
  # ---------------------------------------------------------------------------
  oidc_role_source = include.envcommon.locals.bootstrap_use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v1.0.1" : "${get_repo_root()}//providers/aws/primitives/iam-role"

  tags = include.root.locals.common_tags
}
