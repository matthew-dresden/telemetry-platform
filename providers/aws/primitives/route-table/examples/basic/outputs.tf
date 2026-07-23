output "route_table_ids" {
  description = "Map of route table name to route table ID."
  value       = module.example.route_table_ids
}

output "route_table_ids_list" {
  description = "Ordered list of route table IDs."
  value       = module.example.route_table_ids_list
}
