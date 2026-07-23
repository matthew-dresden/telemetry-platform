<!-- BEGIN_TF_DOCS -->
# kinesis-firehose-delivery-stream

Manages a Kinesis Data Firehose delivery stream that buffers OTLP-derived JSON, converts to Parquet via a Glue catalog table, partitions by `tool`/date using JQ-driven inputs, and writes to the S3 data lake with SSE-KMS (spec 5.A/5.B, resource I7).

The module provisions an `extended_s3` destination only. Dynamic partitioning expressions are sourced from the `dynamic_partitioning_jq` input (`map(string)`) rather than hardcoded in `main.tf` (T27). When `enable_format_conversion` is true, the `data_format_conversion` variable must reference the Glue database and table produced by the `glue-catalog-database` primitive -- the Glue table is the single source of truth for the Parquet schema (spec 1.B B4).

## Resources managed

- `aws_kinesis_firehose_delivery_stream` -- the delivery stream with `extended_s3` destination, SSE-KMS, optional dynamic partitioning, and optional JSON-to-Parquet format conversion
- `aws_cloudwatch_log_group` -- delivery error log group (created only when `cloudwatch_logging_enabled = true`)
- `aws_cloudwatch_log_stream` -- delivery error log stream (created only when `cloudwatch_logging_enabled = true`)

## Usage

