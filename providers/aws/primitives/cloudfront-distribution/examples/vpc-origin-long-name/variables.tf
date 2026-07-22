variable "origin_id" {
  type        = string
  description = "CloudFront origin id passed to the module. This example exercises the VPC-origin Name-length bound, so the value is intentionally a REPRESENTATIVE LONG namespace-derived id (>= the longest real collector namespace) -- the natural '<origin_id>-vpc-origin' name then exceeds CloudFront's 64-character VPC origin Name limit and the module must bound it. The offline-resolvable default models the prod/sandbox collector namespace; terratest injects a per-run-unique long id so the bounded VPC origin Name (and the response-headers-policy name) are unique per apply."
  default     = "telemetry-useast1-sandbox-000-collector-ingestion-000-adot"

  validation {
    condition     = length("${var.origin_id}-vpc-origin") > 64
    error_message = "origin_id must be long enough that '<origin_id>-vpc-origin' exceeds 64 characters; this example must exercise the truncation branch of the module's VPC origin Name bound."
  }
}

variable "sandbox_domain" {
  type        = string
  description = "(Required) Sandbox test domain used for the per-run Route53 hosted zone and the publicly-trusted ACM viewer certificate. Must be a real NS-delegatable domain the qa account controls (CloudFront rejects self-signed/imported certs and untrusted aliases). Supply via TF_VAR_sandbox_domain -- no default is provided so an unset value fails fast at plan time (12-factor config)."
}

variable "sandbox_parent_zone_id" {
  type        = string
  description = "(Required) Route53 zone id of the PARENT public hosted zone for sandbox_domain (the live, NS-delegated qa zone). The fixture writes an NS delegation record into this parent zone for the per-run ACM-validation sub-zone it creates, so the sub-zone is publicly resolvable and ACM DNS validation reaches ISSUED. Supply via TF_VAR_sandbox_parent_zone_id -- no default is provided so an unset value fails fast at plan time (12-factor config)."
}

variable "suffix" {
  type        = string
  description = "Per-run-unique suffix appended to globally/account-scoped resource names (ALB, security group, hosted-zone label, KMS alias, flow-log role) so concurrent terratest runs never collide. The statically-resolvable offline default lets `terraform validate` and the misconfiguration scanner resolve concrete names; terratest injects a unique value."
  default     = "offline00000000"
}

variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.71.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "subnet_cidr_a" {
  type        = string
  description = "CIDR block for the first fixture subnet."
  default     = "10.71.1.0/24"
}

variable "subnet_cidr_b" {
  type        = string
  description = "CIDR block for the second fixture subnet."
  default     = "10.71.2.0/24"
}

variable "alb_https_listener_port" {
  type        = number
  description = "HTTPS port modelled on the VPC origin connection (mirrors the collector-ingestion ALB HTTPS listener port). The fixture ALB carries no listener -- CreateVpcOrigin validates only that the ALB exists and that its VPC has an internet gateway -- so this value only populates the module's vpc origin https_port."
  default     = 8443
}

variable "flow_log_retention_in_days" {
  type        = number
  description = "Retention in days for the fixture VPC Flow Logs CloudWatch log group."
  default     = 7
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
