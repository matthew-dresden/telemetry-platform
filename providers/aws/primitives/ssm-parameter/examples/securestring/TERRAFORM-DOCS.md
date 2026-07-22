# ssm-parameter securestring example -- terraform-docs reference

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
| aws_kms_key.telemetry_config | resource |
| aws_kms_alias.telemetry_config | resource |

## Modules

| Name | Source | Version |
|------|--------|---------|
| example | ../../ | n/a |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | The fully qualified name of the SSM parameter at the section-4 path convention. | string | "/telemetry/prod/ingest/adot-config" | no |
| type | The type of the SSM parameter. | string | "SecureString" | no |
| value | The value of the SecureString SSM parameter. Marked sensitive. | string | (ADOT config YAML placeholder) | no |
| description | Description of the SSM parameter. | string | "ADOT collector config -- securestring example" | no |
| tier | The tier of the parameter. | string | "Standard" | no |
| overwrite | Whether to overwrite an existing parameter value. | bool | false | no |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| parameter_arn | The Amazon Resource Name (ARN) of the SSM parameter. |
| parameter_name | The fully qualified name of the SSM parameter. |
| parameter_version | The version of the SSM parameter. |
| kms_key_arn | The ARN of the fixture KMS CMK used to encrypt the SecureString parameter. |
