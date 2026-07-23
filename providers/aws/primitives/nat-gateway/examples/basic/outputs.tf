output "nat_gateway_ids" {
  description = "Map of NAT gateway name to NAT gateway ID."
  value       = module.example.nat_gateway_ids
}

output "nat_gateway_ids_list" {
  description = "Ordered list of NAT gateway IDs."
  value       = module.example.nat_gateway_ids_list
}

output "elastic_ips" {
  description = "Map of NAT gateway name to Elastic IP public address."
  value       = module.example.elastic_ips
}
