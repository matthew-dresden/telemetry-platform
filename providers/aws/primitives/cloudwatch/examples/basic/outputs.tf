output "alarm_arns" {
  description = "Map of alarm name to CloudWatch metric alarm ARN."
  value       = module.example.alarm_arns
}

output "log_group_arns" {
  description = "Map of logical log-group key to CloudWatch log group ARN."
  value       = module.example.log_group_arns
}

output "dashboard_arn" {
  description = "ARN of the CloudWatch dashboard, or null when create_dashboard is false."
  value       = module.example.dashboard_arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard, or null when create_dashboard is false."
  value       = module.example.dashboard_name
}

output "fixture_sns_topic_arn" {
  description = "ARN of the fixture-created SNS topic used as the alarm action sink."
  value       = aws_sns_topic.alarm_sink.arn
}

output "fixture_kms_key_arn" {
  description = "ARN of the fixture-created KMS key used to encrypt CloudWatch log groups."
  value       = aws_kms_key.log_encryption.arn
}
