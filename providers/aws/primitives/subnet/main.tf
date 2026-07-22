resource "aws_subnet" "this" {
  for_each = local.subnets_by_name

  vpc_id                  = var.vpc_id
  cidr_block              = each.value.cidr_block
  availability_zone       = each.value.availability_zone
  map_public_ip_on_launch = each.value.map_public_ip_on_launch

  tags = merge(local.common_tags, { Name = each.key })
}
