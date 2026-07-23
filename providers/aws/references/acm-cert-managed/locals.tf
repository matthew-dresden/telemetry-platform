locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # ---------------------------------------------------------------------------
  # Cross-account remote-state read gating (default OFF).
  # ---------------------------------------------------------------------------
  enable_tfstate_cmk_grant     = length(var.tfstate_cmk_decrypt_grantee_arns) > 0
  enable_tfstate_bucket_policy = length(var.tfstate_bucket_read_grantee_arns) > 0

  tfstate_bucket_arns = [
    "arn:aws:s3:::${var.tfstate_bucket_name}",
    "arn:aws:s3:::${var.tfstate_bucket_name}/*",
  ]

  # Bucket policy attached to this unit's own remote-state bucket. It REPLICATES
  # terragrunt's two managed backend statements verbatim (Sid "EnforcedTLS" and
  # "RootAccess") so terragrunt's additive S3 backend policy management finds both of its
  # required statements already present and reports no drift, then ADDS the cross-account
  # read statements. aws_s3_bucket_policy owns the whole policy document, so the
  # replicated statements must match terragrunt's exactly.
  tfstate_bucket_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnforcedTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = local.tfstate_bucket_arns
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid    = "RootAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.tfstate_bucket_account_id}:root"
        }
        Action   = "s3:*"
        Resource = local.tfstate_bucket_arns
      },
      {
        Sid    = "CrossAccountStateObjectRead"
        Effect = "Allow"
        Principal = {
          AWS = var.tfstate_bucket_read_grantee_arns
        }
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
        ]
        Resource = "arn:aws:s3:::${var.tfstate_bucket_name}/*"
      },
      {
        Sid    = "CrossAccountStateBucketList"
        Effect = "Allow"
        Principal = {
          AWS = var.tfstate_bucket_read_grantee_arns
        }
        Action = [
          "s3:ListBucket",
          "s3:GetBucketVersioning",
        ]
        Resource = "arn:aws:s3:::${var.tfstate_bucket_name}"
      },
    ]
  }

  tfstate_bucket_policy_json = jsonencode(local.tfstate_bucket_policy)
}
