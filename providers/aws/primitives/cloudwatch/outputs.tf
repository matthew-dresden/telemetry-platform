output "alarm_arns" {
  description = "Map of alarm name to CloudWatch metric alarm ARN."
  value       = { for k, a in aws_cloudwatch_metric_alarm.this : k => a.arn }
}

output "log_group_arns" {
  description = "Map of logical log-group key to CloudWatch log group ARN."
  value       = { for k, g in aws_cloudwatch_log_group.this : k => g.arn }
}

output "dashboard_arn" {
  description = "ARN of the CloudWatch dashboard, or null when create_dashboard is false."
  value       = try(aws_cloudwatch_dashboard.this[0].dashboard_arn, null)
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard, or null when create_dashboard is false."
  value       = var.create_dashboard ? var.dashboard_name : null
}
