data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Construct the OIDC provider ARN from the current account so the fixture
  # works without hard-coding an account ID. The provider itself is an operator
  # prerequisite (D40) and is NOT created here.
  effective_oidc_provider_arn = var.github_oidc_provider_arn != "" ? var.github_oidc_provider_arn : "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })
}

# ---------------------------------------------------------------------------
# oidc-bootstrap reference -- prod-subset example.
# Exercises the two prod roles (telemetry-platform-gha-tg-plan for PR plan trust
# and telemetry-platform-gha-tg-apply bound to environment:prod-apply).
# ---------------------------------------------------------------------------
module "example" {
  source = "../../"

  github_oidc_provider_arn = local.effective_oidc_provider_arn

  roles = var.roles

  tags           = local.tags
  managed_by_tag = var.managed_by_tag
  module_tag     = var.module_tag
}

output "role_arns" {
  description = "Map of role name to IAM role ARN for the two prod OIDC roles."
  value       = module.example.role_arns
}
