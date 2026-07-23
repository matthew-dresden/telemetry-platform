# ---------------------------------------------------------------------------
# vpc-origin-long-name example.
#
# Hardened regression fixture for the CloudFront VPC origin Name-length bound.
#
# CloudFront's CreateVpcOrigin rejects a VPC origin Name longer than 64 characters
# ("InvalidArgument: The parameter VPC Origin Name is too big."). origin_id is
# namespace-derived by the caller, so for the longer prod/sandbox collector namespaces
# the natural "<origin_id>-vpc-origin" name overflows the limit. The qa terratest passed
# only because the qa namespace is shorter, so the boundary was never exercised by a real
# apply -- which let the overflow reach a live prod apply.
#
# This example drives the module with a REPRESENTATIVE LONG origin_id (>= the longest real
# collector namespace) so the module's deterministic name bound (local.vpc_origin_name) is
# exercised on the real apply path, and it stands up the real prerequisites CreateVpcOrigin
# validates: a real, Active INTERNAL Application Load Balancer whose VPC has an internet
# gateway (both are required by CreateVpcOrigin, verified against the live CloudFront API),
# plus a publicly-trusted DNS-validated ACM viewer certificate (CloudFront rejects
# self-signed/imported certs and untrusted aliases). The companion terratest asserts the
# VPC origin and distribution are created and that the resulting Name is <= 64 characters.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_canonical_user_id" "current" {}
data "aws_cloudfront_log_delivery_canonical_user_id" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  name       = "cfvo-${var.suffix}"

  # The per-run hosted zone + ACM viewer certificate live under the operator-supplied
  # sandbox domain so ACM DNS validation can complete against a real, NS-delegated zone.
  fqdn = "${local.name}.${var.sandbox_domain}"

  # CloudFront standard-logging destination bucket. The name carries the account id so
  # concurrent runs in different accounts never collide on the GLOBAL S3 namespace.
  access_log_bucket_name = "${local.name}-logs-${local.account_id}"

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "cloudfront-distribution-vpc-origin-long-name-example"
    Owner       = "terraform"
  })
}

# ---------------------------------------------------------------------------
# Fixture VPC. An internal ALB requires a VPC with subnets in >= 2 AZs, and
# CreateVpcOrigin additionally requires the ALB's VPC to have an internet gateway.
# ---------------------------------------------------------------------------
resource "aws_vpc" "fixture" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, {
    Name = "${local.name}-vpc"
  })
}

# CreateVpcOrigin rejects an ALB whose VPC has no internet gateway
# ("does not have an internet gateway in its VPC"), so attach one.
resource "aws_internet_gateway" "fixture" {
  vpc_id = aws_vpc.fixture.id

  tags = merge(local.tags, {
    Name = "${local.name}-igw"
  })
}

# ---------------------------------------------------------------------------
# VPC Flow Logs -- models secure usage so the static misconfiguration scanner sees the
# fixture VPC with flow logging enabled (clears trivy AWS-0178) without a suppression.
# The flow-log destination, encryption key, and IAM role are created and destroyed with
# this example -- no external resources required.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "flow_logs" {
  description             = "CMK for ${local.name} VPC Flow Logs CloudWatch log group (fixture)"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
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
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.name}-flow-logs-cmk"
  })
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/aws/vpc/flowlogs/${local.name}"
  retention_in_days = var.flow_log_retention_in_days
  kms_key_id        = aws_kms_key.flow_logs.arn

  tags = merge(local.tags, {
    Name = "${local.name}-flow-logs"
  })
}

resource "aws_iam_role" "flow_logs" {
  name = "${local.name}-flow-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
      }
    ]
  })

  tags = merge(local.tags, {
    Name = "${local.name}-flow-logs-role"
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  name = "${local.name}-flow-logs-policy"
  role = aws_iam_role.flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
        ]
        Effect   = "Allow"
        Resource = "${aws_cloudwatch_log_group.flow_logs.arn}:*"
      }
    ]
  })
}

resource "aws_flow_log" "fixture" {
  iam_role_arn    = aws_iam_role.flow_logs.arn
  log_destination = aws_cloudwatch_log_group.flow_logs.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.fixture.id

  tags = merge(local.tags, {
    Name = "${local.name}-flow-log"
  })
}

# ---------------------------------------------------------------------------
# Two subnets across different AZs (an ALB requires >= 2 AZs).
# ---------------------------------------------------------------------------
resource "aws_subnet" "a" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = var.subnet_cidr_a
  availability_zone = data.aws_availability_zones.available.names[0]

  tags = merge(local.tags, {
    Name = "${local.name}-subnet-a"
  })
}

resource "aws_subnet" "b" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = var.subnet_cidr_b
  availability_zone = data.aws_availability_zones.available.names[1]

  tags = merge(local.tags, {
    Name = "${local.name}-subnet-b"
  })
}

# ---------------------------------------------------------------------------
# Security group for the internal ALB. Egress is restricted to the fixture VPC CIDR
# (clears trivy AWS-0104 -- no unrestricted egress); no ingress rule is declared because
# the fixture ALB never serves traffic (CreateVpcOrigin only validates that it exists).
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb-sg"
  description = "Fixture security group for the vpc-origin-long-name internal ALB"
  vpc_id      = aws_vpc.fixture.id

  egress {
    description = "Allow TCP egress to targets within the fixture VPC"
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.fixture.cidr_block]
  }

  tags = merge(local.tags, {
    Name = "${local.name}-alb-sg"
  })
}

