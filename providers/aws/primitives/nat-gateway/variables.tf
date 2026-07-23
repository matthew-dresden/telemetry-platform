variable "nat_gateways" {
  type = list(object({
    name              = string
    public_subnet_id  = string
    connectivity_type = optional(string, "public")
  }))
  description = "(Required) List of NAT gateway definitions. Each entry specifies name, public_subnet_id, and optionally connectivity_type (public or private). One NAT gateway per entry for HA across AZs."

  validation {
    condition     = length(var.nat_gateways) > 0
    error_message = "nat_gateways must contain at least one entry."
  }

  validation {
    condition     = alltrue([for ng in var.nat_gateways : contains(["public", "private"], ng.connectivity_type)])
    error_message = "Every connectivity_type in nat_gateways must be either 'public' or 'private'."
  }

  validation {
    condition     = alltrue([for ng in var.nat_gateways : can(regex("^subnet-", ng.public_subnet_id))])
    error_message = "Every public_subnet_id in nat_gateways must start with 'subnet-'."
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
  default     = "nat-gateway"
}
