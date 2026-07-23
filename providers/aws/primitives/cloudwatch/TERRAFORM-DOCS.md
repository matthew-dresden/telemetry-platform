# cloudwatch -- terraform-docs reference

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
| aws_cloudwatch_metric_alarm.this | resource |
| aws_cloudwatch_dashboard.this | resource |
| aws_cloudwatch_log_group.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| alarms | Map of alarm name to metric alarm configuration. Each alarm must have non-empty alarm_actions and ok_actions (D41 fail-fast contract). | map(object) | {} | no |
| log_groups | Map of logical name to log group configuration. Each entry requires kms_key_id and a valid CloudWatch retention_in_days. | map(object) | {} | no |
| create_dashboard | Whether to create a CloudWatch dashboard. When true, dashboard_name must be non-null. | bool | false | no |
| dashboard_name | Name for the CloudWatch dashboard. Required (non-null) when create_dashboard is true. | string | null | no |
| dashboard_body | JSON body for the CloudWatch dashboard. Must be valid JSON when provided. | string | null | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "cloudwatch" | no |

## Outputs

| Name | Description |
|------|-------------|
| alarm_arns | Map of alarm name to CloudWatch metric alarm ARN. |
| log_group_arns | Map of logical log-group key to CloudWatch log group ARN. |
| dashboard_arn | ARN of the CloudWatch dashboard, or null when create_dashboard is false. |
| dashboard_name | Name of the CloudWatch dashboard, or null when create_dashboard is false. |

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
| [aws_cloudwatch_dashboard.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_dashboard) | resource |
| [aws_cloudwatch_log_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_cloudwatch_metric_alarm.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |

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

| Name | Description |
| ---- | ----------- |
| <a name="output_alarm_arns"></a> [alarm\_arns](#output\_alarm\_arns) | Map of alarm name to CloudWatch metric alarm ARN. |
| <a name="output_dashboard_arn"></a> [dashboard\_arn](#output\_dashboard\_arn) | ARN of the CloudWatch dashboard, or null when create\_dashboard is false. |
| <a name="output_dashboard_name"></a> [dashboard\_name](#output\_dashboard\_name) | Name of the CloudWatch dashboard, or null when create\_dashboard is false. |
| <a name="output_log_group_arns"></a> [log\_group\_arns](#output\_log\_group\_arns) | Map of logical log-group key to CloudWatch log group ARN. |
<!-- END_TF_DOCS -->