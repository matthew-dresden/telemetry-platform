# iam-role -- with-inline example

This example demonstrates creating an IAM role with a Firehose trust policy and two inline policies.

## Usage

```hcl
module "iam_role" {
  source = "../../"

  name = "telemetry-firehose-delivery"
  assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "firehose.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  inline_policies = {
    s3-write   = jsonencode({ ... })
    kms-encrypt = jsonencode({ ... })
  }
}
```

## What this example creates

- One `aws_iam_role` with a Firehose trust policy.
- Two `aws_iam_role_policy` inline policy attachments: one for S3 writes and one for KMS encryption.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `role_arn` | The Amazon Resource Name (ARN) of the IAM role. |
| `role_name` | The name of the IAM role. |
| `role_id` | The stable unique identifier for the IAM role. |
