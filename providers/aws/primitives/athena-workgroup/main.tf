resource "aws_athena_workgroup" "this" {
  name        = var.workgroup_name
  description = var.description

  configuration {
    enforce_workgroup_configuration    = var.enforce_workgroup_configuration
    publish_cloudwatch_metrics_enabled = var.publish_cloudwatch_metrics_enabled
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    result_configuration {
      output_location = "s3://${var.result_s3_bucket}/${var.result_s3_key_prefix}"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.result_kms_key_arn
      }
    }
  }

  tags = local.common_tags
}
