variable "database_name" {
  type        = string
  description = "The name for the Glue catalog database."
}

variable "description" {
  type        = string
  description = "Description for the Glue catalog database."
  default     = "Telemetry usage analytics catalog"
}

variable "s3_location" {
  type        = string
  description = "S3 URI where the Glue table data resides."
  default     = "s3://telemetry-data-lake-example/raw/"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to resources."
  default     = {}
}

module "example" {
  source = "../../"

  database_name = var.database_name
  description   = var.description
  create_table  = true

  table = {
    name                        = "telemetry_events"
    location                    = var.s3_location
    input_format                = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format               = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    serde_serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    columns = [
      {
        name    = "timestamp"
        type    = "timestamp"
        comment = "Event timestamp"
      },
      {
        name    = "service_name"
        type    = "string"
        comment = "Name of the originating service"
      },
      {
        name    = "severity"
        type    = "string"
        comment = "Log severity level"
      },
      {
        name    = "body"
        type    = "string"
        comment = "Log event body"
      },
      {
        name    = "attributes"
        type    = "map<string,string>"
        comment = "Open attributes map for client-specific fields (schema-on-read)"
      },
    ]
    partition_keys = [
      {
        name = "tool"
        type = "string"
      },
      {
        name = "dt"
        type = "string"
      },
    ]

    # Athena partition projection: partitions are resolved from the S3 key layout
    # (tool=<x>/dt=<yyyy-MM-dd>/) without a crawler or registered partitions, so
    # queries work immediately. tool is an injected (unbounded) dimension; dt is a
    # date dimension whose format matches the Firehose timestamp namespace. The
    # storage.location.template maps the projected placeholders onto the data prefix.
    parameters = {
      "projection.enabled"          = "true"
      "projection.tool.type"        = "injected"
      "projection.dt.type"          = "date"
      "projection.dt.format"        = "yyyy-MM-dd"
      "projection.dt.range"         = "NOW-3YEARS,NOW"
      "projection.dt.interval"      = "1"
      "projection.dt.interval.unit" = "DAYS"
      "storage.location.template"   = "${trimsuffix(var.s3_location, "/")}/tool=$${tool}/dt=$${dt}"
    }
  }

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "glue-catalog-database-module-with-table-example"
    Owner       = "terraform"
  })
}

output "database_name" {
  description = "The name of the Glue catalog database."
  value       = module.example.database_name
}

output "database_arn" {
  description = "The ARN of the Glue catalog database."
  value       = module.example.database_arn
}

output "catalog_id" {
  description = "The account catalog ID."
  value       = module.example.catalog_id
}

output "table_name" {
  description = "The Glue table name for Firehose format-conversion."
  value       = module.example.table_name
}

output "table_parameters" {
  description = "The table-level Glue parameters (Athena partition-projection configuration) rendered onto the table."
  value       = module.example.table_parameters
}
