# kms-key with-policy example -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| alias_name | Alias suffix for the KMS key. | string | n/a | yes |
| account_id | AWS account ID used to construct the KMS key resource policy principal. | string | n/a | yes |
| description | Description of the KMS key. | string | "Customer managed KMS key - with-policy example" | no |
| deletion_window_in_days | Waiting period in days before key deletion. | number | 7 | no |
| enable_key_rotation | Whether to enable annual automatic key rotation. | bool | true | no |
| key_usage | Intended use of the key. | string | "ENCRYPT_DECRYPT" | no |
| customer_master_key_spec | Specifies whether the key contains a symmetric key or an asymmetric key pair. | string | "SYMMETRIC_DEFAULT" | no |
| multi_region | Whether the key is a multi-region key. | bool | true | no |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| key_id | The globally unique identifier for the key. |
| key_arn | The Amazon Resource Name (ARN) of the key. |
| alias_name | The display name of the alias, including the 'alias/' prefix. |
| alias_arn | The Amazon Resource Name (ARN) of the key alias. |
