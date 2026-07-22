module "example" {
  source = "../../"

  name            = var.name
  type            = var.type
  value           = var.value
  description     = var.description
  tier            = var.tier
  kms_key_id      = var.kms_key_id
  overwrite       = var.overwrite
  allowed_pattern = var.allowed_pattern
  tags            = var.tags
}
