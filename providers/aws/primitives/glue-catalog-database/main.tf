resource "aws_glue_catalog_database" "this" {
  name         = var.database_name
  description  = var.description
  location_uri = var.location_uri

  tags = local.common_tags
}

resource "aws_glue_catalog_table" "this" {
  count         = var.create_table ? 1 : 0
  name          = var.table.name
  database_name = aws_glue_catalog_database.this.name

  # Table-level Glue parameters (e.g. Athena partition-projection configuration).
  # Empty by default, in which case no table parameters are set.
  parameters = var.table.parameters

  storage_descriptor {
    location      = var.table.location
    input_format  = var.table.input_format
    output_format = var.table.output_format

    ser_de_info {
      serialization_library = var.table.serde_serialization_library
    }

    dynamic "columns" {
      for_each = var.table.columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
      }
    }
  }

  dynamic "partition_keys" {
    for_each = var.table.partition_keys
    content {
      name = partition_keys.value.name
      type = partition_keys.value.type
    }
  }
}
