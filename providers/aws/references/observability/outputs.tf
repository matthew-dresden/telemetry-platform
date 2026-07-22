output "sns_topic_arn" {
  description = "The ARN of the composed SNS notification topic (D41). Consumed by downstream units (E6-F3-S3-T1, E7-F3-S3-T1) to wire alarm_actions/ok_actions and the cost-anomaly SNS subscriber per docs/terragrunt-concepts.md."
  value       = module.sns_topic.topic_arn
}

output "dashboard_name" {
  description = "The name of the composed CloudWatch observability dashboard. Non-null when create_dashboard is true. Per docs/terragrunt-concepts.md."
  value       = module.cloudwatch.dashboard_name
}
