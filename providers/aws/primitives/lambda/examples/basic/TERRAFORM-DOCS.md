<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_example"></a> [example](#module\_example) | ../../ | n/a |

## Resources

No resources.

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_description"></a> [description](#input\_description) | Description of the Lambda function. | `string` | `"Example Lambda function for telemetry portal"` | no |
| <a name="input_environment_variables"></a> [environment\_variables](#input\_environment\_variables) | Map of environment variables for the Lambda function. | `map(string)` | `{}` | no |
| <a name="input_function_name"></a> [function\_name](#input\_function\_name) | Unique name for the Lambda function. | `string` | n/a | yes |
| <a name="input_handler"></a> [handler](#input\_handler) | Function entrypoint. Example: index.handler. | `string` | `"index.handler"` | no |
| <a name="input_memory_size"></a> [memory\_size](#input\_memory\_size) | Amount of memory in MB the function gets at runtime. | `number` | `128` | no |
| <a name="input_package_type"></a> [package\_type](#input\_package\_type) | Package type. Must be Zip. | `string` | `"Zip"` | no |
| <a name="input_project_tag"></a> [project\_tag](#input\_project\_tag) | Project tag value applied to all resources via the provider default\_tags block. | `string` | n/a | yes |
| <a name="input_role"></a> [role](#input\_role) | ARN of the IAM role to attach to the Lambda function. | `string` | n/a | yes |
| <a name="input_runtime"></a> [runtime](#input\_runtime) | Identifier of the Lambda runtime. | `string` | `"python3.12"` | no |
| <a name="input_s3_bucket"></a> [s3\_bucket](#input\_s3\_bucket) | S3 bucket containing the Lambda deployment artifact. | `string` | n/a | yes |
| <a name="input_s3_key"></a> [s3\_key](#input\_s3\_key) | S3 object key of the Lambda deployment artifact. | `string` | n/a | yes |
| <a name="input_source_code_hash"></a> [source\_code\_hash](#input\_source\_code\_hash) | Base64-encoded SHA256 hash of the deployment package. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | Additional tags applied to all resources. | `map(string)` | `{}` | no |
| <a name="input_terratest_run_id"></a> [terratest\_run\_id](#input\_terratest\_run\_id) | Terratest run identifier applied to all resources via the provider default\_tags block. | `string` | n/a | yes |
| <a name="input_timeout"></a> [timeout](#input\_timeout) | Function execution timeout in seconds. | `number` | `3` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_function_arn"></a> [function\_arn](#output\_function\_arn) | The ARN of the Lambda function. |
| <a name="output_function_name"></a> [function\_name](#output\_function\_name) | The name of the Lambda function. |
| <a name="output_invoke_arn"></a> [invoke\_arn](#output\_invoke\_arn) | The ARN for invoking the Lambda function from API Gateway. |
| <a name="output_memory_size"></a> [memory\_size](#output\_memory\_size) | The amount of memory in MB the function gets at runtime. |
| <a name="output_qualified_arn"></a> [qualified\_arn](#output\_qualified\_arn) | The qualified ARN of the Lambda function including the version. |
| <a name="output_timeout"></a> [timeout](#output\_timeout) | The function execution timeout in seconds. |
<!-- END_TF_DOCS -->