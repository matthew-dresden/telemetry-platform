output "cluster_id" {
  description = "The ID of the ECS cluster."
  value       = module.example.cluster_id
}

output "cluster_arn" {
  description = "The ARN of the ECS cluster."
  value       = module.example.cluster_arn
}

output "cluster_name" {
  description = "The name of the ECS cluster."
  value       = module.example.cluster_name
}
