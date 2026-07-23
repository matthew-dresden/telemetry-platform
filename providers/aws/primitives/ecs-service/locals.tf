locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  log_group_name = "/ecs/${var.name}"

  # ECS cluster ARN is arn:aws:ecs:<region>:<account>:cluster/<clusterName>. The Application
  # Auto Scaling resource_id requires the bare cluster name (service/<clusterName>/<serviceName>),
  # so take the path segment after "cluster/" rather than the whole final colon-segment.
  cluster_name = element(split("/", var.cluster_arn), length(split("/", var.cluster_arn)) - 1)
}
