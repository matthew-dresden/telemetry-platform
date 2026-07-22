<!-- BEGIN_TF_DOCS -->
# route-table

Manages route tables, their routes, and subnet associations within a VPC. This is a NEW-LOCAL primitive (decision D6) because the upstream `vpc` shell does not create route table resources.

## Resources managed

- `aws_route_table` -- one per entry in the `route_tables` input list
- `aws_route` -- one per route in each route table's `routes` list
- `aws_route_table_association` -- one per (route_table, subnet_id) pair

## Usage

```hcl
module "route_table" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/route-table?ref=providers/aws/primitives/route-table/v0.1.0"

  vpc_id = module.vpc.vpc_id
  route_tables = [
    {
      name       = "private"
      subnet_ids = module.subnet.subnet_ids_list
      routes = [
        {
          destination_cidr_block = "0.0.0.0/0"
          nat_gateway_id         = module.nat_gateway.nat_gateway_ids["nat-a"]
        }
      ]
    }
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- 1 private route table with default route to a NAT gateway, 2 subnet associations

## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.15.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.49.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 6.49.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_route.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route) | resource |
| [aws_route_table.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route_table) | resource |
| [aws_route_table_association.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route_table_association) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"route-table"` | no |
| <a name="input_route_tables"></a> [route\_tables](#input\_route\_tables) | (Required) List of route table definitions. Each entry specifies a name, subnet associations, and routes. Each route must name exactly one of gateway\_id, nat\_gateway\_id, or vpc\_endpoint\_id. | <pre>list(object({<br/>    name       = string<br/>    subnet_ids = list(string)<br/>    routes = list(object({<br/>      destination_cidr_block = string<br/>      gateway_id             = optional(string)<br/>      nat_gateway_id         = optional(string)<br/>      vpc_endpoint_id        = optional(string)<br/>    }))<br/>  }))</pre> | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_vpc_id"></a> [vpc\_id](#input\_vpc\_id) | (Required) The ID of the VPC in which to create the route tables. Must match the pattern ^vpc-. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_route_table_ids"></a> [route\_table\_ids](#output\_route\_table\_ids) | Map of route table name to route table ID. |
| <a name="output_route_table_ids_list"></a> [route\_table\_ids\_list](#output\_route\_table\_ids\_list) | Ordered list of route table IDs (for gateway endpoint associations). |

## Input validation

- `vpc_id` must match `^vpc-`. Plans fail if the pattern is not satisfied.
- `route_tables` must be non-empty. Plans fail with an empty list.
- Each route must specify exactly one of `gateway_id`, `nat_gateway_id`, or `vpc_endpoint_id`. Plans fail for zero or multiple targets.
<!-- END_TF_DOCS -->