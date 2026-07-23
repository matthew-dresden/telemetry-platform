data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # KMS key policy -- root admin + S3 service principal access.
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

# Customer-managed KMS key for S3 server-side encryption.
# Self-contained: created and destroyed with this example -- no external KMS ARN required.
resource "aws_kms_key" "s3_encryption" {
  description             = "CMK for ${var.bucket_name} S3 server-side encryption (data-lake example)"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.kms_key_policy

  tags = {
    Environment = "test"
    Purpose     = "s3-bucket-module-data-lake-example"
    Owner       = "terraform"
  }
}

resource "aws_kms_alias" "s3_encryption" {
  name          = "alias/${var.bucket_name}-cmk"
  target_key_id = aws_kms_key.s3_encryption.key_id
}

module "example" {
  source = "../../"

  bucket_name        = var.bucket_name
  kms_key_arn        = aws_kms_key.s3_encryption.arn
  versioning_enabled = var.versioning_enabled
  bucket_key_enabled = var.bucket_key_enabled
  force_destroy      = var.force_destroy

  # Model secure usage: enable S3 server access logging. This fixture bucket is a
  # self-logging terminal target (its own access logs are written back under the
  # "<bucket_name>/" prefix), the same self-logging pattern the state-bootstrap
  # access-log bucket uses. S3 permits a bucket to be its own log target with a
  # prefix, so there is no recursion -- and the example clears trivy AWS-0089
  # (logging disabled) by exercising the module's access_log_target_bucket input
  # rather than suppressing the finding.
  access_log_target_bucket = var.bucket_name

  block_public_access = {
    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
  }

  object_ownership = "BucketOwnerEnforced"

  lifecycle_rules = [
    {
      id                       = "data-lake-retention"
      enabled                  = true
      prefix                   = ""
      transition_days          = var.transition_days
      transition_storage_class = var.transition_storage_class
      expiration_days          = var.expiration_days
    }
  ]

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "s3-bucket-module-data-lake-example"
    Owner       = "terraform"
  })

  depends_on = [aws_kms_key.s3_encryption]
}

output "bucket_id" {
  description = "The name of the bucket."
  value       = module.example.bucket_id
}

output "bucket_arn" {
  description = "The ARN of the bucket."
  value       = module.example.bucket_arn
}

output "bucket_domain_name" {
  description = "The bucket domain name."
  value       = module.example.bucket_domain_name
}

output "bucket_regional_domain_name" {
  description = "The bucket region-specific domain name."
  value       = module.example.bucket_regional_domain_name
}
