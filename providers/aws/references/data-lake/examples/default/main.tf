data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); the last
  # 6 chars are its random, collision-free tail. Appending it to every
  # globally/account-unique fixture name lets concurrent CI runs of THIS module
  # apply in the SAME qa account without colliding on a fixed name (S3 bucket,
  # Glue db, Firehose stream, IAM role, and -- critically -- the account-global
  # KMS alias alias/telemetry-data). The offline tfvars value "offline-validate"
  # makes the suffix statically resolvable, so trivy still resolves the bucket
  # name and proves access logging is enabled. substr/length are pure functions
  # evaluated at plan time -- no AWS read, no opaque data source.
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)
  name       = "${var.name}-${local.run_suffix}"

  # The telemetry-data CMK alias is ACCOUNT-GLOBAL: a fixed alias collides across
  # concurrent runs (AlreadyExistsException) and the orphaned alias blocks the
  # next CreateAlias. Scope it by the run suffix too.
  lake_kms_alias = "${var.lake_kms_alias}-${local.run_suffix}"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  # Cross-account read principals passed to the module. When the fixture flag is on,
  # grant the CURRENT (real, PutBucketPolicy-valid) test account root so the applied
  # bucket policy and the cross_account_kms_statements output can be asserted end to
  # end; otherwise pass through the (default {}) input so the no-op default is exercised.
  effective_cross_account_read_principals = var.enable_cross_account_read_fixture ? {
    bi = {
      account_id  = local.account_id
      description = "terratest cross-account read fixture"
    }
  } : var.cross_account_read_principals

  # Derive resource names from the name input so nothing is hard-coded.
  bucket_name             = "${local.name}-lake"
  glue_database_name      = replace("${local.name}_telemetry", "-", "_")
  glue_table_name         = replace("${local.name}_events", "-", "_")
  firehose_stream_name    = "${local.name}-telemetry-events"
  firehose_role_name      = "${local.name}-firehose-role"
  firehose_log_group_name = "/aws/firehose/${local.name}-telemetry-events"

  # CloudWatch-Logs-split transform Lambda names, derived from the fixture name.
  cwl_transform_lambda_name      = "${local.name}-cwl-split"
  cwl_transform_lambda_role_name = "${local.name}-cwl-split-role"

  # ARN references derived from the name so they are input-driven, never hard-coded.
  bucket_arn             = "arn:aws:s3:::${local.bucket_name}"
  glue_table_arn         = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.glue_database_name}/${local.glue_table_name}"
  firehose_log_group_arn = "arn:aws:logs:${local.region}:${local.account_id}:log-group:${local.firehose_log_group_name}:*"

  # Firehose delivery role trust policy.
  # This single role is wired as BOTH the Firehose delivery role_arn AND the
  # data_format_conversion schema_configuration.role_arn, so it must trust
  # firehose.amazonaws.com -- Firehose assumes it both to deliver to S3 and to
  # read the Glue Data Catalog schema during Parquet format conversion.
  firehose_assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "firehose.amazonaws.com" }
        Condition = {
          StringEquals = {
            "sts:ExternalId" = local.account_id
          }
        }
      }
    ]
  })

  # KMS key policy for the telemetry-data CMK per docs/terragrunt-concepts.md.
  # Principals are all input-driven -- no wildcard principal is permitted.
  # The Firehose role ARN is not known at plan time (it's created by the reference module),
  # so we include the root admin statement plus all required service principals in this fixture.
  # CloudWatchLogsAccess is required because the Firehose primitive creates an encrypted log group.
  # FirehoseServiceAccess is required because the Firehose stream uses CUSTOMER_MANAGED_CMK SSE.
  lake_kms_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAdminAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = ["kms:*"]
        Resource = "*"
      },
      {
        Sid    = "AthenaAndQuicksightAccess"
        Effect = "Allow"
        Principal = {
          Service = [
            "athena.amazonaws.com",
            "quicksight.amazonaws.com",
          ]
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsAccess"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
      {
        Sid    = "FirehoseServiceAccess"
        Effect = "Allow"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:CallerAccount" = local.account_id
          }
        }
      },
    ]
  })
}