# ---------------------------------------------------------------------------
# Internal ALB -- the real VPC-origin target. CreateVpcOrigin validates that the ARN
# refers to a deployed load balancer (a placeholder ARN is rejected), so a real, Active
# internal ALB is required. No listener is needed for CreateVpcOrigin to succeed.
# ---------------------------------------------------------------------------
resource "aws_lb" "fixture" {
  name               = local.name
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.a.id, aws_subnet.b.id]

  enable_deletion_protection = false
  drop_invalid_header_fields = true

  tags = merge(local.tags, {
    Name = "${local.name}-alb"
  })
}

# ---------------------------------------------------------------------------
# Per-run Route53 sub-zone + parent NS delegation so ACM DNS validation resolves.
# (Same pattern as the portal/collector-ingestion fixtures.)
# ---------------------------------------------------------------------------
resource "aws_route53_zone" "fixture" {
  name          = local.fqdn
  force_destroy = true

  tags = local.tags
}

resource "aws_route53_record" "zone_delegation" {
  zone_id = var.sandbox_parent_zone_id
  name    = aws_route53_zone.fixture.name
  type    = "NS"
  ttl     = 60
  records = aws_route53_zone.fixture.name_servers
}

# ---------------------------------------------------------------------------
# Publicly-trusted DNS-validated ACM viewer certificate (us-east-1 required for CloudFront).
# ---------------------------------------------------------------------------
resource "aws_acm_certificate" "fixture" {
  domain_name       = local.fqdn
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = local.tags
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.fixture.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id = aws_route53_zone.fixture.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "fixture" {
  certificate_arn         = aws_acm_certificate.fixture.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]

  # The sub-zone must be delegated from the parent before the waiter starts polling,
  # otherwise the validation CNAME is not publicly resolvable and the cert never ISSUES.
  depends_on = [aws_route53_record.zone_delegation]
}

# ---------------------------------------------------------------------------
# CloudFront standard-logging destination bucket. CloudFront standard (legacy) logging
# requires an ACL-enabled (BucketOwnerPreferred), SSE-S3 (AES256) destination. This is a
# self-logging terminal target -- it records its OWN S3 access logs under "s3-access-logs/"
# (same pattern as the alb-origin/vpc-origin examples), clearing trivy AWS-0010 (CloudFront
# logging) and AWS-0089 (S3 access logging) without a suppression.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "access_logs" {
  bucket        = local.access_log_bucket_name
  force_destroy = true

  tags = local.tags
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

    # S3 server access logging is delivered by the S3 LogDelivery group, which requires
    # WRITE on the bucket and READ_ACP on the bucket ACL. This bucket is its own
    # S3-access-log target (aws_s3_bucket_logging.access_logs below).
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

resource "aws_s3_bucket_logging" "access_logs" {
  bucket        = aws_s3_bucket.access_logs.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "s3-access-logs/"
}

# ---------------------------------------------------------------------------
# cloudfront-distribution module -- the unit under test. origin_type = "vpc" with the
# long origin_id drives the module's VPC origin Name bound.
# ---------------------------------------------------------------------------
module "example" {
  source = "../../"

  aliases                  = [local.fqdn]
  acm_certificate_arn      = aws_acm_certificate.fixture.arn
  minimum_protocol_version = "TLSv1.2_2021"

  origin = {
    domain_name                   = local.fqdn
    origin_id                     = var.origin_id
    origin_type                   = "vpc"
    vpc_origin_arn                = aws_lb.fixture.arn
    https_port                    = var.alb_https_listener_port
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

  # enable_response_headers_policy stays true (the module default) so the
  # "<origin_id>-security-headers" policy name (~78 chars for this long origin_id) is also
  # exercised -- it must stay within CloudFront's 128-character policy Name limit.
  enable_response_headers_policy = true

  logging_config = {
    bucket          = aws_s3_bucket.access_logs.bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  tags = local.tags

  depends_on = [
    aws_acm_certificate_validation.fixture,
    aws_internet_gateway.fixture,
    aws_s3_bucket_acl.access_logs,
  ]
}

output "distribution_id" {
  description = "The CloudFront distribution ID."
  value       = module.example.distribution_id
}

output "distribution_domain_name" {
  description = "The CloudFront distribution domain name."
  value       = module.example.distribution_domain_name
}

output "vpc_origin_id" {
  description = "The CloudFront VPC origin ID (non-null proves the VPC origin was created)."
  value       = module.example.vpc_origin_id
}

output "vpc_origin_name" {
  description = "The bounded VPC origin Name the module assigned (must be <= 64 characters)."
  value       = module.example.vpc_origin_name
}

output "vpc_origin_desired_name_length" {
  description = "Length of the UNBOUNDED '<origin_id>-vpc-origin' name -- > 64 here proves the long-name branch was exercised."
  value       = length("${var.origin_id}-vpc-origin")
}
