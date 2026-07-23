output "alb_arn" {
  description = "The ARN of the Application Load Balancer."
  value       = module.example.alb_arn
}

output "alb_dns_name" {
  description = "The DNS name of the Application Load Balancer."
  value       = module.example.alb_dns_name
}

output "alb_zone_id" {
  description = "The canonical hosted zone ID of the Application Load Balancer."
  value       = module.example.alb_zone_id
}

output "target_group_arns" {
  description = "Map of target group name to ARN."
  value       = module.example.target_group_arns
}

output "security_group_id" {
  description = "The ID of the managed security group."
  value       = module.example.security_group_id
}
