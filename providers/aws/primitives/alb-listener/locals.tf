locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  listeners_by_name = { for l in var.listeners : l.name => l }

  https_listener = try(
    one([for l in var.listeners : l if l.protocol == "HTTPS"]),
    null
  )

  listener_rules_by_key = {
    for r in var.listener_rules :
    "${r.listener_name}-${r.priority}" => r
  }
}
