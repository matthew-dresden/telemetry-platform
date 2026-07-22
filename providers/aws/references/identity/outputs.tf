output "analyst_role_arn" {
  description = "ARN of the TelemetryAnalyst IAM role (Author permission set, D2r). Consumed by downstream quicksight and ecs-app-deploy units (docs/terragrunt-concepts.md)."
  value       = module.analyst.role_arn
}

output "admin_role_arn" {
  description = "ARN of the TelemetryAdmin IAM role (Admin permission set, D3r). Consumed by downstream quicksight and ecs-app-deploy units (docs/terragrunt-concepts.md)."
  value       = module.admin.role_arn
}

output "ecs_task_role_arn" {
  description = "ARN of the ECS ADOT task role (docs/terragrunt-concepts.md). Null when ecs_task_role_name is not set. Consumed by collector-ingestion."
  value       = length(module.ecs_task) > 0 ? module.ecs_task[0].role_arn : null
}

output "ecs_task_execution_role_arn" {
  description = "ARN of the ECS execution role (docs/terragrunt-concepts.md). Null when ecs_task_execution_role_name is not set. Consumed by collector-ingestion."
  value       = length(module.ecs_task_execution) > 0 ? module.ecs_task_execution[0].role_arn : null
}

output "group_role_map" {
  description = "Map of Identity Center group name keys (viewer/author/admin) to their corresponding values -- viewer maps to the viewer_group_name input, author maps to the analyst_role_arn, admin maps to the admin_role_arn (D8). Consumed by the quicksight reference to wire permission-set-to-role assignments."
  value       = local.group_role_map
}

output "group_annotations" {
  description = "Map pairing each Identity Center group name key (viewer_group/author_group/admin_group) with the raw group name string from inputs. Consumed by downstream units that need the group names alongside the role ARNs (e.g., quicksight group membership wiring)."
  value       = local.group_annotations
}

output "permission_set_account_id" {
  description = "AWS account ID hosting the IAM Identity Center permission sets (as supplied via input). Re-exported for downstream consumer verification."
  value       = local.permission_set_account_tag
}
