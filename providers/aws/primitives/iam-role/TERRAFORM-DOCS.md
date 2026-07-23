# iam-role -- terraform-docs reference

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
| aws_iam_role.this | resource |
| aws_iam_role_policy_attachment.managed | resource |
| aws_iam_role_policy.inline | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Name of the IAM role. Must be 64 characters or fewer. | string | n/a | yes |
| assume_role_policy_json | A valid JSON trust policy document granting principals permission to assume this role. | string | n/a | yes |
| description | Description of the IAM role. | string | "" | no |
| path | Path under which the role is created. | string | "/" | no |
| max_session_duration | Maximum session duration in seconds. Must be between 3600 and 43200. | number | 3600 | no |
| permissions_boundary | ARN of the policy that is used to set the permissions boundary for the role. | string | null | no |
| managed_policy_arns | List of managed policy ARNs to attach to the role. | list(string) | [] | no |
| inline_policies | Map of inline policy name to JSON policy document. Each value must be valid JSON. | map(string) | {} | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "iam-role" | no |

## Outputs

| Name | Description |
|------|-------------|
| role_arn | The Amazon Resource Name (ARN) of the IAM role. |
| role_name | The name of the IAM role. |
| role_id | The stable and unique string identifying the IAM role. |
