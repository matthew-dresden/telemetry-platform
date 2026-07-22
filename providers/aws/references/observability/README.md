# observability

Composes the platform observability and cost-guardrail plane into a single NEW-LOCAL reference so
the `observability` terragrunt unit deploys CloudWatch alarms, an SNS notification topic, AWS
Budgets, and Cost Anomaly Detection as one unit with a single-sink alert architecture (D41).

The reference root declares no `resource` blocks. All infrastructure is delegated to four composed
primitives wired by published git tags: `sns-topic` (NEW-LOCAL), `cloudwatch` (NEW-LOCAL),
`budget` (REUSED upstream), and `cost-anomaly` (NEW-LOCAL).

## Composed modules

- `sns-topic` (telemetry-platform v1.0.0) -- the KMS-encrypted SNS notification topic (`topic_name`,
  `kms_key_id`, `subscribers`). Its `topic_arn` is wired by locals.tf into every alarm's
  `alarm_actions`/`ok_actions` and into the cost-anomaly SNS subscriber so no alert fires silently
  (D41). locals.tf also composes the topic access policy from the deterministic topic ARN
  (`arn:aws:sns:<region>:<account_id>:<topic_name>`, derived from `aws_caller_identity`/`aws_region`
  -- never the topic resource attribute, so there is no create-time cycle) and passes it as
  `policy_json`. That policy retains owner-account control (an `aws_sns_topic_policy` REPLACES the
  default policy) and grants `costalerts.amazonaws.com` `SNS:Publish` scoped by `aws:SourceAccount`
  so AWS Cost Anomaly Detection can publish to the CMK-encrypted topic (D41). Operators may fully
  override this default via `sns_policy_json`.
- `cloudwatch` (telemetry-platform v1.0.0) -- metric alarms, the observability dashboard, and
  standalone platform log groups. Every alarm receives `alarm_actions` and `ok_actions` equal to
  the composed SNS `topic_arn` (D41). `create_dashboard` is a configurable input (type bool,
  default false); the live sandbox leaf enables it via `terraform.tfvars`.
- `budget` (terraform-modules v1.1.0) -- monthly cost budget with percentage-increment notification
  thresholds and direct `subscriber_email_addresses`. The budget retains its direct email
  subscribers and does NOT route through the SNS topic (D41 -- only alarms and cost-anomaly use
  the SNS sink). Notification thresholds are supplied as percentage increments of `budget_amount`
  via the `budget_notification_thresholds` input (docs/terragrunt-concepts.md).
- `cost-anomaly` (telemetry-platform v1.0.0) -- `aws_ce_anomaly_monitor` + `aws_ce_anomaly_subscription`.
  The subscription SNS subscriber address is the composed `topic_arn` (D41). This reference is the
  SOLE owner of cost-anomaly resources; no other reference composes them (D23/D46).

## D41 single-sink wiring

Per decision D41, the composed SNS `topic_arn` is the ONLY notification destination for:

- Every CloudWatch metric alarm's `alarm_actions` and `ok_actions`.
- The `cost-anomaly` subscription's SNS subscriber address.

The `budget` keeps its direct `subscriber_email_addresses` and does NOT use the SNS topic. This
removes every silent-alert path from the observability stack.

## D41 output contract

Per docs/terragrunt-concepts.md, the following outputs are consumed by downstream observability units
(E6-F3-S3-T1, E7-F3-S3-T1):

