output "database_name" {
  description = "The name of the Glue catalog database."
  value       = aws_glue_catalog_database.this.name
}

output "database_arn" {
  description = "The Amazon Resource Name (ARN) of the Glue catalog database."
  value       = aws_glue_catalog_database.this.arn
}

output "catalog_id" {
  description = "The account-level catalog ID for this Glue database."
  value       = aws_glue_catalog_database.this.catalog_id
}

output "table_name" {
  description = "The name of the Glue catalog table (the Firehose format-conversion target). Null when create_table is false."
  value       = try(aws_glue_catalog_table.this[0].name, null)
}

output "table_parameters" {
  description = "The table-level Glue parameters rendered onto the Glue catalog table (e.g. the Athena partition-projection configuration). Null when create_table is false."
  value       = try(aws_glue_catalog_table.this[0].parameters, null)
}
