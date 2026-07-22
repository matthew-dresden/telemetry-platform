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
| [aws_lambda_function.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description of the Lambda function. | `string` | `""` | no |
| <a name="input_environment_variables"></a> [environment\_variables](#input\_environment\_variables) | (Optional) Map of environment variables for the Lambda function. When empty, no environment block is emitted. | `map(string)` | `{}` | no |
| <a name="input_function_name"></a> [function\_name](#input\_function\_name) | (Required) Unique name for the Lambda function. | `string` | n/a | yes |
| <a name="input_handler"></a> [handler](#input\_handler) | (Required) Function entrypoint in the code. Example: index.handler. | `string` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_memory_size"></a> [memory\_size](#input\_memory\_size) | (Optional) Amount of memory in MB the function gets at runtime (also scales CPU). Defaults to the AWS default of 128 (backward-compatible). Must be between 128 and 10240. | `number` | `128` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"lambda"` | no |
| <a name="input_package_type"></a> [package\_type](#input\_package\_type) | (Required) Package type for the Lambda deployment artifact. Valid value: Zip. | `string` | `"Zip"` | no |
| <a name="input_role"></a> [role](#input\_role) | (Required) ARN of the IAM role to attach to the Lambda function. Must match ^arn:aws:iam:. | `string` | n/a | yes |
| <a name="input_runtime"></a> [runtime](#input\_runtime) | (Required) Identifier of the Lambda runtime. Example: python3.12, nodejs20.x. | `string` | n/a | yes |
| <a name="input_s3_bucket"></a> [s3\_bucket](#input\_s3\_bucket) | (Required) S3 bucket containing the Lambda deployment artifact. | `string` | n/a | yes |
| <a name="input_s3_key"></a> [s3\_key](#input\_s3\_key) | (Required) S3 object key of the Lambda deployment artifact. | `string` | n/a | yes |
| <a name="input_source_code_hash"></a> [source\_code\_hash](#input\_source\_code\_hash) | (Required) Base64-encoded SHA256 hash of the deployment package to detect changes. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_timeout"></a> [timeout](#input\_timeout) | (Optional) Function execution timeout in seconds. Defaults to the AWS default of 3 (backward-compatible for existing callers). Callers whose handler makes external API calls (e.g. the portal embed-URL Lambda calling QuickSight + SSM) must raise this above 3 or the function times out before the API returns. Must be between 1 and 900. | `number` | `3` | no |
| <a name="input_tracing_mode"></a> [tracing\_mode](#input\_tracing\_mode) | (Optional) AWS X-Ray tracing mode for the function. Defaults to Active (secure-by-default) so requests are sampled and traced. Must be one of Active or PassThrough. | `string` | `"Active"` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_function_arn"></a> [function\_arn](#output\_function\_arn) | The Amazon Resource Name (ARN) of the Lambda function. |
| <a name="output_function_name"></a> [function\_name](#output\_function\_name) | The name of the Lambda function. |
| <a name="output_invoke_arn"></a> [invoke\_arn](#output\_invoke\_arn) | The ARN to be used for invoking the Lambda function from API Gateway. |
| <a name="output_memory_size"></a> [memory\_size](#output\_memory\_size) | The amount of memory in MB the function gets at runtime. |
| <a name="output_qualified_arn"></a> [qualified\_arn](#output\_qualified\_arn) | The qualified ARN of the Lambda function including the version. |
| <a name="output_timeout"></a> [timeout](#output\_timeout) | The function execution timeout in seconds. |
<!-- END_TF_DOCS -->