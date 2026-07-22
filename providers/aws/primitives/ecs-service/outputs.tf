output "service_id" {
  description = "The ID (ARN) of the ECS service."
  value       = aws_ecs_service.this.id
}

output "service_name" {
  description = "The name of the ECS service."
  value       = aws_ecs_service.this.name
}

output "task_definition_arn" {
  description = "The ARN of the active task definition revision."
  value       = aws_ecs_task_definition.this.arn
}

output "autoscaling_target_resource_id" {
  description = "The Application Auto Scaling resource ID (e.g. service/<cluster>/<service>). Empty string when enable_autoscaling is false."
  value       = var.enable_autoscaling ? aws_appautoscaling_target.this[0].resource_id : ""
}

output "log_group_name" {
  description = "The name of the CloudWatch log group. Empty string when create_log_group is false."
  value       = var.create_log_group ? aws_cloudwatch_log_group.this[0].name : ""
}
