# identity

Composes the IAM Identity Center group-to-role wiring and the ECS task/execution roles
into a single NEW-LOCAL reference so the `identity` terragrunt unit deploys the
TelemetryAnalyst and TelemetryAdmin QuickSight permission-set roles and the collector ECS
roles as one unit (docs/terragrunt-concepts.md, D8).

The reference root declares no `resource` blocks. All infrastructure is delegated to the
`iam-role` primitive wired by published git tags: `iam-role` (NEW-LOCAL v1.0.0).

## Composed modules

- `analyst` (iam-role telemetry-platform v1.0.0) -- the TelemetryAnalyst IAM role. Receives
  the D2r read-only Athena/Glue/S3-results inline policy as input. Its `role_arn` is
  wired to the `author` key of `group_role_map` (D8).
- `admin` (iam-role telemetry-platform v1.0.0) -- the TelemetryAdmin IAM role. Receives the
  D3r manage QuickSight/datasets inline policy as input. Its `role_arn` is wired to the
  `admin` key of `group_role_map` (D8).
- `ecs_task` (iam-role telemetry-platform v1.0.0) -- the ECS ADOT task role consumed by
  `collector-ingestion` (docs/terragrunt-concepts.md). Created only when `ecs_task_role_name`
  is non-empty. Receives the ARN-scoped Firehose/KMS/SSM/CloudWatch inline policy as input.
- `ecs_task_execution` (iam-role telemetry-platform v1.0.0) -- the ECS execution role
  consumed by `collector-ingestion` (docs/terragrunt-concepts.md). Created only when
  `ecs_task_execution_role_name` is non-empty. Receives the ECR/logs/SSM/KMS inline
  policy as input.

## D8 group-to-role wiring

Per decision D8, the three Identity Center groups are wired to their roles strictly from
input group names and role names. No group or role name is hard-coded in the module:

- viewer group: maps to `viewer_group_name` input (read-only via permission set only).
- author group: maps to the TelemetryAnalyst role ARN (D2r).
- admin group: maps to the TelemetryAdmin role ARN (D3r).

An empty or undefined group name is rejected at the variable validation layer before any
role is created (fail-fast on input contract, AC-13).

## Inline policy contract

All inline policy documents are supplied as inputs (`analyst_inline_policies`,
`admin_inline_policies`, `ecs_task_inline_policies`, `ecs_task_execution_inline_policies`).
The reference does not hard-code any action or resource ARN. The calling terragrunt unit
is responsible for constructing the least-privilege policy documents per docs/terragrunt-concepts.md.

## Output contract

Per docs/terragrunt-concepts.md, the following outputs are consumed by downstream units
(`quicksight`, `collector-ingestion`, E6-F3-S2-T1, E7-F3-S1-T1):

