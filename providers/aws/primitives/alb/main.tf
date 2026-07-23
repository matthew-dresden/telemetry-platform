# Look up the VPC CIDR so the managed ALB security group can restrict egress to the
# VPC by default. An ALB only forwards to its registered targets, which live inside
# the VPC, so VPC-scoped egress is the secure default. Only created when this module
# manages the security group and no explicit egress_cidr_blocks override is supplied.
data "aws_vpc" "this" {
  count = var.create_security_group && var.egress_cidr_blocks == null ? 1 : 0

  id = var.vpc_id
}

resource "aws_security_group" "this" {
  count = var.create_security_group ? 1 : 0

  name        = "${var.name}-alb-sg"
  description = "Managed security group for ALB ${var.name}"
  vpc_id      = var.vpc_id

  dynamic "ingress" {
    for_each = length(var.ingress_cidr_blocks) > 0 ? [1] : []
    content {
      description = "Allow inbound traffic from permitted CIDR blocks"
      from_port   = 0
      to_port     = 65535
      protocol    = "tcp"
      cidr_blocks = var.ingress_cidr_blocks
    }
  }

  egress {
    description = "Allow outbound traffic to ALB targets within the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = var.egress_cidr_blocks != null ? var.egress_cidr_blocks : [data.aws_vpc.this[0].cidr_block]
  }

  tags = merge(local.common_tags, { Name = "${var.name}-alb-sg" })
}

resource "aws_lb" "this" {
  name               = var.name
  internal           = var.internal
  load_balancer_type = "application"
  security_groups    = local.all_security_group_ids
  subnets            = var.subnet_ids
  idle_timeout       = var.idle_timeout

  enable_deletion_protection = var.enable_deletion_protection
  drop_invalid_header_fields = true

  dynamic "access_logs" {
    for_each = var.access_logs != null ? [var.access_logs] : []
    content {
      bucket  = access_logs.value.bucket
      prefix  = access_logs.value.prefix
      enabled = access_logs.value.enabled
    }
  }

  tags = merge(local.common_tags, { Name = var.name })
}

resource "aws_lb_target_group" "this" {
  for_each = local.target_groups_by_name

  name        = each.value.name
  port        = each.value.port
  protocol    = each.value.protocol
  target_type = each.value.target_type
  vpc_id      = var.vpc_id

  health_check {
    path                = each.value.health_check.path
    port                = each.value.health_check.port
    protocol            = each.value.health_check.protocol
    healthy_threshold   = each.value.health_check.healthy_threshold
    unhealthy_threshold = each.value.health_check.unhealthy_threshold
    interval            = each.value.health_check.interval
    timeout             = each.value.health_check.timeout
    matcher             = each.value.health_check.matcher
  }

  tags = merge(local.common_tags, { Name = each.value.name })
}
