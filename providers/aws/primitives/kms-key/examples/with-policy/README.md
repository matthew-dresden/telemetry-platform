# kms-key -- with-policy example

This example demonstrates creating a multi-region KMS key where the key resource policy
is constructed from the caller-supplied `account_id`. The policy grants the account root
full KMS access and is built using Terraform locals so the example remains free of
hardcoded environment-specific values.

## Prerequisites

Set the required environment variable before running:

```bash
export TF_VAR_account_id="$(aws sts get-caller-identity --query Account --output text)"
```

## Usage

```hcl
module "kms_key" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key"

  alias_name   = "telemetry-with-policy-example"
  multi_region = true
  policy_json  = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      }
    ]
  })
}
```

## What this example creates

- One `aws_kms_key` with a custom resource policy and multi-region enabled.
- One `aws_kms_alias` targeting the key.

## Inputs

See `variables.tf` for all configurable inputs. The `account_id` variable is required and
must be supplied at runtime via the `TF_VAR_account_id` environment variable.

## Outputs

| Name | Description |
|------|-------------|
| `key_id` | The globally unique identifier for the key. |
| `key_arn` | The Amazon Resource Name (ARN) of the key. |
| `alias_name` | The display name of the alias, including the 'alias/' prefix. |
| `alias_arn` | The Amazon Resource Name (ARN) of the key alias. |
