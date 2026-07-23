resource "aws_vpc_endpoint" "this" {
  for_each = local.endpoints_by_name

  vpc_id              = var.vpc_id
  service_name        = each.value.service_name
  vpc_endpoint_type   = each.value.vpc_endpoint_type
  subnet_ids          = each.value.subnet_ids
  route_table_ids     = each.value.route_table_ids
  security_group_ids  = length(each.value.security_group_ids) > 0 ? each.value.security_group_ids : null
  private_dns_enabled = each.value.vpc_endpoint_type == "Interface" ? each.value.private_dns_enabled : null

  tags = merge(local.common_tags, { Name = each.key })
}
