locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Every entry in default_capacity_provider_strategy must reference a provider present in
  # capacity_providers. This guards against the PutClusterCapacityProviders error that occurs
  # at apply time when the strategy references a provider not in the cluster. The boolean is
  # consumed by the aws_ecs_cluster_capacity_providers lifecycle precondition in main.tf, which
  # surfaces a clear, actionable plan-time error -- the idiomatic Terraform validation mechanism.
  strategy_providers_subset_valid = alltrue([
    for s in var.default_capacity_provider_strategy : contains(var.capacity_providers, s.capacity_provider)
  ])

  # Compute the effective default capacity provider strategy. When the caller provides an explicit
  # strategy, use it verbatim. When the caller omits it (default=[]), derive a sensible default
  # using only the providers in capacity_providers: FARGATE with base=1 and FARGATE_SPOT with
  # weight=4 if present.
  effective_default_strategy = length(var.default_capacity_provider_strategy) > 0 ? var.default_capacity_provider_strategy : [
    for cp in var.capacity_providers : {
      capacity_provider = cp
      weight            = cp == "FARGATE" ? 1 : 4
      base              = cp == "FARGATE" ? 1 : 0
    }
  ]
}
