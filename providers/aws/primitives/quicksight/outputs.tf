output "data_source_arn" {
  description = "The ARN of the optional Athena QuickSight data source. Empty string when no data source is configured."
  value       = var.athena_data_source != null ? aws_quicksight_data_source.athena[0].arn : ""
}

output "data_source_id" {
  description = "The ID of the optional Athena QuickSight data source. Empty string when no data source is configured."
  value       = var.athena_data_source != null ? aws_quicksight_data_source.athena[0].data_source_id : ""
}
