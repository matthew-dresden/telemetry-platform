locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  nat_gateways_by_name        = { for ng in var.nat_gateways : ng.name => ng }
  public_nat_gateways_by_name = { for ng in var.nat_gateways : ng.name => ng if ng.connectivity_type == "public" }
}
