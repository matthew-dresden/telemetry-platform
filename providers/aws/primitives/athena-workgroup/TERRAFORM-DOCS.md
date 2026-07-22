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
| [aws_athena_workgroup.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/athena_workgroup) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_bytes_scanned_cutoff_per_query"></a> [bytes\_scanned\_cutoff\_per\_query](#input\_bytes\_scanned\_cutoff\_per\_query) | (Required) Maximum number of bytes scanned per query. Queries exceeding this limit are cancelled. Minimum value is 10485760 (10 MB) per the AWS API contract. | `number` | n/a | yes |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description for the Athena workgroup. | `string` | `"Telemetry analytics Athena workgroup"` | no |
| <a name="input_enforce_workgroup_configuration"></a> [enforce\_workgroup\_configuration](#input\_enforce\_workgroup\_configuration) | (Optional) When true, enforces workgroup configuration over client-side settings. Prevents clients from overriding query result location, encryption, or byte cutoff. | `bool` | `true` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"athena-workgroup"` | no |
| <a name="input_publish_cloudwatch_metrics_enabled"></a> [publish\_cloudwatch\_metrics\_enabled](#input\_publish\_cloudwatch\_metrics\_enabled) | (Optional) When true, publishes query metrics to CloudWatch. | `bool` | `true` | no |
| <a name="input_result_kms_key_arn"></a> [result\_kms\_key\_arn](#input\_result\_kms\_key\_arn) | (Required) ARN of the KMS key used for SSE-KMS encryption of Athena query results. Must be a valid KMS key ARN. | `string` | n/a | yes |
| <a name="input_result_s3_bucket"></a> [result\_s3\_bucket](#input\_result\_s3\_bucket) | (Required) The S3 bucket name (without s3:// prefix) where Athena writes query results. Must exist before the workgroup is created. | `string` | n/a | yes |
| <a name="input_result_s3_key_prefix"></a> [result\_s3\_key\_prefix](#input\_result\_s3\_key\_prefix) | (Optional) S3 key prefix within the result bucket where Athena writes query results. | `string` | `"athena-results/"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_workgroup_name"></a> [workgroup\_name](#input\_workgroup\_name) | (Required) The name of the Athena workgroup. Must contain only alphanumeric characters, hyphens, or underscores. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_workgroup_arn"></a> [workgroup\_arn](#output\_workgroup\_arn) | The Amazon Resource Name (ARN) of the Athena workgroup. |
| <a name="output_workgroup_id"></a> [workgroup\_id](#output\_workgroup\_id) | The name of the Athena workgroup (used as its identifier). |
| <a name="output_workgroup_name"></a> [workgroup\_name](#output\_workgroup\_name) | The name of the Athena workgroup. |
<!-- END_TF_DOCS -->