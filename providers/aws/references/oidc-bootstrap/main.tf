# ---------------------------------------------------------------------------
# Fail-fast guard: OIDC-trust roles require a non-empty github_oidc_provider_arn.
#
# When github_oidc_provider_arn is optional (default ""), a role that sets
# sub (OIDC trust path) but omits the provider ARN would silently emit
# Principal.Federated="" -- an invalid trust policy that Terraform accepts at
# plan but AWS rejects at apply. This check block enforces the FR-10 fail-fast
# contract: any role using OIDC trust (sub != "", trust_policy_json == "")
# requires a non-empty provider ARN, named in the error message (D40, FR-10).
#
# Role-chaining roles (trust_policy_json != "") are exempt: they do not use
# the OIDC provider at all (e.g. root dns-writer, D-11).
# ---------------------------------------------------------------------------

locals {
  # Collect all OIDC-trust role names: sub is set but trust_policy_json is empty.
  _oidc_trust_role_names = [
    for name, cfg in var.roles :
    name
    if cfg.sub != "" && cfg.trust_policy_json == ""
  ]
}

check "oidc_trust_roles_require_provider_arn" {
  assert {
    condition     = length(local._oidc_trust_role_names) == 0 || var.github_oidc_provider_arn != ""
    error_message = "github_oidc_provider_arn must be set when any role uses OIDC trust (sub is set and trust_policy_json is empty). Roles requiring the provider ARN: ${join(", ", local._oidc_trust_role_names)}. Provide the ARN of the pre-existing GitHub OIDC identity provider in this account (arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com). The provider must be created as an operator prerequisite before applying this module (D40, FR-10)."
  }
}

# ---------------------------------------------------------------------------
# GitHub Actions OIDC assume-roles -- composed N times over the roles map.
# Each role is sourced from the iam-role primitive via a pinned git tag.
# No OIDC provider resource is created here; the provider is an operator
# prerequisite (D40). The trust sub binding is input-driven per role (D8).
# ---------------------------------------------------------------------------
module "oidc_role" {
  source = var.oidc_role_source

  for_each = var.roles

  name                    = each.key
  description             = each.value.description != "" ? each.value.description : "GitHub Actions OIDC assume-role for ${each.key}"
  assume_role_policy_json = local.role_trust_policies[each.key]
  managed_policy_arns     = each.value.managed_policy_arns
  inline_policies         = each.value.inline_policies
  max_session_duration    = each.value.max_session_duration

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}
