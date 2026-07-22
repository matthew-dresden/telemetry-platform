output "endpoint_ids" {
  description = "Map of endpoint name to VPC endpoint ID."
  value       = module.example.endpoint_ids
}

output "endpoint_dns_entries" {
  description = "Map of endpoint name to DNS entries."
  value       = module.example.endpoint_dns_entries
}
