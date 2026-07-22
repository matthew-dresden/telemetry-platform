<!-- BEGIN_TF_DOCS -->
# ssm-parameter

Manages a single AWS SSM Parameter Store parameter of type String, StringList, or SecureString, with optional KMS encryption for SecureString parameters.

Implements the platform decision (D11) to use SSM Parameter Store over Secrets Manager for runtime application config and secrets. Provisions the `/telemetry/<env>/<plane>/<key>` path inventory defined in docs/terragrunt-concepts.md, where SecureString parameters are encrypted with the `telemetry-config` CMK.

## Resources managed

- `aws_ssm_parameter` -- the SSM parameter (String, StringList, or SecureString)

## Usage

```hcl
module "ssm_parameter" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/ssm-parameter?ref=providers/aws/primitives/ssm-parameter/v0.1.0"

  name  = "/telemetry/prod/ingest/waf-rate-limit"
  type  = "String"
  value = "100"
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- String parameter at the platform path convention
- `examples/securestring` -- SecureString at `/telemetry/prod/ingest/adot-config` bound to a fixture `telemetry-config` CMK

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
| [aws_ssm_parameter.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ssm_parameter) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_allowed_pattern"></a> [allowed\_pattern](#input\_allowed\_pattern) | (Optional) A regular expression used to validate the parameter value. | `string` | `null` | no |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description of the SSM parameter. | `string` | `""` | no |
| <a name="input_kms_key_id"></a> [kms\_key\_id](#input\_kms\_key\_id) | (Optional) The KMS key ID or ARN used to encrypt a SecureString parameter. Required when type is SecureString. | `string` | `null` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"ssm-parameter"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) The fully qualified name of the SSM parameter, including the path prefix (e.g. /telemetry/prod/ingest/adot-config). | `string` | n/a | yes |
| <a name="input_overwrite"></a> [overwrite](#input\_overwrite) | (Optional) Whether to overwrite an existing parameter value. | `bool` | `false` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_tier"></a> [tier](#input\_tier) | (Optional) The tier of the parameter. Valid values: Standard, Advanced, Intelligent-Tiering. | `string` | `"Standard"` | no |
| <a name="input_type"></a> [type](#input\_type) | (Required) The type of the SSM parameter. Must be one of: String, StringList, SecureString. | `string` | n/a | yes |
| <a name="input_value"></a> [value](#input\_value) | (Required) The value of the SSM parameter. Marked sensitive -- this value is never echoed in outputs. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_parameter_arn"></a> [parameter\_arn](#output\_parameter\_arn) | The Amazon Resource Name (ARN) of the SSM parameter. |
| <a name="output_parameter_name"></a> [parameter\_name](#output\_parameter\_name) | The fully qualified name of the SSM parameter. |
| <a name="output_parameter_version"></a> [parameter\_version](#output\_parameter\_version) | The version of the SSM parameter. |

## Input validation

- `type` must be one of `String`, `StringList`, or `SecureString`. Plans fail for any other value.
- `kms_key_id` must be provided when `type` is `SecureString`. Plans fail when omitted for SecureString parameters.
- `tier` must be one of `Standard`, `Advanced`, or `Intelligent-Tiering`. Plans fail for any other value.
- `value` is marked `sensitive = true` and is never exposed in any module output.
<!-- END_TF_DOCS -->