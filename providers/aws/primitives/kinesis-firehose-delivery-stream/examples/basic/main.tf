data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # All resource names derived from stream_name input -- no hard-coded identifiers.
  bucket_name = "${var.stream_name}-bucket"
  role_name   = "${var.stream_name}-firehose-role"
  kms_alias   = "alias/${var.stream_name}-cmk"
  bucket_arn  = "arn:aws:s3:::${local.bucket_name}"

  # Firehose service trust policy -- no wildcard principal.
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

  # IAM inline policy allowing Firehose to write to S3 and use the CMK.
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
    ]
  })

  # KMS key policy -- root admin + Firehose + S3 service principal access.
  # The same CMK encrypts both the Firehose delivery payload and the destination
  # S3 bucket at rest (SSE-KMS), so S3 must be granted GenerateDataKey/Decrypt.
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
    Purpose     = "kinesis-firehose-delivery-stream-module-basic-example"
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
    Purpose     = "kinesis-firehose-delivery-stream-module-basic-example"
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

# IAM role that Firehose assumes to write to S3.
resource "aws_iam_role" "firehose" {
  name               = local.role_name
  assume_role_policy = local.firehose_assume_role_policy

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-basic-example"
    Owner       = "terraform"
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${local.role_name}-policy"
  role   = aws_iam_role.firehose.id
  policy = local.firehose_policy
}

module "example" {
  source = "../../"

  name        = var.stream_name
  destination = "extended_s3"
  bucket_arn  = local.bucket_arn
  role_arn    = aws_iam_role.firehose.arn
  kms_key_arn = aws_kms_key.this.arn

  enable_format_conversion     = false
  dynamic_partitioning_enabled = false
  cloudwatch_logging_enabled   = false

  prefix              = "raw/"
  error_output_prefix = "errors/"
  buffering_size      = 64
  buffering_interval  = 300
  compression_format  = "UNCOMPRESSED"

  tags = {
    Environment = "test"
    Purpose     = "kinesis-firehose-delivery-stream-module-basic-example"
    Owner       = "terraform"
  }

  depends_on = [
    aws_s3_bucket.this,
    aws_iam_role.firehose,
    aws_iam_role_policy.firehose,
    aws_kms_key.this,
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
  description = "The CloudWatch log group name (null when logging is disabled)."
  value       = module.example.log_group_name
}
