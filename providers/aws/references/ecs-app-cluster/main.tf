module "cluster" {
  source = var.cluster_source

  name                      = var.cluster_name
  capacity_providers        = var.capacity_providers
  enable_container_insights = var.enable_container_insights

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "ecs-cluster"
}

module "execution_role" {
  source = var.execution_role_source

  name                    = var.execution_role_name
  assume_role_policy_json = local.execution_role_assume_policy
  description             = "Shared ECS task execution role for cluster ${var.cluster_name}"
  managed_policy_arns     = var.execution_role_managed_policy_arns
  inline_policies         = var.execution_role_inline_policies

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}

module "cloudwatch" {
  source = var.cloudwatch_source

  alarms           = var.alarms
  log_groups       = var.log_groups
  create_dashboard = true
  dashboard_name   = var.dashboard_name
  dashboard_body   = var.dashboard_body

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "cloudwatch"
}
