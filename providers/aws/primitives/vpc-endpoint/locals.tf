locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  endpoints_by_name = { for ep in var.endpoints : ep.name => ep }
}
