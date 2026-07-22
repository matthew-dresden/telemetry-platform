variable "name" {
  type        = string
  description = "Name for the VPC"
}

variable "cidr_block" {
  type        = string
  description = "CIDR block for the VPC"
}

variable "instance_tenancy" {
  type        = string
  description = "A tenancy option for instances launched into the VPC"
  default     = "default"
}

variable "enable_dns_support" {
  type        = bool
  description = "A boolean flag to enable/disable DNS support in the VPC"
  default     = true
}

variable "enable_dns_hostnames" {
  type        = bool
  description = "A boolean flag to enable/disable DNS hostnames in the VPC"
  default     = true
}

variable "enable_network_address_usage_metrics" {
  type        = bool
  description = "Indicates whether Network Address Usage metrics are enabled for your VPC"
  default     = true
}

variable "assign_generated_ipv6_cidr_block" {
  type        = bool
  description = "Requests an Amazon-provided IPv6 CIDR block with a /56 prefix length for the VPC"
  default     = false
}

variable "enable_flow_logs" {
  type        = bool
  description = "Whether to enable VPC Flow Logs"
  # Default to enabled so the example models secure usage: the static security
  # scan resolves this default and confirms VPC Flow Logs are enabled (clears
  # trivy AWS-0178 by exercising the module's enable_flow_logs input rather than
  # suppressing the finding). Terratest still drives both paths via TF_VAR_*.
  default = true
}

variable "flow_logs_traffic_type" {
  type        = string
  description = "The type of traffic to capture. Valid values: ACCEPT, REJECT, ALL"
  default     = "ALL"
}

variable "tags" {
  type        = map(string)
  description = "Tags to apply to the VPC"
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
