variable "name_prefix" {
  type        = string
  description = "Prefix applied to all resource names to distinguish parallel test runs."
}

variable "vpc_cidr_block" {
  type        = string
  description = "CIDR block for the fixture VPC."
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr_block, 0))
    error_message = "vpc_cidr_block must be a valid CIDR block."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all resources created by this fixture module."
  default     = {}
}
