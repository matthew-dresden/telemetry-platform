# ecs-app-deploy reference: per-service ECS task/exec roles, task definition, ECS service,
# and ALB listener wiring for an application deployment.
#
# v1.0.2: the ECS service depends_on the ALB listener (see the service resource below) so
# CreateService never races ahead of the listener -- without it the apply intermittently
# failed with "target group does not have an associated load balancer".

module "task_role" {
  source = var.task_role_source

  name                    = var.task_role_name
  assume_role_policy_json = local.task_role_assume_policy
  description             = "Per-service ECS task role for service ${var.service_name}"
  inline_policies         = var.task_role_inline_policies

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}

module "ssm" {
  source   = var.ssm_source
  for_each = local.ssm_parameter_paths

  name       = each.value.name
  type       = each.value.type
  value      = each.value.value
  kms_key_id = each.value.kms_key_id

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "ssm-parameter"
}

module "alb" {
  source = var.alb_source
  count  = var.alb != null ? 1 : 0

  name                  = var.alb.name
  internal              = var.alb.internal
  vpc_id                = var.vpc_id
  subnet_ids            = var.alb.alb_subnet_ids
  security_group_ids    = var.alb.alb_security_group_ids
  create_security_group = var.alb.create_security_group
  ingress_cidr_blocks   = var.alb.ingress_cidr_blocks
  idle_timeout          = var.alb.idle_timeout
  target_groups         = var.alb.target_groups

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "alb"
}

module "listener" {
  source = var.listener_source
  count  = var.alb != null && var.alb_listeners != null ? 1 : 0

  load_balancer_arn = module.alb[0].alb_arn

  # Auto-fill forward action target_group_arn from the first ALB target group when not
  # explicitly provided. This allows the caller to omit target_group_arn from the listener
  # definition and have it resolved from the ALB module output.
  listeners = [
    for l in var.alb_listeners.listeners : merge(l, {
      default_action = merge(l.default_action, {
        target_group_arn = (
          l.default_action.type == "forward" && (l.default_action.target_group_arn == null || l.default_action.target_group_arn == "")
          ? local.first_tg_arn
          : l.default_action.target_group_arn
        )
      })
    })
  ]

  listener_rules = var.alb_listeners.listener_rules

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "alb-listener"
}

module "service" {
  source = var.service_source

  name                     = var.service_name
  cluster_arn              = var.cluster_arn
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = module.task_role.role_arn
  container_definitions    = var.container_definitions
  desired_count            = var.desired_count
  task_cpu                 = var.task_cpu
  task_memory              = var.task_memory
  subnet_ids               = var.subnet_ids
  security_group_ids       = var.security_group_ids
  assign_public_ip         = var.assign_public_ip
  enable_autoscaling       = var.enable_autoscaling
  autoscaling              = var.autoscaling
  log_group_retention_days = var.log_group_retention_days
  log_group_kms_key_arn    = var.log_group_kms_key_arn

  load_balancer = var.alb != null && length(var.alb.target_groups) > 0 ? {
    target_group_arn = module.alb[0].target_group_arns[var.alb.target_groups[0].name]
    container_name   = var.service_name
    container_port   = var.alb.target_groups[0].port
  } : null

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "ecs-service"

  # The ECS service must be created AFTER the ALB listener. When load_balancer is set,
  # aws_ecs_service references the target group, and ECS CreateService fails with
  # "InvalidParameterException: The target group ... does not have an associated load
  # balancer" if the listener (which is what associates the target group with the ALB)
  # has not finished yet. The service references module.alb's target_group_arns directly,
  # but NOT module.listener, so without this explicit dependency terraform creates the
  # service and the listener in parallel and races the association. Ordering the service
  # after module.listener removes the race on a cold apply.
  # v1.0.2: ECS service depends_on the listener so CreateService never races ahead of the ALB listener.
  depends_on = [module.task_role, module.listener]
}

module "cloudwatch" {
  source = var.cloudwatch_source

  alarms = var.alarms

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "cloudwatch"
}
