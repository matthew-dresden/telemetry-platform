# s3-bucket -- basic example

This example demonstrates creating a hardened S3 bucket with KMS encryption, versioning enabled, full block-public-access, and no lifecycle rules.

## Usage

```hcl
module "s3_bucket" {
  source = "../../"

  bucket_name = "my-bucket-name"
}
```

## What this example creates

- One `aws_kms_key` (CMK) for S3 server-side encryption with a 7-day deletion window (self-contained -- no external KMS ARN required).
- One `aws_kms_alias` for the CMK.
- One `aws_s3_bucket` with the specified name.
- One `aws_s3_bucket_versioning` with versioning enabled.
- One `aws_s3_bucket_public_access_block` with all four settings set to true.
- One `aws_s3_bucket_server_side_encryption_configuration` using SSE-KMS with bucket_key_enabled.
- One `aws_s3_bucket_ownership_controls` with BucketOwnerEnforced.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `bucket_id` | The name of the bucket. |
| `bucket_arn` | The Amazon Resource Name (ARN) of the bucket. |
| `bucket_domain_name` | The bucket domain name. |
| `bucket_regional_domain_name` | The bucket region-specific domain name. |
