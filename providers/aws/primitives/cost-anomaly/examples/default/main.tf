variable "monitor_name" {
  type        = string
  description = "Display name for the anomaly monitor."
}

variable "monitor_type" {
  type        = string
  description = "Monitor type: DIMENSIONAL or CUSTOM."
  default     = "DIMENSIONAL"
}

variable "monitor_specification" {
  type        = string
  description = "JSON cost-expression passed through to the module when monitor_type is CUSTOM; ignored for DIMENSIONAL."
  default     = null
}

variable "subscription_name" {
  type        = string
  description = "Display name for the alert subscription."
}

variable "subscription_frequency" {
  type        = string
  description = "How often alerts are delivered: DAILY, IMMEDIATE, or WEEKLY."
  default     = "DAILY"
}

variable "threshold_expression" {
  type        = string
  description = "JSON cost-expression defining the spend anomaly threshold."
}

variable "subscribers" {
  type = list(object({
    address = string
    type    = string
  }))
  description = "Alert destinations. Must be non-empty. Each subscriber requires address and type (EMAIL or SNS)."
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

module "example" {
  source = "../../"

  monitor_name           = var.monitor_name
  monitor_type           = var.monitor_type
  monitor_specification  = var.monitor_specification
  subscription_name      = var.subscription_name
  subscription_frequency = var.subscription_frequency
  threshold_expression   = var.threshold_expression
  subscribers            = var.subscribers
  tags                   = var.tags
}

output "monitor_arn" {
  description = "The ARN of the anomaly monitor."
  value       = module.example.monitor_arn
}

output "subscription_arn" {
  description = "The ARN of the alert subscription."
  value       = module.example.subscription_arn
}
