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
| <a name="provider_terraform"></a> [terraform](#provider\_terraform) | n/a |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_appautoscaling_policy.alb_request_count](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/appautoscaling_policy) | resource |
| [aws_appautoscaling_policy.cpu](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/appautoscaling_policy) | resource |
| [aws_appautoscaling_target.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/appautoscaling_target) | resource |
| [aws_cloudwatch_log_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_ecs_service.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecs_service) | resource |
| [aws_ecs_task_definition.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecs_task_definition) | resource |
| [terraform_data.autoscaling_config_guard](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_assign_public_ip"></a> [assign\_public\_ip](#input\_assign\_public\_ip) | (Optional) Whether to assign a public IP to Fargate tasks. Defaults to false for private subnets. | `bool` | `false` | no |
| <a name="input_autoscaling"></a> [autoscaling](#input\_autoscaling) | (Optional) Auto Scaling configuration. Required when enable\_autoscaling is true. A CPU target-tracking policy (cpu\_target\_percent) is always applied. Additionally, when request\_count\_target AND alb\_resource\_label are both set, an ALBRequestCountPerTarget target-tracking policy is also applied -- load-proportional and faster-reacting than CPU (recommended primary signal for request-driven services; CPU stays as a safety ceiling). alb\_resource\_label is the predefined-metric ResourceLabel in the form 'app/<alb-name>/<alb-id>/targetgroup/<tg-name>/<tg-id>'. max\_capacity must be >= min\_capacity. | <pre>object({<br/>    min_capacity         = number<br/>    max_capacity         = number<br/>    cpu_target_percent   = optional(number, 60)<br/>    request_count_target = optional(number)<br/>    alb_resource_label   = optional(string)<br/>    scale_in_cooldown    = optional(number, 300)<br/>    scale_out_cooldown   = optional(number, 60)<br/>  })</pre> | `null` | no |
| <a name="input_cluster_arn"></a> [cluster\_arn](#input\_cluster\_arn) | (Required) ARN of the ECS cluster where this service runs. | `string` | n/a | yes |
| <a name="input_container_definitions"></a> [container\_definitions](#input\_container\_definitions) | (Required) JSON-encoded list of container definitions. Must be valid JSON. Container config must reference SSM -- not baked into the image. | `string` | n/a | yes |
| <a name="input_create_log_group"></a> [create\_log\_group](#input\_create\_log\_group) | (Optional) Whether to create a CloudWatch log group for the service. Defaults to true. | `bool` | `true` | no |
| <a name="input_desired_count"></a> [desired\_count](#input\_desired\_count) | (Optional) Desired number of tasks. Defaults to 1 for basic (no ALB) deployments. | `number` | `1` | no |
| <a name="input_enable_autoscaling"></a> [enable\_autoscaling](#input\_enable\_autoscaling) | (Optional) Whether to enable Application Auto Scaling for the service. When true, autoscaling must be non-null. | `bool` | `false` | no |
| <a name="input_execution_role_arn"></a> [execution\_role\_arn](#input\_execution\_role\_arn) | (Required) ARN of the IAM role used by ECS to pull images and write logs. | `string` | n/a | yes |
| <a name="input_force_new_deployment"></a> [force\_new\_deployment](#input\_force\_new\_deployment) | (Optional) Whether to force a new deployment of the service on every apply. Defaults to true. | `bool` | `true` | no |
| <a name="input_health_check_grace_period_seconds"></a> [health\_check\_grace\_period\_seconds](#input\_health\_check\_grace\_period\_seconds) | (Optional) Seconds to ignore failing load balancer health checks on newly instantiated tasks. Only applied when load\_balancer is non-null. Defaults to 60. | `number` | `60` | no |
| <a name="input_load_balancer"></a> [load\_balancer](#input\_load\_balancer) | (Optional) ALB load balancer configuration. When non-null, the service is attached to the specified target group. | <pre>object({<br/>    target_group_arn = string<br/>    container_name   = string<br/>    container_port   = number<br/>  })</pre> | `null` | no |
| <a name="input_log_group_kms_key_arn"></a> [log\_group\_kms\_key\_arn](#input\_log\_group\_kms\_key\_arn) | (Optional) ARN of the KMS CMK used to encrypt the CloudWatch log group at rest. When null, the log group uses the default CloudWatch Logs service key. Supply a CMK ARN to satisfy customer-managed-key encryption requirements. Must match ^arn:aws:kms: when provided. | `string` | `null` | no |
| <a name="input_log_group_retention_days"></a> [log\_group\_retention\_days](#input\_log\_group\_retention\_days) | (Optional) Retention period in days for the CloudWatch log group. | `number` | `30` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"ecs-service"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) ECS service name. Must match ^[a-zA-Z0-9\_-]+$ and be 255 characters or fewer. | `string` | n/a | yes |
| <a name="input_security_group_ids"></a> [security\_group\_ids](#input\_security\_group\_ids) | (Required) List of security group IDs for the ECS service tasks. Must be non-empty. | `list(string)` | n/a | yes |
| <a name="input_subnet_ids"></a> [subnet\_ids](#input\_subnet\_ids) | (Required) List of subnet IDs for the ECS service. At least 2 subnets across availability zones are required. | `list(string)` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_task_cpu"></a> [task\_cpu](#input\_task\_cpu) | (Optional) CPU units for the task. Must be a valid Fargate CPU value (256, 512, 1024, 2048, 4096). Defaults to 512 per D44. | `number` | `512` | no |
| <a name="input_task_memory"></a> [task\_memory](#input\_task\_memory) | (Optional) Memory (MiB) for the task. Must be >= 512 and a valid Fargate combination for task\_cpu. Defaults to 1024 per D44. | `number` | `1024` | no |
| <a name="input_task_role_arn"></a> [task\_role\_arn](#input\_task\_role\_arn) | (Required) ARN of the IAM role assumed by the task for AWS API calls. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_autoscaling_target_resource_id"></a> [autoscaling\_target\_resource\_id](#output\_autoscaling\_target\_resource\_id) | The Application Auto Scaling resource ID (e.g. service/<cluster>/<service>). Empty string when enable\_autoscaling is false. |
| <a name="output_log_group_name"></a> [log\_group\_name](#output\_log\_group\_name) | The name of the CloudWatch log group. Empty string when create\_log\_group is false. |
| <a name="output_service_id"></a> [service\_id](#output\_service\_id) | The ID (ARN) of the ECS service. |
| <a name="output_service_name"></a> [service\_name](#output\_service\_name) | The name of the ECS service. |
| <a name="output_task_definition_arn"></a> [task\_definition\_arn](#output\_task\_definition\_arn) | The ARN of the active task definition revision. |
<!-- END_TF_DOCS -->