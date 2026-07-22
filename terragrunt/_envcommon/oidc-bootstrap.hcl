# terragrunt/_envcommon/oidc-bootstrap.hcl
#
# Shared input template for the prod oidc-bootstrap service unit.
# Included by the prod account oidc-bootstrap leaf via:
#   include "envcommon" {
#     path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/oidc-bootstrap.hcl"
#     expose         = true
#     merge_strategy = "deep"
#   }
# (iac/02 section 1.1 line 139)
#
# Ledger decisions applied here:
#   D16 - bootstrap units in EVERY account use in-repo local sources regardless of the
#         prod account.hcl use_pinned_module_sources toggle (which is honored only by
#         service units). This file exposes bootstrap_use_pinned_module_sources = false
#         so the prod bootstrap leaf can reference include.envcommon.locals.bootstrap_use_pinned_module_sources
#         in its terraform { source } toggle, forcing in-repo even when the prod account
#         flag is true.
#   D37 - bootstrap inputs: github_oidc_provider_arn (canonical name) + roles map (D40)
#   D40 - per-account roles subset resolved from common/oidc-roles.json keyed by account id
#         (E8-F6-S2-T1: roles moved out of inline literals to common/ for copy-safety)
#   D45 - account id sourced from account.hcl basename (not hardcoded)

locals {
  # account.hcl carries aws_account_id for this account's identity values (D45).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # D16: bootstrap applies always use in-repo local module sources, never pinned git
  # URLs. The prod account.hcl sets use_pinned_module_sources=true for service units,
  # but bootstrap units must be applyable pre-push (before the branch is published and
  # release tags exist). Exposing bootstrap_use_pinned_module_sources=false here lets
  # the prod bootstrap leaf override the account-level toggle:
  #   source = include.envcommon.locals.bootstrap_use_pinned_module_sources ? "git::..." : "${get_repo_root()}//..."
  # Since this value is always false, the prod bootstrap leaf unconditionally resolves
  # to the in-repo local path (spec section 4.12, D16).
  bootstrap_use_pinned_module_sources = false

  # ---------------------------------------------------------------------------
  # Roles map resolution from common/oidc-roles.json (E8-F6-S2-T1, spec 4.9)
  #
  # The per-account roles map is loaded from common/ (outside the copy boundary,
  # spec D3) keyed by the derived account id. A missing account row fails fast at
  # Terragrunt parse time with the account id and file named in the error message,
  # before any AWS call (spec section 4.1, S3.5).
  #
  # Fail-fast idiom: lookup(map, key, null) -> tobool("ERROR: ...")
  # ---------------------------------------------------------------------------

  _oidc_roles_all = jsondecode(file("${get_repo_root()}/terragrunt/common/oidc-roles.json"))

  _account_roles_entry = lookup(local._oidc_roles_all, local.aws_account_id, null)

  roles = local._account_roles_entry != null ? local._account_roles_entry.roles : tobool(
    "ERROR: account id '${local.aws_account_id}' not found in common/oidc-roles.json -- add a roles row for this account id before deploying oidc-bootstrap for this account (spec section 4.1, E8-F6-S2-T1)."
  )
}

inputs = {
  # D37/D40: github_oidc_provider_arn is the canonical input name. The OIDC provider
  # is a ONE-TIME OPERATOR PREREQUISITE (NOT created by oidc-bootstrap; D14/D40).
  # Its ARN follows the pattern: arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com
  # The ARN is derived from the basename account id (copy-safe, spec section 4.9).
  github_oidc_provider_arn = "arn:aws:iam::${local.aws_account_id}:oidc-provider/token.actions.githubusercontent.com"

  # D40: per-account roles map resolved from common/oidc-roles.json.
  # Each account's entry in common/oidc-roles.json contains the roles subset
  # to create for that account (E8-F6-S2-T1, spec section 4.9, AC-16).
  roles = local.roles
}
