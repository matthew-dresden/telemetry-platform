variable "name" {
  type        = string
  description = "(Required) LB name. Must be alphanumeric and hyphens only, max 32 characters (ALB name limit)."

  validation {
    condition     = length(var.name) <= 32 && can(regex("^[a-zA-Z0-9-]+$", var.name))
    error_message = "name must be at most 32 characters and contain only alphanumeric characters and hyphens."
  }
}

variable "internal" {
  type        = bool
  description = "(Optional) Whether the ALB is internal. Defaults to true because CloudFront is the public edge for this platform."
  default     = true
}

variable "vpc_id" {
  type        = string
  description = "(Required) VPC ID where the ALB is deployed."

  validation {
    condition     = can(regex("^vpc-", var.vpc_id))
    error_message = "vpc_id must start with 'vpc-'."
  }
}

variable "subnet_ids" {
  type        = list(string)
  description = "(Required) List of subnet IDs. At least 2 subnets across availability zones are required (spec 5.A)."

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "subnet_ids must contain at least 2 subnets across availability zones."
  }
}

variable "security_group_ids" {
  type        = list(string)
  description = "(Optional) List of existing security group IDs to attach to the ALB. If empty and create_security_group is true, a managed SG is created."
  default     = []
}

variable "create_security_group" {
  type        = bool
  description = "(Optional) Whether to create a managed security group for the ALB."
  default     = true
}

variable "ingress_cidr_blocks" {
  type        = list(string)
  description = "(Optional) Allowed ingress CIDR blocks. CloudFront managed prefix list is preferred; supply via this variable."
  default     = []
}

variable "egress_cidr_blocks" {
  type        = list(string)
  description = "(Optional) Egress destination CIDR blocks for the managed ALB security group. When null (the default), egress is restricted to the ALB's own VPC CIDR (looked up from vpc_id) because an ALB only forwards to its in-VPC targets; this is the secure default. Supply an explicit list (for example [\"0.0.0.0/0\"]) to widen egress. Only used when create_security_group is true."
  default     = null

  validation {
    condition     = var.egress_cidr_blocks == null || length(var.egress_cidr_blocks) > 0
    error_message = "egress_cidr_blocks must be null (VPC-scoped default) or a non-empty list of CIDR blocks."
  }
}

variable "idle_timeout" {
  type        = number
  description = "(Optional) ALB idle connection timeout in seconds. Must be between 1 and 4000."
  default     = 60

  validation {
    condition     = var.idle_timeout >= 1 && var.idle_timeout <= 4000
    error_message = "idle_timeout must be between 1 and 4000 seconds inclusive."
  }
}

variable "enable_deletion_protection" {
  type        = bool
  description = "(Optional) Whether to enable deletion protection on the ALB."
  default     = false
}

variable "access_logs" {
  type = object({
    bucket  = string
    prefix  = optional(string, "")
    enabled = optional(bool, true)
  })
  description = "(Optional) S3 access log configuration. When null, access logging is disabled."
  default     = null
}

variable "target_groups" {
  type = list(object({
    name        = string
    port        = number
    protocol    = string
    target_type = string
    health_check = object({
      path                = string
      port                = optional(string, "traffic-port")
      protocol            = optional(string, "HTTP")
      healthy_threshold   = optional(number, 3)
      unhealthy_threshold = optional(number, 3)
      interval            = optional(number, 30)
      timeout             = optional(number, 5)
      matcher             = optional(string, "200")
    })
  }))
  description = "(Required) List of target groups to create. Must be non-empty. The ADOT OTLP/HTTP port is typically 4318."

  validation {
    condition     = length(var.target_groups) > 0
    error_message = "target_groups must contain at least one target group."
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
  default     = "alb"
}
