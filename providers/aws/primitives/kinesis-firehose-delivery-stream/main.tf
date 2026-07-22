resource "aws_cloudwatch_log_group" "this" {
  count             = var.cloudwatch_logging_enabled ? 1 : 0
  name              = local.log_group_name
  retention_in_days = var.log_retention_in_days
  kms_key_id        = var.kms_key_arn

  tags = local.common_tags
}

resource "aws_cloudwatch_log_stream" "this" {
  count          = var.cloudwatch_logging_enabled ? 1 : 0
  name           = local.log_stream_name
  log_group_name = aws_cloudwatch_log_group.this[0].name
}

resource "aws_kinesis_firehose_delivery_stream" "this" {
  name        = var.name
  destination = var.destination

  # Bounded create/update/delete waits. The AWS provider otherwise polls
  # DescribeDeliveryStream for the ACTIVE (create/update) or deleted state up to its
  # 30m create / 10m update / 30m delete defaults. A stream that gets stuck in
  # CREATING or DELETING (e.g. a transient KMS-grant or IAM-propagation delay) would
  # silently consume that full default budget, which -- stacked on a sequential
  # terratest suite -- can blow the per-package Go test timeout and present as an
  # intermittent hang. These bounded, input-driven timeouts make a stuck operation
  # fail fast with a clear provider error instead.
  timeouts {
    create = var.create_timeout
    update = var.update_timeout
    delete = var.delete_timeout
  }

  server_side_encryption {
    enabled  = true
    key_type = "CUSTOMER_MANAGED_CMK"
    key_arn  = var.kms_key_arn
  }

  extended_s3_configuration {
    role_arn            = var.role_arn
    bucket_arn          = var.bucket_arn
    prefix              = var.prefix
    error_output_prefix = var.error_output_prefix
    compression_format  = var.compression_format

    buffering_size     = var.buffering_size
    buffering_interval = var.buffering_interval

    dynamic "cloudwatch_logging_options" {
      for_each = var.cloudwatch_logging_enabled ? [1] : []
      content {
        enabled         = true
        log_group_name  = aws_cloudwatch_log_group.this[0].name
        log_stream_name = aws_cloudwatch_log_stream.this[0].name
      }
    }

    dynamic "dynamic_partitioning_configuration" {
      for_each = var.dynamic_partitioning_enabled ? [1] : []
      content {
        enabled = true
      }
    }

    dynamic "processing_configuration" {
      for_each = length(local.firehose_processors) > 0 ? [1] : []
      content {
        enabled = true

        # Single processing_configuration with one or more ORDERED processors built in
        # locals.firehose_processors: an optional Lambda transform (the record-splitting
        # front end for a CloudWatch Logs subscription source, set via transform_lambda_arn)
        # followed by the dynamic-partitioning MetadataExtraction (when dynamic partitioning
        # is enabled). AWS allows exactly one MetadataExtraction processor, so all
        # dynamic_partitioning_jq expressions are merged into a single combined query of the
        # form {key1:val1,key2:val2,...}.
        dynamic "processors" {
          for_each = local.firehose_processors
          content {
            type = processors.value.type

            dynamic "parameters" {
              for_each = processors.value.parameters
              content {
                parameter_name  = parameters.value.name
                parameter_value = parameters.value.value
              }
            }
          }
        }
      }
    }

    dynamic "data_format_conversion_configuration" {
      for_each = var.enable_format_conversion ? [var.data_format_conversion] : []
      content {
        enabled = true

        input_format_configuration {
          deserializer {
            open_x_json_ser_de {}
          }
        }

        output_format_configuration {
          serializer {
            parquet_ser_de {}
          }
        }

        schema_configuration {
          role_arn      = data_format_conversion_configuration.value.glue_role_arn
          database_name = data_format_conversion_configuration.value.glue_database_name
          table_name    = data_format_conversion_configuration.value.glue_table_name
        }
      }
    }
  }

  tags = local.common_tags
}
