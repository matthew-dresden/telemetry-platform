# s3-bucket -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Providers

| Name | Version |
|------|---------|
| aws | ~> 6.0.0 |

## Resources

| Name | Type |
|------|------|
| aws_s3_bucket.this | resource |
| aws_s3_bucket_versioning.this | resource |
| aws_s3_bucket_public_access_block.this | resource |
| aws_s3_bucket_server_side_encryption_configuration.this | resource |
| aws_s3_bucket_ownership_controls.this | resource |
| aws_s3_bucket_lifecycle_configuration.this | resource |
| aws_s3_bucket_logging.this | resource |
| aws_s3_bucket_policy.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| bucket_name | The name of the S3 bucket. Must be globally unique and follow S3 naming conventions. | string | n/a | yes |
| kms_key_arn | ARN of the KMS CMK used for server-side encryption. Must match ^arn:aws:kms:. | string | n/a | yes |
| force_destroy | Whether to allow Terraform to destroy the bucket even if it contains objects. Set true only for test environments. | bool | false | no |
| versioning_enabled | Whether to enable S3 versioning on the bucket. | bool | true | no |
| bucket_key_enabled | Whether to use an S3 bucket key to reduce KMS request costs. | bool | true | no |
| block_public_access | Block public access settings. All four must be true to satisfy D22 hardening and the trivy security gate. | object | all true | no |
| object_ownership | Object ownership setting. Valid values: BucketOwnerEnforced, BucketOwnerPreferred, ObjectWriter. | string | "BucketOwnerEnforced" | no |
| lifecycle_rules | List of lifecycle rules. Each rule may specify a transition with an input-driven cold storage class from GLACIER, GLACIER_IR, or DEEP_ARCHIVE and an expiration. Expiration must be greater than transition when both are specified. | list(object) | [] | no |
| access_log_target_bucket | Name of the S3 bucket to receive access logs. When null, access logging is disabled. | string | null | no |
| bucket_policy_json | A valid JSON bucket resource policy. When null, no bucket policy is attached. | string | null | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "s3-bucket" | no |

## Outputs

| Name | Description |
|------|-------------|
| bucket_id | The name of the bucket (same as bucket_name input). |
| bucket_arn | The Amazon Resource Name (ARN) of the bucket. |
| bucket_domain_name | The bucket domain name in the format <bucket>.s3.amazonaws.com. |
| bucket_regional_domain_name | The bucket region-specific domain name in the format <bucket>.s3.<region>.amazonaws.com. |
