resource "aws_cloudfront_origin_access_control" "this" {
  count = local.create_oac ? 1 : 0

  name                              = "${var.origin.origin_id}-oac"
  description                       = "OAC for S3 origin ${var.origin.domain_name}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_response_headers_policy" "this" {
  count = local.create_response_headers_policy ? 1 : 0

  name    = "${var.origin.origin_id}-security-headers"
  comment = "Security response headers policy for ${var.origin.origin_id}"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = var.hsts_max_age_sec
      include_subdomains         = true
      override                   = true
      preload                    = true
    }

    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    xss_protection {
      mode_block = true
      override   = true
      protection = true
    }
  }
}

# ---------------------------------------------------------------------------
# CloudFront VPC origin (created only for origin_type = "vpc").
#
# A VPC origin lets this distribution reach a PRIVATE/INTERNAL Application Load
# Balancer, Network Load Balancer, or EC2 instance inside a VPC over a private,
# CloudFront-managed connection (a service-managed ENI), so the origin never has
# to be internet-facing. CloudFront routes to the resource by this VPC origin
# (identified by its ARN), NOT by public DNS resolution of the distribution
# origin domain_name -- so a domain_name that resolves (publicly) to this same
# distribution does NOT create a CloudFront->CloudFront self-loop.
#
# The endpoint protocol/ports mirror the custom-origin inputs so a caller
# configures one origin object for either shape. For an https-only VPC origin the
# distribution origin domain_name must be a name the origin's TLS certificate
# covers (CloudFront validates the origin certificate against domain_name via SNI).
# ---------------------------------------------------------------------------
resource "aws_cloudfront_vpc_origin" "this" {
  count = local.is_vpc_origin ? 1 : 0

  vpc_origin_endpoint_config {
    # Namespace-derived name, deterministically bounded to CloudFront's VPC origin Name
    # limit (see local.vpc_origin_name in locals.tf). The raw "<origin_id>-vpc-origin"
    # overflows the limit for the longer prod/sandbox namespaces, which fails CreateVpcOrigin
    # with "The parameter VPC Origin Name is too big.".
    name                   = local.vpc_origin_name
    arn                    = var.origin.vpc_origin_arn
    http_port              = var.origin.http_port
    https_port             = var.origin.https_port
    origin_protocol_policy = var.origin.custom_origin_protocol_policy

    origin_ssl_protocols {
      items    = var.origin.custom_origin_ssl_protocols
      quantity = length(var.origin.custom_origin_ssl_protocols)
    }
  }

  tags = local.common_tags
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = var.enabled
  aliases             = var.aliases
  price_class         = var.price_class
  web_acl_id          = var.web_acl_id
  default_root_object = var.default_root_object
  is_ipv6_enabled     = true
  comment             = "Managed by cloudfront-distribution module -- origin ${var.origin.origin_id}"

  origin {
    domain_name              = var.origin.domain_name
    origin_id                = var.origin.origin_id
    origin_access_control_id = local.create_oac ? aws_cloudfront_origin_access_control.this[0].id : null

    dynamic "custom_origin_config" {
      for_each = local.is_custom_origin ? [1] : []
      content {
        http_port                = var.origin.http_port
        https_port               = var.origin.https_port
        origin_protocol_policy   = var.origin.custom_origin_protocol_policy
        origin_ssl_protocols     = var.origin.custom_origin_ssl_protocols
        origin_keepalive_timeout = var.origin.origin_keepalive_timeout
        origin_read_timeout      = var.origin.origin_read_timeout
      }
    }

    # VPC origin: reference the CloudFront VPC origin so CloudFront reaches the
    # private/internal origin resource over the CloudFront-managed private path.
    # Mutually exclusive with custom_origin_config (is_vpc_origin vs is_custom_origin).
    dynamic "vpc_origin_config" {
      for_each = local.is_vpc_origin ? [1] : []
      content {
        vpc_origin_id            = aws_cloudfront_vpc_origin.this[0].id
        origin_keepalive_timeout = var.origin.origin_keepalive_timeout
        origin_read_timeout      = var.origin.origin_read_timeout
      }
    }
  }

  default_cache_behavior {
    allowed_methods            = var.default_cache_behavior.allowed_methods
    cached_methods             = var.default_cache_behavior.cached_methods
    target_origin_id           = var.origin.origin_id
    viewer_protocol_policy     = var.default_cache_behavior.viewer_protocol_policy
    cache_policy_id            = var.default_cache_behavior.cache_policy_id
    origin_request_policy_id   = var.default_cache_behavior.origin_request_policy_id
    compress                   = var.default_cache_behavior.compress
    response_headers_policy_id = local.create_response_headers_policy ? aws_cloudfront_response_headers_policy.this[0].id : null

    # CloudFront requires exactly one of cache_policy_id or forwarded_values.
    # When no cache_policy_id is supplied, render the legacy forwarded_values block
    # so the distribution is accepted (InvalidArgument: ForwardedValues is required).
    # When a cache_policy_id is supplied, forwarded_values must be absent.
    dynamic "forwarded_values" {
      for_each = var.default_cache_behavior.cache_policy_id == null ? [var.default_cache_behavior.forwarded_values] : []
      content {
        query_string = forwarded_values.value.query_string
        headers      = forwarded_values.value.headers

        cookies {
          forward           = forwarded_values.value.cookies_forward
          whitelisted_names = forwarded_values.value.cookies_whitelisted_names
        }
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.acm_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = var.minimum_protocol_version
  }

  dynamic "logging_config" {
    for_each = var.logging_config != null ? [var.logging_config] : []
    content {
      bucket          = logging_config.value.bucket
      prefix          = logging_config.value.prefix
      include_cookies = logging_config.value.include_cookies
    }
  }

  tags = local.common_tags
}
