variable "alb_origin_domain" {
  type        = string
  description = "ALB DNS name used as CloudFront origin."
}

variable "aliases" {
  type        = list(string)
  description = "List of CNAMEs (alternate domain names) for the distribution. Injected at test time."
}

variable "acm_certificate_arn" {
  type        = string
  description = "ARN of the ACM certificate in us-east-1 for the viewer certificate. On the apply path this must be a real, publicly-trusted (DNS-validated) certificate that covers the aliases -- CloudFront rejects self-signed/imported certs and untrusted aliases when an alias is set. Negative plan-only tests inject an empty string or a non-us-east-1 ARN to exercise the module's certificate validation."
}

variable "web_acl_id" {
  type        = string
  description = "Override ARN of the WAF v2 Web ACL (CLOUDFRONT scope). When null (the default) the example self-provisions a real CLOUDFRONT-scope WAF Web ACL owned by this account so CloudFront CreateDistribution succeeds (a foreign-account WAF ARN is rejected with InvalidWebACLId). Injected only when a specific Web ACL ARN must be exercised."
  default     = null
}

variable "minimum_protocol_version" {
  type        = string
  description = "Minimum TLS protocol version for viewer connections."
  default     = "TLSv1.2_2021"
}

variable "log_bucket_suffix" {
  type        = string
  description = "Unique suffix appended to the CloudFront access log S3 bucket name. Injected at test time via TF_VAR_log_bucket_suffix. The statically-resolvable offline default lets the misconfiguration scanner (trivy) resolve the concrete bucket name and prove that S3 server access logging is ENABLED on the self-logging CloudFront-log destination bucket (a fully opaque name defeats the logging-destination dataflow and re-triggers AWS-0089/AWS-0132)."
  default     = "offline00000000"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
