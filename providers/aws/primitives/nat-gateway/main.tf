resource "aws_eip" "this" {
  for_each = local.public_nat_gateways_by_name

  domain = "vpc"

  tags = merge(local.common_tags, { Name = "${each.key}-eip" })
}

resource "aws_nat_gateway" "this" {
  for_each = local.nat_gateways_by_name

  allocation_id     = each.value.connectivity_type == "public" ? aws_eip.this[each.key].id : null
  subnet_id         = each.value.public_subnet_id
  connectivity_type = each.value.connectivity_type

  tags = merge(local.common_tags, { Name = each.key })

  depends_on = [aws_eip.this]
}
