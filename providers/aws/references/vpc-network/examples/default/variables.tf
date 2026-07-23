variable "name" {
  type        = string
  description = "(Required) Base name applied to all resources created by this fixture."
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default     = {}
}

variable "vpc_cidr_block" {
  type        = string
  description = "(Required) CIDR block for the VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "(Required) List of exactly three CIDR blocks for the public subnets (one per AZ: a, b, c)."
  default     = ["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 3
    error_message = "public_subnet_cidrs must contain exactly three entries (one per AZ)."
  }
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "(Required) List of exactly three CIDR blocks for the private subnets (one per AZ: a, b, c)."
  default     = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"]

  validation {
    condition     = length(var.private_subnet_cidrs) == 3
    error_message = "private_subnet_cidrs must contain exactly three entries (one per AZ)."
  }
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
