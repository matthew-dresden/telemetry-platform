variable "enabled" {
  type        = bool
  description = "(Optional) Whether the distribution is enabled to accept end user requests."
  default     = true
}

variable "aliases" {
  type        = list(string)
  description = "(Required) List of CNAMEs (alternate domain names) associated with this distribution."

  validation {
    condition     = length(var.aliases) > 0
    error_message = "aliases must be a non-empty list of domain names."
  }
}

variable "acm_certificate_arn" {
  type        = string
  description = "(Required) ARN of the ACM certificate in us-east-1. CloudFront requires certificates to be in us-east-1. Must match ^arn:aws:acm:us-east-1:."

  validation {
    condition     = length(var.acm_certificate_arn) > 0
    error_message = "acm_certificate_arn must not be empty when aliases are set."
  }

  validation {
    condition     = can(regex("^arn:aws:acm:us-east-1:", var.acm_certificate_arn))
    error_message = "acm_certificate_arn must be a valid ACM certificate ARN in us-east-1 matching ^arn:aws:acm:us-east-1:."
  }
}

variable "web_acl_id" {
  type        = string
  description = "(Optional) ARN of the WAF v2 Web ACL with CLOUDFRONT scope. Must match ^arn:aws:wafv2: when provided."
  default     = null

  validation {
    condition     = var.web_acl_id == null || can(regex("^arn:aws:wafv2:", var.web_acl_id))
    error_message = "web_acl_id must be a valid WAF v2 ARN matching ^arn:aws:wafv2: when provided."
  }
}

variable "origin" {
  type = object({
    domain_name                   = string
    origin_id                     = string
    origin_type                   = string
    custom_origin_protocol_policy = optional(string, "https-only")
    custom_origin_ssl_protocols   = optional(list(string), ["TLSv1.2"])
    s3_oac_enabled                = optional(bool, false)
    vpc_origin_arn                = optional(string, null)
    http_port                     = optional(number, 80)
    https_port                    = optional(number, 443)
    origin_keepalive_timeout      = optional(number, 5)
    origin_read_timeout           = optional(number, 30)
  })
  description = "(Required) Single origin configuration. origin_type must be one of 'alb', 'custom', 'vpc', or 's3'. For S3 origins set s3_oac_enabled=true to enable OAC. For 'custom' and 'alb' origins CloudFront reaches the origin over the public internet via custom_origin_config (origin domain_name must resolve publicly). For 'vpc' origins CloudFront reaches a private/internal ALB, NLB, or EC2 instance INSIDE the VPC via an aws_cloudfront_vpc_origin (set vpc_origin_arn to the resource ARN); routing is private (through the CloudFront VPC origin), NOT by public DNS resolution of domain_name, so a domain_name that resolves to this same distribution does NOT create an origin self-loop. domain_name is still required for a 'vpc' origin and is used for the Host header and TLS SNI/certificate validation, so for an https-only VPC origin it must be a name the origin's certificate covers. http_port, https_port, custom_origin_protocol_policy, custom_origin_ssl_protocols, origin_keepalive_timeout, and origin_read_timeout configure both the custom and vpc origin connection."

  validation {
    condition     = contains(["alb", "custom", "vpc", "s3"], var.origin.origin_type)
    error_message = "origin.origin_type must be one of 'alb', 'custom', 'vpc', or 's3'."
  }

  validation {
    condition     = var.origin.origin_type != "vpc" || (var.origin.vpc_origin_arn != null && try(length(var.origin.vpc_origin_arn) > 0, false))
    error_message = "origin.vpc_origin_arn must be set to the ALB/NLB/EC2 ARN when origin.origin_type is 'vpc' (the aws_cloudfront_vpc_origin endpoint resource ARN)."
  }

  validation {
    condition     = var.origin.origin_type == "vpc" || var.origin.vpc_origin_arn == null
    error_message = "origin.vpc_origin_arn must only be set when origin.origin_type is 'vpc'."
  }
}

variable "default_cache_behavior" {
  type = object({
    allowed_methods          = list(string)
    cached_methods           = list(string)
    viewer_protocol_policy   = string
    cache_policy_id          = optional(string, null)
    origin_request_policy_id = optional(string, null)
    compress                 = optional(bool, true)
    forwarded_values = optional(object({
      query_string              = optional(bool, false)
      headers                   = optional(list(string), [])
      cookies_forward           = optional(string, "none")
      cookies_whitelisted_names = optional(list(string), null)
    }), {})
  })
  description = "(Required) Default cache behavior configuration. viewer_protocol_policy must be one of 'redirect-to-https', 'https-only', or 'allow-all'. When cache_policy_id is null the forwarded_values legacy block is rendered (CloudFront requires exactly one of cache_policy_id or forwarded_values); forwarded_values.cookies_forward must be one of 'none', 'whitelist', or 'all'."

  validation {
    condition     = contains(["redirect-to-https", "https-only", "allow-all"], var.default_cache_behavior.viewer_protocol_policy)
    error_message = "default_cache_behavior.viewer_protocol_policy must be one of 'redirect-to-https', 'https-only', or 'allow-all'."
  }

  validation {
    condition     = var.default_cache_behavior.cache_policy_id != null || contains(["none", "whitelist", "all"], var.default_cache_behavior.forwarded_values.cookies_forward)
    error_message = "default_cache_behavior.forwarded_values.cookies_forward must be one of 'none', 'whitelist', or 'all'."
  }
}

variable "price_class" {
  type        = string
  description = "(Optional) Price class for the distribution edge footprint. One of PriceClass_100, PriceClass_200, or PriceClass_All."
  default     = "PriceClass_100"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.price_class)
    error_message = "price_class must be one of PriceClass_100, PriceClass_200, or PriceClass_All."
  }
}

variable "minimum_protocol_version" {
  type        = string
  description = "(Optional) Minimum TLS protocol version for viewer connections. Must be TLSv1.2_2021 to enforce DoD-required transport security."
  default     = "TLSv1.2_2021"

  validation {
    condition     = contains(["TLSv1.2_2021"], var.minimum_protocol_version)
    error_message = "minimum_protocol_version must be TLSv1.2_2021. Values below TLSv1.2_2021 (including TLSv1.2_2019, TLSv1.2_2018, TLSv1.1_2016, TLSv1_2016) are rejected to enforce DoD-required strong transport security."
  }
}

variable "enable_response_headers_policy" {
  type        = bool
  description = "(Optional) Whether to create and attach a security response-headers policy (HSTS, X-Content-Type-Options, X-Frame-Options, etc.)."
  default     = true
}

variable "hsts_max_age_sec" {
  type        = number
  description = "(Optional) HSTS max-age in seconds for the Strict-Transport-Security header in the response-headers policy. Defaults to 31536000 (1 year)."
  default     = 31536000
}

variable "default_root_object" {
  type        = string
  description = "(Optional) Default root object served when a request is made to the root URL. Set to 'index.html' for S3/portal origins. Leave empty for OTLP collector distributions per docs/adr/ D11."
  default     = ""
}

variable "logging_config" {
  type = object({
    bucket          = string
    prefix          = optional(string, "")
    include_cookies = optional(bool, false)
  })
  description = "(Optional) Access logging configuration to an S3 bucket. When null, access logging is disabled."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source module."
  default     = "cloudfront-distribution"
}
