variable "name" {
  type        = string
  description = "(Required) Unique fixture name prefix for all resources created in this example."
}

variable "env" {
  type        = string
  description = "(Optional) Deployment environment label used for SSM parameter path prefixes."
  default     = "sandbox"
}

variable "adot_config_content" {
  type        = string
  description = "(Optional) ADOT collector configuration YAML injected as AOT_CONFIG_CONTENT per docs/terragrunt-concepts.md."
  default     = "receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: '0.0.0.0:4318'\nexporters:\n  logging:\n    loglevel: debug\nservice:\n  pipelines:\n    traces:\n      receivers: [otlp]\n      exporters: [logging]\n"
}

variable "waf_rate_limit" {
  type        = string
  description = "(Optional) WAF rate limit count per IP."
  default     = "2000"
}

variable "otlp_max_body_bytes" {
  type        = string
  description = "(Optional) OTLP HTTP request body size ceiling in bytes."
  default     = "5242880"
}

variable "public_client_id" {
  type        = string
  description = "(Optional) Public OTLP client identifier."
  default     = "telemetry-public"
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags to apply to all resources."
  default     = {}
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