| Output | Source | Description |
|--------|--------|-------------|
| `sns_topic_arn` | `module.sns_topic.topic_arn` | The SNS notification topic ARN (D41 single sink) |
| `dashboard_name` | `module.cloudwatch.dashboard_name` | The CloudWatch observability dashboard name |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 6.49.0 |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_budget"></a> [budget](#module\_budget) | git::https://github.com/caylent-solutions/terraform-modules.git//providers/aws/primitives/budget | providers/aws/primitives/budget/v1.1.0 |
| <a name="module_cloudwatch"></a> [cloudwatch](#module\_cloudwatch) | var.cloudwatch\_source | n/a |
| <a name="module_cost_anomaly"></a> [cost\_anomaly](#module\_cost\_anomaly) | var.cost\_anomaly\_source | n/a |
| <a name="module_sns_topic"></a> [sns\_topic](#module\_sns\_topic) | var.sns\_topic\_source | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_alarm_notification_email"></a> [alarm\_notification\_email](#input\_alarm\_notification\_email) | (Optional) Email address subscribed to the SNS alarm/anomaly topic so CloudWatch alarms and cost-anomaly notifications reach a human (D41 -- closes the no-human-delivery gap where the topic had zero subscribers). Defaults to null (no email subscription is composed from this input). When set, the module composes an email subscription to this address and merges it with any sns\_subscribers entries, deduplicated by endpoint (so setting both this and a matching sns\_subscribers entry yields ONE subscription). NOTE: an SNS email subscription is created in PendingConfirmation state -- the recipient MUST click the confirmation link AWS emails after apply, or no notifications are delivered. AWS auto-deletes an unconfirmed subscription after ~3 days, and Terraform does not recreate a still-pending subscription on re-apply, so the link must be confirmed promptly after the apply that creates it. | `string` | `null` | no |
| <a name="input_alarms"></a> [alarms](#input\_alarms) | (Optional) Map of alarm name to metric alarm configuration. The observability reference wires every alarm's alarm\_actions and ok\_actions to the composed SNS topic\_arn (D41). Do NOT pass alarm\_actions or ok\_actions here -- they are set by locals.tf. Each alarm must set EXACTLY ONE of statistic (SampleCount, Average, Sum, Minimum, Maximum) or extended\_statistic (a percentile such as p95); the value is passed through to the cloudwatch primitive. | <pre>map(object({<br/>    comparison_operator = string<br/>    evaluation_periods  = number<br/>    metric_name         = string<br/>    namespace           = string<br/>    period              = number<br/>    statistic           = optional(string)<br/>    extended_statistic  = optional(string)<br/>    threshold           = number<br/>    alarm_description   = optional(string, "")<br/>    dimensions          = optional(map(string), {})<br/>    treat_missing_data  = optional(string, "missing")<br/>  }))</pre> | `{}` | no |
| <a name="input_anomaly_monitor_name"></a> [anomaly\_monitor\_name](#input\_anomaly\_monitor\_name) | (Required) Display name for the cost anomaly monitor. Must be non-empty. | `string` | n/a | yes |
| <a name="input_anomaly_monitor_type"></a> [anomaly\_monitor\_type](#input\_anomaly\_monitor\_type) | (Optional) Monitor type for cost anomaly detection. DIMENSIONAL monitors all AWS services; CUSTOM allows a cost-category filter. Defaults to DIMENSIONAL. | `string` | `"DIMENSIONAL"` | no |
| <a name="input_anomaly_subscription_frequency"></a> [anomaly\_subscription\_frequency](#input\_anomaly\_subscription\_frequency) | (Optional) How often anomaly alerts are delivered. Valid values: DAILY, IMMEDIATE, WEEKLY. Defaults to DAILY. | `string` | `"DAILY"` | no |
| <a name="input_anomaly_subscription_name"></a> [anomaly\_subscription\_name](#input\_anomaly\_subscription\_name) | (Required) Display name for the cost anomaly alert subscription. Must be non-empty. | `string` | n/a | yes |
| <a name="input_anomaly_threshold_expression"></a> [anomaly\_threshold\_expression](#input\_anomaly\_threshold\_expression) | (Required) JSON cost-expression defining the spend anomaly threshold. Passed directly to the cost-anomaly primitive threshold\_expression input. | `string` | n/a | yes |
| <a name="input_budget_amount"></a> [budget\_amount](#input\_budget\_amount) | (Required) Monthly budget limit amount in USD. Must be greater than 0. Passed to the reused budget primitive. | `number` | n/a | yes |
| <a name="input_budget_notification_thresholds"></a> [budget\_notification\_thresholds](#input\_budget\_notification\_thresholds) | (Required) Percentage increments of budget\_amount at which the budget alerts. Each entry creates one notification threshold. Must be non-empty. notification\_type defaults to ACTUAL; comparison defaults to GREATER\_THAN. | <pre>list(object({<br/>    threshold         = number<br/>    notification_type = optional(string, "ACTUAL")<br/>    comparison        = optional(string, "GREATER_THAN")<br/>  }))</pre> | n/a | yes |
| <a name="input_budget_subscriber_email_addresses"></a> [budget\_subscriber\_email\_addresses](#input\_budget\_subscriber\_email\_addresses) | (Required) Direct email addresses for budget notifications. The budget keeps these direct subscribers (D41 -- budget does not route through the SNS topic). Must be non-empty. | `list(string)` | n/a | yes |
| <a name="input_create_dashboard"></a> [create\_dashboard](#input\_create\_dashboard) | (Optional) Whether to provision the CloudWatch dashboard resource. When true, dashboard\_name must be non-empty. Passed to the cloudwatch primitive create\_dashboard input. | `bool` | `false` | no |
| <a name="input_dashboard_body"></a> [dashboard\_body](#input\_dashboard\_body) | (Optional) JSON body for the CloudWatch dashboard. Must be valid JSON when provided. | `string` | `null` | no |
| <a name="input_dashboard_name"></a> [dashboard\_name](#input\_dashboard\_name) | (Required) Name for the CloudWatch observability dashboard. Must be non-empty. | `string` | n/a | yes |
| <a name="input_log_groups"></a> [log\_groups](#input\_log\_groups) | (Optional) Map of logical name to CloudWatch log group configuration. Each log group requires a kms\_key\_id for encryption at rest. | <pre>map(object({<br/>    name              = string<br/>    retention_in_days = number<br/>    kms_key_id        = string<br/>  }))</pre> | `{}` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source reference module. | `string` | `"observability"` | no |
| <a name="input_sns_kms_key_id"></a> [sns\_kms\_key\_id](#input\_sns\_kms\_key\_id) | (Required) CMK id/ARN or alias for server-side encryption on the SNS topic (passed to sns-topic kms\_key\_id). Must be a KMS ARN (arn:aws:kms:...) or alias (alias/...). | `string` | n/a | yes |
| <a name="input_sns_policy_json"></a> [sns\_policy\_json](#input\_sns\_policy\_json) | (Optional) Operator override for the SNS topic access policy as a JSON string. When null (default), the module applies a constructed policy that retains owner-account control and grants costalerts.amazonaws.com SNS:Publish (scoped by aws:SourceAccount) so AWS Cost Anomaly Detection can publish to the CMK-encrypted topic (D41). Set this only to fully replace that default; the supplied policy is then the SOLE topic policy. | `string` | `null` | no |
| <a name="input_sns_subscribers"></a> [sns\_subscribers](#input\_sns\_subscribers) | (Optional) SNS topic subscriptions (e.g. operator email). Each entry requires a protocol and a non-empty endpoint. Passed to the sns-topic primitive. | <pre>list(object({<br/>    protocol = string<br/>    endpoint = string<br/>  }))</pre> | `[]` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this reference module. | `map(string)` | `{}` | no |
| <a name="input_topic_name"></a> [topic\_name](#input\_topic\_name) | (Required) SNS topic name passed to the sns-topic primitive. Must match ^[A-Za-z0-9\_-]+$. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_dashboard_name"></a> [dashboard\_name](#output\_dashboard\_name) | The name of the composed CloudWatch observability dashboard. Non-null when create\_dashboard is true. Per docs/terragrunt-concepts.md. |
| <a name="output_sns_topic_arn"></a> [sns\_topic\_arn](#output\_sns\_topic\_arn) | The ARN of the composed SNS notification topic (D41). Consumed by downstream units (E6-F3-S3-T1, E7-F3-S3-T1) to wire alarm\_actions/ok\_actions and the cost-anomaly SNS subscriber per docs/terragrunt-concepts.md. |
<!-- END_TF_DOCS -->

## Child module source variables (const=true convention)

Each in-repo child module source is declared as a `const=true` variable so `terraform init -backend=false`
resolves to the in-repo local relative path without network access. Callers do not override these.

The external `module "budget"` (caylent-solutions/terraform-modules) is NOT variable-ized per the
external-literal rule (spec section 4.4). Its source remains a pinned git URL at all times.

| Variable | Default (local path) | Description |
|----------|----------------------|-------------|
| `sns_topic_source` | `../../primitives/sns-topic` | Source for the sns-topic primitive module |
| `cloudwatch_source` | `../../primitives/cloudwatch` | Source for the cloudwatch primitive module |
| `cost_anomaly_source` | `../../primitives/cost-anomaly` | Source for the cost-anomaly primitive module |

By default (when `use_pinned_module_sources = false` in `account.hcl`), each source resolves to the
relative in-repo path above. When `use_pinned_module_sources = true` (prod), the terragrunt leaf
sets every `*_source` input to the pinned `git::https://github.com/example-org/telemetry-platform.git//<path>?ref=<path>/v<semver>`
URL. The toggle flows from `account.hcl` through `_envcommon/observability.hcl` to the leaf.
See `docs/terraform-module-sourcing.md` for the end-to-end workflow.

## Policies

- `source_policy.rego` -- in-repo child sources are `source = var.<name>_source` (var-driven, const=true); the external `budget` (terraform-modules) source remains a pinned literal; prod-context pin gate enforces that in-repo sources resolve to pinned git URLs when `use_pinned_module_sources = true`
- `no_resources_policy` -- the reference root declares no `resource` blocks
- `composition_policy` -- the reference root uses only `module` blocks for infrastructure
