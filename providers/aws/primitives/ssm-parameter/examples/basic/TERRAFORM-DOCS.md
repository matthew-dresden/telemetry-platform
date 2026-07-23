# ssm-parameter basic example -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Providers

| Name | Version |
|------|---------|
| aws | ~> 6.0.0 |

## Modules

| Name | Source | Version |
|------|--------|---------|
| example | ../../ | n/a |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | The fully qualified name of the SSM parameter. | string | n/a | yes |
| type | The type of the SSM parameter. | string | "String" | no |
| value | The value of the SSM parameter. | string | "100" | no |
| description | Description of the SSM parameter. | string | "WAF rate limit config -- basic example" | no |
| tier | The tier of the parameter. | string | "Standard" | no |
| kms_key_id | The KMS key ID for SecureString parameters. | string | null | no |
| overwrite | Whether to overwrite an existing parameter value. | bool | false | no |
| allowed_pattern | A regular expression used to validate the parameter value. | string | null | no |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| parameter_arn | The Amazon Resource Name (ARN) of the SSM parameter. |
| parameter_name | The fully qualified name of the SSM parameter. |
| parameter_version | The version of the SSM parameter. |
