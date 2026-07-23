variable "database_name" {
  type        = string
  description = "(Required) The name of the Glue catalog database. Must contain only lowercase letters, digits, and underscores."

  validation {
    condition     = can(regex("^[a-z0-9_]+$", var.database_name))
    error_message = "database_name must contain only lowercase letters, digits, and underscores and must not be empty."
  }
}

variable "description" {
  type        = string
  description = "(Optional) Description for the Glue catalog database."
  default     = "Telemetry usage analytics catalog"
}

variable "location_uri" {
  type        = string
  description = "(Optional) The location of the database (for example, an HDFS path). Must start with s3:// when provided."
  default     = null

  validation {
    condition     = var.location_uri == null || can(regex("^s3://", var.location_uri))
    error_message = "location_uri must start with s3:// when provided."
  }
}

variable "create_table" {
  type        = bool
  description = "(Optional) When true, provisions an aws_glue_catalog_table for Parquet format conversion. Requires the table variable to be non-null."
  default     = false
}

variable "table" {
  type = object({
    name                        = string
    location                    = string
    input_format                = string
    output_format               = string
    serde_serialization_library = string
    columns = list(object({
      name    = string
      type    = string
      comment = optional(string, "")
    }))
    partition_keys = optional(list(object({
      name = string
      type = string
    })), [])
    parameters = optional(map(string), {})
  })
  description = "(Optional) Flexible-schema table configuration. Required when create_table is true. Must include tool and dt partition keys when create_table is true. location must start with s3://. parameters carries table-level Glue parameters (rendered as aws_glue_catalog_table.parameters) -- e.g. an Athena partition-projection configuration (projection.enabled, projection.<key>.type, projection.<key>.range/format, storage.location.template) so partitions are resolved without a crawler. Defaults to an empty map (no table parameters)."
  default     = null

  validation {
    condition     = !var.create_table || var.table != null
    error_message = "table must be non-null when create_table is true."
  }

  validation {
    condition     = var.table == null || can(regex("^s3://", var.table.location))
    error_message = "table.location must start with s3:// when table is provided."
  }

  validation {
    condition = !var.create_table || var.table == null || (
      length([for pk in var.table.partition_keys : pk if pk.name == "tool"]) > 0 &&
      length([for pk in var.table.partition_keys : pk if pk.name == "dt"]) > 0
    )
    error_message = "table.partition_keys must include both 'tool' and 'dt' partition keys when create_table is true."
  }

  # A column name may never also be a partition key. Glue stores partition keys as
  # additional columns in the table descriptor, so an overlapping name produces a
  # descriptor with duplicate columns and Athena/QuickSight reject every query with
  # "HIVE_INVALID_METADATA: Table descriptor contains duplicate columns". Enforcing
  # disjointness here makes that unqueryable-table failure impossible at plan time for
  # every consumer of this primitive.
  validation {
    condition = var.table == null || length(
      setintersection(
        toset([for c in var.table.columns : c.name]),
        toset([for pk in var.table.partition_keys : pk.name])
      )
    ) == 0
    error_message = "table.columns and table.partition_keys must be disjoint: a data column name may not also be a partition key, or Glue rejects the table with HIVE_INVALID_METADATA duplicate columns."
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
  default     = "glue-catalog-database"
}
