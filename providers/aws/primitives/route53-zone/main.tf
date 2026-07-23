resource "aws_route53_zone" "this" {
  name          = var.zone_name
  comment       = var.comment
  force_destroy = var.force_destroy

  tags = local.common_tags
}
