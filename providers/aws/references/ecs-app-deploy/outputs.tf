output "service_arn" {
  description = "The ARN of the ECS service (re-exported from ecs-service service_id)."
  value       = module.service.service_id
}

output "service_name" {
  description = "The name of the ECS service (re-exported from ecs-service)."
  value       = module.service.service_name
}

output "task_definition_arn" {
  description = "The ARN of the active task definition revision (re-exported from ecs-service)."
  value       = module.service.task_definition_arn
}

output "autoscaling_target_resource_id" {
  description = "The Application Auto Scaling resource ID. Empty string when autoscaling is disabled."
  value       = module.service.autoscaling_target_resource_id
}

output "task_role_arn" {
  description = "The ARN of the per-service ECS task IAM role (re-exported from iam-role)."
  value       = module.task_role.role_arn
}

output "alb_arn" {
  description = "The ARN of the Application Load Balancer. Null when no ALB is configured."
  value       = length(module.alb) > 0 ? module.alb[0].alb_arn : null
}

output "alb_dns_name" {
  description = "The DNS name of the Application Load Balancer. Null when no ALB is configured."
  value       = length(module.alb) > 0 ? module.alb[0].alb_dns_name : null
}

output "alb_arn_suffix" {
  description = "The ARN suffix of the ALB (app/<name>/<id>), the LoadBalancer dimension for CloudWatch ApplicationELB alarms. Null when no ALB is configured."
  value       = length(module.alb) > 0 ? module.alb[0].alb_arn_suffix : null
}

output "alb_zone_id" {
  description = "The canonical hosted zone ID of the ALB (for Route53 alias records). Null when no ALB is configured."
  value       = length(module.alb) > 0 ? module.alb[0].alb_zone_id : null
}

output "target_group_arns" {
  description = "Map of target group name to ARN. Empty map when no ALB is configured."
  value       = length(module.alb) > 0 ? module.alb[0].target_group_arns : {}
}

output "https_listener_arn" {
  description = "The ARN of the HTTPS listener. Null when no HTTPS listener is configured."
  value       = length(module.listener) > 0 ? module.listener[0].https_listener_arn : null
}

output "ssm_parameter_arns" {
  description = "Map of SSM parameter logical key to ARN. One entry per ssm_parameters input key."
  value       = { for k, v in module.ssm : k => v.parameter_arn }
}

# ---------------------------------------------------------------------------
# Accepted interface inputs -- surfaced for caller introspection and to assert
# interface stability. These inputs are accepted at the module interface for
# call-site symmetry / toggles that this module does not consume internally.
# ---------------------------------------------------------------------------

output "accepted_inputs" {
  description = "Inputs accepted at the module interface for call-site symmetry / toggles that this module does not consume internally; surfaced for caller introspection and to assert interface stability."
  value = {
    env = var.env
  }
}
