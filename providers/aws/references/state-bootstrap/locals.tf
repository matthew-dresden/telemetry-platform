locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Derived bucket names: access-log bucket and artifact (state) bucket.
  access_log_bucket_name = "${var.bucket_prefix}-access-logs"
  artifact_bucket_name   = "${var.bucket_prefix}-tfstate"

  # TLS-only bucket policy: deny all HTTP requests (aws:SecureTransport=false).
  # Applied to the artifact bucket to enforce encrypted-in-transit access.
  # Spec AC #24/#25 hardening posture requirement.
  artifact_bucket_tls_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${local.artifact_bucket_name}",
          "arn:aws:s3:::${local.artifact_bucket_name}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}
