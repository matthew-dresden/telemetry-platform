output "route_table_ids" {
  description = "Map of route table name to route table ID."
  value       = { for k, rt in aws_route_table.this : k => rt.id }
}

output "route_table_ids_list" {
  description = "Ordered list of route table IDs (for gateway endpoint associations)."
  value       = [for rt in aws_route_table.this : rt.id]
}
