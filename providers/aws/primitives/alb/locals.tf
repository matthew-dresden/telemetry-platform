locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  target_groups_by_name = { for tg in var.target_groups : tg.name => tg }

  all_security_group_ids = var.create_security_group ? concat(
    var.security_group_ids,
    [aws_security_group.this[0].id]
  ) : var.security_group_ids
}
