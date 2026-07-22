# route-table -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.12.1 |
| aws | ~> 6.0.0 |

## Providers

| Name | Version |
|------|---------|
| aws | ~> 6.0.0 |

## Resources

| Name | Type |
|------|------|
| aws_route_table.this | resource |
| aws_route.this | resource |
| aws_route_table_association.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| vpc_id | (Required) The ID of the VPC in which to create the route tables. Must match the pattern ^vpc-. | string | n/a | yes |
| route_tables | (Required) List of route table definitions. Each entry specifies a name, subnet associations, and routes. Each route must name exactly one of gateway_id, nat_gateway_id, or vpc_endpoint_id. | list(object) | n/a | yes |
| tags | (Optional) Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | (Optional) Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | (Optional) Value for the Module tag identifying the source module. | string | "route-table" | no |

## Outputs

| Name | Description |
|------|-------------|
| route_table_ids | Map of route table name to route table ID. |
| route_table_ids_list | Ordered list of route table IDs (for gateway endpoint associations). |
