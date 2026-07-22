variable "name" {
  type        = string
  description = "(Required) Unique fixture name prefix for all resources created in this example."
}

variable "env" {
  type        = string
  description = "(Optional) Deployment environment label used for SSM parameter path prefixes."
  default     = "sandbox"
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
