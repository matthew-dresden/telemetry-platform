# ---------------------------------------------------------------------------
# vpc-origin example.
#
# Models the OTLP collector edge AFTER the CloudFront origin self-loop fix: the
# distribution reaches a PRIVATE/INTERNAL ALB inside a VPC through a CloudFront
# VPC origin (aws_cloudfront_vpc_origin) instead of over the public internet.
# CloudFront routes to the ALB by the VPC origin (the ALB ARN) and NOT by public
# DNS resolution of the origin domain_name, so a domain_name that resolves to this
# same distribution does NOT create a CloudFront->CloudFront self-loop.
#
# vpc_origin_alb_arn is injected at apply time as the ARN of a real, Active internal
# ALB (CreateVpcOrigin validates the ARN). The statically-resolvable offline default
# lets `terraform validate` and the negative plan tests exercise the VPC-origin HCL
# path (origin_type = "vpc" -> vpc_origin_config + aws_cloudfront_vpc_origin) without
# standing up an ALB; the real end-to-end apply path is exercised by the
# collector-ingestion reference module, which composes this primitive with the live
# internal ALB it builds.
# ---------------------------------------------------------------------------
data "aws_canonical_user_id" "current" {}

data "aws_cloudfront_log_delivery_canonical_user_id" "current" {}

resource "aws_s3_bucket" "access_logs" {
  bucket        = "cf-vpc-access-logs-${var.log_bucket_suffix}"
  force_destroy = true

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "cloudfront-distribution-vpc-origin-example-access-logs"
    Owner       = "terraform"
  })
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_ownership_controls" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  access_control_policy {
    owner {
      id = data.aws_canonical_user_id.current.id
    }

    grant {
      grantee {
        id   = data.aws_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }

    grant {
      grantee {
        id   = data.aws_cloudfront_log_delivery_canonical_user_id.current.id
        type = "CanonicalUser"
      }
      permission = "FULL_CONTROL"
    }

    # S3 server access logging is delivered by the S3 LogDelivery group, which
    # requires WRITE on the bucket and READ_ACP on the bucket ACL. This bucket is
    # its own S3-access-log target (see aws_s3_bucket_logging.access_logs below),
    # so granting LogDelivery is the AWS-required, secure permission for that role.
    grant {
      grantee {
        type = "Group"
        uri  = "http://acs.amazonaws.com/groups/s3/LogDelivery"
      }
      permission = "WRITE"
    }

    grant {
      grantee {
        type = "Group"
        uri  = "http://acs.amazonaws.com/groups/s3/LogDelivery"
      }
      permission = "READ_ACP"
    }
  }

  depends_on = [
    aws_s3_bucket_ownership_controls.access_logs,
    aws_s3_bucket_public_access_block.access_logs,
  ]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Self-logging terminal target -- the CloudFront standard-logging bucket records its
# OWN S3 access logs under the "s3-access-logs/" prefix (same pattern as alb-origin),
# clearing trivy AWS-0089 without a suppression while staying on the SSE-S3 (AES256)
# encryption CloudFront standard logging requires.
resource "aws_s3_bucket_logging" "access_logs" {
  bucket        = aws_s3_bucket.access_logs.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "s3-access-logs/"
}

# ---------------------------------------------------------------------------
# Real CLOUDFRONT-scope WAF Web ACL fixture.
# CloudFront CreateDistribution validates the Web ACL ARN belongs to the calling
# account (a foreign-account ARN is rejected with InvalidWebACLId). This provisions
# a real, account-owned CLOUDFRONT-scope Web ACL so the distribution attaches a
# valid WAF. CLOUDFRONT-scope WAF resources must be created in us-east-1.
# ---------------------------------------------------------------------------
resource "aws_wafv2_web_acl" "cf" {
  name        = "cf-vpc-origin-${var.log_bucket_suffix}"
  description = "CLOUDFRONT-scope Web ACL fixture for cloudfront-distribution vpc-origin example"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "cf-vpc-origin-${var.log_bucket_suffix}"
    sampled_requests_enabled   = true
  }

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "cloudfront-distribution-vpc-origin-example-waf"
    Owner       = "terraform"
  })
}

locals {
  # acm_certificate_arn is the externally-provided ARN (real validated cert on the
  # apply path; an empty or non-us-east-1 ARN on the negative plan-only path so the
  # module's certificate validation fires). web_acl_id defaults to the real
  # fixture-created CLOUDFRONT-scope Web ACL when no override is injected.
  effective_web_acl_id = var.web_acl_id != null ? var.web_acl_id : aws_wafv2_web_acl.cf.arn
}

module "example" {
  source = "../../"

  aliases                  = var.aliases
  acm_certificate_arn      = var.acm_certificate_arn
  web_acl_id               = local.effective_web_acl_id
  minimum_protocol_version = var.minimum_protocol_version

  origin = {
    # Per-run-unique origin_id so the module-derived names (the
    # ${origin_id}-security-headers response headers policy and the
    # ${origin_id}-vpc-origin VPC origin) are unique per apply.
    domain_name = var.vpc_origin_domain
    origin_id   = "vpc-origin-${var.log_bucket_suffix}"
    origin_type = "vpc"
    # The ALB/NLB/EC2 ARN CloudFront reaches privately via the VPC origin. CloudFront
    # routes by this ARN, not by public DNS resolution of domain_name, so there is no
    # origin self-loop even when domain_name resolves to this distribution.
    vpc_origin_arn                = var.vpc_origin_alb_arn
    custom_origin_protocol_policy = "https-only"
    custom_origin_ssl_protocols   = ["TLSv1.2"]
  }

  default_cache_behavior = {
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    viewer_protocol_policy   = "redirect-to-https"
    cache_policy_id          = null
    origin_request_policy_id = null
    compress                 = true
  }

  enable_response_headers_policy = true

  logging_config = {
    bucket          = aws_s3_bucket.access_logs.bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "cloudfront-distribution-vpc-origin-example"
    Owner       = "terraform"
  })

  depends_on = [aws_s3_bucket_acl.access_logs]
}

output "distribution_id" {
  description = "The CloudFront distribution ID."
  value       = module.example.distribution_id
}

output "distribution_arn" {
  description = "The CloudFront distribution ARN."
  value       = module.example.distribution_arn
}

output "distribution_domain_name" {
  description = "The CloudFront distribution domain name."
  value       = module.example.distribution_domain_name
}

output "distribution_hosted_zone_id" {
  description = "The CloudFront distribution hosted zone ID."
  value       = module.example.distribution_hosted_zone_id
}

output "vpc_origin_id" {
  description = "The CloudFront VPC origin ID (non-null for the vpc origin)."
  value       = module.example.vpc_origin_id
}
