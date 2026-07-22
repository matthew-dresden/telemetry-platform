output "subnet_ids" {
  description = "Map of subnet name to subnet ID."
  value       = module.example.subnet_ids
}

output "subnet_ids_list" {
  description = "Ordered list of subnet IDs."
  value       = module.example.subnet_ids_list
}

output "subnet_arns" {
  description = "Map of subnet name to subnet ARN."
  value       = module.example.subnet_arns
}

output "availability_zones" {
  description = "Map of subnet name to availability zone."
  value       = module.example.availability_zones
}