module "example" {
  source = "../../"

  # S3 data lake bucket
  bucket_name = local.bucket_name

  # Test cleanup: the end-to-end terratest pushes a record through Firehose so it
  # lands a delivered object under raw/. force_destroy lets `terraform destroy` tear
  # the bucket down without a manual empty step. Test-environment only (the live
  # leaves set this true as well so the env stack is destroy/recreate-able).
  force_destroy = true

  # Lifecycle cold-tier transition -- storage class is input-driven (AC-11).
  transition_storage_class = var.transition_storage_class
  transition_days          = var.transition_days

  # Glue catalog database and format-conversion table
  glue_database_name = local.glue_database_name
  glue_table_name    = local.glue_table_name

  # Athena enum partition-projection values for the tool partition. The governed set
  # of tools that report into the lake, PLUS the terratest's unique per-run tool value,
  # which the test injects into this list before apply so the enum projects the per-run
  # partitions the end-to-end Athena proof queries (enum only projects the enumerated
  # tool values; an out-of-set tool would never be projected and its count would be 0).
  glue_partition_projection_tool_values = var.glue_partition_projection_tool_values

  # Maps a structured OTLP record's resource.service.name to the lake 'tool'
  # partition value the cwl_split transform Lambda assigns (RESHAPE). Inert ({}) unless
  # overridden; the terratest injects a mapping before apply for the reshape end-to-end proof.
  service_tool_map = var.service_tool_map

  # Firehose delivery stream
  firehose_stream_name = local.firehose_stream_name

  # Firehose dynamic partitioning JQ expressions (T27, AC-11).
  firehose_dynamic_partitioning_jq = var.firehose_dynamic_partitioning_jq

  # CloudWatch-Logs-split transform Lambda names (BUG-6).
  cwl_transform_lambda_name      = local.cwl_transform_lambda_name
  cwl_transform_lambda_role_name = local.cwl_transform_lambda_role_name

  # CloudWatch-Logs-split transform Lambda reserved concurrency passthrough (default null =
  # no reservation, existing behavior); the terratest overrides it to exercise a real reservation.
  cwl_transform_lambda_reserved_concurrent_executions = var.cwl_transform_lambda_reserved_concurrent_executions

  # IAM role name for the Firehose delivery role (also the schema_configuration role).
  firehose_role_name = local.firehose_role_name

  # Trust policy for the composed Firehose delivery role.
  firehose_assume_role_policy_json = local.firehose_assume_role_policy

  # ARN inputs for the Firehose role policy statements (B1, B4, S7).
  # The module builds the inline policy JSON internally from these ARN-scoped inputs
  # and wires module.lake_kms_key.key_arn for the KMS statement automatically.
  firehose_role_s3_bucket_arn  = local.bucket_arn
  firehose_role_glue_table_arn = local.glue_table_arn
  firehose_role_log_group_arn  = local.firehose_log_group_arn

  # telemetry-data CMK per docs/terragrunt-concepts.md (run-id-scoped alias, account-global).
  lake_kms_alias       = local.lake_kms_alias
  lake_kms_policy_json = local.lake_kms_policy

  # AWS region for kms:ViaService condition construction (docs/terragrunt-concepts.md).
  region = local.region

  # Cross-account read grant (input-driven, default {} = no grant). Enabled by the
  # fixture flag to the current account root so the applied bucket policy + KMS output
  # can be asserted (feat data-lake cross-account read grant).
  cross_account_read_principals = local.effective_cross_account_read_principals

  tags = local.tags
}

# -- Outputs re-exported from the data-lake reference --

output "firehose_delivery_stream_arn" {
  description = "ARN of the Firehose delivery stream (D37 input for collector-ingestion)."
  value       = module.example.firehose_delivery_stream_arn
}

output "firehose_stream_name" {
  description = "Name of the Firehose delivery stream (D37 alarm dimension consumed by observability)."
  value       = module.example.firehose_stream_name
}

output "data_lake_bucket_arn" {
  description = "ARN of the S3 data lake bucket."
  value       = module.example.data_lake_bucket_arn
}

output "glue_database_name" {
  description = "Name of the Glue catalog database."
  value       = module.example.glue_database_name
}

