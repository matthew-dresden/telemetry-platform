<!-- BEGIN_TF_DOCS -->
# cloudwatch

Manages CloudWatch metric alarms, an optional dashboard, and standalone log groups for the telemetry collector platform. The module is the single owner of platform-wide CloudWatch observability resources (ADOT collector, ALB, and Firehose delivery stream log groups).

Every declared alarm must route to a real SNS sink: `alarm_actions` and `ok_actions` are required non-empty inputs per decision D41. All log groups are encrypted at rest using a caller-supplied KMS key.

Each alarm must set **exactly one** of `statistic` (one of `SampleCount`, `Average`, `Sum`, `Minimum`, `Maximum`) or `extended_statistic` (a percentile such as `p95`). `aws_cloudwatch_metric_alarm` rejects setting both or neither, so the module fails fast at plan time when that contract is violated.

## Resources managed

- `aws_cloudwatch_metric_alarm` -- one per entry in the `alarms` map
- `aws_cloudwatch_dashboard` -- optional, gated by `create_dashboard` (default `false`)
- `aws_cloudwatch_log_group` -- one per entry in the `log_groups` map, each KMS-encrypted

## Usage

```hcl
module "cloudwatch" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/cloudwatch?ref=providers/aws/primitives/cloudwatch/v0.1.0"

  alarms = {
    high_cpu = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 2
      metric_name         = "CPUUtilization"
      namespace           = "AWS/ECS"
      period              = 60
      statistic           = "Average"
      threshold           = 80
      alarm_actions       = [module.sns_topic.topic_arn]
      ok_actions          = [module.sns_topic.topic_arn]
    }
    # Percentile SLO alarm: set extended_statistic (e.g. p95) instead of statistic.
    alb_latency_p95 = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 3
      metric_name         = "TargetResponseTime"
      namespace           = "AWS/ApplicationELB"
      period              = 300
      extended_statistic  = "p95"
      threshold           = 2
      alarm_actions       = [module.sns_topic.topic_arn]
      ok_actions          = [module.sns_topic.topic_arn]
    }
  }

  log_groups = {
    adot_collector = {
      name              = "/telemetry/adot-collector"
      retention_in_days = 365
      kms_key_id        = module.kms_key.key_arn
    }
  }

  create_dashboard = true
  dashboard_name   = "telemetry-collector"
  dashboard_body   = jsonencode({ widgets = [] })

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- two alarms + two log groups, no dashboard
- `examples/with-dashboard` -- alarms + log groups + a dashboard

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
| [aws_cloudwatch_dashboard.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_dashboard) | resource |
| [aws_cloudwatch_log_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_cloudwatch_metric_alarm.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_alarms"></a> [alarms](#input\_alarms) | (Optional) Map of alarm name to metric alarm configuration. Each alarm must have non-empty alarm\_actions and ok\_actions wired to an SNS topic ARN (D41 fail-fast contract). Each alarm must set EXACTLY ONE of statistic (one of SampleCount, Average, Sum, Minimum, Maximum) or extended\_statistic (a percentile such as p95) -- aws\_cloudwatch\_metric\_alarm rejects setting both or neither. | <pre>map(object({<br/>    comparison_operator = string<br/>    evaluation_periods  = number<br/>    metric_name         = string<br/>    namespace           = string<br/>    period              = number<br/>    statistic           = optional(string)<br/>    extended_statistic  = optional(string)<br/>    threshold           = number<br/>    alarm_description   = optional(string, "")<br/>    dimensions          = optional(map(string), {})<br/>    alarm_actions       = list(string)<br/>    ok_actions          = list(string)<br/>    treat_missing_data  = optional(string, "missing")<br/>  }))</pre> | `{}` | no |
| <a name="input_create_dashboard"></a> [create\_dashboard](#input\_create\_dashboard) | (Optional) Whether to create a CloudWatch dashboard. When true, dashboard\_name must be non-null. | `bool` | `false` | no |
| <a name="input_dashboard_body"></a> [dashboard\_body](#input\_dashboard\_body) | (Optional) JSON body for the CloudWatch dashboard. Must be valid JSON when provided. | `string` | `null` | no |
| <a name="input_dashboard_name"></a> [dashboard\_name](#input\_dashboard\_name) | (Optional) Name for the CloudWatch dashboard. Required (non-null) when create\_dashboard is true. | `string` | `null` | no |
| <a name="input_log_groups"></a> [log\_groups](#input\_log\_groups) | (Optional) Map of logical name to log group configuration. Each log group requires a kms\_key\_id for encryption at rest and a retention\_in\_days from the valid CloudWatch set. | <pre>map(object({<br/>    name              = string<br/>    retention_in_days = number<br/>    kms_key_id        = string<br/>  }))</pre> | `{}` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"cloudwatch"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_alarm_arns"></a> [alarm\_arns](#output\_alarm\_arns) | Map of alarm name to CloudWatch metric alarm ARN. |
| <a name="output_dashboard_arn"></a> [dashboard\_arn](#output\_dashboard\_arn) | ARN of the CloudWatch dashboard, or null when create\_dashboard is false. |
| <a name="output_dashboard_name"></a> [dashboard\_name](#output\_dashboard\_name) | Name of the CloudWatch dashboard, or null when create\_dashboard is false. |
| <a name="output_log_group_arns"></a> [log\_group\_arns](#output\_log\_group\_arns) | Map of logical log-group key to CloudWatch log group ARN. |

## Input validation

- `alarms`: every entry must have `evaluation_periods >= 1` and `period >= 10`.
- `alarms`: every entry must have non-empty `alarm_actions` and `ok_actions`. An empty list is a D41 contract violation.
- `log_groups`: every entry must specify a `kms_key_id`. Log groups without KMS encryption are a security violation.
- `log_groups`: every entry must have `retention_in_days` from the valid CloudWatch set: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653.
- `dashboard_name` must be non-null when `create_dashboard` is true.
- `dashboard_body`, when provided, must be valid JSON.
<!-- END_TF_DOCS -->