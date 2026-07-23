resource "aws_ssm_parameter" "this" {
  name            = var.name
  type            = var.type
  value           = var.value
  description     = var.description
  tier            = var.tier
  key_id          = var.kms_key_id
  overwrite       = var.overwrite
  allowed_pattern = var.allowed_pattern

  tags = local.common_tags
}
