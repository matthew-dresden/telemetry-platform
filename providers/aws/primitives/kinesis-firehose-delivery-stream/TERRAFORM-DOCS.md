# kinesis-firehose-delivery-stream -- terraform-docs reference

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
| aws_kinesis_firehose_delivery_stream.this | resource |
| aws_cloudwatch_log_group.this | resource |
| aws_cloudwatch_log_stream.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | The name of the Firehose delivery stream. Must contain only alphanumeric characters, underscores, hyphens, and dots. | string | n/a | yes |
| destination | Destination type. Only extended_s3 is supported for the telemetry data lake. | string | "extended_s3" | no |
| role_arn | ARN of the IAM role that grants Firehose permission to write to S3. Must match ^arn:aws:iam::. | string | n/a | yes |
| bucket_arn | ARN of the target S3 data lake bucket. Must match ^arn:aws:s3:::. | string | n/a | yes |
| prefix | S3 object prefix for successful records (supports dynamic partitioning expressions). | string | "raw/" | no |
| error_output_prefix | S3 prefix for failed records. | string | "errors/" | no |
| buffering_size | Buffer size in MB before delivery. Must be between 1 and 128 (AWS allowed range). | number | 64 | no |
| buffering_interval | Buffer time in seconds before delivery. Must be between 60 and 900 (AWS allowed range). | number | 300 | no |
| compression_format | Compression format for S3 objects. Use UNCOMPRESSED when format conversion to Parquet is enabled. | string | "UNCOMPRESSED" | no |
| kms_key_arn | ARN of the KMS key used for server-side encryption of Firehose delivery. Must match ^arn:aws:kms:. | string | n/a | yes |
| enable_format_conversion | When true, enables JSON-to-Parquet format conversion using the Glue table referenced in data_format_conversion. | bool | true | no |
| data_format_conversion | Glue schema source for Parquet conversion (the Glue catalog table B4). Required when enable_format_conversion is true. | object | null | no |
| dynamic_partitioning_enabled | When true, enables dynamic partitioning using JQ expressions from dynamic_partitioning_jq. | bool | true | no |
| dynamic_partitioning_jq | JQ expressions for dynamic-partitioning metadata extraction. Each value must be non-empty (T27). | map(string) | { tool = ".tool", date = "%Y/%m/%d" } | no |
| transform_lambda_arn | ARN of a Firehose transform Lambda prepended as the record-splitting front end for a CloudWatch Logs subscription source. The Lambda decompresses + envelope-strips each record and re-ingests each logEvents[].message as its own single-JSON record via firehose:PutRecordBatch (no 500-record cap), so any-size delivery is split before MetadataExtraction. The delivery role must hold lambda:InvokeFunction + lambda:GetFunctionConfiguration. | string | null | no |
| cloudwatch_logging_enabled | When true, enables CloudWatch error logging for failed delivery records. | bool | true | no |
| log_retention_in_days | CloudWatch log group retention in days. Must be one of the AWS allowed values. | number | 30 | no |
| tags | Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | Value for the Module tag identifying the source module. | string | "kinesis-firehose-delivery-stream" | no |

## Outputs

