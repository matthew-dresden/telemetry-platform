module "example" {
  source = "../../"

  name                    = var.name
  assume_role_policy_json = var.assume_role_policy_json
  description             = var.description
  path                    = var.path
  max_session_duration    = var.max_session_duration
  permissions_boundary    = var.permissions_boundary
  managed_policy_arns     = var.managed_policy_arns
  inline_policies         = var.inline_policies
  tags                    = var.tags
}
