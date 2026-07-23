<!-- BEGIN_TF_DOCS -->
# cost-anomaly

Manages an AWS Cost Explorer anomaly monitor and its alert subscription.

Used by the observability reference module to detect unexpected spend spikes. This is the sole module for Cost Anomaly Detection and is composed by the observability reference module.

## Resources managed

- `aws_ce_anomaly_monitor` -- the anomaly monitor (DIMENSIONAL or CUSTOM)
- `aws_ce_anomaly_subscription` -- the alert subscription wired to the monitor

## Usage

```hcl
module "cost_anomaly" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/cost-anomaly?ref=providers/aws/primitives/cost-anomaly/v0.1.0"

  monitor_name      = "platform-spend-monitor"
  subscription_name = "platform-spend-alerts"
  threshold_expression = jsonencode({
    Dimensions = {
      Key          = "ANOMALY_TOTAL_IMPACT_PERCENTAGE"
      Values       = ["10"]
      MatchOptions = ["GREATER_THAN_OR_EQUAL"]
    }
  })
  subscribers = [
    {
      address = module.sns_topic.topic_arn
      type    = "SNS"
    }
  ]
}
```

## Examples

- `examples/default` -- DIMENSIONAL monitor with DAILY frequency and an SNS subscriber wired to the sns-topic topic_arn

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
| [aws_ce_anomaly_monitor.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ce_anomaly_monitor) | resource |
| [aws_ce_anomaly_subscription.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ce_anomaly_subscription) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"cost-anomaly"` | no |
| <a name="input_monitor_name"></a> [monitor\_name](#input\_monitor\_name) | (Required) Display name for the anomaly monitor. Must be non-empty. | `string` | n/a | yes |
| <a name="input_monitor_specification"></a> [monitor\_specification](#input\_monitor\_specification) | (Optional) Cost expression JSON required when monitor\_type is CUSTOM; ignored for DIMENSIONAL. | `string` | `null` | no |
| <a name="input_monitor_type"></a> [monitor\_type](#input\_monitor\_type) | (Optional) Monitor type. DIMENSIONAL monitors all AWS services; CUSTOM allows a cost-category filter expression via monitor\_specification. | `string` | `"DIMENSIONAL"` | no |
| <a name="input_subscribers"></a> [subscribers](#input\_subscribers) | (Required) Alert destinations. Must be non-empty. Each subscriber requires a non-empty address and a type of EMAIL or SNS. | <pre>list(object({<br/>    address = string<br/>    type    = string<br/>  }))</pre> | n/a | yes |
| <a name="input_subscription_frequency"></a> [subscription\_frequency](#input\_subscription\_frequency) | (Optional) How often alerts are delivered. Valid values: DAILY, IMMEDIATE, WEEKLY. | `string` | `"DAILY"` | no |
| <a name="input_subscription_name"></a> [subscription\_name](#input\_subscription\_name) | (Required) Display name for the alert subscription. Must be non-empty. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all taggable resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_threshold_expression"></a> [threshold\_expression](#input\_threshold\_expression) | (Required) JSON cost-expression defining the spend anomaly threshold. Must be valid JSON containing a Dimensions object with Key, Values, and MatchOptions fields. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_monitor_arn"></a> [monitor\_arn](#output\_monitor\_arn) | The ARN of the anomaly monitor. |
| <a name="output_subscription_arn"></a> [subscription\_arn](#output\_subscription\_arn) | The ARN of the alert subscription. |

## Input validation

- `monitor_name` must be non-empty. Plans fail for empty strings.
- `monitor_type` must be one of `DIMENSIONAL` or `CUSTOM`.
- `monitor_specification` must be provided when `monitor_type` is `CUSTOM`.
- `subscription_name` must be non-empty. Plans fail for empty strings.
- `subscription_frequency` must be one of `DAILY`, `IMMEDIATE`, or `WEEKLY`.
- `threshold_expression` must be valid JSON containing a `Dimensions` object with `Key`, `Values`, and `MatchOptions` fields. Plans fail for malformed JSON or missing required keys.
- `subscribers` must be non-empty -- anomaly alerts must always have a destination (fail-fast).
- Each subscriber `type` must be one of `EMAIL` or `SNS`.
- Each subscriber `address` must be non-empty.
<!-- END_TF_DOCS -->