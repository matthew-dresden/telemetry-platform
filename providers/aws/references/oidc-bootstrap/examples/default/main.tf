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
# oidc-bootstrap reference -- the module under test.
# Exercises a single OIDC-trusted role with an input-driven sub binding (D8).
# The github_oidc_provider_arn is supplied as an input variable so the test
# fixture can inject the real ARN (no provider created here, D40).
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
  description = "Map of role name to IAM role ARN from the oidc-bootstrap reference."
  value       = module.example.role_arns
}
