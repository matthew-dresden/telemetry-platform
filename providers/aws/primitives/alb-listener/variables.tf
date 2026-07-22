variable "load_balancer_arn" {
  type        = string
  description = "(Required) ARN of the target ALB (from alb module output alb_arn)."

  validation {
    condition     = can(regex("^arn:aws:elasticloadbalancing:", var.load_balancer_arn))
    error_message = "load_balancer_arn must be a valid ALB ARN starting with 'arn:aws:elasticloadbalancing:'."
  }
}

variable "listeners" {
  type = list(object({
    name            = string
    port            = number
    protocol        = string
    ssl_policy      = optional(string)
    certificate_arn = optional(string)
    default_action = object({
      type             = string
      target_group_arn = optional(string)
      redirect = optional(object({
        port        = string
        protocol    = string
        status_code = string
      }))
    })
  }))
  description = "(Required) Listeners to create. HTTPS listeners require ssl_policy and a valid ACM certificate_arn. Forward actions require target_group_arn."

  validation {
    condition     = length(var.listeners) > 0
    error_message = "listeners must contain at least one listener."
  }

  validation {
    condition = alltrue([
      for l in var.listeners : contains(["HTTP", "HTTPS"], l.protocol)
    ])
    error_message = "Each listener protocol must be one of: HTTP, HTTPS."
  }

  validation {
    condition = alltrue([
      for l in var.listeners :
      l.protocol != "HTTPS" || (l.ssl_policy != null && l.ssl_policy != "" && l.certificate_arn != null && can(regex("^arn:aws:acm:", l.certificate_arn)))
    ])
    error_message = "HTTPS listeners require both ssl_policy (non-null, non-empty) and a valid ACM certificate_arn starting with 'arn:aws:acm:'."
  }

  validation {
    condition = alltrue([
      for l in var.listeners :
      l.default_action.type != "forward" || (l.default_action.target_group_arn != null && l.default_action.target_group_arn != "")
    ])
    error_message = "Listeners with a forward default_action must provide a non-empty target_group_arn."
  }

  validation {
    condition = alltrue([
      for l in var.listeners :
      l.default_action.type != "redirect" || l.default_action.redirect != null
    ])
    error_message = "Listeners with a redirect default_action must provide a redirect configuration block."
  }
}

variable "listener_rules" {
  type = list(object({
    listener_name = string
    priority      = number
    conditions = list(object({
      field  = string
      values = list(string)
    }))
    action = object({
      type             = string
      target_group_arn = optional(string)
    })
  }))
  description = "(Optional) Listener rules to create. Priority must be between 1 and 50000."
  default     = []

  validation {
    condition = alltrue([
      for r in var.listener_rules : r.priority >= 1 && r.priority <= 50000
    ])
    error_message = "Each listener rule priority must be between 1 and 50000 inclusive."
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
  default     = "alb-listener"
}
