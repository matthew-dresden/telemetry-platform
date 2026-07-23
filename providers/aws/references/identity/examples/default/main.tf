data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); the last
  # 6 chars are its random, collision-free tail. Appending it to every account-unique
  # IAM role name this fixture creates (the TelemetryAnalyst and TelemetryAdmin roles)
  # lets concurrent CI runs of THIS module apply in the SAME qa account without
  # colliding on a fixed role name. The viewer/author/admin group-name inputs are NOT
  # scoped: they create no AWS resource (the module only surfaces them in the
  # group_role_map output and in trust-policy ExternalId condition strings), and the
  # default-fixture negative tests inject an empty string for each to assert the
  # fail-fast non-empty validation. The offline tfvars value "offline-validate" keeps
  # the suffix statically resolvable, so trivy still resolves names. substr/length are
  # pure plan-time functions.
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)

  analyst_role_name = "${var.analyst_role_name}-${local.run_suffix}"
  admin_role_name   = "${var.admin_role_name}-${local.run_suffix}"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  # Trust policy for the TelemetryAnalyst role (Author permission set).
  # Trusts IAM Identity Center SSO service in the permission_set_account_id account.
  # All principals are input-driven -- no wildcard principals (security rule).
  analyst_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowIdentityCenterAssumption"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "identity-center-${var.author_group_name}"
          }
        }
      }
    ]
  })

  # Trust policy for the TelemetryAdmin role (Admin permission set).
  admin_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowIdentityCenterAssumption"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = "identity-center-${var.admin_group_name}"
          }
        }
      }
    ]
  })

  # D2r analyst inline policy -- read-only Athena/Glue/S3-results (docs/terragrunt-concepts.md).
  # All resource ARNs are input-driven from account context.
  analyst_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaQueryExecution"
        Effect = "Allow"
        Action = [
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StartQueryExecution",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup",
        ]
        Resource = "arn:aws:athena:*:${local.account_id}:workgroup/telemetry-*"
      },
      {
        Sid    = "GlueReadAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartition",
          "glue:GetPartitions",
        ]
        Resource = [
          "arn:aws:glue:*:${local.account_id}:catalog",
          "arn:aws:glue:*:${local.account_id}:database/telemetry_*",
          "arn:aws:glue:*:${local.account_id}:table/telemetry_*/*",
        ]
      },
      {
        Sid    = "AthenaResultsS3Access"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:PutObject",
        ]
        Resource = [
          "arn:aws:s3:::telemetry-athena-results-${local.account_id}",
          "arn:aws:s3:::telemetry-athena-results-${local.account_id}/*",
        ]
      }
    ]
  })

  # D3r admin inline policy -- manage QuickSight/datasets (docs/terragrunt-concepts.md).
  admin_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "QuickSightDatasetManagement"
        Effect = "Allow"
        Action = [
          "quicksight:CreateDataSet",
          "quicksight:DeleteDataSet",
          "quicksight:DescribeDataSet",
          "quicksight:ListDataSets",
          "quicksight:UpdateDataSet",
          "quicksight:CreateDataSource",
          "quicksight:DeleteDataSource",
          "quicksight:DescribeDataSource",
          "quicksight:UpdateDataSource",
          "quicksight:ListDashboards",
          "quicksight:DescribeDashboard",
          "quicksight:CreateDashboard",
          "quicksight:UpdateDashboard",
          "quicksight:DeleteDashboard",
          "quicksight:DescribeUser",
          "quicksight:ListUsers",
          "quicksight:RegisterUser",
          "quicksight:DeleteUser",
        ]
        Resource = "arn:aws:quicksight:*:${local.account_id}:*"
      }
    ]
  })
}

# Identity reference -- the module under test.
# Exercises the analyst and admin roles plus the group_role_map output.
module "example" {
  source = "../../"

  viewer_group_name = var.viewer_group_name
  author_group_name = var.author_group_name
  admin_group_name  = var.admin_group_name

  analyst_role_name = local.analyst_role_name
  admin_role_name   = local.admin_role_name

  analyst_assume_role_policy_json = local.analyst_trust_policy
  admin_assume_role_policy_json   = local.admin_trust_policy

  analyst_inline_policies = {
    AnalystReadOnlyPolicy = local.analyst_policy
  }
  admin_inline_policies = {
    AdminQuickSightPolicy = local.admin_policy
  }

  permission_set_account_id = local.account_id

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Outputs re-exported from the identity reference.
# analyst_role_arn is consumed here to prove the re-export is not dangling (AC-3).
# ---------------------------------------------------------------------------

output "analyst_role_arn" {
  description = "The TelemetryAnalyst role ARN from the identity reference (D2r)."
  value       = module.example.analyst_role_arn
}

output "admin_role_arn" {
  description = "The TelemetryAdmin role ARN from the identity reference (D3r)."
  value       = module.example.admin_role_arn
}

output "group_role_map" {
  description = "The group-to-role map from the identity reference (D8)."
  value       = module.example.group_role_map
}

output "ecs_task_role_arn" {
  description = "The ECS task role ARN from the identity reference. Null in the default example (no ECS roles)."
  value       = module.example.ecs_task_role_arn
}

output "ecs_task_execution_role_arn" {
  description = "The ECS execution role ARN from the identity reference. Null in the default example (no ECS roles)."
  value       = module.example.ecs_task_execution_role_arn
}
