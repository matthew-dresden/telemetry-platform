variable "name" {
  type        = string
  description = "(Required) Base name used to derive all resource names in the fixture."
  default     = "telemetry-data-lake-test"
}

variable "transition_storage_class" {
  type        = string
  description = "(Optional) S3 lifecycle transition storage class for cold-tier data."
  default     = "GLACIER"

  validation {
    condition     = contains(["GLACIER", "GLACIER_IR", "DEEP_ARCHIVE"], var.transition_storage_class)
    error_message = "transition_storage_class must be one of GLACIER, GLACIER_IR, or DEEP_ARCHIVE."
  }
}

variable "transition_days" {
  type        = number
  description = "(Optional) Number of days after which objects transition to the cold storage class."
  default     = 90
}

variable "firehose_dynamic_partitioning_jq" {
  type        = map(string)
  description = "(Optional) JQ expressions for Firehose dynamic partitioning (T27). Each value must be valid JQ; the dt partition comes from the Firehose-native !{timestamp:<fmt>} namespace, not JQ, so only the tool key is extracted."
  default = {
    tool = ".tool"
  }
}

variable "glue_partition_projection_tool_values" {
  type        = list(string)
  description = "(Optional) The governed set of tool identifiers used as the Athena enum partition-projection values for the tool partition (projection.tool.values), passed straight through to the module. enum keeps partition pruning scoped to these tools while leaving the table queryable with no tool filter (SELECT * works), unlike an injected tool column which rejects any query lacking a static WHERE tool='...' equality (CONSTRAINT_VIOLATION). The terratest injects its unique per-run tool value into this list BEFORE apply so the end-to-end Athena delivery proof's per-run partitions are projected. Defaults to the tools currently emitting telemetry plus the reserved 'e2e-smoke' value so the example plans standalone."
  default     = ["e2e-smoke", "example-cli"]

  validation {
    condition     = length(var.glue_partition_projection_tool_values) > 0
    error_message = "glue_partition_projection_tool_values must contain at least one tool value."
  }

  validation {
    condition     = alltrue([for t in var.glue_partition_projection_tool_values : can(regex("^[a-zA-Z0-9._-]+$", t))])
    error_message = "each glue_partition_projection_tool_values entry must be non-empty, comma-free, and contain only alphanumeric characters, dots, underscores, or hyphens (values are joined with commas into projection.tool.values)."
  }
}

variable "service_tool_map" {
  type        = map(string)
  description = "(Optional) Passthrough to the module's service_tool_map input: maps a structured OTLP record's resource.service.name to the lake 'tool' partition value the cwl_split transform Lambda assigns. Defaults to {} (inert; only affects structured OTLP records). NO FALLBACK: an unmapped service.name is not reshaped with a catch-all tool."
  default     = {}
}

variable "lake_kms_alias" {
  type        = string
  description = "(Optional) Alias suffix for the telemetry-data KMS CMK (alias/ prefix is added automatically)."
  default     = "telemetry-data"
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources."
  default = {
    Environment = "test"
    Purpose     = "data-lake-module-testing"
  }
}

variable "cross_account_read_principals" {
  type = map(object({
    account_id  = string
    description = optional(string, "")
  }))
  description = "(Optional) Cross-account principals granted read to the data lake, passed straight through to the module. Defaults to {} (no grant). Overridden by enable_cross_account_read_fixture when that flag is true."
  default     = {}
}

variable "enable_cross_account_read_fixture" {
  type        = bool
  description = "(Optional) When true, the fixture grants the CURRENT (test) account root cross-account read to the data lake so the applied bucket policy + KMS-statements output can be asserted against a real, PutBucketPolicy-valid principal that exercises the exact cross-account grant shape. Defaults to false (no grant)."
  default     = false
}

variable "cwl_transform_lambda_reserved_concurrent_executions" {
  type        = number
  description = "(Optional) Passthrough to the module's cwl_transform_lambda_reserved_concurrent_executions input: reserves a guaranteed slice of the account's regional Lambda concurrency pool for the CloudWatch-Logs-split transform Lambda so the Firehose transform is never starved under high-volume re-ingestion. Defaults to null (no reservation, existing behavior); the terratest overrides it to exercise a real reservation."
  default     = null
}

variable "project_tag" {
  type        = string
  description = "(Required) Value for the Project default tag applied to all resources via the provider default_tags block. Supplied by terratest at run time; the offline tfvars value is telemetry-platform."
}

variable "terratest_run_id" {
  type        = string
  description = "(Required) Unique run identifier injected by terratest for scoped-destroy (D-2/D-4). No default -- must be supplied explicitly so an unset run id is caught at plan time."
}
