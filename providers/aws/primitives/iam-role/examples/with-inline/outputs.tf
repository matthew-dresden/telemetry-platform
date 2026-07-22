output "role_arn" {
  description = "The Amazon Resource Name (ARN) of the IAM role."
  value       = module.example.role_arn
}

output "role_name" {
  description = "The name of the IAM role."
  value       = module.example.role_name
}

output "role_id" {
  description = "The stable unique identifier for the IAM role."
  value       = module.example.role_id
}
