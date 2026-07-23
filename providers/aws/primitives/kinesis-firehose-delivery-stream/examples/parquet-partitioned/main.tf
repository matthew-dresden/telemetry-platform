data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # All resource names derived from stream_name input -- no hard-coded identifiers.
  bucket_name        = "${var.stream_name}-bucket"
  firehose_role_name = "${var.stream_name}-fh-role"
  glue_role_name     = "${var.stream_name}-glue-role"
  kms_alias          = "alias/${var.stream_name}-cmk"
  glue_database_name = replace("${var.stream_name}_telemetry", "-", "_")
  glue_table_name    = replace("${var.stream_name}_events", "-", "_")
  bucket_arn         = "arn:aws:s3:::${local.bucket_name}"
  glue_table_arn     = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.glue_database_name}/${local.glue_table_name}"

  # Firehose service trust policy.
  firehose_assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FirehoseAssumeRole"
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
        Condition = {
          StringEquals = {
            "sts:ExternalId" = local.account_id
          }
        }
      }
    ]
  })

  # Glue service trust policy.
  # Firehose must also be able to assume the Glue role to access the schema configuration
  # (schema_configuration.role_arn in data_format_conversion_configuration).
  # Use separate statements per service principal for broadest AWS compatibility.
  glue_assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GlueAssumeRole"
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "glue.amazonaws.com"
        }
      },
      {
        Sid    = "FirehoseAssumeGlueRole"
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
      }
    ]
  })

  # IAM inline policy allowing Firehose to write to S3, use KMS, access Glue, and write CloudWatch logs.
  firehose_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Write"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject",
        ]
        Resource = [
          "arn:aws:s3:::${local.bucket_name}",
          "arn:aws:s3:::${local.bucket_name}/*",
        ]
      },
      {
        Sid    = "KMSAccess"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = aws_kms_key.this.arn
      },
      {
        Sid    = "GlueCatalogAccess"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTableVersion",
          "glue:GetTableVersions",
        ]
        Resource = [
          "arn:aws:glue:${local.region}:${local.account_id}:catalog",
          "arn:aws:glue:${local.region}:${local.account_id}:database/${local.glue_database_name}",
          local.glue_table_arn,
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:PutLogEvents",
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/firehose/*"
      },
    ]
  })

  # IAM inline policy allowing the Glue role to access the Glue catalog.
  glue_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GlueCatalogAccess"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTableVersion",
          "glue:GetTableVersions",
          "glue:GetDatabase",
        ]
        Resource = [
          "arn:aws:glue:${local.region}:${local.account_id}:catalog",
          "arn:aws:glue:${local.region}:${local.account_id}:database/${local.glue_database_name}",
          local.glue_table_arn,
        ]
      },
    ]
  })

  # KMS key policy -- root admin + Firehose + CloudWatch Logs service principal access.
  # CloudWatch Logs requires explicit KMS key policy access to create encrypted log groups.
  kms_key_policy = jsonencode({
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
      },
      {
        Sid    = "S3ServiceAccess"
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
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
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
    ]
  })
}

# Customer-managed KMS key for at-rest encryption.
resource "aws_kms_key" "this" {
  description             = "CMK for ${var.stream_name} Firehose delivery stream"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.kms_key_policy

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-parquet-partitioned-example"
    Owner       = "terraform"
  }
}

resource "aws_kms_alias" "this" {
  name          = local.kms_alias
  target_key_id = aws_kms_key.this.key_id
}

# S3 bucket for Firehose delivery target.
resource "aws_s3_bucket" "this" {
  bucket        = local.bucket_name
  force_destroy = true

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-parquet-partitioned-example"
    Owner       = "terraform"
  }
}

# Block all public access to the delivery-target bucket.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning so delivered objects can be recovered.
resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt the bucket at rest with the customer-managed CMK (SSE-KMS).
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.this.arn
    }
    bucket_key_enabled = true
  }
}

# Model secure usage: enable S3 server access logging. This self-contained fixture
# bucket is a self-logging terminal target -- its own access logs are written back
# under the "s3-access-logs/" prefix, the same self-logging pattern used by the
# repo's state-bootstrap access-log bucket. S3 permits a bucket to be its own log
# target with a prefix, so there is no recursion.
resource "aws_s3_bucket_logging" "this" {
  bucket = aws_s3_bucket.this.id

  target_bucket = aws_s3_bucket.this.id
  target_prefix = "s3-access-logs/"
}

# Glue catalog database for format conversion schema.
resource "aws_glue_catalog_database" "this" {
  name        = local.glue_database_name
  description = "Glue database for ${var.stream_name} Parquet conversion test"
}

# Glue catalog table with minimal Parquet schema required for Firehose format conversion.
resource "aws_glue_catalog_table" "this" {
  name          = local.glue_table_name
  database_name = aws_glue_catalog_database.this.name

  storage_descriptor {
    location      = "s3://${local.bucket_name}/raw/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "tool"
      type = "string"
    }

    columns {
      name = "timestamp"
      type = "string"
    }

    columns {
      name = "body"
      type = "string"
    }
  }
}

# IAM role for Firehose delivery.
resource "aws_iam_role" "firehose" {
  name               = local.firehose_role_name
  assume_role_policy = local.firehose_assume_role_policy

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-parquet-partitioned-example"
    Owner       = "terraform"
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${local.firehose_role_name}-policy"
  role   = aws_iam_role.firehose.id
  policy = local.firehose_policy
}

# IAM role for Glue format conversion.
resource "aws_iam_role" "glue" {
  name               = local.glue_role_name
  assume_role_policy = local.glue_assume_role_policy

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-parquet-partitioned-example"
    Owner       = "terraform"
  }
}

resource "aws_iam_role_policy" "glue" {
  name   = "${local.glue_role_name}-policy"
  role   = aws_iam_role.glue.id
  policy = local.glue_policy
}

module "example" {
  source = "../../"

  name        = var.stream_name
  destination = "extended_s3"
  bucket_arn  = local.bucket_arn
  role_arn    = aws_iam_role.firehose.arn
  kms_key_arn = aws_kms_key.this.arn

  enable_format_conversion = true
  data_format_conversion = {
    glue_database_name = aws_glue_catalog_database.this.name
    glue_table_name    = aws_glue_catalog_table.this.name
    glue_role_arn      = aws_iam_role.glue.arn
  }

  dynamic_partitioning_enabled = true
  dynamic_partitioning_jq      = var.dynamic_partitioning_jq

  # Optional record-splitting transform Lambda (the CloudWatch Logs subscription
  # front end), input-driven and default null.
  transform_lambda_arn = var.transform_lambda_arn

  prefix              = "raw/tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp:yyyy-MM-dd}/"
  error_output_prefix = "errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/"
  buffering_size      = 64
  buffering_interval  = 300
  compression_format  = "UNCOMPRESSED"

  cloudwatch_logging_enabled = true
  log_retention_in_days      = 30

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-parquet-partitioned-example"
    Owner       = "terraform"
  }

  depends_on = [
    aws_s3_bucket.this,
    aws_iam_role.firehose,
    aws_iam_role_policy.firehose,
    aws_iam_role.glue,
    aws_iam_role_policy.glue,
    aws_kms_key.this,
    aws_glue_catalog_database.this,
    aws_glue_catalog_table.this,
  ]
}

output "delivery_stream_arn" {
  description = "The ARN of the Firehose delivery stream."
  value       = module.example.delivery_stream_arn
}

output "delivery_stream_name" {
  description = "The name of the Firehose delivery stream."
  value       = module.example.delivery_stream_name
}

output "log_group_name" {
  description = "The CloudWatch log group name for delivery errors."
  value       = module.example.log_group_name
}