| Output | Source | Description |
|--------|--------|-------------|
| `analyst_role_arn` | `module.analyst.role_arn` | TelemetryAnalyst role ARN (D2r) |
| `admin_role_arn` | `module.admin.role_arn` | TelemetryAdmin role ARN (D3r) |
| `ecs_task_role_arn` | `module.ecs_task[0].role_arn` | ECS ADOT task role ARN (section 3.1); null when not set |
| `ecs_task_execution_role_arn` | `module.ecs_task_execution[0].role_arn` | ECS execution role ARN (section 3.2); null when not set |
| `group_role_map` | `local.group_role_map` | `{ viewer, author, admin }` map for quicksight wiring |
| `group_annotations` | `local.group_annotations` | Raw group name strings for downstream use |
| `permission_set_account_id` | `var.permission_set_account_id` | Account hosting the permission sets |

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
| <a name="module_admin"></a> [admin](#module\_admin) | var.admin\_source | n/a |
| <a name="module_analyst"></a> [analyst](#module\_analyst) | var.analyst\_source | n/a |
| <a name="module_ecs_task"></a> [ecs\_task](#module\_ecs\_task) | var.ecs\_task\_source | n/a |
| <a name="module_ecs_task_execution"></a> [ecs\_task\_execution](#module\_ecs\_task\_execution) | var.ecs\_task\_execution\_source | n/a |

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_admin_assume_role_policy_json"></a> [admin\_assume\_role\_policy\_json](#input\_admin\_assume\_role\_policy\_json) | (Required) Trust policy JSON for the TelemetryAdmin role. Must be valid JSON. | `string` | n/a | yes |
| <a name="input_admin_group_name"></a> [admin\_group\_name](#input\_admin\_group\_name) | (Required) IAM Identity Center group name for the admin group. Must be non-empty. | `string` | n/a | yes |
| <a name="input_admin_inline_policies"></a> [admin\_inline\_policies](#input\_admin\_inline\_policies) | (Required) Map of inline policy name to JSON policy document for the TelemetryAdmin role. D3r: manage QuickSight/datasets. Must be a non-empty map with valid JSON values. | `map(string)` | n/a | yes |
| <a name="input_admin_role_name"></a> [admin\_role\_name](#input\_admin\_role\_name) | (Required) Name for the TelemetryAdmin IAM role (Admin permission set). Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_analyst_assume_role_policy_json"></a> [analyst\_assume\_role\_policy\_json](#input\_analyst\_assume\_role\_policy\_json) | (Required) Trust policy JSON for the TelemetryAnalyst role. Must be valid JSON. | `string` | n/a | yes |
| <a name="input_analyst_inline_policies"></a> [analyst\_inline\_policies](#input\_analyst\_inline\_policies) | (Required) Map of inline policy name to JSON policy document for the TelemetryAnalyst role. D2r: read-only Athena/Glue/S3-results. Must be a non-empty map with valid JSON values. | `map(string)` | n/a | yes |
| <a name="input_analyst_role_name"></a> [analyst\_role\_name](#input\_analyst\_role\_name) | (Required) Name for the TelemetryAnalyst IAM role (Author permission set). Must be 64 characters or fewer. | `string` | n/a | yes |
| <a name="input_author_group_name"></a> [author\_group\_name](#input\_author\_group\_name) | (Required) IAM Identity Center group name for the author (analyst) group. Must be non-empty. | `string` | n/a | yes |
| <a name="input_ecs_task_assume_role_policy_json"></a> [ecs\_task\_assume\_role\_policy\_json](#input\_ecs\_task\_assume\_role\_policy\_json) | (Optional) Trust policy JSON for the ECS task role. Required when ecs\_task\_role\_name is non-empty. Must be valid JSON. | `string` | `null` | no |
| <a name="input_ecs_task_execution_assume_role_policy_json"></a> [ecs\_task\_execution\_assume\_role\_policy\_json](#input\_ecs\_task\_execution\_assume\_role\_policy\_json) | (Optional) Trust policy JSON for the ECS execution role. Required when ecs\_task\_execution\_role\_name is non-empty. Must be valid JSON. | `string` | `null` | no |
| <a name="input_ecs_task_execution_inline_policies"></a> [ecs\_task\_execution\_inline\_policies](#input\_ecs\_task\_execution\_inline\_policies) | (Optional) Map of inline policy name to JSON policy document for the ECS execution role (docs/terragrunt-concepts.md -- ECR pull, ADOT log group logs, AOT\_CONFIG\_CONTENT SSM, telemetry-config KMS). Required when ecs\_task\_execution\_role\_name is non-empty. | `map(string)` | `{}` | no |
| <a name="input_ecs_task_execution_role_name"></a> [ecs\_task\_execution\_role\_name](#input\_ecs\_task\_execution\_role\_name) | (Optional) Name for the ECS execution role consumed by collector-ingestion (docs/terragrunt-concepts.md). When non-empty, the ecs\_task\_execution\_role module is created and ecs\_task\_execution\_role\_arn is non-null. Must be 64 characters or fewer when provided. | `string` | `""` | no |
| <a name="input_ecs_task_inline_policies"></a> [ecs\_task\_inline\_policies](#input\_ecs\_task\_inline\_policies) | (Optional) Map of inline policy name to JSON policy document for the ECS task role (docs/terragrunt-concepts.md -- ARN-scoped firehose/KMS/SSM/CloudWatch, no S3/Athena/Glue). Required when ecs\_task\_role\_name is non-empty. | `map(string)` | `{}` | no |
| <a name="input_ecs_task_role_name"></a> [ecs\_task\_role\_name](#input\_ecs\_task\_role\_name) | (Optional) Name for the ECS task role consumed by collector-ingestion (docs/terragrunt-concepts.md). When non-empty, the ecs\_task\_role module is created and ecs\_task\_role\_arn is non-null. Must be 64 characters or fewer when provided. | `string` | `""` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source reference module. | `string` | `"identity"` | no |
| <a name="input_permission_set_account_id"></a> [permission\_set\_account\_id](#input\_permission\_set\_account\_id) | (Required) AWS account ID hosting the IAM Identity Center permission sets. Used in the trust policy principal for the analyst/admin roles. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this reference module. | `map(string)` | `{}` | no |
| <a name="input_viewer_group_name"></a> [viewer\_group\_name](#input\_viewer\_group\_name) | (Required) IAM Identity Center group name for the viewer (read-only) group. Must be non-empty. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_admin_role_arn"></a> [admin\_role\_arn](#output\_admin\_role\_arn) | ARN of the TelemetryAdmin IAM role (Admin permission set, D3r). Consumed by downstream quicksight and ecs-app-deploy units (docs/terragrunt-concepts.md). |
| <a name="output_analyst_role_arn"></a> [analyst\_role\_arn](#output\_analyst\_role\_arn) | ARN of the TelemetryAnalyst IAM role (Author permission set, D2r). Consumed by downstream quicksight and ecs-app-deploy units (docs/terragrunt-concepts.md). |
| <a name="output_ecs_task_execution_role_arn"></a> [ecs\_task\_execution\_role\_arn](#output\_ecs\_task\_execution\_role\_arn) | ARN of the ECS execution role (docs/terragrunt-concepts.md). Null when ecs\_task\_execution\_role\_name is not set. Consumed by collector-ingestion. |
| <a name="output_ecs_task_role_arn"></a> [ecs\_task\_role\_arn](#output\_ecs\_task\_role\_arn) | ARN of the ECS ADOT task role (docs/terragrunt-concepts.md). Null when ecs\_task\_role\_name is not set. Consumed by collector-ingestion. |
| <a name="output_group_annotations"></a> [group\_annotations](#output\_group\_annotations) | Map pairing each Identity Center group name key (viewer\_group/author\_group/admin\_group) with the raw group name string from inputs. Consumed by downstream units that need the group names alongside the role ARNs (e.g., quicksight group membership wiring). |
| <a name="output_group_role_map"></a> [group\_role\_map](#output\_group\_role\_map) | Map of Identity Center group name keys (viewer/author/admin) to their corresponding values -- viewer maps to the viewer\_group\_name input, author maps to the analyst\_role\_arn, admin maps to the admin\_role\_arn (D8). Consumed by the quicksight reference to wire permission-set-to-role assignments. |
| <a name="output_permission_set_account_id"></a> [permission\_set\_account\_id](#output\_permission\_set\_account\_id) | AWS account ID hosting the IAM Identity Center permission sets (as supplied via input). Re-exported for downstream consumer verification. |
<!-- END_TF_DOCS -->

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

The `identity` reference instantiates `iam-role` four times (analyst, admin, ecs_task, ecs_task_execution).
Each block has its own distinct source variable per the spec (AC-5).

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `analyst_source` | `../../primitives/iam-role` | Source for the TelemetryAnalyst iam-role module |
| `admin_source` | `../../primitives/iam-role` | Source for the TelemetryAdmin iam-role module |
| `ecs_task_source` | `../../primitives/iam-role` | Source for the ECS task iam-role module |
| `ecs_task_execution_source` | `../../primitives/iam-role` | Source for the ECS execution iam-role module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/identity.hcl` to the leaf.
See `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks
- `composition_policy` -- the reference root uses only `module` blocks for infrastructure
