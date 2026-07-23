locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Map route table name to its definition for for_each
  route_tables_by_name = { for rt in var.route_tables : rt.name => rt }

  # Flatten associations: one entry per (route_table_name, subnet_index) pair.
  # The key uses a numeric index rather than the subnet_id so that for_each
  # keys are always known at plan time even when subnet_id is computed.
  associations = flatten([
    for rt in var.route_tables : [
      for i, subnet_id in rt.subnet_ids : {
        key              = "${rt.name}--${i}"
        route_table_name = rt.name
        subnet_id        = subnet_id
      }
    ]
  ])

  associations_map = { for a in local.associations : a.key => a }

  # Flatten routes: one entry per (route_table_name, route_index) pair
  routes = flatten([
    for rt in var.route_tables : [
      for idx, r in rt.routes : {
        key                    = "${rt.name}--${idx}"
        route_table_name       = rt.name
        destination_cidr_block = r.destination_cidr_block
        gateway_id             = r.gateway_id
        nat_gateway_id         = r.nat_gateway_id
        vpc_endpoint_id        = r.vpc_endpoint_id
      }
    ]
  ])

  routes_map = { for r in local.routes : r.key => r }
}
