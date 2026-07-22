# ecs-app-deploy

Composes a complete ECS application deployment onto an existing ECS cluster: the ECS service,
its per-service task role, the SSM config/secret parameters, the ALB with target group wiring,
the HTTPS listener, and per-service CloudWatch alarms. This is a NEW-LOCAL reference module
(decision D48, docs/terragrunt-concepts.md) consumed by the `collector-ingestion` reference to
deploy the ADOT service onto the shared cluster from `ecs-app-cluster`.

The reference root declares no `resource` blocks. All infrastructure is delegated to composed
primitives. In-repo child sources are `source = var.<name>_source` (const=true, default to
in-repo relative paths). Sources resolve to pinned git URLs only when `use_pinned_module_sources = true`
is set in `account.hcl`; external `caylent-solutions/terraform-modules` sources remain pinned literals.

## Composed modules

- `iam-role` (telemetry-platform v1.0.0) -- per-service ECS task IAM role
- `ssm-parameter` (telemetry-platform v1.0.0) -- one instance per `ssm_parameters` entry via `for_each`
- `alb` (telemetry-platform v1.0.0) -- Application Load Balancer and target groups (optional)
- `alb-listener` (telemetry-platform v1.0.0) -- ALB listeners (optional)
- `ecs-service` (telemetry-platform v1.0.0) -- ECS Fargate service and task definition
- `cloudwatch` (telemetry-platform v1.0.0) -- per-service CloudWatch alarms

## Usage

When `use_pinned_module_sources = true` (prod environments), the terragrunt leaf sources this reference
via a pinned `git::...?ref=providers/aws/references/ecs-app-deploy/v<semver>` URL. When false (dev/sandbox),
the leaf uses `${get_repo_root()}//providers/aws/references/ecs-app-deploy` so local relative defaults apply.

```hcl
# Pinned usage (use_pinned_module_sources = true, prod):
module "ecs_app_deploy" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/references/ecs-app-deploy?ref=providers/aws/references/ecs-app-deploy/v1.0.0"

  service_name       = "adot-collector"
  cluster_arn        = module.ecs_app_cluster.cluster_arn
  execution_role_arn = module.ecs_app_cluster.execution_role_arn
  task_role_name     = "adot-collector-task-role"
  container_definitions = jsonencode([...])

  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.service.id]
  vpc_id             = var.vpc_id

  env = var.env

  # docs/terragrunt-concepts.md ingest SSM inventory -- caller provides the complete set.
  ssm_parameters = {
    "adot-config" = {
      type       = "SecureString"
      value      = var.adot_config_yaml
      kms_key_id = var.telemetry_config_kms_key_id
    }
    "waf-rate-limit"      = { type = "String", value = var.waf_rate_limit }
    "otlp-max-body-bytes" = { type = "String", value = var.otlp_max_body_bytes }
    "public-client-id"    = { type = "String", value = var.public_client_id }
  }

  tags = {
    Environment = var.env
  }
}
```

## Examples

- `examples/basic` -- ECS service + task role + 1 SSM parameter, no ALB
- `examples/with-alb` -- ECS service + task role + ALB + HTTPS listener + autoscaling + full ingest SSM inventory (adot-config SecureString + 3 String params per docs/terragrunt-concepts.md)

## SSM parameter path convention

