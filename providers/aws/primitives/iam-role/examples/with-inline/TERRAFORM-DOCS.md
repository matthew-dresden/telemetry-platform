# iam-role with-inline example -- terraform-docs reference

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
| iam_role | ../../ | n/a |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Name of the IAM role. | string | n/a | yes |
| assume_role_policy_json | A valid JSON trust policy document. | string | n/a | yes |
| description | Description of the IAM role. | string | "Firehose delivery role -- with-inline example" | no |
| path | Path under which the role is created. | string | "/" | no |
| max_session_duration | Maximum session duration in seconds. | number | 3600 | no |
| permissions_boundary | ARN of the permissions boundary policy. | string | null | no |
| managed_policy_arns | List of managed policy ARNs to attach. | list(string) | [] | no |
| inline_policies | Map of inline policy name to JSON document. | map(string) | {} | no |
| tags | Additional tags applied to all resources. | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| role_arn | The Amazon Resource Name (ARN) of the IAM role. |
| role_name | The name of the IAM role. |
| role_id | The stable unique identifier for the IAM role. |
