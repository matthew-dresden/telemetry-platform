output "vpc_id" {
  description = "The ID of the VPC."
  value       = module.vpc.vpc_id
}

output "internet_gateway_id" {
  description = "The ID of the Internet Gateway created and attached to the VPC. Used as the public route table gateway (docs/terragrunt-concepts.md)."
  value       = aws_internet_gateway.this.id
}

output "public_subnet_ids" {
  description = "Ordered list of public subnet IDs."
  value       = module.public_subnets.subnet_ids_list
}

output "private_subnet_ids" {
  description = "Ordered list of private subnet IDs."
  value       = module.private_subnets.subnet_ids_list
}

output "nat_gateway_ids" {
  description = "Ordered list of NAT gateway IDs."
  value       = module.nat_gateways.nat_gateway_ids_list
}

output "route_table_ids" {
  description = "Ordered list of private route table IDs."
  value       = module.private_route_tables.route_table_ids_list
}

output "interface_endpoint_ids" {
  description = "Ordered list of Interface-type VPC endpoint IDs."
  value = [
    for svc_name in var.interface_endpoint_service_names :
    module.vpc_endpoints.endpoint_ids[replace(svc_name, ".", "-")]
  ]
}

output "s3_gateway_endpoint_id" {
  description = "The ID of the S3 Gateway VPC endpoint."
  value       = module.vpc_endpoints.endpoint_ids["s3-gateway"]
}
