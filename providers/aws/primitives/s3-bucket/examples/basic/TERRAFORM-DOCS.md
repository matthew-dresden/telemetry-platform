# s3-bucket basic example -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.15.5 |
| aws | >= 6.49.0 |

## Providers

| Name | Version |
|------|---------|
| aws | >= 6.49.0 |

## Resources

| Name | Type |
|------|------|
| aws_kms_key.s3_encryption | resource |
| aws_kms_alias.s3_encryption | resource |
| aws_caller_identity.current | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| bucket_name | The name of the S3 bucket. Overridden at test time via TF_VAR_bucket_name. | string | n/a | yes |
| versioning_enabled | Whether to enable S3 versioning. | bool | true | no |
| bucket_key_enabled | Whether to use an S3 bucket key to reduce KMS request costs. | bool | true | no |
| lifecycle_rules | List of lifecycle rules. | list(object) | [] | no |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| bucket_id | The name of the bucket. |
| bucket_arn | The Amazon Resource Name (ARN) of the bucket. |
| bucket_domain_name | The bucket domain name. |
| bucket_regional_domain_name | The bucket region-specific domain name. |
