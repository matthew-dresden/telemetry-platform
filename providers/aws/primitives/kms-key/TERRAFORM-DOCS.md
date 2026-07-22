# kms-key -- terraform-docs reference

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
| aws_kms_key.this | resource |
| aws_kms_alias.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| alias_name | Alias suffix for the KMS key. The module prefixes 'alias/' automatically. Must match pattern [A-Za-z0-9/_-]+. | string | n/a | yes |
| description | Description of the KMS key. | string | "Customer managed KMS key" | no |
| deletion_window_in_days | Waiting period in days before key deletion. Must be between 7 and 30 inclusive. | number | 30 | no |
| enable_key_rotation | Whether to enable annual automatic key rotation. | bool | true | no |
| key_usage | Intended use of the key. One of ENCRYPT_DECRYPT, SIGN_VERIFY, GENERATE_VERIFY_MAC. | string | "ENCRYPT_DECRYPT" | no |
| customer_master_key_spec | Specifies whether the key contains a symmetric key or an asymmetric key pair. | string | "SYMMETRIC_DEFAULT" | no |
| multi_region | Whether the key is a multi-region primary key. | bool | false | no |
| policy_json | A valid JSON key resource policy. When null, the default KMS key policy is used. | string | null | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "kms-key" | no |

## Outputs

| Name | Description |
|------|-------------|
| key_id | The globally unique identifier for the key. |
| key_arn | The Amazon Resource Name (ARN) of the key. |
| alias_name | The display name of the alias, including the 'alias/' prefix. |
| alias_arn | The Amazon Resource Name (ARN) of the key alias. |
