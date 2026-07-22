variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region used to construct endpoint service names."
  default     = "us-east-1"
}

variable "vpc_id" {
  type        = string
  description = "Override vpc_id (unused -- the fixture creates its own VPC). Present only to support validation error tests via ExtraVars."
  default     = null
}

variable "endpoints" {
  type = list(object({
    name                = string
    service_name        = string
    vpc_endpoint_type   = string
    subnet_ids          = optional(list(string), [])
    route_table_ids     = optional(list(string), [])
    security_group_ids  = optional(list(string), [])
    private_dns_enabled = optional(bool, true)
  }))
  description = "Override endpoints list. When null, the fixture creates one S3 gateway endpoint."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "vpc-endpoint-module-testing"
  }
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
