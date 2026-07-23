module "example" {
  source = "../../"

  alias_name               = var.alias_name
  description              = var.description
  deletion_window_in_days  = var.deletion_window_in_days
  enable_key_rotation      = var.enable_key_rotation
  key_usage                = var.key_usage
  customer_master_key_spec = var.customer_master_key_spec
  multi_region             = var.multi_region
  policy_json              = var.policy_json
  tags                     = var.tags
}