All parameters follow `/telemetry/<env>/ingest/<key>` per docs/terragrunt-concepts.md. Every `SecureString`
entry must bind the `telemetry-config` CMK via `kms_key_id` (docs/terragrunt-concepts.md).

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_alb"></a> [alb](#module\_alb) | var.alb\_source | n/a |
| <a name="module_cloudwatch"></a> [cloudwatch](#module\_cloudwatch) | var.cloudwatch\_source | n/a |
| <a name="module_listener"></a> [listener](#module\_listener) | var.listener\_source | n/a |
| <a name="module_service"></a> [service](#module\_service) | var.service\_source | n/a |
| <a name="module_ssm"></a> [ssm](#module\_ssm) | var.ssm\_source | n/a |
| <a name="module_task_role"></a> [task\_role](#module\_task\_role) | var.task\_role\_source | n/a |

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_alarms"></a> [alarms](#input\_alarms) | (Optional) Map of alarm name to metric alarm configuration for the service. Each alarm must have non-empty alarm\_actions and ok\_actions. | <pre>map(object({<br/>    comparison_operator = string<br/>    evaluation_periods  = number<br/>    metric_name         = string<br/>    namespace           = string<br/>    period              = number<br/>    statistic           = string<br/>    threshold           = number<br/>    alarm_description   = optional(string, "")<br/>    dimensions          = optional(map(string), {})<br/>    alarm_actions       = list(string)<br/>    ok_actions          = list(string)<br/>    treat_missing_data  = optional(string, "missing")<br/>  }))</pre> | `{}` | no |
| <a name="input_alb"></a> [alb](#input\_alb) | (Optional) ALB configuration. When non-null, an Application Load Balancer is created for the service. | `object(...)` | `null` | no |
| <a name="input_alb_listeners"></a> [alb\_listeners](#input\_alb\_listeners) | (Optional) ALB listener configuration. Required when alb is non-null. | `object(...)` | `null` | no |
| <a name="input_assign_public_ip"></a> [assign\_public\_ip](#input\_assign\_public\_ip) | (Optional) Whether to assign a public IP to Fargate tasks. Defaults to false for private subnets. | `bool` | `false` | no |
| <a name="input_autoscaling"></a> [autoscaling](#input\_autoscaling) | (Optional) Auto Scaling configuration. Required when enable\_autoscaling is true. | `object(...)` | `null` | no |
| <a name="input_cluster_arn"></a> [cluster\_arn](#input\_cluster\_arn) | (Required) ARN of the ECS cluster where this service runs. | `string` | n/a | yes |
| <a name="input_container_definitions"></a> [container\_definitions](#input\_container\_definitions) | (Required) JSON-encoded list of container definitions. Must be valid JSON. | `string` | n/a | yes |
| <a name="input_desired_count"></a> [desired\_count](#input\_desired\_count) | (Optional) Desired number of tasks. Defaults to 1. | `number` | `1` | no |
| <a name="input_enable_autoscaling"></a> [enable\_autoscaling](#input\_enable\_autoscaling) | (Optional) Whether to enable Application Auto Scaling. | `bool` | `false` | no |
| <a name="input_env"></a> [env](#input\_env) | (Required) Deployment environment label for SSM parameter path prefixes. | `string` | n/a | yes |
| <a name="input_execution_role_arn"></a> [execution\_role\_arn](#input\_execution\_role\_arn) | (Required) ARN of the IAM role used by ECS to pull images and write logs. | `string` | n/a | yes |
| <a name="input_log_group_retention_days"></a> [log\_group\_retention\_days](#input\_log\_group\_retention\_days) | (Optional) Retention period in days for the CloudWatch log group. | `number` | `30` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"ecs-app-deploy"` | no |
| <a name="input_security_group_ids"></a> [security\_group\_ids](#input\_security\_group\_ids) | (Required) List of security group IDs for the ECS service tasks. | `list(string)` | n/a | yes |
| <a name="input_service_name"></a> [service\_name](#input\_service\_name) | (Required) ECS service name. | `string` | n/a | yes |
| <a name="input_ssm_parameters"></a> [ssm\_parameters](#input\_ssm\_parameters) | (Optional) Map of SSM parameter logical key to parameter configuration. SecureString entries must set kms\_key\_id. | <pre>map(object({<br/>    type       = string<br/>    value      = string<br/>    kms_key_id = optional(string)<br/>  }))</pre> | `{}` | no |
| <a name="input_subnet_ids"></a> [subnet\_ids](#input\_subnet\_ids) | (Required) List of subnet IDs for the ECS service. At least 2 required. | `list(string)` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_task_cpu"></a> [task\_cpu](#input\_task\_cpu) | (Optional) CPU units for the task. Defaults to 512. | `number` | `512` | no |
| <a name="input_task_memory"></a> [task\_memory](#input\_task\_memory) | (Optional) Memory (MiB) for the task. Defaults to 1024. | `number` | `1024` | no |
| <a name="input_task_role_inline_policies"></a> [task\_role\_inline\_policies](#input\_task\_role\_inline\_policies) | (Optional) Map of inline policy name to JSON policy document for the task role. | `map(string)` | `{}` | no |
| <a name="input_task_role_name"></a> [task\_role\_name](#input\_task\_role\_name) | (Required) Name of the per-service ECS task IAM role. | `string` | n/a | yes |
| <a name="input_vpc_id"></a> [vpc\_id](#input\_vpc\_id) | (Optional) VPC ID where the ALB is deployed. Required when alb is non-null. | `string` | `null` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_alb_arn"></a> [alb\_arn](#output\_alb\_arn) | The ARN of the Application Load Balancer. Null when no ALB is configured. |
| <a name="output_alb_dns_name"></a> [alb\_dns\_name](#output\_alb\_dns\_name) | The DNS name of the Application Load Balancer. Null when no ALB is configured. |
| <a name="output_alb_zone_id"></a> [alb\_zone\_id](#output\_alb\_zone\_id) | The canonical hosted zone ID of the ALB. Null when no ALB is configured. |
| <a name="output_autoscaling_target_resource_id"></a> [autoscaling\_target\_resource\_id](#output\_autoscaling\_target\_resource\_id) | The Application Auto Scaling resource ID. Empty string when autoscaling is disabled. |
| <a name="output_https_listener_arn"></a> [https\_listener\_arn](#output\_https\_listener\_arn) | The ARN of the HTTPS listener. Null when no HTTPS listener is configured. |
| <a name="output_service_arn"></a> [service\_arn](#output\_service\_arn) | The ARN of the ECS service. |
| <a name="output_service_name"></a> [service\_name](#output\_service\_name) | The name of the ECS service. |
| <a name="output_ssm_parameter_arns"></a> [ssm\_parameter\_arns](#output\_ssm\_parameter\_arns) | Map of SSM parameter logical key to ARN. |
| <a name="output_target_group_arns"></a> [target\_group\_arns](#output\_target\_group\_arns) | Map of target group name to ARN. Empty map when no ALB is configured. |
| <a name="output_task_definition_arn"></a> [task\_definition\_arn](#output\_task\_definition\_arn) | The ARN of the active task definition revision. |
| <a name="output_task_role_arn"></a> [task\_role\_arn](#output\_task\_role\_arn) | The ARN of the per-service ECS task IAM role. |
<!-- END_TF_DOCS -->

## Input validation

- `service_name` must be 1-255 characters matching `^[a-zA-Z0-9_-]+$`.
- `task_role_name` must be 1-64 characters.
- `cluster_arn` must be a valid ECS cluster ARN.
- `execution_role_arn` must be a valid IAM role ARN.
- `ssm_parameters` entries with `type = "SecureString"` must set `kms_key_id` (enforced by the `ssm-parameter` primitive).
- `autoscaling.max_capacity` must be >= `autoscaling.min_capacity`.

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `task_role_source` | `../../primitives/iam-role` | Source for the iam-role primitive module (task role) |
| `ssm_source` | `../../primitives/ssm-parameter` | Source for the ssm-parameter primitive module |
| `alb_source` | `../../primitives/alb` | Source for the alb primitive module |
| `listener_source` | `../../primitives/alb-listener` | Source for the alb-listener primitive module |
| `service_source` | `../../primitives/ecs-service` | Source for the ecs-service primitive module |
| `cloudwatch_source` | `../../primitives/cloudwatch` | Source for the cloudwatch primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/collector-ingestion.hcl` (or the service
that instantiates this reference) to the leaf. See `docs/terraform-module-sourcing.md` for the
end-to-end workflow.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks
- `composition_policy` -- the reference root uses only `module` blocks for infrastructure
