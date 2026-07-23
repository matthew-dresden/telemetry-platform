<!-- BEGIN_TF_DOCS -->
# cloudfront-distribution

Manages a hardened CloudFront distribution fronting an ALB over the public internet (`alb`/`custom` origin), a PRIVATE/INTERNAL ALB inside a VPC (`vpc` origin, via a CloudFront VPC origin), or an S3 static portal app (`s3` origin), with optional WAF association, ACM viewer certificate pinned to `TLSv1.2_2021` with `sni-only` SSL support, Origin Access Control (OAC) for S3 origins, and a security response-headers policy (HSTS, X-Content-Type-Options, X-Frame-Options, referrer-policy, XSS-protection per the CLAUDE.md API security headers requirement).

**VPC origin (`origin.origin_type = "vpc"`):** set `origin.vpc_origin_arn` to the ARN of an internal Application Load Balancer (or NLB/EC2 instance) inside a VPC and CloudFront reaches it over a private, CloudFront-managed connection through an `aws_cloudfront_vpc_origin` -- the origin never has to be internet-facing. CloudFront routes to the resource by the VPC origin (the ARN), **not** by public DNS resolution of `origin.domain_name`, so a `domain_name` that resolves (publicly) to this same distribution does **not** create a CloudFront-to-CloudFront origin self-loop. `origin.domain_name` is still required and is used for the Host header and TLS SNI/certificate validation, so for an `https-only` VPC origin it must be a name the origin's TLS certificate covers. The origin's security group must allow inbound from the CloudFront origin-facing managed prefix list (`com.amazonaws.global.cloudfront.origin-facing`) on the listener port (configured by the caller -- see the `collector-ingestion` reference). The VPC origin's `Name` is derived from `origin.origin_id` and **deterministically bounded** to CloudFront's 64-character VPC origin Name limit (`CreateVpcOrigin` rejects a longer Name with "The parameter VPC Origin Name is too big."): when `<origin_id>-vpc-origin` fits it is used verbatim, otherwise the namespace-derived portion is truncated and an 8-hex-character `sha256` discriminator is appended so the bounded name stays unique -- the caller does not need to shorten `origin_id` (see `vpc_origin_name`). CreateVpcOrigin additionally requires the target ALB's VPC to have an internet gateway.

Used as the shared edge primitive by both the `collector-ingestion` and `portal` reference modules.

**OTLP collector requirement (docs/adr/ D11):** When this module fronts the OTLP collector distribution, set `default_root_object = ""` (empty string, the default). The OTLP collector path `/v1/logs` is non-cacheable and there is no root document. Setting a non-empty `default_root_object` for collector distributions is a misconfiguration -- the portal shape should use `"index.html"` instead.

## Resources managed

- `aws_cloudfront_distribution` -- the CloudFront distribution
- `aws_cloudfront_origin_access_control` -- OAC for S3 origins (created only when `origin.origin_type = "s3"` and `origin.s3_oac_enabled = true`)
- `aws_cloudfront_vpc_origin` -- VPC origin reaching a private/internal ALB/NLB/EC2 inside a VPC (created only when `origin.origin_type = "vpc"`)
- `aws_cloudfront_response_headers_policy` -- security headers policy (created only when `enable_response_headers_policy = true`)

## Usage

```hcl
module "cloudfront" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/cloudfront-distribution?ref=providers/aws/primitives/cloudfront-distribution/v0.1.0"

  aliases             = ["collector.prod.telemetry.example.com"]
  acm_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/my-cert-id"
  web_acl_id          = "arn:aws:wafv2:us-east-1:123456789012:global/webacl/collector-waf/some-id"

  origin = {
    domain_name                   = "internal-alb.us-east-1.elb.amazonaws.com"
    origin_id                     = "collector-alb"
    origin_type                   = "custom"
    custom_origin_protocol_policy = "https-only"
    custom_origin_ssl_protocols   = ["TLSv1.2"]
    s3_oac_enabled                = false
  }

  default_cache_behavior = {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
  }

  enable_response_headers_policy = true

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/alb-origin` -- ALB/custom origin with WAF attached and response-headers policy enabled; models a public-internet origin
- `examples/vpc-origin` -- `vpc` origin reaching a private/internal ALB via a CloudFront VPC origin (no public custom origin, no self-loop); models the OTLP collector edge after the origin self-loop fix
- `examples/vpc-origin-long-name` -- `vpc` origin driven with a REPRESENTATIVE LONG namespace-derived `origin_id` so the natural `<origin_id>-vpc-origin` name overflows CloudFront's 64-character VPC origin Name limit; the fixture stands up a real internal ALB (whose VPC has an internet gateway) and a DNS-validated ACM cert so the apply proves the module's deterministic Name bound is accepted by CreateVpcOrigin
- `examples/s3-origin` -- S3 origin with OAC enabled and response-headers policy disabled; models the portal edge; fixture creates a throwaway S3 bucket as origin

