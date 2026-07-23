variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "public_subnet_cidr" {
  type        = string
  description = "CIDR block for the public fixture subnet (used for the NAT gateway)."
  default     = "10.0.100.0/24"

  validation {
    condition     = can(cidrhost(var.public_subnet_cidr, 0))
    error_message = "public_subnet_cidr must be a valid CIDR block."
  }
}

variable "availability_zone" {
  type        = string
  description = "Availability zone for the public fixture subnet."
  default     = "us-east-1a"
}

variable "nat_gateways" {
  type = list(object({
    name              = string
    public_subnet_id  = string
    connectivity_type = optional(string, "public")
  }))
  description = "Override nat_gateways list. When null, the fixture creates one public NAT gateway in the fixture subnet."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "nat-gateway-module-testing"
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
