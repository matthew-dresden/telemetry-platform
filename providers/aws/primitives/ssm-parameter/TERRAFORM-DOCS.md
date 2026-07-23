# ssm-parameter -- terraform-docs reference

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
| aws_ssm_parameter.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | The fully qualified name of the SSM parameter, including the path prefix (e.g. /telemetry/prod/ingest/adot-config). | string | n/a | yes |
| type | The type of the SSM parameter. Must be one of: String, StringList, SecureString. | string | n/a | yes |
| value | The value of the SSM parameter. Marked sensitive -- this value is never echoed in outputs. | string | n/a | yes |
| description | Description of the SSM parameter. | string | "" | no |
| tier | The tier of the parameter. Valid values: Standard, Advanced, Intelligent-Tiering. | string | "Standard" | no |
| kms_key_id | The KMS key ID or ARN used to encrypt a SecureString parameter. Required when type is SecureString. | string | null | no |
| overwrite | Whether to overwrite an existing parameter value. | bool | false | no |
| allowed_pattern | A regular expression used to validate the parameter value. | string | null | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "ssm-parameter" | no |

## Outputs

| Name | Description |
|------|-------------|
| parameter_arn | The Amazon Resource Name (ARN) of the SSM parameter. |
| parameter_name | The fully qualified name of the SSM parameter. |
| parameter_version | The version of the SSM parameter. |
