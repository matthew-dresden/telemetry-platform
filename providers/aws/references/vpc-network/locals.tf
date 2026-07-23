locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Map public subnet names to their IDs (resolved from the subnet module output).
  public_subnet_ids_by_name = module.public_subnets.subnet_ids

  # Map private subnet names to their IDs (resolved from the subnet module output).
  private_subnet_ids_by_name = module.private_subnets.subnet_ids

  # Resolve the public subnet ID for each NAT gateway by looking up the subnet name.
  nat_gateway_definitions = [
    for ng in var.nat_gateways : {
      name              = ng.name
      public_subnet_id  = local.public_subnet_ids_by_name[ng.public_subnet_name]
      connectivity_type = ng.connectivity_type
    }
  ]

  # The primary NAT gateway name (first in list) used for the single private route table.
  primary_nat_gateway_name = var.nat_gateways[0].name

  # Resolve the private subnet IDs for interface endpoint attachment.
  interface_endpoint_subnet_ids = [
    for name in var.interface_endpoint_subnet_names :
    local.private_subnet_ids_by_name[name]
  ]

  # Resolve route table IDs for the S3 gateway endpoint.
  private_route_table_ids = values(module.private_route_tables.route_table_ids)

  # Build interface endpoint definitions from service names.
  # The endpoint name is derived from the service name by replacing dots with dashes
  # and stripping the common AWS prefix pattern.
  interface_endpoint_definitions = [
    for svc_name in var.interface_endpoint_service_names : {
      name              = replace(svc_name, ".", "-")
      service_name      = svc_name
      vpc_endpoint_type = "Interface"
      subnet_ids        = local.interface_endpoint_subnet_ids
      route_table_ids   = []
      # Attach the managed endpoint security group so clients in the VPC (e.g. the ADOT
      # ECS tasks) can reach the interface endpoints on 443. Without an SG that allows
      # 443 ingress, the default SG blocks the call and SSM/ECR/Logs time out at task start.
      security_group_ids  = [aws_security_group.vpc_endpoints.id]
      private_dns_enabled = true
    }
  ]

  # S3 gateway endpoint definition.
  s3_gateway_endpoint_definition = [
    {
      name                = "s3-gateway"
      service_name        = var.s3_gateway_endpoint_service_name
      vpc_endpoint_type   = "Gateway"
      subnet_ids          = []
      route_table_ids     = local.private_route_table_ids
      security_group_ids  = []
      private_dns_enabled = false
    }
  ]

  # All endpoint definitions combined.
  all_endpoint_definitions = concat(
    local.interface_endpoint_definitions,
    local.s3_gateway_endpoint_definition
  )
}
