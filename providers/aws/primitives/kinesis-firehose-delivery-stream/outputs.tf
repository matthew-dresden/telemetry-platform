output "delivery_stream_arn" {
  description = "The Amazon Resource Name (ARN) of the Firehose delivery stream (ADOT exporter target)."
  value       = aws_kinesis_firehose_delivery_stream.this.arn
}

output "delivery_stream_name" {
  description = "The name of the Firehose delivery stream."
  value       = aws_kinesis_firehose_delivery_stream.this.name
}

output "log_group_name" {
  description = "The name of the CloudWatch log group for delivery errors. Null when cloudwatch_logging_enabled is false."
  value       = try(aws_cloudwatch_log_group.this[0].name, null)
}
