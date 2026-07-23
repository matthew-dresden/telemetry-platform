# s3-bucket -- data-lake example

This example demonstrates the data lake retention model: 1 year hot storage, then transition to the input-driven cold storage class (GLACIER), and expire at 2 years. The `force_destroy = true` setting allows Terraform to destroy the bucket during test cleanup.

## Usage

```hcl
module "s3_bucket" {
  source = "../../"

  bucket_name   = "my-data-lake-raw"
  force_destroy = true

  lifecycle_rules = [
    {
      id                       = "data-lake-retention"
      enabled                  = true
      prefix                   = ""
      transition_days          = 365
      transition_storage_class = "GLACIER"
      expiration_days          = 730
    }
  ]
}
```

## What this example creates

- One `aws_kms_key` (CMK) for S3 server-side encryption with a 7-day deletion window (self-contained -- no external KMS ARN required).
- One `aws_kms_alias` for the CMK.
- One `aws_s3_bucket` with force_destroy enabled.
- One `aws_s3_bucket_versioning` with versioning enabled.
- One `aws_s3_bucket_public_access_block` with all four settings set to true.
- One `aws_s3_bucket_server_side_encryption_configuration` using SSE-KMS with bucket_key_enabled.
- One `aws_s3_bucket_ownership_controls` with BucketOwnerEnforced.
- One `aws_s3_bucket_lifecycle_configuration` with the Glacier@365 + expire@730 retention rule.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `bucket_id` | The name of the bucket. |
| `bucket_arn` | The Amazon Resource Name (ARN) of the bucket. |
| `bucket_domain_name` | The bucket domain name. |
| `bucket_regional_domain_name` | The bucket region-specific domain name. |
