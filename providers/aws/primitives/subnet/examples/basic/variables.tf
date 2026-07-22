variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"
}

variable "vpc_id" {
  type        = string
  description = "Override vpc_id (unused -- the fixture creates its own VPC). Present only to support validation error tests via ExtraVars."
  default     = null
}

variable "subnets" {
  type = list(object({
    name                    = string
    cidr_block              = string
    availability_zone       = string
    map_public_ip_on_launch = optional(bool, false)
  }))
  description = "Subnets to create. Defaults to 3 private subnets across 3 AZs."
  default = [
    {
      name              = "private-a"
      cidr_block        = "10.0.1.0/24"
      availability_zone = "us-east-1a"
    },
    {
      name              = "private-b"
      cidr_block        = "10.0.2.0/24"
      availability_zone = "us-east-1b"
    },
    {
      name              = "private-c"
      cidr_block        = "10.0.3.0/24"
      availability_zone = "us-east-1c"
    },
  ]
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "subnet-module-testing"
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
