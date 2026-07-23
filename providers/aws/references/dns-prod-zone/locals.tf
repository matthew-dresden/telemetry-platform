locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Principal-scoped KMS key policy for the prod DNS/cert CMK (docs/terragrunt-concepts.md).
  # Grants kms:Decrypt and kms:GenerateDataKey to the SSM service under a ViaService condition
  # scoped to ssm.<region>.amazonaws.com (region is injected via var.region). No wildcard principals are permitted.
  kms_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableKeyAdministration"
        Effect = "Allow"
        Principal = {
          AWS = var.kms_key_principals
        }
        Action = [
          "kms:Create*",
          "kms:Describe*",
          "kms:Enable*",
          "kms:List*",
          "kms:Put*",
          "kms:Update*",
          "kms:Revoke*",
          "kms:Disable*",
          "kms:Get*",
          "kms:Delete*",
          "kms:ScheduleKeyDeletion",
          "kms:CancelKeyDeletion",
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowSSMViaService"
        Effect = "Allow"
        Principal = {
          AWS = var.kms_key_principals
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.region}.amazonaws.com"
          }
        }
      },
      # CloudWatch Logs service grant (docs/terragrunt-concepts.md; AWS CloudWatch Logs
      # CMK docs). The telemetry-config CMK (this key) encrypts the portal WAF
      # CloudWatch log group: the portal reference wires waf_log_kms_key_arn = this
      # key's ARN to the reused waf-webacl create_log_group, which sets kms_key_id on
      # its aws_cloudwatch_log_group. CloudWatch Logs validates that the encrypting CMK
      # grants the logs service principal at log-group create time; without this
      # statement CreateLogGroup fails with AccessDeniedException ("KMS key does not
      # exist or is not allowed to be used"). The ArnLike condition on
      # kms:EncryptionContext:aws:logs:arn scopes the grant to this account/region's log
      # groups (AWS-recommended least-privilege condition). region is input-driven
      # (var.region) -- never hardcoded (D47). No wildcard principal is used.
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.region}.amazonaws.com"
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
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
          }
        }
      },
      # Cost Anomaly Detection service grant. The observability reference encrypts its
      # alerts SNS topic with this CMK (alias/telemetry-config). AWS Cost Anomaly
      # Detection publishes from the costalerts.amazonaws.com service principal and must
      # be able to generate a data key / decrypt against the encrypting CMK, or
      # publishing to the encrypted topic fails. Action set is the AWS-documented minimum
      # for a service publishing to a CMK-encrypted SNS topic. The aws:SourceAccount
      # condition scopes the grant to this account's cost-anomaly activity
      # (AWS-recommended least privilege). account id is input-driven via
      # data.aws_caller_identity -- never hardcoded (D45/D47). No wildcard principal.
      {
        Sid    = "AllowCostAnomalyDetectionEncryptedSNS"
        Effect = "Allow"
        Principal = {
          Service = "costalerts.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
    ]
  }

  kms_policy_json = jsonencode(local.kms_policy)

  # ---------------------------------------------------------------------------
  # Cross-account remote-state read gating (foundation-tier only; default OFF).
  # ---------------------------------------------------------------------------
  enable_tfstate_cmk_grant     = length(var.tfstate_cmk_decrypt_grantee_arns) > 0
  enable_tfstate_bucket_policy = length(var.tfstate_bucket_read_grantee_arns) > 0

  tfstate_bucket_arns = [
    "arn:aws:s3:::${var.tfstate_bucket_name}",
    "arn:aws:s3:::${var.tfstate_bucket_name}/*",
  ]

  # Bucket policy attached to this unit's own remote-state bucket. It REPLICATES
  # terragrunt's two managed backend statements verbatim (Sid "EnforcedTLS" and
  # "RootAccess") so terragrunt's additive S3 backend policy management finds both
  # of its required statements already present and reports no drift, then ADDS the
  # cross-account read statements. aws_s3_bucket_policy owns the whole policy
  # document, so the replicated statements must match terragrunt's exactly.
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
