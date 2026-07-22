variable "vpc_id" {
  type        = string
  description = "(Required) The ID of the VPC in which to create the endpoints. Must match the pattern ^vpc-."

  validation {
    condition     = can(regex("^vpc-", var.vpc_id))
    error_message = "vpc_id must start with 'vpc-'."
  }
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
  description = "(Required) List of VPC endpoint definitions. Each entry must specify name, service_name, and vpc_endpoint_type. Interface endpoints require non-empty subnet_ids; Gateway endpoints require non-empty route_table_ids."

  validation {
    condition     = length(var.endpoints) > 0
    error_message = "endpoints must contain at least one entry."
  }

  validation {
    condition     = alltrue([for ep in var.endpoints : contains(["Interface", "Gateway", "GatewayLoadBalancer"], ep.vpc_endpoint_type)])
    error_message = "Every vpc_endpoint_type in endpoints must be one of: Interface, Gateway, GatewayLoadBalancer."
  }

  validation {
    condition     = alltrue([for ep in var.endpoints : ep.vpc_endpoint_type != "Interface" || length(ep.subnet_ids) > 0])
    error_message = "Interface endpoints require at least one subnet_id in subnet_ids."
  }

  validation {
    condition     = alltrue([for ep in var.endpoints : ep.vpc_endpoint_type != "Gateway" || length(ep.route_table_ids) > 0])
    error_message = "Gateway endpoints require at least one route_table_id in route_table_ids."
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
  default     = "vpc-endpoint"
}
