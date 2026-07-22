data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); the last
  # 6 chars are its random, collision-free tail. Appending it to every account-unique
  # IAM role name this fixture creates (TelemetryAnalyst, TelemetryAdmin, the ECS task
  # role, and the ECS task-execution role) lets concurrent CI runs of THIS module
  # apply in the SAME qa account without colliding on a fixed role name. The
  # viewer/author/admin group-name inputs are NOT scoped: they create no AWS resource
  # (the module only surfaces them in the group_role_map output and in trust-policy
  # ExternalId condition strings). The offline tfvars value "offline-validate" keeps
  # the suffix statically resolvable, so trivy still resolves names. substr/length are
  # pure plan-time functions.
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)

  analyst_role_name            = "${var.analyst_role_name}-${local.run_suffix}"
  admin_role_name              = "${var.admin_role_name}-${local.run_suffix}"
  ecs_task_role_name           = "${var.ecs_task_role_name}-${local.run_suffix}"
  ecs_task_execution_role_name = "${var.ecs_task_execution_role_name}-${local.run_suffix}"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  # Trust policy for the TelemetryAnalyst role (Author permission set).
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

  # Trust policy for ECS task and execution roles.
  ecs_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTasksAssumption"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  # D2r analyst inline policy -- read-only Athena/Glue/S3-results.
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

  # D3r admin inline policy -- manage QuickSight/datasets.
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
        ]
        Resource = "arn:aws:quicksight:*:${local.account_id}:*"
      }
    ]
  })

  # ECS task role inline policy -- docs/terragrunt-concepts.md (ADOT task role, least-privilege).
  # ARN-scoped resources; NO S3/Athena/Glue actions.
  ecs_task_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FirehosePutRecords"
        Effect = "Allow"
        Action = [
          "firehose:PutRecord",
          "firehose:PutRecordBatch",
        ]
        Resource = var.firehose_stream_arn
      },
      {
        Sid    = "KMSDataKeyOperations"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = var.telemetry_data_kms_key_arn
      },
      {
        Sid    = "SSMParameterAccess"
        Effect = "Allow"
        Action = [
          "ssm:GetParameters",
        ]
        Resource = "arn:aws:ssm:*:${local.account_id}:parameter/telemetry/prod/ingest/*"
      },
      {
        Sid    = "CloudWatchMetricsNamespaceScoped"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = var.adot_metrics_namespace
          }
        }
      }
    ]
  })

  # ECS execution role inline policy -- docs/terragrunt-concepts.md (ECS execution role).
  # ECR pull, ADOT log group logs, AOT_CONFIG_CONTENT SSM only, telemetry-config KMS.
  ecs_execution_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRPullAccess"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = "*"
      },
      {
        Sid    = "ADOTLogGroupAccess"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = var.adot_log_group_arn
      },
      {
        Sid    = "AOTConfigSSMAccess"
        Effect = "Allow"
        Action = [
          "ssm:GetParameters",
        ]
        Resource = var.aot_config_parameter_arn
      },
      {
        Sid    = "TelemetryConfigKMSDecrypt"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
        ]
        Resource = var.telemetry_config_kms_key_arn
      }
    ]
  })
}

# Identity reference -- the module under test.
# Exercises analyst + admin + ECS task + ECS execution roles.
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

  ecs_task_role_name           = local.ecs_task_role_name
  ecs_task_execution_role_name = local.ecs_task_execution_role_name

  ecs_task_assume_role_policy_json           = local.ecs_trust_policy
  ecs_task_execution_assume_role_policy_json = local.ecs_trust_policy

  ecs_task_inline_policies = {
    ECSTaskPolicy = local.ecs_task_policy
  }
  ecs_task_execution_inline_policies = {
    ECSExecutionPolicy = local.ecs_execution_policy
  }

  permission_set_account_id = local.account_id

  tags = local.tags
}

# ---------------------------------------------------------------------------
# Outputs re-exported from the identity reference.
# ecs_task_role_arn is consumed here to prove the re-export is not dangling (AC-3).
# ---------------------------------------------------------------------------

output "analyst_role_arn" {
  description = "The TelemetryAnalyst role ARN from the identity reference (D2r)."
  value       = module.example.analyst_role_arn
}

output "admin_role_arn" {
  description = "The TelemetryAdmin role ARN from the identity reference (D3r)."
  value       = module.example.admin_role_arn
}

output "ecs_task_role_arn" {
  description = "The ECS task role ARN from the identity reference (docs/terragrunt-concepts.md). Consumed to prove the re-export is not dangling."
  value       = module.example.ecs_task_role_arn
}

output "ecs_task_execution_role_arn" {
  description = "The ECS execution role ARN from the identity reference (docs/terragrunt-concepts.md)."
  value       = module.example.ecs_task_execution_role_arn
}

output "group_role_map" {
  description = "The group-to-role map from the identity reference (D8)."
  value       = module.example.group_role_map
}
