variable "monitor_name" {
  type        = string
  description = "(Required) Display name for the anomaly monitor. Must be non-empty."

  validation {
    condition     = length(var.monitor_name) >= 1
    error_message = "monitor_name must be non-empty."
  }
}

variable "monitor_type" {
  type        = string
  description = "(Optional) Monitor type. DIMENSIONAL monitors all AWS services; CUSTOM allows a cost-category filter expression via monitor_specification."
  default     = "DIMENSIONAL"

  validation {
    condition     = contains(["DIMENSIONAL", "CUSTOM"], var.monitor_type)
    error_message = "monitor_type must be one of: DIMENSIONAL, CUSTOM."
  }
}

variable "monitor_specification" {
  type        = string
  description = "(Optional) Cost expression JSON required when monitor_type is CUSTOM; ignored for DIMENSIONAL."
  default     = null

  validation {
    condition     = var.monitor_type != "CUSTOM" || var.monitor_specification != null
    error_message = "monitor_specification must be provided when monitor_type is CUSTOM."
  }
}

variable "subscription_name" {
  type        = string
  description = "(Required) Display name for the alert subscription. Must be non-empty."

  validation {
    condition     = length(var.subscription_name) >= 1
    error_message = "subscription_name must be non-empty."
  }
}

variable "subscription_frequency" {
  type        = string
  description = "(Optional) How often alerts are delivered. Valid values: DAILY, IMMEDIATE, WEEKLY."
  default     = "DAILY"

  validation {
    condition     = contains(["DAILY", "IMMEDIATE", "WEEKLY"], var.subscription_frequency)
    error_message = "subscription_frequency must be one of: DAILY, IMMEDIATE, WEEKLY."
  }
}

variable "threshold_expression" {
  type        = string
  description = "(Required) JSON cost-expression defining the spend anomaly threshold. Must be valid JSON containing a Dimensions object with Key, Values, and MatchOptions fields."

  validation {
    condition     = can(jsondecode(var.threshold_expression))
    error_message = "threshold_expression must be a valid JSON string."
  }

  validation {
    condition     = can(jsondecode(var.threshold_expression)["Dimensions"]["Key"])
    error_message = "threshold_expression must contain a Dimensions.Key field."
  }

  validation {
    condition     = can(jsondecode(var.threshold_expression)["Dimensions"]["Values"])
    error_message = "threshold_expression must contain a Dimensions.Values field."
  }

  validation {
    condition     = can(jsondecode(var.threshold_expression)["Dimensions"]["MatchOptions"])
    error_message = "threshold_expression must contain a Dimensions.MatchOptions field."
  }
}

variable "subscribers" {
  type = list(object({
    address = string
    type    = string
  }))
  description = "(Required) Alert destinations. Must be non-empty. Each subscriber requires a non-empty address and a type of EMAIL or SNS."

  validation {
    condition     = length(var.subscribers) > 0
    error_message = "subscribers must be non-empty -- anomaly alerts must always have a destination."
  }

  validation {
    condition = alltrue([
      for s in var.subscribers : contains(["EMAIL", "SNS"], s.type)
    ])
    error_message = "Each subscriber type must be one of: EMAIL, SNS."
  }

  validation {
    condition = alltrue([
      for s in var.subscribers : length(s.address) > 0
    ])
    error_message = "Each subscriber address must be non-empty."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all taggable resources created by this module."
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
  default     = "cost-anomaly"
}
