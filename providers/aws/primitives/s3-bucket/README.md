<!-- BEGIN_TF_DOCS -->
# s3-bucket

Manages a hardened S3 bucket with full block-public-access, SSE-KMS encryption with bucket key enabled, versioning, input-driven lifecycle rules for the data lake retention model, ownership controls, optional access logging, and an optional bucket policy.

Used as the common baseline for all S3 buckets in the telemetry-collector platform: raw data lake, curated lake, Athena results, SPA origin, access-log, and artifact buckets.

## Resources managed

- `aws_s3_bucket` -- the S3 bucket
- `aws_s3_bucket_versioning` -- versioning configuration
- `aws_s3_bucket_public_access_block` -- all four block-public-access settings enforced true
- `aws_s3_bucket_server_side_encryption_configuration` -- SSE-KMS with bucket_key_enabled
- `aws_s3_bucket_ownership_controls` -- object ownership setting
- `aws_s3_bucket_lifecycle_configuration` -- lifecycle rules (created only when lifecycle_rules is non-empty)
- `aws_s3_bucket_logging` -- access logging (created only when access_log_target_bucket is set)
- `aws_s3_bucket_policy` -- bucket policy (created only when bucket_policy_json is provided)

## Usage

```hcl
module "s3_bucket" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/s3-bucket?ref=providers/aws/primitives/s3-bucket/v0.1.0"

  bucket_name = "my-data-lake-raw"
  kms_key_arn = "arn:aws:kms:us-east-1:123456789012:key/my-key-id"

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

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- KMS-encrypted, versioned, full block-public-access, no lifecycle rules
- `examples/data-lake` -- Glacier@365 + expire@730 lifecycle with force_destroy enabled for test cleanup

## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 6.49.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_s3_bucket.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket) | resource |
| [aws_s3_bucket_lifecycle_configuration.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_lifecycle_configuration) | resource |
| [aws_s3_bucket_logging.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_logging) | resource |
| [aws_s3_bucket_ownership_controls.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_ownership_controls) | resource |
| [aws_s3_bucket_policy.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_policy) | resource |
| [aws_s3_bucket_public_access_block.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_public_access_block) | resource |
| [aws_s3_bucket_server_side_encryption_configuration.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_server_side_encryption_configuration) | resource |
| [aws_s3_bucket_versioning.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_versioning) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_access_log_target_bucket"></a> [access\_log\_target\_bucket](#input\_access\_log\_target\_bucket) | (Optional) Name of the S3 bucket to receive access logs. When null, access logging is disabled. | `string` | `null` | no |
| <a name="input_block_public_access"></a> [block\_public\_access](#input\_block\_public\_access) | (Optional) Block public access settings. All four must be true to satisfy D22 hardening and the trivy security gate. | <pre>object({<br/>    block_public_acls       = bool<br/>    block_public_policy     = bool<br/>    ignore_public_acls      = bool<br/>    restrict_public_buckets = bool<br/>  })</pre> | <pre>{<br/>  "block_public_acls": true,<br/>  "block_public_policy": true,<br/>  "ignore_public_acls": true,<br/>  "restrict_public_buckets": true<br/>}</pre> | no |
| <a name="input_bucket_key_enabled"></a> [bucket\_key\_enabled](#input\_bucket\_key\_enabled) | (Optional) Whether to use an S3 bucket key to reduce KMS request costs. | `bool` | `true` | no |
| <a name="input_bucket_name"></a> [bucket\_name](#input\_bucket\_name) | (Required) The name of the S3 bucket. Must be globally unique and follow S3 naming conventions. | `string` | n/a | yes |
| <a name="input_bucket_policy_json"></a> [bucket\_policy\_json](#input\_bucket\_policy\_json) | (Optional) A valid JSON bucket resource policy. When null, no bucket policy is attached. | `string` | `null` | no |
| <a name="input_force_destroy"></a> [force\_destroy](#input\_force\_destroy) | (Optional) Whether to allow Terraform to destroy the bucket even if it contains objects. Set true only for test environments. | `bool` | `false` | no |
| <a name="input_kms_key_arn"></a> [kms\_key\_arn](#input\_kms\_key\_arn) | (Required) ARN of the KMS CMK used for server-side encryption. Must match ^arn:aws:kms:. | `string` | n/a | yes |
| <a name="input_lifecycle_rules"></a> [lifecycle\_rules](#input\_lifecycle\_rules) | (Optional) List of lifecycle rules. Each rule may specify a transition (with an input-driven cold storage class from GLACIER, GLACIER\_IR, or DEEP\_ARCHIVE) and an expiration. Expiration must be greater than transition when both are specified. | <pre>list(object({<br/>    id                       = string<br/>    enabled                  = bool<br/>    prefix                   = optional(string, "")<br/>    transition_days          = optional(number, null)<br/>    transition_storage_class = optional(string, "GLACIER")<br/>    expiration_days          = optional(number, null)<br/>  }))</pre> | `[]` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"s3-bucket"` | no |
| <a name="input_object_ownership"></a> [object\_ownership](#input\_object\_ownership) | (Optional) Object ownership setting. Valid values: BucketOwnerEnforced, BucketOwnerPreferred, ObjectWriter. | `string` | `"BucketOwnerEnforced"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_versioning_enabled"></a> [versioning\_enabled](#input\_versioning\_enabled) | (Optional) Whether to enable S3 versioning on the bucket. | `bool` | `true` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_bucket_arn"></a> [bucket\_arn](#output\_bucket\_arn) | The Amazon Resource Name (ARN) of the bucket. |
| <a name="output_bucket_domain_name"></a> [bucket\_domain\_name](#output\_bucket\_domain\_name) | The bucket domain name in the format <bucket>.s3.amazonaws.com. |
| <a name="output_bucket_id"></a> [bucket\_id](#output\_bucket\_id) | The name of the bucket (same as bucket\_name input). |
| <a name="output_bucket_regional_domain_name"></a> [bucket\_regional\_domain\_name](#output\_bucket\_regional\_domain\_name) | The bucket region-specific domain name in the format <bucket>.s3.<region>.amazonaws.com. |

## Input validation

- `bucket_name` must match `^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$`. Plans fail for invalid names.
- `kms_key_arn` must match `^arn:aws:kms:`. Plans fail for non-KMS ARNs.
- `block_public_access` -- all four settings must be true. Plans fail for partial configuration (D22 hardening).
- `object_ownership` must be one of `BucketOwnerEnforced`, `BucketOwnerPreferred`, or `ObjectWriter`.
- `lifecycle_rules[*].transition_storage_class` must be one of `GLACIER`, `GLACIER_IR`, or `DEEP_ARCHIVE`. Plans fail for other values.
- `lifecycle_rules[*].expiration_days` must be a positive integer when specified.
- `lifecycle_rules[*].expiration_days` must be greater than `transition_days` when both are specified.
- `bucket_policy_json`, when provided, must be valid JSON.
<!-- END_TF_DOCS -->