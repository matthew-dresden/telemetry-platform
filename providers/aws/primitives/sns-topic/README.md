<!-- BEGIN_TF_DOCS -->
# sns-topic

Manages a KMS-encrypted SNS topic with its subscriptions and access policy.

Used as the notification sink for CloudWatch alarm actions and cost-anomaly SNS subscriber in the observability reference module. A CloudWatch metric alarm has no email field; it can only notify via an SNS topic ARN, so this topic is the mandatory delivery path for platform alerts.

## Resources managed

- `aws_sns_topic` -- the KMS-encrypted SNS topic
- `aws_sns_topic_subscription` -- one subscription per entry in `subscribers`
- `aws_sns_topic_policy` -- the optional access policy when `policy_json` is provided

## Usage

```hcl
module "sns_topic" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/sns-topic?ref=providers/aws/primitives/sns-topic/v0.1.0"

  topic_name = "platform-alerts"
  kms_key_id = module.kms_key.key_arn
  subscribers = [
    {
      protocol = "email"
      endpoint = "ops@example.com"
    }
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- KMS-encrypted topic with no subscriptions
- `examples/with-subscriptions` -- topic with an email subscription and a topic policy

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
| [aws_sns_topic.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic) | resource |
| [aws_sns_topic_policy.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic_policy) | resource |
| [aws_sns_topic_subscription.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic_subscription) | resource |

## Inputs

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

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_topic_arn"></a> [topic\_arn](#output\_topic\_arn) | The ARN of the SNS topic. Passed to cloudwatch alarm\_actions/ok\_actions and to cost-anomaly as the SNS subscriber address. |
| <a name="output_topic_name"></a> [topic\_name](#output\_topic\_name) | The name of the SNS topic. |

## Input validation

- `topic_name` must match `^[A-Za-z0-9_-]+$`. Plans fail if the pattern is not satisfied.
- `kms_key_id` must start with `arn:aws:kms:` or `alias/`. Plans fail if the topic would be unencrypted.
- Each `subscribers` entry `protocol` must be one of: `email`, `email-json`, `https`, `sqs`, `lambda`, `sms`.
- Each `subscribers` entry `endpoint` must be non-empty.
- `policy_json`, when provided, must be valid JSON. Plans fail for malformed JSON.
<!-- END_TF_DOCS -->