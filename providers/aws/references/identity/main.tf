# ---------------------------------------------------------------------------
# TelemetryAnalyst role (Author permission set, D2r read-only Athena/Glue/S3-results).
# Sourced from the iam-role primitive via var.analyst_source (const string variable).
# inline_policies carries the D2r least-privilege policy document supplied as input.
# ---------------------------------------------------------------------------
module "analyst" {
  source = var.analyst_source

  name                    = var.analyst_role_name
  description             = "TelemetryAnalyst permission-set role -- D2r read-only Athena/Glue/S3-results (docs/terragrunt-concepts.md)"
  assume_role_policy_json = var.analyst_assume_role_policy_json
  inline_policies         = var.analyst_inline_policies

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}

# ---------------------------------------------------------------------------
# TelemetryAdmin role (Admin permission set, D3r manage QuickSight/datasets).
# Sourced from the iam-role primitive via var.admin_source (const string variable).
# inline_policies carries the D3r least-privilege policy document supplied as input.
# ---------------------------------------------------------------------------
module "admin" {
  source = var.admin_source

  name                    = var.admin_role_name
  description             = "TelemetryAdmin permission-set role -- D3r manage QuickSight/datasets (docs/terragrunt-concepts.md)"
  assume_role_policy_json = var.admin_assume_role_policy_json
  inline_policies         = var.admin_inline_policies

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}

# ---------------------------------------------------------------------------
# ECS task role (docs/terragrunt-concepts.md -- ADOT task role, least-privilege).
# Created only when ecs_task_role_name is non-empty so the default example
# (analyst + admin only) does not require ECS inputs.
# inline_policies carries the ARN-scoped policy (docs/terragrunt-concepts.md) supplied as input:
#   firehose:PutRecord* on the specific stream ARN only,
#   kms:GenerateDataKey/Decrypt on telemetry-data,
#   ssm:GetParameters on /telemetry/prod/ingest/*,
#   cloudwatch:PutMetricData with cloudwatch:namespace Condition.
#   NO S3/Athena/Glue.
# ---------------------------------------------------------------------------
module "ecs_task" {
  source = var.ecs_task_source

  count = var.ecs_task_role_name != "" ? 1 : 0

  name                    = var.ecs_task_role_name
  description             = "ECS ADOT task role for collector-ingestion -- least-privilege per docs/terragrunt-concepts.md"
  assume_role_policy_json = var.ecs_task_assume_role_policy_json
  inline_policies         = var.ecs_task_inline_policies

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}

# ---------------------------------------------------------------------------
# ECS execution role (docs/terragrunt-concepts.md -- ECS execution role, least-privilege).
# Created only when ecs_task_execution_role_name is non-empty.
# inline_policies carries the ARN-scoped policy (docs/terragrunt-concepts.md) supplied as input:
#   ECR pull, logs:CreateLogStream/PutLogEvents on the ADOT log group ARN only,
#   ssm:GetParameters for the AOT_CONFIG_CONTENT parameter ARN only,
#   kms:Decrypt on telemetry-config.
# ---------------------------------------------------------------------------
module "ecs_task_execution" {
  source = var.ecs_task_execution_source

  count = var.ecs_task_execution_role_name != "" ? 1 : 0

  name                    = var.ecs_task_execution_role_name
  description             = "ECS execution role for collector-ingestion -- least-privilege per docs/terragrunt-concepts.md"
  assume_role_policy_json = var.ecs_task_execution_assume_role_policy_json
  inline_policies         = var.ecs_task_execution_inline_policies

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}
