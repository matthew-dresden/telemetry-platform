output "firehose_delivery_stream_arn" {
  description = "ARN of the Firehose delivery stream. Per decision D37, this is consumed as an INPUT by the collector-ingestion reference to keep the dependency edge one-directional."
  value       = module.firehose.delivery_stream_arn
}

output "firehose_stream_name" {
  description = "Name of the Firehose delivery stream. Consumed by the observability unit as the DeliveryStreamName CloudWatch alarm dimension (one-directional dependency edge, D37). Wired from the composed Firehose primitive's resource name, not the input var, so it reflects the actually-created stream."
  value       = module.firehose.delivery_stream_name
}

output "data_lake_bucket_arn" {
  description = "ARN of the S3 data lake bucket (B1)."
  value       = module.lake_bucket.bucket_arn
}

output "glue_database_name" {
  description = "Name of the Glue catalog database."
  value       = module.glue_catalog.database_name
}

output "glue_table_parameters" {
  description = "The table-level Glue parameters rendered onto the Glue format-conversion table -- the Athena partition-projection configuration (projection.enabled, projection.tool.type=injected, projection.dt.type=date with projection.dt.format/range/interval, and storage.location.template). Exposed so consumers and Terratest can confirm partitions resolve via projection without a crawler and that the projected partition layout matches the Firehose delivery path (BUG-3)."
  value       = module.glue_catalog.table_parameters
}

output "lake_kms_key_arn" {
  description = "ARN of the telemetry-data KMS CMK (alias/telemetry-data). This reference owns only this CMK -- telemetry-config (S4) and telemetry-spice (S5) CMKs are owned by other modules."
  value       = module.lake_kms_key.key_arn
}

output "firehose_role_inline_policy_json" {
  description = "The Firehose delivery role inline policy JSON built from input-driven ARNs. Exposed for Terratest assertions verifying per-statement ARN scoping per docs/terragrunt-concepts.md."
  value       = local.firehose_delivery_policy_json
}

output "firehose_role_arn" {
  description = "ARN of the Firehose delivery role. This single role is wired as BOTH the Firehose delivery role_arn AND the data_format_conversion schema_configuration.role_arn, so it is exposed for Terratest assertions confirming the schema-configuration role equals the delivery role."
  value       = module.firehose_role.role_arn
}

output "firehose_assume_role_policy_json" {
  description = "The Firehose delivery role trust policy JSON. Exposed for Terratest assertions confirming the role (which is also the schema_configuration role) trusts firehose.amazonaws.com so Firehose can assume it for format conversion."
  value       = var.firehose_assume_role_policy_json
}

output "lake_kms_key_policy_json" {
  description = "The telemetry-data KMS key policy JSON. Exposed for Terratest assertions verifying absence of wildcard principals per docs/terragrunt-concepts.md."
  value       = var.lake_kms_policy_json
}

output "transition_storage_class" {
  description = "The S3 lifecycle transition storage class propagated to the composed s3-bucket (AC-11). Exposed for Terratest assertions verifying cold-tier propagation."
  value       = var.transition_storage_class
}

output "firehose_dynamic_partitioning_jq_output" {
  description = "The firehose_dynamic_partitioning_jq map passed through to the composed Firehose primitive (T27, AC-11). Exposed for Terratest assertions verifying passthrough."
  value       = var.firehose_dynamic_partitioning_jq
}

output "cross_account_kms_statements" {
  description = "KMS key-policy statement objects (kms:Decrypt + kms:DescribeKey for each cross_account_read_principals account root, conditioned on kms:ViaService=s3.<region>.amazonaws.com) granting the configured cross-account BI principals decrypt access to the telemetry-data CMK. Empty list when cross_account_read_principals is {} (the default no-op). This reference does NOT own the whole telemetry-data key policy -- the terragrunt leaf owns the lake_kms_policy_json it passes to var.lake_kms_policy_json -- so the leaf MUST MERGE these statements into that policy's Statement array (jsondecode the base policy, concat these statements, jsonencode) so the external role can decrypt the SSE-KMS lake objects it reads. The matching S3 read grant is attached to the lake bucket by this module (aws_s3_bucket_policy.lake_cross_account), so only the KMS half needs merging by the leaf."
  value       = local.cross_account_kms_statements
}

output "cwl_transform_lambda_arn" {
  description = "ARN of the CloudWatch-Logs-split Firehose transform Lambda. Wired as the Firehose processing_configuration's first processor (transform_lambda_arn): it decompresses + envelope-strips each GZIP CWL subscription record and re-ingests each logEvents[].message as its own single-JSON record via firehose:PutRecordBatch (no 500-record cap), so a delivery of any size is split into individual JSON records before MetadataExtraction. Exposed for Terratest assertions verifying the split path."
  value       = aws_lambda_function.cwl_transform.arn
}

output "cwl_transform_lambda_name" {
  description = "Name of the CloudWatch-Logs-split Firehose transform Lambda. The FunctionName dimension for CloudWatch AWS/Lambda alarms (consumed by the observability unit, D37). Wired from the composed Lambda resource's function_name attribute, not the input var, so it reflects the actually-created function -- mirrors the firehose_stream_name output pattern above."
  value       = aws_lambda_function.cwl_transform.function_name
}
