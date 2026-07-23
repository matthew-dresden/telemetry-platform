# Example: ECS cluster with FARGATE and FARGATE_SPOT capacity providers and Container Insights enabled.
# Resource blocks are allowed in examples/ per no_resources_policy exclusion.
module "example" {
  source = "../../"

  name                      = var.name
  capacity_providers        = var.capacity_providers
  enable_container_insights = var.enable_container_insights
  execute_command_logging   = var.execute_command_logging
  tags                      = var.tags

  default_capacity_provider_strategy = var.default_capacity_provider_strategy
}
