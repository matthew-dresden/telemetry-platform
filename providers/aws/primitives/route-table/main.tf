resource "aws_route_table" "this" {
  for_each = local.route_tables_by_name

  vpc_id = var.vpc_id

  tags = merge(local.common_tags, { Name = each.key })
}

resource "aws_route" "this" {
  for_each = local.routes_map

  route_table_id         = aws_route_table.this[each.value.route_table_name].id
  destination_cidr_block = each.value.destination_cidr_block
  gateway_id             = each.value.gateway_id
  nat_gateway_id         = each.value.nat_gateway_id
  vpc_endpoint_id        = each.value.vpc_endpoint_id
}

resource "aws_route_table_association" "this" {
  for_each = local.associations_map

  route_table_id = aws_route_table.this[each.value.route_table_name].id
  subnet_id      = each.value.subnet_id
}
