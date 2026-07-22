variable "vpc_id" {
  type        = string
  description = "(Required) The ID of the VPC in which to create the subnets. Must match the pattern ^vpc-."

  validation {
    condition     = can(regex("^vpc-", var.vpc_id))
    error_message = "vpc_id must start with 'vpc-'."
  }
}

variable "subnets" {
  type = list(object({
    name                    = string
    cidr_block              = string
    availability_zone       = string
    map_public_ip_on_launch = optional(bool, false)
  }))
  description = "(Required) List of subnet definitions. Each entry must specify name, cidr_block, availability_zone, and optionally map_public_ip_on_launch."

  validation {
    condition     = length(var.subnets) > 0
    error_message = "subnets must contain at least one entry."
  }

  validation {
    condition     = alltrue([for s in var.subnets : can(cidrhost(s.cidr_block, 0))])
    error_message = "Every cidr_block in subnets must be a valid CIDR block."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source module."
  default     = "subnet"
}
