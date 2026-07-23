variable "zone_name" {
  type        = string
  description = "(Required) DNS name for the hosted zone. Must match pattern ^[a-z0-9.-]+$."

  validation {
    condition     = can(regex("^[a-z0-9.-]+$", var.zone_name))
    error_message = "zone_name must contain only lowercase alphanumeric characters, hyphens, and dots."
  }
}

variable "comment" {
  type        = string
  description = "(Optional) Comment for the hosted zone."
  default     = "Managed by terraform"
}

variable "force_destroy" {
  type        = bool
  description = "(Optional) Whether to destroy all records in the zone so the zone can be destroyed without error. Defaults to false; set to true only for ephemeral test zones."
  default     = false
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
  default     = "route53-zone"
}
