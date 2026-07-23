output "nat_gateway_ids" {
  description = "Map of NAT gateway name to NAT gateway ID."
  value       = { for k, n in aws_nat_gateway.this : k => n.id }
}

output "nat_gateway_ids_list" {
  description = "Ordered list of NAT gateway IDs."
  value       = [for n in aws_nat_gateway.this : n.id]
}

output "elastic_ips" {
  description = "Map of NAT gateway name to Elastic IP public address."
  value       = { for k, e in aws_eip.this : k => e.public_ip }
}