| Name | Description |
|------|-------------|
| delivery_stream_arn | The Amazon Resource Name (ARN) of the Firehose delivery stream (ADOT exporter target). |
| delivery_stream_name | The name of the Firehose delivery stream. |
| log_group_name | The name of the CloudWatch log group for delivery errors. Null when cloudwatch_logging_enabled is false. |

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
| [aws_cloudwatch_log_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_cloudwatch_log_stream.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_stream) | resource |
| [aws_kinesis_firehose_delivery_stream.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/kinesis_firehose_delivery_stream) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_bucket_arn"></a> [bucket\_arn](#input\_bucket\_arn) | (Required) ARN of the target S3 data lake bucket. Must match ^arn:aws:s3:::. | `string` | n/a | yes |
| <a name="input_buffering_interval"></a> [buffering\_interval](#input\_buffering\_interval) | (Optional) Buffer time in seconds before delivery. Must be between 60 and 900 (AWS allowed range). | `number` | `300` | no |
| <a name="input_buffering_size"></a> [buffering\_size](#input\_buffering\_size) | (Optional) Buffer size in MB before delivery. Must be between 1 and 128 (AWS allowed range). | `number` | `64` | no |
| <a name="input_cloudwatch_logging_enabled"></a> [cloudwatch\_logging\_enabled](#input\_cloudwatch\_logging\_enabled) | (Optional) When true, enables CloudWatch error logging for failed delivery records. | `bool` | `true` | no |
| <a name="input_compression_format"></a> [compression\_format](#input\_compression\_format) | (Optional) Compression format for S3 objects. Use UNCOMPRESSED when format conversion to Parquet is enabled. | `string` | `"UNCOMPRESSED"` | no |
| <a name="input_data_format_conversion"></a> [data\_format\_conversion](#input\_data\_format\_conversion) | (Optional) Glue schema source for Parquet conversion (the Glue catalog table B4). Required when enable\_format\_conversion is true. | <pre>object({<br/>    glue_database_name = string<br/>    glue_table_name    = string<br/>    glue_role_arn      = string<br/>  })</pre> | `null` | no |
| <a name="input_destination"></a> [destination](#input\_destination) | (Optional) Destination type. Only extended\_s3 is supported for the telemetry data lake. | `string` | `"extended_s3"` | no |
| <a name="input_dynamic_partitioning_enabled"></a> [dynamic\_partitioning\_enabled](#input\_dynamic\_partitioning\_enabled) | (Optional) When true, enables dynamic partitioning using JQ expressions from dynamic\_partitioning\_jq. | `bool` | `true` | no |
| <a name="input_dynamic_partitioning_jq"></a> [dynamic\_partitioning\_jq](#input\_dynamic\_partitioning\_jq) | (Optional) JQ expressions for dynamic-partitioning metadata extraction. Each value must be non-empty. Keys are used as partition metadata extraction names in the Firehose processor (T27: these are input-driven, never hardcoded in main.tf). | `map(string)` | <pre>{<br/>  "date": "%Y/%m/%d",<br/>  "tool": ".tool"<br/>}</pre> | no |
| <a name="input_enable_format_conversion"></a> [enable\_format\_conversion](#input\_enable\_format\_conversion) | (Optional) When true, enables JSON-to-Parquet format conversion using the Glue table referenced in data\_format\_conversion. | `bool` | `true` | no |
| <a name="input_error_output_prefix"></a> [error\_output\_prefix](#input\_error\_output\_prefix) | (Optional) S3 prefix for failed records. | `string` | `"errors/"` | no |
| <a name="input_kms_key_arn"></a> [kms\_key\_arn](#input\_kms\_key\_arn) | (Required) ARN of the KMS key used for server-side encryption of Firehose delivery. Must match ^arn:aws:kms:. | `string` | n/a | yes |
| <a name="input_log_retention_in_days"></a> [log\_retention\_in\_days](#input\_log\_retention\_in\_days) | (Optional) CloudWatch log group retention in days. Must be one of the AWS allowed values. | `number` | `30` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"kinesis-firehose-delivery-stream"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) The name of the Firehose delivery stream. Must contain only alphanumeric characters, underscores, hyphens, and dots. | `string` | n/a | yes |
| <a name="input_prefix"></a> [prefix](#input\_prefix) | (Optional) S3 object prefix for successful records (supports dynamic partitioning expressions). | `string` | `"raw/"` | no |
| <a name="input_role_arn"></a> [role\_arn](#input\_role\_arn) | (Required) ARN of the IAM role that grants Firehose permission to write to S3. Must match ^arn:aws:iam::. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_transform_lambda_arn"></a> [transform\_lambda\_arn](#input\_transform\_lambda\_arn) | (Optional) ARN of a Firehose transform Lambda prepended to the processing\_configuration as the record-splitting front end for a CloudWatch Logs subscription source. The Lambda decompresses + envelope-strips each record and re-ingests each logEvents[].message as its own single-JSON record via firehose:PutRecordBatch (no 500-record cap), so a delivery of any size is split into individual JSON records before MetadataExtraction. The delivery role must hold lambda:InvokeFunction + lambda:GetFunctionConfiguration. Defaults to null. | `string` | `null` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_delivery_stream_arn"></a> [delivery\_stream\_arn](#output\_delivery\_stream\_arn) | The Amazon Resource Name (ARN) of the Firehose delivery stream (ADOT exporter target). |
| <a name="output_delivery_stream_name"></a> [delivery\_stream\_name](#output\_delivery\_stream\_name) | The name of the Firehose delivery stream. |
| <a name="output_log_group_name"></a> [log\_group\_name](#output\_log\_group\_name) | The name of the CloudWatch log group for delivery errors. Null when cloudwatch\_logging\_enabled is false. |
<!-- END_TF_DOCS -->
