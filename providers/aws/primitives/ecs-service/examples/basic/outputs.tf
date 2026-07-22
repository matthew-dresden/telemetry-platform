output "service_id" {
  description = "The ID of the ECS service."
  value       = module.example.service_id
}

output "service_name" {
  description = "The name of the ECS service."
  value       = module.example.service_name
}

output "task_definition_arn" {
  description = "The ARN of the active task definition."
  value       = module.example.task_definition_arn
}

output "autoscaling_target_resource_id" {
  description = "The autoscaling target resource ID (empty when autoscaling is disabled)."
  value       = module.example.autoscaling_target_resource_id
}

output "log_group_name" {
  description = "The CloudWatch log group name."
  value       = module.example.log_group_name
}
