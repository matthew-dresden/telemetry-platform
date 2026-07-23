output "listener_arns" {
  description = "Map of listener name to ARN."
  value       = module.example.listener_arns
}

output "https_listener_arn" {
  description = "The ARN of the HTTPS listener."
  value       = module.example.https_listener_arn
}

output "listener_rule_arns" {
  description = "Map of rule key to ARN."
  value       = module.example.listener_rule_arns
}