```hcl
module "firehose" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/kinesis-firehose-delivery-stream?ref=providers/aws/primitives/kinesis-firehose-delivery-stream/v0.1.0"

  name        = "telemetry-delivery"
  bucket_arn  = module.lake_bucket.bucket_arn
  role_arn    = module.firehose_role.role_arn
  kms_key_arn = module.lake_kms.key_arn

  enable_format_conversion = true
  data_format_conversion = {
    glue_database_name = module.catalog.database_name
    glue_table_name    = module.catalog.table_name
    glue_role_arn      = module.glue_role.role_arn
  }

  dynamic_partitioning_enabled = true
  dynamic_partitioning_jq = {
    tool = ".tool"
    date = "%Y/%m/%d"
  }

  prefix              = "raw/tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp:yyyy-MM-dd}/"
  error_output_prefix = "errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/"

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- `extended_s3` destination with format conversion and dynamic partitioning disabled; self-contained: creates its own S3 bucket, KMS key/alias, and IAM role inline; only input is `stream_name` and tag vars
- `examples/parquet-partitioned` -- format conversion ON against a Glue database/table, dynamic partitioning ON with an input-driven `dynamic_partitioning_jq` map; self-contained: creates its own S3 bucket, KMS key/alias, Glue database/table, and IAM roles inline; only input is `stream_name` and tag vars

## dynamic_partitioning_jq contract (T27)

The `dynamic_partitioning_jq` variable (`map(string)`, default `{ tool = ".tool", date = "%Y/%m/%d" }`) supplies the JQ metadata extraction expressions used by the Firehose `MetadataExtraction` processor. AWS allows exactly one `MetadataExtraction` processor per delivery stream, so all map entries are merged into a single combined JQ query of the form `{key1:val1,key2:val2,...}`. The key is the partition metadata name and the value is the JQ expression or timestamp pattern. No partition key expressions are hardcoded in `main.tf` -- all partitioning behavior is driven by this input.

Validation rejects any empty value in the map. A non-empty JQ expression is required for each key.

## Record-splitting transform Lambda (transform_lambda_arn)

When the delivery stream's source is a CloudWatch Logs subscription filter, set
`transform_lambda_arn` to the ARN of a Firehose transform Lambda that splits each delivery
into individual JSON records. CloudWatch Logs subscription records arrive GZIP-compressed and
wrapped in the `{messageType, owner, logGroup, logStream, subscriptionFilters, logEvents[]}`
envelope, and a single record batches MANY `logEvents`.

The AWS-native unwrap path (`Decompression` -> `CloudWatchLogProcessing` ->
`RecordDeAggregation`) cannot be used at scale: `RecordDeAggregation` is hard-capped at **500
sub-records per record**, so a delivery whose record carries more than 500 events is passed
WHOLE to the dynamic-partitioning `MetadataExtraction` JQ engine, which rejects the
multi-object blob with `Non JSON record provided`
(`DynamicPartitioning.MetadataExtractionFailed`) and routes EVERY event to
`errors/metadata-extraction-failed/`.

Because a Firehose transform Lambda is strictly 1:1 (it cannot emit more records than it
received), the Lambda instead decompresses and envelope-strips each record and RE-INGESTS each
`logEvents[].message` as its own single-JSON record via `firehose:PutRecordBatch` (marking the
original aggregated record `Dropped`). Re-ingestion has no 500-record cap, so a CloudWatch Logs
delivery of ANY size is split into individual JSON records. The resulting ordered processor
list is `Lambda -> MetadataExtraction`. The Firehose delivery role (`role_arn`) must hold
`lambda:InvokeFunction` + `lambda:GetFunctionConfiguration` on the Lambda. `transform_lambda_arn`
defaults to `null`, leaving the standalone fixtures with their single `MetadataExtraction`
processor unchanged.

## data_format_conversion and Glue coupling (B4)

When `enable_format_conversion = true`, the `data_format_conversion` object must reference the Glue catalog database and table created by the `glue-catalog-database` primitive. The Glue table is the authoritative Parquet schema definition. The `data_format_conversion.glue_role_arn` must belong to a role with `glue:GetTable`, `glue:GetTableVersion`, and `glue:GetTableVersions` permissions on the referenced database and table.

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
| [aws_cloudwatch_log_group.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_cloudwatch_log_stream.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_stream) | resource |
| [aws_kinesis_firehose_delivery_stream.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/kinesis_firehose_delivery_stream) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_bucket_arn"></a> [bucket\_arn](#input\_bucket\_arn) | (Required) ARN of the target S3 data lake bucket. Must match ^arn:aws:s3:::. | `string` | n/a | yes |
| <a name="input_buffering_interval"></a> [buffering\_interval](#input\_buffering\_interval) | (Optional) Buffer time in seconds before delivery. Must be between 60 and 900 (AWS allowed range). | `number` | `300` | no |
| <a name="input_buffering_size"></a> [buffering\_size](#input\_buffering\_size) | (Optional) Buffer size in MB before delivery. Must be between 1 and 128 (AWS allowed range). | `number` | `64` | no |
| <a name="input_cloudwatch_logging_enabled"></a> [cloudwatch\_logging\_enabled](#input\_cloudwatch\_logging\_enabled) | (Optional) When true, enables CloudWatch error logging for failed delivery records. | `bool` | `true` | no |
| <a name="input_compression_format"></a> [compression\_format](#input\_compression\_format) | (Optional) Compression format for S3 objects. Use UNCOMPRESSED when format conversion to Parquet is enabled. | `string` | `"UNCOMPRESSED"` | no |
| <a name="input_create_timeout"></a> [create\_timeout](#input\_create\_timeout) | (Optional) Bounded timeout the AWS provider waits for the delivery stream to reach ACTIVE before failing fast. Overrides the provider's 30m default so a stream stuck in CREATING surfaces a clear timeout error instead of silently blocking. Format: a Go duration ending in s, m, or h (e.g. 10m). | `string` | `"10m"` | no |
| <a name="input_data_format_conversion"></a> [data\_format\_conversion](#input\_data\_format\_conversion) | (Optional) Glue schema source for Parquet conversion (the Glue catalog table B4). Required when enable\_format\_conversion is true. | <pre>object({<br/>    glue_database_name = string<br/>    glue_table_name    = string<br/>    glue_role_arn      = string<br/>  })</pre> | `null` | no |
| <a name="input_delete_timeout"></a> [delete\_timeout](#input\_delete\_timeout) | (Optional) Bounded timeout the AWS provider waits for the delivery stream to be fully deleted before failing fast. Overrides the provider's 30m default so a stream stuck in DELETING surfaces a clear timeout error instead of silently blocking. Format: a Go duration ending in s, m, or h (e.g. 10m). | `string` | `"10m"` | no |
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
| <a name="input_transform_lambda_arn"></a> [transform\_lambda\_arn](#input\_transform\_lambda\_arn) | (Optional) ARN of a Firehose transform Lambda prepended to the processing\_configuration as the record-splitting front end for a CloudWatch Logs subscription source. CloudWatch Logs subscription records arrive GZIP-compressed and wrapped in the {messageType, owner, logGroup, logStream, subscriptionFilters, logEvents[]} envelope, batching MANY logEvents per record. The AWS-native unwrap path (Decompression -> CloudWatchLogProcessing -> RecordDeAggregation) cannot be used at scale because RecordDeAggregation is hard-capped at 500 sub-records per record: a delivery whose record carries more than 500 events is passed WHOLE to the dynamic-partitioning MetadataExtraction JQ engine, which rejects it with 'Non JSON record provided' and routes every event to errors/metadata-extraction-failed/. Because a Firehose transform Lambda is strictly 1:1 (it cannot emit more records than it received), the Lambda decompresses + envelope-strips each record and re-ingests each logEvents[].message as its own single-JSON record via firehose:PutRecordBatch (marking the original Dropped), which has no 500-record cap, so a CloudWatch Logs delivery of any size is split into individual JSON records before MetadataExtraction. The Firehose delivery role (role\_arn) must hold lambda:InvokeFunction + lambda:GetFunctionConfiguration on this Lambda. Defaults to null so standalone fixtures keep their single-MetadataExtraction processing\_configuration. | `string` | `null` | no |
| <a name="input_update_timeout"></a> [update\_timeout](#input\_update\_timeout) | (Optional) Bounded timeout the AWS provider waits for an in-place delivery stream update to converge before failing fast. Overrides the provider's 10m default. Format: a Go duration ending in s, m, or h (e.g. 10m). | `string` | `"10m"` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_delivery_stream_arn"></a> [delivery\_stream\_arn](#output\_delivery\_stream\_arn) | The Amazon Resource Name (ARN) of the Firehose delivery stream (ADOT exporter target). |
| <a name="output_delivery_stream_name"></a> [delivery\_stream\_name](#output\_delivery\_stream\_name) | The name of the Firehose delivery stream. |
| <a name="output_log_group_name"></a> [log\_group\_name](#output\_log\_group\_name) | The name of the CloudWatch log group for delivery errors. Null when cloudwatch\_logging\_enabled is false. |

## Input validation

- `name` must match `^[a-zA-Z0-9_.-]+$`. Plans fail for invalid names.
- `destination` must be `extended_s3`. Plans fail for other destination types.
- `role_arn` must match `^arn:aws:iam::`. Plans fail for invalid ARNs.
- `bucket_arn` must match `^arn:aws:s3:::`. Plans fail for invalid ARNs.
- `buffering_size` must be between 1 and 128. Plans fail outside this range.
- `buffering_interval` must be between 60 and 900. Plans fail outside this range.
- `compression_format` must be one of UNCOMPRESSED, GZIP, Snappy, ZIP, or HADOOP_SNAPPY.
- `kms_key_arn` must match `^arn:aws:kms:`. Plans fail for non-KMS ARNs.
- `data_format_conversion` must be non-null when `enable_format_conversion` is true. Plans fail otherwise.
- `dynamic_partitioning_jq` -- each value must be non-empty. Plans fail when any value is empty.
- `log_retention_in_days` must be one of the AWS allowed retention values.
<!-- END_TF_DOCS -->