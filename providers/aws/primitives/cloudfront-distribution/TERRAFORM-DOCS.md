<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.50.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_cloudfront_distribution.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_distribution) | resource |
| [aws_cloudfront_origin_access_control.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_origin_access_control) | resource |
| [aws_cloudfront_response_headers_policy.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_response_headers_policy) | resource |

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
| <a name="input_origin"></a> [origin](#input\_origin) | (Required) Single origin configuration. origin\_type must be one of 'alb', 'custom', or 's3'. For S3 origins set s3\_oac\_enabled=true to enable OAC. For custom origins http\_port, https\_port, origin\_keepalive\_timeout, and origin\_read\_timeout are configurable. | <pre>object({<br/>    domain_name                   = string<br/>    origin_id                     = string<br/>    origin_type                   = string<br/>    custom_origin_protocol_policy = optional(string, "https-only")<br/>    custom_origin_ssl_protocols   = optional(list(string), ["TLSv1.2"])<br/>    s3_oac_enabled                = optional(bool, false)<br/>    http_port                     = optional(number, 80)<br/>    https_port                    = optional(number, 443)<br/>    origin_keepalive_timeout      = optional(number, 5)<br/>    origin_read_timeout           = optional(number, 30)<br/>  })</pre> | n/a | yes |
| <a name="input_price_class"></a> [price\_class](#input\_price\_class) | (Optional) Price class for the distribution edge footprint. One of PriceClass\_100, PriceClass\_200, or PriceClass\_All. | `string` | `"PriceClass_100"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_web_acl_id"></a> [web\_acl\_id](#input\_web\_acl\_id) | (Optional) ARN of the WAF v2 Web ACL with CLOUDFRONT scope. Must match ^arn:aws:wafv2: when provided. | `string` | `null` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_distribution_arn"></a> [distribution\_arn](#output\_distribution\_arn) | The ARN of the CloudFront distribution. |
| <a name="output_distribution_domain_name"></a> [distribution\_domain\_name](#output\_distribution\_domain\_name) | The domain name of the CloudFront distribution (e.g. dxxxx.cloudfront.net). Use as the alias target for Route53 A/AAAA records. |
| <a name="output_distribution_hosted_zone_id"></a> [distribution\_hosted\_zone\_id](#output\_distribution\_hosted\_zone\_id) | The hosted zone ID of the CloudFront distribution. Used for Route53 alias A/AAAA records. The fixed CloudFront alias zone ID is Z2FDTNDATAQYW2 (AWS global constant). |
| <a name="output_distribution_id"></a> [distribution\_id](#output\_distribution\_id) | The CloudFront distribution ID. |
| <a name="output_oac_id"></a> [oac\_id](#output\_oac\_id) | The ID of the Origin Access Control (OAC) created for S3 origins. Null when origin\_type is not 's3' or s3\_oac\_enabled is false. |
<!-- END_TF_DOCS -->
