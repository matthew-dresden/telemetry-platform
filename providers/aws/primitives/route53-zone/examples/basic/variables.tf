variable "zone_name" {
  type        = string
  description = "DNS name for the hosted zone."
}

variable "comment" {
  type        = string
  description = "Comment for the hosted zone."
  default     = "Managed by terraform"
}

variable "force_destroy" {
  type        = bool
  description = "Whether to destroy all records in the zone so the zone can be destroyed without error."
  default     = true
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
