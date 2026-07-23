resource "aws_kms_key" "telemetry_config" {
  description             = "Fixture CMK for telemetry-config SecureString parameters"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = {
    Name      = "telemetry-config-fixture"
    ManagedBy = "terraform"
    Module    = "ssm-parameter-securestring-example"
  }
}

resource "aws_kms_alias" "telemetry_config" {
  name          = "alias/telemetry-config-fixture"
  target_key_id = aws_kms_key.telemetry_config.key_id
}

module "example" {
  source = "../../"

  name        = var.name
  type        = var.type
  value       = var.value
  description = var.description
  tier        = var.tier
  kms_key_id  = aws_kms_key.telemetry_config.arn
  overwrite   = var.overwrite
  tags        = var.tags
}
