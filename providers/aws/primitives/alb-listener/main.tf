resource "aws_lb_listener" "this" {
  for_each = local.listeners_by_name

  load_balancer_arn = var.load_balancer_arn
  port              = each.value.port
  protocol          = each.value.protocol
  ssl_policy        = each.value.protocol == "HTTPS" ? each.value.ssl_policy : null
  certificate_arn   = each.value.protocol == "HTTPS" ? each.value.certificate_arn : null

  default_action {
    type             = each.value.default_action.type
    target_group_arn = each.value.default_action.type == "forward" ? each.value.default_action.target_group_arn : null

    dynamic "redirect" {
      for_each = each.value.default_action.type == "redirect" ? [each.value.default_action.redirect] : []
      content {
        port        = redirect.value.port
        protocol    = redirect.value.protocol
        status_code = redirect.value.status_code
      }
    }
  }

  tags = merge(local.common_tags, { Name = "${each.key}-listener" })
}

resource "aws_lb_listener_rule" "this" {
  for_each = local.listener_rules_by_key

  listener_arn = aws_lb_listener.this[each.value.listener_name].arn
  priority     = each.value.priority

  action {
    type             = each.value.action.type
    target_group_arn = each.value.action.type == "forward" ? each.value.action.target_group_arn : null
  }

  dynamic "condition" {
    for_each = each.value.conditions
    content {
      dynamic "path_pattern" {
        for_each = condition.value.field == "path-pattern" ? [condition.value] : []
        content {
          values = path_pattern.value.values
        }
      }
      dynamic "host_header" {
        for_each = condition.value.field == "host-header" ? [condition.value] : []
        content {
          values = host_header.value.values
        }
      }
    }
  }

  tags = merge(local.common_tags, { Name = "${each.key}-rule" })
}
