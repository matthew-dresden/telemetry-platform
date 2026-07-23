locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Derive per-role trust policy documents from the roles input map.
  #
  # Two trust-policy modes:
  # 1. OIDC trust (default): requires github_oidc_provider_arn + role.sub set.
  #    Used by QA (terratest) and prod (tg-plan, tg-apply) OIDC roles.
  # 2. Role-chaining trust (D40): role.trust_policy_json is a pre-built JSON
  #    document for accounts where direct OIDC is not used (e.g. root dns-writer).
  #    When trust_policy_json is non-empty it takes precedence over OIDC generation.
  role_trust_policies = {
    for role_name, role_cfg in var.roles :
    role_name => (
      role_cfg.trust_policy_json != "" ? role_cfg.trust_policy_json :
      jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Sid    = "AllowGitHubActionsOIDC"
            Effect = "Allow"
            Principal = {
              Federated = var.github_oidc_provider_arn
            }
            Action = "sts:AssumeRoleWithWebIdentity"
            # aud is always an exact value (sts.amazonaws.com) -> StringEquals.
            # sub is a wildcard pattern (e.g. repo:org/repo:*) -> StringLike.
            # StringEquals treats "*" as a literal and would match nothing,
            # rejecting every sts:AssumeRoleWithWebIdentity call.
            Condition = {
              StringEquals = {
                "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
              }
              StringLike = {
                "token.actions.githubusercontent.com:sub" = role_cfg.sub
              }
            }
          }
        ]
      })
    )
  }
}
