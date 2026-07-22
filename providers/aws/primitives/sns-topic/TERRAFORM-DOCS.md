<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_sns_topic.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic) | resource |
| [aws_sns_topic_policy.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic_policy) | resource |
| [aws_sns_topic_subscription.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic_subscription) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_kms_key_id"></a> [kms\_key\_id](#input\_kms\_key\_id) | (Required) CMK id/ARN or alias for server-side encryption on the SNS topic. Must be a KMS ARN (arn:aws:kms:...) or alias (alias/...). | `string` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"sns-topic"` | no |
| <a name="input_policy_json"></a> [policy\_json](#input\_policy\_json) | (Optional) Optional SNS topic access policy as a JSON string (e.g. to allow CloudWatch or Cost Explorer to publish). When null, no explicit policy is applied. | `string` | `null` | no |
| <a name="input_subscribers"></a> [subscribers](#input\_subscribers) | (Optional) List of topic subscriptions. Each entry requires a protocol (one of email, email-json, https, sqs, lambda, sms) and a non-empty endpoint. | <pre>list(object({<br/>    protocol = string<br/>    endpoint = string<br/>  }))</pre> | `[]` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_topic_name"></a> [topic\_name](#input\_topic\_name) | (Required) SNS topic name. Must match pattern ^[A-Za-z0-9\_-]+$. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_topic_arn"></a> [topic\_arn](#output\_topic\_arn) | The ARN of the SNS topic. Passed to cloudwatch alarm\_actions/ok\_actions and to cost-anomaly as the SNS subscriber address. |
| <a name="output_topic_name"></a> [topic\_name](#output\_topic\_name) | The name of the SNS topic. |
<!-- END_TF_DOCS -->