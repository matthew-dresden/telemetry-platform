# ecs-app-cluster

Composes a standalone shared ECS Fargate cluster together with its execution role and cluster-scoped
CloudWatch dashboard. This is a NEW-LOCAL reference module (decision D48, docs/terragrunt-concepts.md) that
the `collector-ingestion` reference instantiates to provide the shared cluster for the ADOT service.

The reference root declares no `resource` blocks. All infrastructure is delegated to the composed
primitives: `ecs-cluster`, `iam-role`, and `cloudwatch`.

## Composed modules

- `ecs-cluster` (telemetry-platform v1.0.0) -- ECS Fargate cluster with Container Insights and capacity providers
- `iam-role` (telemetry-platform v1.0.0) -- shared ECS task execution IAM role with managed policy attachments
- `cloudwatch` (telemetry-platform v1.0.0) -- cluster dashboard, alarms, and log groups

## Usage

When `use_pinned_module_sources = true` (prod environments), the terragrunt leaf sources this reference
via a pinned `git::...?ref=providers/aws/references/ecs-app-cluster/v<semver>` URL. When false (dev/sandbox),
the leaf uses `${get_repo_root()}//providers/aws/references/ecs-app-cluster` so local relative defaults apply.

```hcl
# Pinned usage (use_pinned_module_sources = true, prod):
module "ecs_app_cluster" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/references/ecs-app-cluster?ref=providers/aws/references/ecs-app-cluster/v1.0.0"

  cluster_name        = "telemetry-collector"
  execution_role_name = "telemetry-collector-exec-role"
  dashboard_name      = "telemetry-collector-cluster"

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/default` -- ECS Fargate cluster + shared execution role + CloudWatch dashboard

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
| <a name="module_cloudwatch"></a> [cloudwatch](#module\_cloudwatch) | var.cloudwatch\_source | n/a |
| <a name="module_cluster"></a> [cluster](#module\_cluster) | var.cluster\_source | n/a |
| <a name="module_execution_role"></a> [execution\_role](#module\_execution\_role) | var.execution\_role\_source | n/a |

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_alarms"></a> [alarms](#input\_alarms) | (Optional) Map of alarm name to metric alarm configuration for the cluster. Each alarm must have non-empty alarm\_actions and ok\_actions. | <pre>map(object({<br/>    comparison_operator = string<br/>    evaluation_periods  = number<br/>    metric_name         = string<br/>    namespace           = string<br/>    period              = number<br/>    statistic           = string<br/>    threshold           = number<br/>    alarm_description   = optional(string, "")<br/>    dimensions          = optional(map(string), {})<br/>    alarm_actions       = list(string)<br/>    ok_actions          = list(string)<br/>    treat_missing_data  = optional(string, "missing")<br/>  }))</pre> | `{}` | no |
| <a name="input_capacity_providers"></a> [capacity\_providers](#input\_capacity\_providers) | (Optional) List of capacity providers to associate with the cluster. Must be a non-empty subset of [FARGATE, FARGATE\_SPOT]. | `list(string)` | <pre>[<br/>  "FARGATE",<br/>  "FARGATE_SPOT"<br/>]</pre> | no |
| <a name="input_cluster_name"></a> [cluster\_name](#input\_cluster\_name) | (Required) Name of the ECS Fargate cluster. Must match ^[a-zA-Z0-9\_-]+$ and be 255 characters or fewer. | `string` | n/a | yes |
| <a name="input_dashboard_body"></a> [dashboard\_body](#input\_dashboard\_body) | (Optional) JSON body for the CloudWatch dashboard. Must be valid JSON when provided. | `string` | `null` | no |
| <a name="input_dashboard_name"></a> [dashboard\_name](#input\_dashboard\_name) | (Required) Name for the cluster CloudWatch dashboard. | `string` | n/a | yes |
| <a name="input_enable_container_insights"></a> [enable\_container\_insights](#input\_enable\_container\_insights) | (Optional) Whether to enable Container Insights for the cluster. Defaults to true per D11. | `bool` | `true` | no |
| <a name="input_execution_role_managed_policy_arns"></a> [execution\_role\_managed\_policy\_arns](#input\_execution\_role\_managed\_policy\_arns) | (Optional) List of managed policy ARNs to attach to the shared execution role. | `list(string)` | <pre>[<br/>  "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"<br/>]</pre> | no |
| <a name="input_execution_role_name"></a> [execution\_role\_name](#input\_execution\_role\_name) | (Required) Name of the shared ECS task execution IAM role. Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_log_groups"></a> [log\_groups](#input\_log\_groups) | (Optional) Map of logical name to log group configuration for the cluster. Each log group requires a kms\_key\_id and a valid retention\_in\_days. | <pre>map(object({<br/>    name              = string<br/>    retention_in_days = number<br/>    kms_key_id        = string<br/>  }))</pre> | `{}` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"ecs-app-cluster"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_cluster_arn"></a> [cluster\_arn](#output\_cluster\_arn) | The ARN of the ECS Fargate cluster. |
| <a name="output_cluster_id"></a> [cluster\_id](#output\_cluster\_id) | The ID (ARN) of the ECS Fargate cluster. |
| <a name="output_cluster_name"></a> [cluster\_name](#output\_cluster\_name) | The name of the ECS Fargate cluster. |
| <a name="output_dashboard_arn"></a> [dashboard\_arn](#output\_dashboard\_arn) | The ARN of the cluster CloudWatch dashboard. |
| <a name="output_execution_role_arn"></a> [execution\_role\_arn](#output\_execution\_role\_arn) | The ARN of the shared ECS task execution IAM role. |
<!-- END_TF_DOCS -->

## Input validation

- `cluster_name` must be 1-255 characters matching `^[a-zA-Z0-9_-]+$`.
- `capacity_providers` must be a non-empty subset of `[FARGATE, FARGATE_SPOT]`.
- `execution_role_name` must be 1-64 characters.
- `dashboard_body` must be valid JSON when provided.

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `cluster_source` | `../../primitives/ecs-cluster` | Source for the ecs-cluster primitive module |
| `execution_role_source` | `../../primitives/iam-role` | Source for the iam-role primitive module |
| `cloudwatch_source` | `../../primitives/cloudwatch` | Source for the cloudwatch primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/matthew-dresden/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/collector-ingestion.hcl` (or the service
that instantiates this reference) to the leaf. See `docs/terraform-module-sourcing.md` for the
end-to-end workflow.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks
- `composition_policy` -- the reference root uses only `module` blocks for infrastructure
