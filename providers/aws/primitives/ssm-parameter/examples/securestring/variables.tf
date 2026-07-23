variable "name" {
  type        = string
  description = "The fully qualified name of the SSM parameter at the section-4 path convention."
  default     = "/telemetry/prod/ingest/adot-config"
}

variable "type" {
  type        = string
  description = "The type of the SSM parameter."
  default     = "SecureString"
}

variable "value" {
  type        = string
  description = "The value of the SecureString SSM parameter. Marked sensitive."
  sensitive   = true
  default     = "extensions:\n  health_check:\n    endpoint: 0.0.0.0:13133\nreceivers:\n  otlp:\n    protocols:\n      grpc:\n        endpoint: 0.0.0.0:4317\nexporters:\n  awsxray:\n    region: us-east-1\nservice:\n  pipelines:\n    traces:\n      receivers: [otlp]\n      exporters: [awsxray]"
}

variable "description" {
  type        = string
  description = "Description of the SSM parameter."
  default     = "ADOT collector config -- securestring example"
}

variable "tier" {
  type        = string
  description = "The tier of the parameter."
  default     = "Standard"
}

variable "overwrite" {
  type        = bool
  description = "Whether to overwrite an existing parameter value."
  default     = false
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
