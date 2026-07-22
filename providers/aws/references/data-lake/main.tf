# The current account id, used to construct the deterministic Firehose stream ARN for the
# split-Lambda's IAM policy without forming a dependency cycle on the stream resource (D31:
# the current account, not an account-id literal).
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# telemetry-data KMS CMK (alias/telemetry-data, per docs/terragrunt-concepts.md).
# This reference owns ONLY the telemetry-data CMK.
# The telemetry-config (S4) and telemetry-spice (S5) CMKs are owned elsewhere.
# ---------------------------------------------------------------------------
module "lake_kms_key" {
  source = var.lake_kms_key_source

  alias_name          = var.lake_kms_alias
  description         = "Customer managed key for telemetry data lake S3 encryption (alias/telemetry-data)"
  enable_key_rotation = true
  policy_json         = var.lake_kms_policy_json

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "kms-key"
}

# ---------------------------------------------------------------------------
# Access-log bucket -- receives S3 server access logs from the data lake bucket.
# This terminal log bucket SELF-LOGS: its own access logs are written back to
# itself under the "<bucket_name>/" prefix (the s3-bucket primitive's
# target_prefix). S3 permits a bucket to be its own log target with a prefix, so
# there is no circular recursion -- which enables S3 server access logging on the
# data lake bucket (clearing trivy AWS-0089 "logging disabled" without a
# suppression). A dedicated log bucket -- rather than self-logging the data lake
# bucket -- keeps access-log objects out of the data lake so Firehose/Glue/Athena
# never see them. This mirrors the self-logging terminal access-log bucket the
# state-bootstrap reference owns. It reuses the telemetry-data CMK for at-rest
# encryption (BucketOwnerPreferred so the S3 LogDelivery grant the data lake
# bucket's server-access-logging writes is accepted).
# ---------------------------------------------------------------------------
module "access_log_bucket" {
  source = var.lake_bucket_source

  bucket_name              = local.access_log_bucket_name
  force_destroy            = var.force_destroy
  versioning_enabled       = true
  kms_key_arn              = module.lake_kms_key.key_arn
  bucket_key_enabled       = true
  access_log_target_bucket = local.access_log_bucket_name
  object_ownership         = "BucketOwnerPreferred"

  block_public_access = {
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
  }

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "s3-bucket"

  depends_on = [module.lake_kms_key]
}

# ---------------------------------------------------------------------------
# S3 data lake bucket (B1) encrypted with the telemetry-data CMK.
# Lifecycle cold-tier transition is propagated from var.transition_storage_class (AC-11).
# S3 server access logging is delivered to the dedicated access-log bucket above.
# ---------------------------------------------------------------------------
module "lake_bucket" {
  source = var.lake_bucket_source

  bucket_name              = var.bucket_name
  force_destroy            = var.force_destroy
  versioning_enabled       = true
  kms_key_arn              = module.lake_kms_key.key_arn
  bucket_key_enabled       = true
  lifecycle_rules          = local.lifecycle_rules
  access_log_target_bucket = local.access_log_bucket_name

  block_public_access = {
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
  }

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "s3-bucket"

  depends_on = [module.lake_kms_key, module.access_log_bucket]
}

# ---------------------------------------------------------------------------
# Cross-account read grant on the data lake bucket (input-driven, default off).
# Created only when var.cross_account_read_principals is non-empty. Grants the
# configured external account roots S3 read (GetObject/ListBucket/GetBucketLocation)
# on the lake bucket + its objects so a central BI/analytics role can read the
# telemetry data lake via Athena/Glue (Lake Formation is not in use). The matching
# KMS Decrypt/DescribeKey grant is exposed via the cross_account_kms_statements
# output for the terragrunt leaf to merge into the telemetry-data CMK key policy
# (this reference does not own the whole key policy). The bucket keeps
# block_public_policy = true: an account-root principal is not a public grant, so
# PutBucketPolicy accepts it. count (0/1) driven purely by the input map size, so
# the default {} preserves existing behavior with no policy resource created.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket_policy" "lake_cross_account" {
  count = length(var.cross_account_read_principals) > 0 ? 1 : 0

  bucket = module.lake_bucket.bucket_id
  policy = local.cross_account_s3_bucket_policy_json
}

