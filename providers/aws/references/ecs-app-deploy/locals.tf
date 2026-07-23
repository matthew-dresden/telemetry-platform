locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Derive the SSM parameter full paths from the caller-supplied map.
  # Path convention: /telemetry/<env>/ingest/<key>
  # The caller is responsible for providing the complete SSM parameter inventory.
  ssm_parameter_paths = {
    for k, v in var.ssm_parameters :
    k => {
      name       = "/telemetry/${var.namespace}/ingest/${k}"
      type       = v.type
      value      = v.value
      kms_key_id = try(v.kms_key_id, null)
    }
  }

  # ECS task role trust policy -- grants ecs-tasks.amazonaws.com permission to assume the role.
  task_role_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  # Derive the first target group ARN from the ALB module output for listener wiring.
  # Used to auto-fill forward action target_group_arn when the caller leaves it null.
  # An empty string sentinel is used when no ALB is configured; the listener module is
  # only instantiated when alb != null, so this value is only referenced when valid.
  first_tg_arn = (
    var.alb != null && length(var.alb.target_groups) > 0
    ? module.alb[0].target_group_arns[var.alb.target_groups[0].name]
    : ""
  )
}
