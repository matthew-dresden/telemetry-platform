variable "aws_account_id" {
  type        = string
  description = "(Required) The 12-digit AWS account ID where QuickSight is active."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be exactly 12 digits."
  }
}

variable "athena_data_source" {
  type = object({
    data_source_id = string
    name           = string
    workgroup_name = string
    catalog        = optional(string, "AwsDataCatalog")
    database       = optional(string, "default")
  })
  description = "(Optional) Configuration for an optional Athena data source pointing at a cost-capped workgroup. When null, no data source is created."
  default     = null
}

variable "data_source_permissions" {
  type = list(object({
    principal = string
    actions   = list(string)
  }))
  description = "(Optional) Explicit permission grants applied to the Athena data source. Each principal must be a QuickSight user ARN (Standard-edition compatible). When empty, the data source is created with only the implicit creator access."
  default     = []
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
  default     = "quicksight"
}
