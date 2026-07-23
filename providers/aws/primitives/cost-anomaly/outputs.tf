output "monitor_arn" {
  description = "The ARN of the anomaly monitor."
  value       = aws_ce_anomaly_monitor.this.arn
}

output "subscription_arn" {
  description = "The ARN of the alert subscription."
  value       = aws_ce_anomaly_subscription.this.arn
}
