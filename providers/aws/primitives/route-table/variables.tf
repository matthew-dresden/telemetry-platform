variable "vpc_id" {
  type        = string
  description = "(Required) The ID of the VPC in which to create the route tables. Must match the pattern ^vpc-."

  validation {
    condition     = can(regex("^vpc-", var.vpc_id))
    error_message = "vpc_id must start with 'vpc-'."
  }
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
  description = "(Required) List of route table definitions. Each entry specifies a name, subnet associations, and routes. Each route must name exactly one of gateway_id, nat_gateway_id, or vpc_endpoint_id."

  validation {
    condition     = length(var.route_tables) > 0
    error_message = "route_tables must contain at least one entry."
  }

  validation {
    condition = alltrue(flatten([
      for rt in var.route_tables : [
        for r in rt.routes : (
          (r.gateway_id != null ? 1 : 0) +
          (r.nat_gateway_id != null ? 1 : 0) +
          (r.vpc_endpoint_id != null ? 1 : 0)
        ) == 1
      ]
    ]))
    error_message = "Each route must specify exactly one of gateway_id, nat_gateway_id, or vpc_endpoint_id."
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
  default     = "route-table"
}
