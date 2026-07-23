locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  log_group_name  = "/aws/firehose/${var.name}"
  log_stream_name = "S3Delivery"

  # ---------------------------------------------------------------------------
  # Ordered processor list for the single processing_configuration block.
  #
  # AWS allows ONE processing_configuration with MULTIPLE ordered processors, rendered in
  # declaration order. The list is composed of two optional segments:
  #
  #   1. A Lambda transform processor (when transform_lambda_arn is set). This is the
  #      record-splitting front end for a CloudWatch Logs subscription source. CloudWatch
  #      Logs subscription records arrive GZIP-compressed and wrapped in the
  #      {messageType, owner, logGroup, logStream, ..., logEvents[]} envelope, and a single
  #      record batches MANY logEvents. The AWS-native CloudWatch unwrap path
  #      (Decompression -> CloudWatchLogProcessing -> RecordDeAggregation) cannot be used at
  #      scale: RecordDeAggregation is hard-capped at 500 sub-records per record, so a
  #      delivery whose record carries more than 500 events is passed WHOLE to the
  #      dynamic-partitioning MetadataExtraction JQ engine, which rejects the multi-object
  #      blob with "Non JSON record provided" (DynamicPartitioning.MetadataExtractionFailed)
  #      and routes EVERY event to errors/metadata-extraction-failed/ (proven in qa: a
  #      2000-event delivery landed wholly under errors/, a 50-event delivery under raw/).
  #      A Firehose transform Lambda is strictly 1:1 (each returned record reuses its input
  #      recordId; it cannot emit more records than it received), so the Lambda instead
  #      decompresses + envelope-strips each record and RE-INGESTS each logEvents[].message
  #      as its own single-JSON record via firehose:PutRecordBatch, marking the original
  #      aggregated record Dropped. Re-ingestion has no 500-record cap, so a CloudWatch Logs
  #      delivery of ANY size is split into individual JSON records before MetadataExtraction.
  #
  #   2. The dynamic-partitioning MetadataExtraction processor (when dynamic_partitioning_enabled).
  #      AWS allows exactly one MetadataExtraction processor, so all dynamic_partitioning_jq
  #      entries are merged into a single combined query of the form {key1:val1,key2:val2,...}.
  #
  # Each entry is {type, parameters=[{name,value},...]} so a single dynamic "processors"
  # block with a nested dynamic "parameters" block renders all of them in order.
  # ---------------------------------------------------------------------------
  # Only the LambdaArn parameter is set. AWS applies its default NumberOfRetries (3) and
  # DescribeDeliveryStream does NOT echo that default back, so setting NumberOfRetries
  # explicitly to its default produces a perpetual plan diff (the refreshed state lacks the
  # parameter, so every plan re-adds it -- non-idempotent). The retry count is therefore left
  # to the AWS default.
  lambda_transform_processors = var.transform_lambda_arn != null ? [
    {
      type = "Lambda"
      parameters = [
        { name = "LambdaArn", value = var.transform_lambda_arn },
      ]
    },
  ] : []

  metadata_extraction_processors = var.dynamic_partitioning_enabled ? [
    {
      type = "MetadataExtraction"
      parameters = [
        {
          name  = "MetadataExtractionQuery"
          value = "{${join(",", [for k, v in var.dynamic_partitioning_jq : "${k}:${v}"])}}"
        },
        {
          name  = "JsonParsingEngine"
          value = "JQ-1.6"
        },
      ]
    },
  ] : []

  # Final ordered processor list: the record-splitting Lambda transform (when set) first,
  # then the dynamic-partitioning MetadataExtraction (when dynamic partitioning is enabled).
  firehose_processors = concat(local.lambda_transform_processors, local.metadata_extraction_processors)
}
