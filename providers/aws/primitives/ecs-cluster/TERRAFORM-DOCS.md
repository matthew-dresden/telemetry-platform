<!-- BEGIN_TF_DOCS -->
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
| [aws_ecs_cluster.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecs_cluster) | resource |
| [aws_ecs_cluster_capacity_providers.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecs_cluster_capacity_providers) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_capacity_providers"></a> [capacity\_providers](#input\_capacity\_providers) | (Optional) List of capacity providers to associate. Must be a non-empty subset of [FARGATE, FARGATE\_SPOT]. | `list(string)` | <pre>[<br/>  "FARGATE",<br/>  "FARGATE_SPOT"<br/>]</pre> | no |
| <a name="input_default_capacity_provider_strategy"></a> [default\_capacity\_provider\_strategy](#input\_default\_capacity\_provider\_strategy) | (Optional) Default capacity provider strategy for the cluster. Defaults to [] so the module auto-derives a strategy from capacity\_providers (FARGATE base=1 weight=1, FARGATE\_SPOT weight=4 when present). Each explicit entry must reference a provider present in capacity\_providers. | <pre>list(object({<br/>    capacity_provider = string<br/>    weight            = number<br/>    base              = optional(number, 0)<br/>  }))</pre> | `[]` | no |
| <a name="input_enable_container_insights"></a> [enable\_container\_insights](#input\_enable\_container\_insights) | (Optional) Whether to enable Container Insights for the cluster. Defaults to true per D11. | `bool` | `true` | no |
| <a name="input_execute_command_logging"></a> [execute\_command\_logging](#input\_execute\_command\_logging) | (Optional) Logging configuration for ECS Exec. Must be one of NONE, DEFAULT, OVERRIDE. | `string` | `"DEFAULT"` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"ecs-cluster"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) ECS cluster name. Must match ^[a-zA-Z0-9\_-]+$ and be 255 characters or fewer. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_cluster_arn"></a> [cluster\_arn](#output\_cluster\_arn) | The ARN of the ECS cluster. Passed to ecs-service as cluster\_arn. |
| <a name="output_cluster_id"></a> [cluster\_id](#output\_cluster\_id) | The ID (ARN) of the ECS cluster. |
| <a name="output_cluster_name"></a> [cluster\_name](#output\_cluster\_name) | The name of the ECS cluster. |
<!-- END_TF_DOCS -->