output "glue_table_name" {
  description = "Name of the Glue format-conversion table (exposed for the Athena query proof)."
  value       = local.glue_table_name
}

output "glue_table_parameters" {
  description = "The Athena partition-projection parameters rendered onto the Glue table (exposed for Terratest projection assertions)."
  value       = module.example.glue_table_parameters
}

output "lake_kms_key_arn" {
  description = "ARN of the telemetry-data KMS CMK."
  value       = module.example.lake_kms_key_arn
}

# Expose the AWS region for Terratest dynamic assertions (e.g. kms:ViaService construction).
output "aws_region" {
  description = "The AWS region in which the data-lake resources are deployed."
  value       = local.region
}

# Expose the Firehose role inline policy JSON for Terratest assertions (AC-3).
output "firehose_role_inline_policy_json" {
  description = "The Firehose delivery role inline policy JSON (exposed for Terratest scope assertions)."
  value       = module.example.firehose_role_inline_policy_json
}

# Expose the Firehose delivery role ARN so Terratest can confirm the Firehose
# data_format_conversion schema_configuration.role_arn equals the delivery role ARN.
output "firehose_role_arn" {
  description = "ARN of the Firehose delivery role (also the schema_configuration role)."
  value       = module.example.firehose_role_arn
}

# Expose the Firehose delivery role trust policy JSON so Terratest can confirm the
# role (which is also the schema_configuration role) trusts firehose.amazonaws.com.
output "firehose_assume_role_policy_json" {
  description = "The Firehose delivery role trust policy JSON (exposed for Terratest assume-role assertions)."
  value       = module.example.firehose_assume_role_policy_json
}

# Expose the KMS key policy JSON for Terratest no-wildcard assertions (AC-3).
output "lake_kms_key_policy_json" {
  description = "The telemetry-data KMS key policy JSON (exposed for Terratest wildcard assertions)."
  value       = module.example.lake_kms_key_policy_json
}

# Expose transition_storage_class for Terratest lifecycle propagation assertion (AC-11).
output "transition_storage_class" {
  description = "The S3 lifecycle transition storage class (exposed for Terratest propagation assertions)."
  value       = module.example.transition_storage_class
}

# Expose the JQ partitioning map for Terratest passthrough assertion (AC-11).
output "firehose_dynamic_partitioning_jq_output" {
  description = "The firehose_dynamic_partitioning_jq map passed through to the composed Firehose primitive."
  value       = module.example.firehose_dynamic_partitioning_jq_output
}

# Expose the CloudWatch-Logs-split transform Lambda ARN for Terratest assertions (the
# Firehose processing_configuration's first processor, which splits any-size CWL deliveries).
output "cwl_transform_lambda_arn" {
  description = "ARN of the CloudWatch-Logs-split Firehose transform Lambda (the record-splitting processor)."
  value       = module.example.cwl_transform_lambda_arn
}

# Expose the CloudWatch-Logs-split transform Lambda function name for Terratest assertions
# (the FunctionName CloudWatch AWS/Lambda alarm dimension consumed by the observability unit, D37).
output "cwl_transform_lambda_name" {
  description = "Name of the CloudWatch-Logs-split Firehose transform Lambda (the FunctionName alarm dimension consumed by observability)."
  value       = module.example.cwl_transform_lambda_name
}

# Expose the cross-account KMS key-policy statements the module derives, so Terratest can
# assert the empty-by-default no-op and the Decrypt/DescribeKey grant when the fixture is on.
output "cross_account_kms_statements" {
  description = "The cross-account KMS key-policy statements exposed by the data-lake module (empty when no cross-account principals are configured); the terragrunt leaf merges these into lake_kms_policy_json."
  value       = module.example.cross_account_kms_statements
}

# Expose the account ids granted cross-account read in this fixture so Terratest can assert
# the applied bucket policy + KMS statements reference the expected account root (input-driven).
output "cross_account_principal_account_ids" {
  description = "The account ids granted cross-account read in this fixture (empty by default; the current account when enable_cross_account_read_fixture is true)."
  value       = [for p in values(local.effective_cross_account_read_principals) : p.account_id]
}
