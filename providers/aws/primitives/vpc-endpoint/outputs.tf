output "endpoint_ids" {
  description = "Map of endpoint name to VPC endpoint ID."
  value       = { for k, e in aws_vpc_endpoint.this : k => e.id }
}

output "endpoint_dns_entries" {
  description = "Map of endpoint name to DNS entries (list of maps with dns_name and hosted_zone_id)."
  value       = { for k, e in aws_vpc_endpoint.this : k => e.dns_entry }
}