# ---------------------------------------------------------------------------
# Glue catalog database and format-conversion table (B4).
# ---------------------------------------------------------------------------
module "glue_catalog" {
  source = var.glue_catalog_source

  database_name = var.glue_database_name
  description   = "Telemetry data lake catalog for Firehose Parquet format conversion"
  location_uri  = "s3://${var.bucket_name}/"
  create_table  = true
  table         = local.glue_table

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "glue-catalog-database"

  depends_on = [module.lake_bucket]
}

# ---------------------------------------------------------------------------
# CloudWatch-Logs-split transform Lambda (BUG-6).
#
# The telemetry Firehose stream's source is a CloudWatch Logs subscription filter, whose
# records arrive GZIP-compressed and wrapped in the CloudWatch Logs envelope, batching MANY
# logEvents per record. The AWS-native unwrap path (Decompression -> CloudWatchLogProcessing
# -> RecordDeAggregation) cannot handle deliveries larger than 500 events because
# RecordDeAggregation is hard-capped at 500 sub-records per record (a larger record is passed
# WHOLE to the dynamic-partitioning MetadataExtraction JQ, which rejects it and routes every
# event to errors/metadata-extraction-failed/). This Lambda is wired as the Firehose
# processing_configuration's first processor (transform_lambda_arn below): it decompresses +
# envelope-strips each record and re-ingests each logEvents[].message as its own single-JSON
# record via firehose:PutRecordBatch (no 500-record cap), so a delivery of ANY size is split
# into individual JSON records before MetadataExtraction + Parquet conversion.
#
# A raw aws_lambda_function (not the lambda primitive) is used so the function's timeout +
# memory can be sized for high-volume re-ingestion -- the lambda primitive exposes neither.
# ---------------------------------------------------------------------------
data "archive_file" "cwl_transform" {
  type        = "zip"
  source_file = "${path.module}/lambda/cwl_split/index.py"
  output_path = "${path.module}/.build/cwl_split.zip"
}

# Pre-created so the function never auto-creates an unencrypted, never-expiring log group.
# Encrypted with the telemetry-data CMK (the CMK policy already grants logs.<region>).
resource "aws_cloudwatch_log_group" "cwl_transform" {
  name              = local.cwl_transform_lambda_log_group_name
  retention_in_days = var.cwl_transform_lambda_log_retention_in_days
  kms_key_id        = module.lake_kms_key.key_arn

  tags = local.common_tags

  depends_on = [module.lake_kms_key]
}

# Execution role: scoped inline policy (firehose re-ingest + CMK + self-logging) plus the
# AWS-managed AWSXRayDaemonWriteAccess for X-Ray write (which AWS only grants on "*", kept out
# of the inline policy so it stays wildcard-free). Reuses the iam-role primitive source.
module "cwl_transform_lambda_role" {
  source = var.firehose_role_source

  name        = var.cwl_transform_lambda_role_name
  description = "Execution role for the CloudWatch-Logs-split Firehose transform Lambda (BUG-6)"
  assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })
  managed_policy_arns = ["arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"]
  inline_policies = {
    CwlSplitTransformPolicy = local.cwl_transform_lambda_policy_json
  }

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"

  depends_on = [aws_cloudwatch_log_group.cwl_transform]
}

resource "aws_lambda_function" "cwl_transform" {
  function_name    = var.cwl_transform_lambda_name
  description      = "Splits CloudWatch Logs subscription deliveries into single-JSON records via re-ingestion (BUG-6)"
  runtime          = var.cwl_transform_lambda_runtime
  handler          = "index.handler"
  filename         = data.archive_file.cwl_transform.output_path
  source_code_hash = data.archive_file.cwl_transform.output_base64sha256
  role             = module.cwl_transform_lambda_role.role_arn
  timeout          = var.cwl_transform_lambda_timeout
  memory_size      = var.cwl_transform_lambda_memory_size
  # Reserves a guaranteed slice of the account's regional concurrency pool for this
  # function so the Firehose transform is never starved by other functions under
  # high-volume re-ingestion. null (the default) = no reservation, drawing from the
  # unreserved account pool (Terraform/AWS accept null as "no reservation set").
  reserved_concurrent_executions = var.cwl_transform_lambda_reserved_concurrent_executions

  environment {
    variables = {
      DELIVERY_STREAM_NAME = var.firehose_stream_name
      SERVICE_TOOL_MAP     = jsonencode(var.service_tool_map)
    }
  }

  # Active X-Ray tracing (secure-by-default); the role carries AWSXRayDaemonWriteAccess.
  tracing_config {
    mode = "Active"
  }

  tags = local.common_tags

  depends_on = [
    module.cwl_transform_lambda_role,
    aws_cloudwatch_log_group.cwl_transform,
  ]
}

