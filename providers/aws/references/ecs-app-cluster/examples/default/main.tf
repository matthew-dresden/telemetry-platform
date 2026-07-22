locals {
  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); the last
  # 6 chars are its random, collision-free tail. Appending it to every
  # globally/account-unique fixture name (the ECS cluster, the shared execution IAM
  # role, and the CloudWatch dashboard) lets concurrent CI runs of THIS module apply
  # in the SAME qa account without colliding on a fixed name. The offline tfvars
  # value "offline-validate" keeps the suffix statically resolvable, so trivy still
  # resolves names. substr/length are pure plan-time functions.
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)

  cluster_name        = "${var.cluster_name}-${local.run_suffix}"
  execution_role_name = "${var.execution_role_name}-${local.run_suffix}"
  dashboard_name      = "${var.dashboard_name}-${local.run_suffix}"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 1
        properties = {
          markdown = "## ECS App Cluster"
        }
      }
    ]
  })
}

module "example" {
  source = "../../"

  cluster_name              = local.cluster_name
  capacity_providers        = var.capacity_providers
  enable_container_insights = var.enable_container_insights
  execution_role_name       = local.execution_role_name
  dashboard_name            = local.dashboard_name
  dashboard_body            = local.dashboard_body

  tags = local.tags
}

# Consume cluster_arn to prove the re-export is not dangling.
output "cluster_arn" {
  description = "The ARN of the ECS cluster (proves re-export is wired and non-dangling)."
  value       = module.example.cluster_arn
}

output "cluster_name" {
  description = "The name of the ECS cluster."
  value       = module.example.cluster_name
}

output "cluster_id" {
  description = "The ID of the ECS cluster."
  value       = module.example.cluster_id
}

output "execution_role_arn" {
  description = "The ARN of the shared ECS task execution IAM role."
  value       = module.example.execution_role_arn
}

output "dashboard_arn" {
  description = "The ARN of the cluster CloudWatch dashboard."
  value       = module.example.dashboard_arn
}
