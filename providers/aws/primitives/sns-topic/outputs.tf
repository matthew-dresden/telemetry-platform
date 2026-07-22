output "topic_arn" {
  description = "The ARN of the SNS topic. Passed to cloudwatch alarm_actions/ok_actions and to cost-anomaly as the SNS subscriber address."
  value       = aws_sns_topic.this.arn
}

output "topic_name" {
  description = "The name of the SNS topic."
  value       = aws_sns_topic.this.name
}
