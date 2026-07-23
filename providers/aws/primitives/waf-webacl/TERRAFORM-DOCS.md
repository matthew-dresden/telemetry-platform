# waf-webacl -- terraform-docs reference

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.49.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_wafv2_web_acl.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/wafv2_web_acl) | resource |
| [aws_wafv2_web_acl_logging_configuration.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/wafv2_web_acl_logging_configuration) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_default_action"></a> [default\_action](#input\_default\_action) | (Required) Default action for the WAFv2 web ACL. Valid values: allow, block. | `string` | n/a | yes |
| <a name="input_log_destination_arns"></a> [log\_destination\_arns](#input\_log\_destination\_arns) | (Optional) List of CloudWatch Logs, Kinesis Data Firehose, or S3 log destination ARNs for the WAFv2 logging configuration. Must contain at least one entry for a valid AWS deployment when logging\_enabled is true; the AWS provider requires at least one destination at plan time. | `list(string)` | `[]` | no |
| <a name="input_log_kms_key_arn"></a> [log\_kms\_key\_arn](#input\_log\_kms\_key\_arn) | (Optional) ARN of the KMS key used to enforce CMK encryption on the log destination. Required when logging\_enabled is true (decision D2). Must match ^arn:aws:kms: when provided. The WAFv2 resource itself has no native KMS field; supply this ARN to the destination (e.g., Kinesis Firehose) to enforce encryption at rest. | `string` | `null` | no |
| <a name="input_logging_enabled"></a> [logging\_enabled](#input\_logging\_enabled) | (Required) Whether to attach a WAFv2 logging configuration. When true, log\_kms\_key\_arn must be supplied (decision D2). log\_destination\_arns must also be supplied for a valid AWS deployment; it is not validated here because portal callers may omit it at module-composition time and supply it via the outer stack. | `bool` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_managed_rule_groups"></a> [managed\_rule\_groups](#input\_managed\_rule\_groups) | (Required) List of AWS managed rule groups to attach. Each entry specifies name, vendor\_name, priority, and override\_action (none or count). | <pre>list(object({<br/>    name            = string<br/>    vendor_name     = string<br/>    priority        = number<br/>    override_action = string<br/>  }))</pre> | n/a | yes |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"waf-webacl"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) Name for the WAFv2 web ACL. | `string` | n/a | yes |
| <a name="input_rate_limit_per_ip"></a> [rate\_limit\_per\_ip](#input\_rate\_limit\_per\_ip) | (Required) Rate-based rule limit: maximum requests per 5-minute window per IP. Must be a positive integer. | `number` | n/a | yes |
| <a name="input_scope"></a> [scope](#input\_scope) | (Required) Scope of the WAFv2 web ACL. Valid values: CLOUDFRONT, REGIONAL. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_web_acl_arn"></a> [web\_acl\_arn](#output\_web\_acl\_arn) | The Amazon Resource Name (ARN) of the WAFv2 web ACL. Consumed by CloudFront as web\_acl\_id. |
| <a name="output_web_acl_capacity"></a> [web\_acl\_capacity](#output\_web\_acl\_capacity) | The web ACL capacity units (WCU) currently being used by this web ACL. |
| <a name="output_web_acl_id"></a> [web\_acl\_id](#output\_web\_acl\_id) | The unique identifier of the WAFv2 web ACL. |
| <a name="output_web_acl_name"></a> [web\_acl\_name](#output\_web\_acl\_name) | The name of the WAFv2 web ACL. |
<!-- END_TF_DOCS -->
