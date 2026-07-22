output "cluster_arn" {
  description = "The ARN of the ECS Fargate cluster."
  value       = module.cluster.cluster_arn
}

output "cluster_name" {
  description = "The name of the ECS Fargate cluster."
  value       = module.cluster.cluster_name
}

output "cluster_id" {
  description = "The ID (ARN) of the ECS Fargate cluster."
  value       = module.cluster.cluster_id
}

output "execution_role_arn" {
  description = "The ARN of the shared ECS task execution IAM role."
  value       = module.execution_role.role_arn
}

output "dashboard_arn" {
  description = "The ARN of the cluster CloudWatch dashboard."
  value       = module.cloudwatch.dashboard_arn
}
