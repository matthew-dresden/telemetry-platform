<!-- BEGIN_TF_DOCS -->
# glue-catalog-database

Manages a Glue Data Catalog database and an optional format-conversion table with Parquet SerDe and `tool`/`dt` partition keys for the telemetry-collector data lake.

The `create_table` gate (default `false`) controls whether an `aws_glue_catalog_table` is provisioned alongside the database. When the gate is on (`create_table = true`) the `table` variable must be supplied and must include both `tool` and `dt` partition keys. The table is the single source of truth for the Parquet schema that Firehose reads via its `data_format_conversion` block (spec 1.B B4).

## Resources managed

- `aws_glue_catalog_database` -- the Glue catalog database
- `aws_glue_catalog_table` -- the Parquet format-conversion table (created only when `create_table = true`)

## Usage

```hcl
module "catalog" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/glue-catalog-database?ref=providers/aws/primitives/glue-catalog-database/v0.1.0"

  database_name = "telemetry"
  description   = "Telemetry usage analytics catalog"
  create_table  = true

  table = {
    name                       = "telemetry_events"
    location                   = "s3://my-data-lake/raw/"
    input_format               = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format              = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    serde_serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    columns = [
      { name = "timestamp",    type = "timestamp",        comment = "Event timestamp" },
      { name = "service_name", type = "string",           comment = "Originating service" },
      { name = "severity",     type = "string",           comment = "Log severity" },
      { name = "body",         type = "string",           comment = "Log body" },
      { name = "attributes",   type = "map<string,string>", comment = "Open attributes map for schema-on-read" },
    ]
    partition_keys = [
      { name = "tool", type = "string" },
      { name = "dt",   type = "string" },
    ]

    # Athena partition projection -- partitions resolved from the S3 key layout
    # without a crawler so queries work immediately.
    parameters = {
      "projection.enabled"          = "true"
      "projection.tool.type"        = "injected"
      "projection.dt.type"          = "date"
      "projection.dt.format"        = "yyyy-MM-dd"
      "projection.dt.range"         = "NOW-3YEARS,NOW"
      "projection.dt.interval"      = "1"
      "projection.dt.interval.unit" = "DAYS"
      "storage.location.template"   = "s3://my-data-lake/raw/tool=$${tool}/dt=$${dt}"
    }
  }

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- database only, `create_table` left at its `false` default
- `examples/with-table` -- database + table with typed columns, an `attributes` `map<string,string>` column, `tool`/`dt` partition keys with a Parquet SerDe, and an Athena partition-projection configuration passed via `table.parameters`

## Table parameters and Athena partition projection

`table.parameters` is an optional `map(string)` rendered onto `aws_glue_catalog_table.parameters`. It carries table-level Glue parameters -- most importantly an Athena partition-projection configuration, which lets Athena resolve partitions directly from the S3 key layout (`tool=<x>/dt=<yyyy-MM-dd>/`) without a Glue crawler or registered partitions, so queries work immediately. A typical projection sets `projection.enabled = "true"`, an `injected` `tool` dimension, a `date` `dt` dimension whose `projection.dt.format` matches the Firehose `!{timestamp:...}` namespace, and a `storage.location.template` mapping the projected placeholders onto the data prefix. The default is an empty map (no table parameters).

## create_table gate

The `create_table` variable (default `false`) gates the `aws_glue_catalog_table` resource via `count = var.create_table ? 1 : 0`. When the gate is off, no table is created and `table_name` outputs `null`. When the gate is on:

- `table` must be non-null (validation fails otherwise)
- `table.partition_keys` must include both `tool` and `dt` (validation fails otherwise)
- `table.location` must start with `s3://` (validation fails otherwise)

## tool/dt partition contract

All Glue tables managed by this module that are used for Firehose format conversion must include `tool` (string) and `dt` (string) partition keys. This contract aligns with spec section 1.B B4 and the dynamic partitioning prefix written by Firehose: `tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp:yyyy-MM-dd}/`.

## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 6.49.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_glue_catalog_database.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/glue_catalog_database) | resource |
| [aws_glue_catalog_table.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/glue_catalog_table) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_create_table"></a> [create\_table](#input\_create\_table) | (Optional) When true, provisions an aws\_glue\_catalog\_table for Parquet format conversion. Requires the table variable to be non-null. | `bool` | `false` | no |
| <a name="input_database_name"></a> [database\_name](#input\_database\_name) | (Required) The name of the Glue catalog database. Must contain only lowercase letters, digits, and underscores. | `string` | n/a | yes |
| <a name="input_description"></a> [description](#input\_description) | (Optional) Description for the Glue catalog database. | `string` | `"Telemetry usage analytics catalog"` | no |
| <a name="input_location_uri"></a> [location\_uri](#input\_location\_uri) | (Optional) The location of the database (for example, an HDFS path). Must start with s3:// when provided. | `string` | `null` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"glue-catalog-database"` | no |
| <a name="input_table"></a> [table](#input\_table) | (Optional) Flexible-schema table configuration. Required when create\_table is true. Must include tool and dt partition keys when create\_table is true. location must start with s3://. parameters carries table-level Glue parameters (rendered as aws\_glue\_catalog\_table.parameters) -- e.g. an Athena partition-projection configuration (projection.enabled, projection.<key>.type, projection.<key>.range/format, storage.location.template) so partitions are resolved without a crawler. Defaults to an empty map (no table parameters). | <pre>object({<br/>    name                        = string<br/>    location                    = string<br/>    input_format                = string<br/>    output_format               = string<br/>    serde_serialization_library = string<br/>    columns = list(object({<br/>      name    = string<br/>      type    = string<br/>      comment = optional(string, "")<br/>    }))<br/>    partition_keys = optional(list(object({<br/>      name = string<br/>      type = string<br/>    })), [])<br/>    parameters = optional(map(string), {})<br/>  })</pre> | `null` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_catalog_id"></a> [catalog\_id](#output\_catalog\_id) | The account-level catalog ID for this Glue database. |
| <a name="output_database_arn"></a> [database\_arn](#output\_database\_arn) | The Amazon Resource Name (ARN) of the Glue catalog database. |
| <a name="output_database_name"></a> [database\_name](#output\_database\_name) | The name of the Glue catalog database. |
| <a name="output_table_name"></a> [table\_name](#output\_table\_name) | The name of the Glue catalog table (the Firehose format-conversion target). Null when create\_table is false. |
| <a name="output_table_parameters"></a> [table\_parameters](#output\_table\_parameters) | The table-level Glue parameters rendered onto the Glue catalog table (e.g. the Athena partition-projection configuration). Null when create\_table is false. |

## Input validation

- `database_name` must match `^[a-z0-9_]+$`. Plans fail for invalid or empty names.
- `location_uri`, when provided, must start with `s3://`. Plans fail for non-S3 URIs.
- `table` must be non-null when `create_table` is true. Plans fail otherwise.
- `table.location` must start with `s3://` when `table` is provided. Plans fail otherwise.
- `table.partition_keys` must include both `tool` and `dt` when `create_table` is true. Plans fail when either is absent.
- `table.columns` and `table.partition_keys` must be disjoint: a data column name may not also be a partition key, or Glue rejects the table with `HIVE_INVALID_METADATA: Table descriptor contains duplicate columns`. Plans fail when a name appears in both.
<!-- END_TF_DOCS -->