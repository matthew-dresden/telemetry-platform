variable "name" {
  type        = string
  description = "(Required) Short base name prefix used to derive all fixture resource names. main.tf appends the per-run terratest_run_id tail (local.run_suffix) so concurrent runs never collide on the globally/account-unique names (S3 buckets, IAM roles, ECS cluster, KMS alias, per-run Route53 sub-zone). Kept DELIBERATELY SHORT: the per-run ACM certificate's primary domain is collector.<name>-<run_suffix>.<sandbox_domain>, which AWS ACM caps at 64 characters -- a long base name overflows it (InvalidDomainValidationOptionsException). With the qa sandbox apex (~33 chars) and the '-<6-char suffix>', the base must stay <= ~13 chars. Statically resolvable (no data-source) so trivy can resolve the derived access-log/firehose bucket names."
  default     = "ttci"
}

variable "namespace" {
  type        = string
  description = "(Required) Fully-qualified 6-field namespace (product-region-env-envinstance-service-serviceinstance) passed to the reference so it derives SET-SCOPED names (the telemetry ingest CloudWatch log group /telemetry/<namespace>/ingest/otlp-logs and the ADOT SSM config path). Like name, this is a parallel-isolation key: two concurrent terratest runs sharing this namespace collide on the namespace-derived telemetry ingest log group and SSM parameter, so the serviceinstance field is kept distinct per fixture variant."
  default     = "telemetry-useast1-qa-000-collector-bsz"
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "collector-ingestion-module-testing"
  }
}

variable "account_id" {
  type        = string
  description = "(Optional) AWS account id used as the GLOBAL-uniqueness suffix for the firehose-destination and access-log DESTINATION bucket names. Terratest injects the real caller account id (from AWS_ACCOUNT_ID) so concurrent runs in different accounts never collide on a global S3 bucket name. The 12-digit offline default is a statically-resolvable placeholder so the misconfiguration scanner (trivy) can prove the access-log destination name is non-empty and therefore that S3 server access logging and CloudFront standard access logging are ENABLED (a data-source-derived name is opaque to the static scanner). Must be 12 digits."
  default     = "000000000000"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be exactly 12 digits."
  }
}

variable "adot_image" {
  type        = string
  description = "(Optional) Pinned Docker image URI for the ADOT collector."
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.43.1"
}

variable "adot_task_cpu" {
  type        = number
  description = "(Optional) CPU units for the ADOT ECS task. Mirrors the module's right-sized default."
  default     = 1024
}

variable "adot_task_memory" {
  type        = number
  description = "(Optional) Memory (MiB) for the ADOT ECS task. Mirrors the module's right-sized default."
  default     = 2048
}

variable "adot_receiver_max_request_body_size" {
  type        = number
  description = "(Optional) Maximum OTLP HTTP request body size in bytes (D44)."
  default     = 4194304
}

variable "memory_limiter_limit_mib" {
  type        = number
  description = "(Optional) Hard memory limit for the ADOT memory_limiter processor (D5). Mirrors the module's right-sized default (proportional to the 2048 MiB task)."
  default     = 1800
}

variable "memory_limiter_spike_limit_mib" {
  type        = number
  description = "(Optional) Soft spike limit for the ADOT memory_limiter processor (D5). Mirrors the module's right-sized default (proportional to the 2048 MiB task)."
  default     = 400
}

variable "rate_limit_per_ip" {
  type        = number
  description = "(Optional) WAF rate limit per source IP per 5-minute window (D44)."
  default     = 2000
}

variable "structured_otlp_service_names" {
  type        = list(string)
  description = "(Optional) Passthrough of the module's structured_otlp_service_names input; empty default keeps the structured-OTLP logs pipeline inert."
  default     = []
}

variable "vpc_cidr_block" {
  type        = string
  description = "(Optional) CIDR block for the VPC created within the fixture."
  default     = "10.0.0.0/16"
}

variable "sandbox_domain" {
  type        = string
  description = "(Required) Sandbox test domain used for the Route53 hosted zone and ACM certificate. Must be a real NS-delegatable domain the sandbox account controls. Supply via TF_VAR_sandbox_domain -- no default is provided so an unset value fails fast at plan time (12-factor config)."
}

variable "sandbox_parent_zone_id" {
  type        = string
  description = "(Required) Route53 zone id of the PARENT public hosted zone for sandbox_domain (the live, NS-delegated qa.platform zone). The fixture writes an NS delegation record into this parent zone for the per-run ACM-validation sub-zone it creates, so the sub-zone is publicly resolvable and ACM DNS validation reaches ISSUED. Supply via TF_VAR_sandbox_parent_zone_id -- no default is provided so an unset value fails fast at plan time (12-factor config)."
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
