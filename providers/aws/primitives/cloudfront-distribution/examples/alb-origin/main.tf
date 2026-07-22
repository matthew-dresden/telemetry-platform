# ---------------------------------------------------------------------------
# Dedicated CloudFront standard-logging (legacy) bucket.
# CloudFront standard logging requires the bucket to have ACLs enabled
# (BucketOwnerPreferred, not BucketOwnerEnforced), grant FULL_CONTROL to the
# CloudFront log-delivery account, and use SSE-S3 (AES256). SSE-KMS with a
# customer CMK is rejected by CloudFront standard logging.
# ---------------------------------------------------------------------------
data "aws_canonical_user_id" "current" {}

data "aws_cloudfront_log_delivery_canonical_user_id" "current" {}

resource "aws_s3_bucket" "access_logs" {
  bucket        = "cf-alb-access-logs-${var.log_bucket_suffix}"
  force_destroy = true

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "cloudfront-distribution-alb-origin-example-access-logs"
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

# Model secure usage: enable S3 server access logging on the CloudFront standard-
# logging bucket. It is a self-logging terminal target -- its own S3 access logs are
# written back under the "s3-access-logs/" prefix, the same self-logging pattern the
# state-bootstrap access-log bucket uses. S3 permits a bucket to be its own log target
# with a prefix, so there is no recursion. This clears trivy AWS-0089 (logging disabled)
# without a suppression, and -- because the bucket is now an S3 access-logging
# destination -- trivy exempts it from AWS-0132 (CMK encryption), which is correct:
# CloudFront standard logging rejects SSE-KMS with a customer CMK and requires SSE-S3
# (AES256), so the bucket legitimately stays on AES256.
resource "aws_s3_bucket_logging" "access_logs" {
  bucket        = aws_s3_bucket.access_logs.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "s3-access-logs/"
}

# ---------------------------------------------------------------------------
# Viewer certificate: a REAL, externally-provided ACM certificate ARN.
#
# CloudFront validates the viewer certificate server-side at CreateDistribution.
# When a distribution declares an alias (CNAME) -- which this module always does
# (aliases is required) -- CloudFront requires the attached certificate to be issued
# by a publicly-trusted CA and to cover the alias. A self-signed/imported certificate
# is rejected ("the certificate ... was not issued by a trusted Certificate Authority"),
# and reserved alias domains (e.g. example.com) are rejected ("The parameter CNAME ...
# not valid"). A trusted certificate requires DNS validation against a hosted zone for a
# domain the account controls, which is an environment-level prerequisite that cannot be
# provisioned from this self-contained example. The apply-based terratests therefore
# inject a real validated certificate ARN and a matching owned alias via var.acm_certificate_arn
# and var.aliases, and are skipped when those prerequisites are not configured.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Real CLOUDFRONT-scope WAF Web ACL fixture.
# CloudFront CreateDistribution validates the Web ACL ARN belongs to the calling
# account (a foreign-account ARN is rejected with InvalidWebACLId). This provisions
# a real, account-owned CLOUDFRONT-scope Web ACL so the distribution attaches a
# valid WAF. CLOUDFRONT-scope WAF resources must be created in us-east-1.
# ---------------------------------------------------------------------------
resource "aws_wafv2_web_acl" "cf" {
  name        = "cf-alb-origin-${var.log_bucket_suffix}"
  description = "CLOUDFRONT-scope Web ACL fixture for cloudfront-distribution alb-origin example"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "cf-alb-origin-${var.log_bucket_suffix}"
    sampled_requests_enabled   = true
  }

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "cloudfront-distribution-alb-origin-example-waf"
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
    # ${origin_id}-security-headers response headers policy and the ${origin_id}-oac
    # OAC) are unique per apply. With a fixed origin_id, concurrent and repeated runs
    # collided on a fixed-name CloudFront response headers policy
    # (ResponseHeadersPolicyAlreadyExists).
    domain_name                   = var.alb_origin_domain
    origin_id                     = "alb-origin-${var.log_bucket_suffix}"
    origin_type                   = "custom"
    custom_origin_protocol_policy = "https-only"
    custom_origin_ssl_protocols   = ["TLSv1.2"]
    s3_oac_enabled                = false
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
    Purpose     = "cloudfront-distribution-alb-origin-example"
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

output "oac_id" {
  description = "The OAC ID (null for ALB origin)."
  value       = module.example.oac_id
}
