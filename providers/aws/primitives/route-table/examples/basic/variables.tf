variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "vpc_id" {
  type        = string
  description = "Override vpc_id (unused -- the fixture creates its own VPC). Present only to support validation error tests via ExtraVars."
  default     = null
}

variable "private_subnet_a_cidr" {
  type        = string
  description = "CIDR block for the first private fixture subnet."
  default     = "10.0.1.0/24"

  validation {
    condition     = can(cidrhost(var.private_subnet_a_cidr, 0))
    error_message = "private_subnet_a_cidr must be a valid CIDR block."
  }
}

variable "private_subnet_b_cidr" {
  type        = string
  description = "CIDR block for the second private fixture subnet."
  default     = "10.0.2.0/24"

  validation {
    condition     = can(cidrhost(var.private_subnet_b_cidr, 0))
    error_message = "private_subnet_b_cidr must be a valid CIDR block."
  }
}

variable "public_subnet_a_cidr" {
  type        = string
  description = "CIDR block for the public fixture subnet (used for the NAT gateway)."
  default     = "10.0.100.0/24"

  validation {
    condition     = can(cidrhost(var.public_subnet_a_cidr, 0))
    error_message = "public_subnet_a_cidr must be a valid CIDR block."
  }
}

variable "availability_zone_a" {
  type        = string
  description = "First availability zone for fixture subnets."
  default     = "us-east-1a"
}

variable "availability_zone_b" {
  type        = string
  description = "Second availability zone for fixture subnets."
  default     = "us-east-1b"
}

variable "route_tables" {
  type = list(object({
    name       = string
    subnet_ids = list(string)
    routes = list(object({
      destination_cidr_block = string
      gateway_id             = optional(string)
      nat_gateway_id         = optional(string)
      vpc_endpoint_id        = optional(string)
    }))
  }))
  description = "Override route_tables. When null, the fixture uses a default private route table with NAT gateway."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "route-table-module-testing"
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
