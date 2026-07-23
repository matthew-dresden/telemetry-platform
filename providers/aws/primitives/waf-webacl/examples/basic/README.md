<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.52.0 |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_example"></a> [example](#module\_example) | ../../ | n/a |

## Resources

| Name | Type |
| ---- | ---- |
| [aws_kms_alias.logs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/kms_alias) | resource |
| [aws_kms_key.logs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/kms_key) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_create_log_group"></a> [create\_log\_group](#input\_create\_log\_group) | When true, the module creates its own aws-waf-logs-* CloudWatch log group as the logging destination (docs/terragrunt-concepts.md). | `bool` | `false` | no |
| <a name="input_default_action"></a> [default\_action](#input\_default\_action) | Default action. Valid values: allow, block. | `string` | `"allow"` | no |
| <a name="input_log_destination_arns"></a> [log\_destination\_arns](#input\_log\_destination\_arns) | External log destination ARNs (Kinesis, S3, or CloudWatch Logs). Used only when create\_log\_group is false. | `list(string)` | `[]` | no |
| <a name="input_log_kms_key_arn"></a> [log\_kms\_key\_arn](#input\_log\_kms\_key\_arn) | KMS key ARN for encrypting WAF logs. Required when logging\_enabled is true (per D2). | `string` | `null` | no |
| <a name="input_log_retention_in_days"></a> [log\_retention\_in\_days](#input\_log\_retention\_in\_days) | Retention for the module-owned CloudWatch log group when create\_log\_group is true. | `number` | `365` | no |
| <a name="input_logging_enabled"></a> [logging\_enabled](#input\_logging\_enabled) | Whether to attach a logging configuration. | `bool` | `false` | no |
| <a name="input_name"></a> [name](#input\_name) | Name for the WAFv2 web ACL. | `string` | n/a | yes |
| <a name="input_project_tag"></a> [project\_tag](#input\_project\_tag) | Project tag value applied to all resources via the provider default\_tags block. | `string` | n/a | yes |
| <a name="input_rate_limit_per_ip"></a> [rate\_limit\_per\_ip](#input\_rate\_limit\_per\_ip) | Rate-based rule limit: requests per 5-minute window per IP. | `number` | `2000` | no |
| <a name="input_scope"></a> [scope](#input\_scope) | Scope of the web ACL. Valid values: CLOUDFRONT, REGIONAL. | `string` | `"CLOUDFRONT"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | Additional tags applied to all resources. | `map(string)` | `{}` | no |
| <a name="input_terratest_run_id"></a> [terratest\_run\_id](#input\_terratest\_run\_id) | Terratest run identifier applied to all resources via the provider default\_tags block. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_log_group_arn"></a> [log\_group\_arn](#output\_log\_group\_arn) | ARN of the module-owned CloudWatch log group (null unless create\_log\_group is true). |
| <a name="output_log_group_name"></a> [log\_group\_name](#output\_log\_group\_name) | Name of the module-owned CloudWatch log group (null unless create\_log\_group is true). |
| <a name="output_rule_action_overrides"></a> [rule\_action\_overrides](#output\_rule\_action\_overrides) | Map of managed rule group name -> { sub-rule -> action } for the configured per-sub-rule overrides. Exposed so the terratest can assert the CommonRuleSet SizeRestrictions\_BODY sub-rule is set to count. |
| <a name="output_web_acl_arn"></a> [web\_acl\_arn](#output\_web\_acl\_arn) | The ARN of the WAFv2 web ACL. |
| <a name="output_web_acl_id"></a> [web\_acl\_id](#output\_web\_acl\_id) | The unique identifier of the WAFv2 web ACL. |
| <a name="output_web_acl_name"></a> [web\_acl\_name](#output\_web\_acl\_name) | The name of the WAFv2 web ACL. |
<!-- END_TF_DOCS -->