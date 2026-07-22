# kms-key -- basic example

This example demonstrates creating a KMS key with key rotation enabled and the default KMS key policy.

## Usage

```hcl
module "kms_key" {
  source = "../../"

  alias_name          = "telemetry-basic-example"
  enable_key_rotation = true
}
```

## What this example creates

- One `aws_kms_key` with automatic annual rotation enabled and the default KMS key policy.
- One `aws_kms_alias` targeting the key.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `key_id` | The globally unique identifier for the key. |
| `key_arn` | The Amazon Resource Name (ARN) of the key. |
| `alias_name` | The display name of the alias, including the 'alias/' prefix. |
| `alias_arn` | The Amazon Resource Name (ARN) of the key alias. |