### CloudFront standard-logging bucket requirements

When `logging_config` is set, CloudFront standard logging (legacy) requires the target log bucket to have ACLs enabled (S3 Object Ownership `BucketOwnerPreferred`, not `BucketOwnerEnforced`), grant `FULL_CONTROL` to the CloudFront log-delivery account (canonical user id from the `aws_cloudfront_log_delivery_canonical_user_id` data source), and use SSE-S3 (`AES256`). SSE-KMS with a customer CMK and ACL-disabled buckets are rejected by CloudFront standard logging. The example fixtures create a dedicated log bucket configured this way.

## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 6.49.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_cloudfront_distribution.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_distribution) | resource |
| [aws_cloudfront_origin_access_control.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_origin_access_control) | resource |
| [aws_cloudfront_response_headers_policy.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_response_headers_policy) | resource |
| [aws_cloudfront_vpc_origin.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_vpc_origin) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_acm_certificate_arn"></a> [acm\_certificate\_arn](#input\_acm\_certificate\_arn) | (Required) ARN of the ACM certificate in us-east-1. CloudFront requires certificates to be in us-east-1. Must match ^arn:aws:acm:us-east-1:. | `string` | n/a | yes |
| <a name="input_aliases"></a> [aliases](#input\_aliases) | (Required) List of CNAMEs (alternate domain names) associated with this distribution. | `list(string)` | n/a | yes |
| <a name="input_default_cache_behavior"></a> [default\_cache\_behavior](#input\_default\_cache\_behavior) | (Required) Default cache behavior configuration. viewer\_protocol\_policy must be one of 'redirect-to-https', 'https-only', or 'allow-all'. When cache\_policy\_id is null the forwarded\_values legacy block is rendered (CloudFront requires exactly one of cache\_policy\_id or forwarded\_values); forwarded\_values.cookies\_forward must be one of 'none', 'whitelist', or 'all'. | <pre>object({<br/>    allowed_methods          = list(string)<br/>    cached_methods           = list(string)<br/>    viewer_protocol_policy   = string<br/>    cache_policy_id          = optional(string, null)<br/>    origin_request_policy_id = optional(string, null)<br/>    compress                 = optional(bool, true)<br/>    forwarded_values = optional(object({<br/>      query_string              = optional(bool, false)<br/>      headers                   = optional(list(string), [])<br/>      cookies_forward           = optional(string, "none")<br/>      cookies_whitelisted_names = optional(list(string), null)<br/>    }), {})<br/>  })</pre> | n/a | yes |
| <a name="input_default_root_object"></a> [default\_root\_object](#input\_default\_root\_object) | (Optional) Default root object served when a request is made to the root URL. Set to 'index.html' for S3/portal origins. Leave empty for OTLP collector distributions per docs/adr/ D11. | `string` | `""` | no |
| <a name="input_enable_response_headers_policy"></a> [enable\_response\_headers\_policy](#input\_enable\_response\_headers\_policy) | (Optional) Whether to create and attach a security response-headers policy (HSTS, X-Content-Type-Options, X-Frame-Options, etc.). | `bool` | `true` | no |
| <a name="input_enabled"></a> [enabled](#input\_enabled) | (Optional) Whether the distribution is enabled to accept end user requests. | `bool` | `true` | no |
| <a name="input_hsts_max_age_sec"></a> [hsts\_max\_age\_sec](#input\_hsts\_max\_age\_sec) | (Optional) HSTS max-age in seconds for the Strict-Transport-Security header in the response-headers policy. Defaults to 31536000 (1 year). | `number` | `31536000` | no |
| <a name="input_logging_config"></a> [logging\_config](#input\_logging\_config) | (Optional) Access logging configuration to an S3 bucket. When null, access logging is disabled. | <pre>object({<br/>    bucket          = string<br/>    prefix          = optional(string, "")<br/>    include_cookies = optional(bool, false)<br/>  })</pre> | `null` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_minimum_protocol_version"></a> [minimum\_protocol\_version](#input\_minimum\_protocol\_version) | (Optional) Minimum TLS protocol version for viewer connections. Must be TLSv1.2\_2021 to enforce DoD-required transport security. | `string` | `"TLSv1.2_2021"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"cloudfront-distribution"` | no |
| <a name="input_origin"></a> [origin](#input\_origin) | (Required) Single origin configuration. origin\_type must be one of 'alb', 'custom', 'vpc', or 's3'. For S3 origins set s3\_oac\_enabled=true to enable OAC. For 'custom' and 'alb' origins CloudFront reaches the origin over the public internet via custom\_origin\_config (origin domain\_name must resolve publicly). For 'vpc' origins CloudFront reaches a private/internal ALB, NLB, or EC2 instance INSIDE the VPC via an aws\_cloudfront\_vpc\_origin (set vpc\_origin\_arn to the resource ARN); routing is private (through the CloudFront VPC origin), NOT by public DNS resolution of domain\_name, so a domain\_name that resolves to this same distribution does NOT create an origin self-loop. domain\_name is still required for a 'vpc' origin and is used for the Host header and TLS SNI/certificate validation, so for an https-only VPC origin it must be a name the origin's certificate covers. http\_port, https\_port, custom\_origin\_protocol\_policy, custom\_origin\_ssl\_protocols, origin\_keepalive\_timeout, and origin\_read\_timeout configure both the custom and vpc origin connection. | <pre>object({<br/>    domain_name                   = string<br/>    origin_id                     = string<br/>    origin_type                   = string<br/>    custom_origin_protocol_policy = optional(string, "https-only")<br/>    custom_origin_ssl_protocols   = optional(list(string), ["TLSv1.2"])<br/>    s3_oac_enabled                = optional(bool, false)<br/>    vpc_origin_arn                = optional(string, null)<br/>    http_port                     = optional(number, 80)<br/>    https_port                    = optional(number, 443)<br/>    origin_keepalive_timeout      = optional(number, 5)<br/>    origin_read_timeout           = optional(number, 30)<br/>  })</pre> | n/a | yes |
| <a name="input_price_class"></a> [price\_class](#input\_price\_class) | (Optional) Price class for the distribution edge footprint. One of PriceClass\_100, PriceClass\_200, or PriceClass\_All. | `string` | `"PriceClass_100"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_web_acl_id"></a> [web\_acl\_id](#input\_web\_acl\_id) | (Optional) ARN of the WAF v2 Web ACL with CLOUDFRONT scope. Must match ^arn:aws:wafv2: when provided. | `string` | `null` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_distribution_arn"></a> [distribution\_arn](#output\_distribution\_arn) | The ARN of the CloudFront distribution. |
| <a name="output_distribution_domain_name"></a> [distribution\_domain\_name](#output\_distribution\_domain\_name) | The domain name of the CloudFront distribution (e.g. dxxxx.cloudfront.net). Use as the alias target for Route53 A/AAAA records. |
| <a name="output_distribution_hosted_zone_id"></a> [distribution\_hosted\_zone\_id](#output\_distribution\_hosted\_zone\_id) | The hosted zone ID of the CloudFront distribution. Used for Route53 alias A/AAAA records. The fixed CloudFront alias zone ID is Z2FDTNDATAQYW2 (AWS global constant). |
| <a name="output_distribution_id"></a> [distribution\_id](#output\_distribution\_id) | The CloudFront distribution ID. |
| <a name="output_oac_id"></a> [oac\_id](#output\_oac\_id) | The ID of the Origin Access Control (OAC) created for S3 origins. Null when origin\_type is not 's3' or s3\_oac\_enabled is false. |
| <a name="output_vpc_origin_arn"></a> [vpc\_origin\_arn](#output\_vpc\_origin\_arn) | The ARN of the CloudFront VPC origin created for a 'vpc' origin. Null when origin\_type is not 'vpc'. |
| <a name="output_vpc_origin_id"></a> [vpc\_origin\_id](#output\_vpc\_origin\_id) | The ID of the CloudFront VPC origin created for a 'vpc' origin (used by the distribution origin's vpc\_origin\_config). Null when origin\_type is not 'vpc'. |
| <a name="output_vpc_origin_name"></a> [vpc\_origin\_name](#output\_vpc\_origin\_name) | The Name assigned to the CloudFront VPC origin (aws\_cloudfront\_vpc\_origin). Namespace-derived from origin.origin\_id and deterministically bounded to CloudFront's VPC origin Name limit (64 chars) -- see local.vpc\_origin\_name. Null when origin\_type is not 'vpc'. |

## Input validation

- `aliases` must be non-empty. Plans fail for empty lists.
- `acm_certificate_arn` must not be empty and must match `^arn:aws:acm:us-east-1:`. Plans fail for missing or non-us-east-1 ARNs (CloudFront requirement).
- `web_acl_id`, when provided, must match `^arn:aws:wafv2:`. Plans fail for non-WAFv2 ARNs.
- `origin.origin_type` must be one of `alb`, `custom`, `vpc`, or `s3`. Plans fail for other values.
- `origin.vpc_origin_arn` must be set (non-empty) when `origin.origin_type = "vpc"`, and must be unset for every other origin type. Plans fail otherwise.
- `default_cache_behavior.viewer_protocol_policy` must be one of `redirect-to-https`, `https-only`, or `allow-all`.
- `price_class` must be one of `PriceClass_100`, `PriceClass_200`, or `PriceClass_All`.
- `minimum_protocol_version` must be `TLSv1.2_2021`. Values below `TLSv1.2_2021` (including TLSv1.2_2019, TLSv1.2_2018, TLSv1.1_2016, TLSv1_2016) are rejected to enforce DoD-required strong transport security per docs/terragrunt-concepts.md.
<!-- END_TF_DOCS -->