output "alb_arn" {
  description = "The ARN of the Application Load Balancer. Passed to alb-listener as load_balancer_arn."
  value       = aws_lb.this.arn
}

output "alb_dns_name" {
  description = "The DNS name of the Application Load Balancer. Used as the CloudFront origin domain."
  value       = aws_lb.this.dns_name
}

output "alb_arn_suffix" {
  description = "The ARN suffix of the Application Load Balancer (app/<name>/<id>), the LoadBalancer dimension value for CloudWatch ApplicationELB alarms."
  value       = aws_lb.this.arn_suffix
}

output "alb_zone_id" {
  description = "The canonical hosted zone ID of the Application Load Balancer (for Route53 alias records)."
  value       = aws_lb.this.zone_id
}

output "target_group_arns" {
  description = "Map of target group name to ARN. Passed to alb-listener and ecs-service for attachment."
  value       = { for k, tg in aws_lb_target_group.this : k => tg.arn }
}

output "security_group_id" {
  description = "The ID of the managed security group, or null when create_security_group is false."
  value       = try(aws_security_group.this[0].id, null)
}
