output "listener_arns" {
  description = "Map of listener name to ARN."
  value       = { for k, l in aws_lb_listener.this : k => l.arn }
}

output "https_listener_arn" {
  description = "The ARN of the HTTPS listener. For wiring and monitoring the collector OTLP listener."
  value       = local.https_listener != null ? aws_lb_listener.this[local.https_listener.name].arn : null
}

output "listener_rule_arns" {
  description = "Map of rule key to ARN."
  value       = { for k, r in aws_lb_listener_rule.this : k => r.arn }
}