# ---------------------------------------------------------------------------
# Firehose delivery IAM role (per docs/terragrunt-concepts.md).
# The role_arn output feeds the Firehose primitive below -- no dangling wiring.
# This single role serves BOTH the delivery role_arn AND the format-conversion
# schema_configuration.role_arn: it trusts firehose.amazonaws.com and its inline
# policy already grants glue:GetTable/GetTableVersion/GetTableVersions on the
# format-conversion table (locals.tf GlueFormatConversionTableAccess), so Firehose
# can assume it both to deliver to S3 and to read the Glue Data Catalog schema.
# ---------------------------------------------------------------------------
module "firehose_role" {
  source = var.firehose_role_source

  name                    = var.firehose_role_name
  description             = "Firehose delivery role for telemetry data lake (least-privilege per docs/terragrunt-concepts.md)"
  assume_role_policy_json = var.firehose_assume_role_policy_json
  # Inline policy built from input-driven ARNs scoped per docs/terragrunt-concepts.md.
  # Defined in locals.tf so it can reference module.lake_kms_key.key_arn.
  inline_policies = {
    FirehoseDeliveryPolicy = local.firehose_delivery_policy_json
  }

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "iam-role"
}

# ---------------------------------------------------------------------------
# Kinesis Firehose delivery stream.
# role_arn is wired from module.firehose_role.role_arn (not a passed-in literal).
# data_format_conversion.glue_role_arn is the schema_configuration role that
# Firehose itself assumes to read the Glue Data Catalog during Parquet format
# conversion. It is wired from module.firehose_role.role_arn (the delivery role),
# because that role already trusts firehose.amazonaws.com AND already holds
# glue:GetTable/GetTableVersion/GetTableVersions on the format-conversion table
# (locals.tf GlueFormatConversionTableAccess). A separate glue.amazonaws.com-only
# role cannot be assumed by Firehose, which is why CreateDeliveryStream rejected it.
# firehose_dynamic_partitioning_jq is passed through from var input (T27, AC-11).
# ---------------------------------------------------------------------------
module "firehose" {
  source = var.firehose_source

  name        = var.firehose_stream_name
  role_arn    = module.firehose_role.role_arn
  bucket_arn  = module.lake_bucket.bucket_arn
  kms_key_arn = module.lake_kms_key.key_arn

  enable_format_conversion = true
  data_format_conversion = {
    glue_database_name = module.glue_catalog.database_name
    glue_table_name    = var.glue_table_name
    glue_role_arn      = module.firehose_role.role_arn
  }

  dynamic_partitioning_enabled = true
  dynamic_partitioning_jq      = var.firehose_dynamic_partitioning_jq

  # CloudWatch-Logs-split transform Lambda (the collector-ingestion awscloudwatchlogs hop).
  # Wired as the processing_configuration's first processor: it decompresses + envelope-strips
  # each GZIP CWL subscription record and re-ingests each logEvents[].message as its own
  # single-JSON record via firehose:PutRecordBatch (no 500-record cap), so a delivery of any
  # size is split into individual JSON records before the dynamic-partitioning MetadataExtraction
  # JQ and Parquet conversion run. The firehose delivery role holds lambda:InvokeFunction on it.
  transform_lambda_arn = aws_lambda_function.cwl_transform.arn

  # S3 prefixes must contain !{partitionKeyFromQuery:<key>} or !{timestamp:<fmt>}
  # namespaces when dynamic partitioning is enabled; AWS rejects a stream whose
  # prefix omits those namespaces (InvalidArgumentException).
  prefix              = var.firehose_prefix
  error_output_prefix = var.firehose_error_output_prefix

  cloudwatch_logging_enabled = true

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "kinesis-firehose-delivery-stream"

  depends_on = [module.firehose_role, module.glue_catalog, aws_lambda_function.cwl_transform]
}
