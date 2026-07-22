<!-- BEGIN_TF_DOCS -->
# iam-role

Manages a single AWS IAM role with its trust (assume-role) policy, optional managed-policy attachments, and optional inline policies.

Used for provisioning ECS task execution roles, Firehose delivery roles, OIDC-federated CI roles, and other service roles across the telemetry-collector platform.

## Resources managed

- `aws_iam_role` -- the IAM role with trust policy
- `aws_iam_role_policy_attachment` -- one attachment per managed policy ARN
- `aws_iam_role_policy` -- one inline policy per entry in the inline_policies map

## Usage

```hcl
module "iam_role" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/iam-role?ref=providers/aws/primitives/iam-role/v0.1.0"

  name = "telemetry-ecs-execution"
  assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- ECS-tasks trust policy, one managed policy attachment
- `examples/with-inline` -- Firehose trust policy, two inline policies (S3 write + KMS encrypt)

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
| [aws_iam_role.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role_policy.inline](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy_attachment.managed](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy_attachment) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_assume_role_policy_json"></a> [assume\_role\_policy\_json](#input\_assume\_role\_policy\_json) | (Required) A valid JSON trust policy document granting principals permission to assume this role. | `string` | n/a | yes |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description of the IAM role. | `string` | `""` | no |
| <a name="input_inline_policies"></a> [inline\_policies](#input\_inline\_policies) | (Optional) Map of inline policy name to JSON policy document. Each value must be valid JSON. | `map(string)` | `{}` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_managed_policy_arns"></a> [managed\_policy\_arns](#input\_managed\_policy\_arns) | (Optional) List of managed policy ARNs to attach to the role. | `list(string)` | `[]` | no |
| <a name="input_max_session_duration"></a> [max\_session\_duration](#input\_max\_session\_duration) | (Optional) Maximum session duration in seconds. Must be between 3600 and 43200. | `number` | `3600` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"iam-role"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) Name of the IAM role. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_path"></a> [path](#input\_path) | (Optional) Path under which the role is created. | `string` | `"/"` | no |
| <a name="input_permissions_boundary"></a> [permissions\_boundary](#input\_permissions\_boundary) | (Optional) ARN of the policy that is used to set the permissions boundary for the role. | `string` | `null` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_role_arn"></a> [role\_arn](#output\_role\_arn) | The Amazon Resource Name (ARN) of the IAM role. |
| <a name="output_role_id"></a> [role\_id](#output\_role\_id) | The stable and unique string identifying the IAM role. |
| <a name="output_role_name"></a> [role\_name](#output\_role\_name) | The name of the IAM role. |

## Input validation

- `name` must be 64 characters or fewer to satisfy the IAM role name limit. Plans fail if the limit is exceeded.
- `assume_role_policy_json` must be a valid JSON string. Plans fail for malformed JSON.
- Every value in `inline_policies` must be a valid JSON string. Plans fail if any value is malformed.
- `max_session_duration` must be between 3600 and 43200 seconds. Plans fail outside this range.
<!-- END_TF_DOCS -->