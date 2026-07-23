<!-- BEGIN_TF_DOCS -->
# kms-key

Manages a single customer-managed KMS CMK with a companion alias, key rotation, and an optional resource policy.

Used for encrypting S3 data lake buckets, Kinesis Firehose, SSM SecureString parameters, and QuickSight SPICE datasets across the telemetry-collector platform.

## Resources managed

- `aws_kms_key` -- the customer-managed key
- `aws_kms_alias` -- the human-readable alias targeting the key

## Usage

```hcl
module "kms_key" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/kms-key?ref=providers/aws/primitives/kms-key/v0.1.0"

  alias_name          = "my-service-key"
  enable_key_rotation = true
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- rotation on, default KMS policy
- `examples/with-policy` -- custom resource policy, multi-region enabled

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
| [aws_kms_alias.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/kms_alias) | resource |
| [aws_kms_key.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/kms_key) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_alias_name"></a> [alias\_name](#input\_alias\_name) | (Required) Alias suffix for the KMS key. The module prefixes 'alias/' automatically. Must match pattern [A-Za-z0-9/\_-]+. | `string` | n/a | yes |
| <a name="input_customer_master_key_spec"></a> [customer\_master\_key\_spec](#input\_customer\_master\_key\_spec) | (Optional) Specifies whether the key contains a symmetric key or an asymmetric key pair. | `string` | `"SYMMETRIC_DEFAULT"` | no |
| <a name="input_deletion_window_in_days"></a> [deletion\_window\_in\_days](#input\_deletion\_window\_in\_days) | (Optional) Waiting period in days before key deletion. Must be between 7 and 30 inclusive. | `number` | `30` | no |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description of the KMS key. | `string` | `"Customer managed KMS key"` | no |
| <a name="input_enable_key_rotation"></a> [enable\_key\_rotation](#input\_enable\_key\_rotation) | (Optional) Whether to enable annual automatic key rotation. | `bool` | `true` | no |
| <a name="input_key_usage"></a> [key\_usage](#input\_key\_usage) | (Optional) Intended use of the key. Valid values: ENCRYPT\_DECRYPT, SIGN\_VERIFY, GENERATE\_VERIFY\_MAC. | `string` | `"ENCRYPT_DECRYPT"` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"kms-key"` | no |
| <a name="input_multi_region"></a> [multi\_region](#input\_multi\_region) | (Optional) Whether the key is a multi-region key. | `bool` | `false` | no |
| <a name="input_policy_json"></a> [policy\_json](#input\_policy\_json) | (Optional) A valid JSON key resource policy. When null, the default KMS key policy is used. | `string` | `null` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_alias_arn"></a> [alias\_arn](#output\_alias\_arn) | The Amazon Resource Name (ARN) of the key alias. |
| <a name="output_alias_name"></a> [alias\_name](#output\_alias\_name) | The display name of the alias, including the 'alias/' prefix. |
| <a name="output_key_arn"></a> [key\_arn](#output\_key\_arn) | The Amazon Resource Name (ARN) of the key. |
| <a name="output_key_id"></a> [key\_id](#output\_key\_id) | The globally unique identifier for the key. |

## Input validation

- `alias_name` must match `^[A-Za-z0-9/_-]+$`. Plans fail if the pattern is not satisfied.
- `deletion_window_in_days` must be between 7 and 30 inclusive. Plans fail outside this range.
- `key_usage` must be one of `ENCRYPT_DECRYPT`, `SIGN_VERIFY`, or `GENERATE_VERIFY_MAC`.
- `policy_json`, when provided, must be valid JSON. Plans fail for malformed JSON.
<!-- END_TF_DOCS -->