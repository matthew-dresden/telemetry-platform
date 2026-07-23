output "subnet_ids" {
  description = "Map of subnet name to subnet ID."
  value       = { for k, s in aws_subnet.this : k => s.id }
}

output "subnet_ids_list" {
  description = "Ordered list of subnet IDs (for ALB/ECS subnet_ids inputs)."
  value       = [for s in aws_subnet.this : s.id]
}

output "subnet_arns" {
  description = "Map of subnet name to subnet ARN."
  value       = { for k, s in aws_subnet.this : k => s.arn }
}

output "availability_zones" {
  description = "Map of subnet name to availability zone."
  value       = { for k, s in aws_subnet.this : k => s.availability_zone }
}
