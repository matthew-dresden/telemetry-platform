# ssm-parameter -- securestring example

This example demonstrates creating a SecureString SSM parameter at the docs/terragrunt-concepts.md path `/telemetry/prod/ingest/adot-config`, encrypted with a fixture `telemetry-config` CMK. The parameter carries the ADOT collector configuration injected as `AOT_CONFIG_CONTENT`.

## Usage

```hcl
resource "aws_kms_key" "telemetry_config" {
  description             = "Fixture CMK for telemetry-config SecureString parameters"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

module "example" {
  source = "../../"

  name       = "/telemetry/prod/ingest/adot-config"
  type       = "SecureString"
  value      = var.adot_config_content
  kms_key_id = aws_kms_key.telemetry_config.arn
}
```

## What this example creates

- One fixture `aws_kms_key` aliased `alias/telemetry-config-fixture`.
- One `aws_ssm_parameter` of type `SecureString` at `/telemetry/prod/ingest/adot-config`, encrypted with the fixture CMK.

## Security note

The `value` input is marked `sensitive = true`. The module exposes only metadata outputs (`parameter_arn`, `parameter_name`, `parameter_version`). The secret value is never echoed in state outputs.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `parameter_arn` | The Amazon Resource Name (ARN) of the SSM parameter. |
| `parameter_name` | The fully qualified name of the SSM parameter. |
| `parameter_version` | The version of the SSM parameter. |
| `kms_key_arn` | The ARN of the fixture KMS CMK. |
