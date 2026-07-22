# route-table -- examples/basic

This example creates 1 private route table with a default route to a fixture NAT gateway and 2 subnet associations.

## What this example does

1. Creates a fixture VPC, 2 private subnets, 1 public subnet, an internet gateway, an EIP, and a NAT gateway.
2. Passes a single route table definition to the `route-table` module with the NAT gateway as the default route target.
3. Associates the 2 private subnets with the route table.
4. Exports the resulting `route_table_ids` and `route_table_ids_list` outputs.

## Usage

This example is intended for automated Terratest runs. To apply it manually:

```bash
cd examples/basic
terraform init
terraform apply
```

## Inputs

| Name | Description | Default |
|------|-------------|---------|
| vpc_cidr_block | CIDR block for the fixture VPC. | "10.0.0.0/16" |
| vpc_id | Override vpc_id (unused -- the fixture creates its own VPC). Present only to support validation error tests via ExtraVars. | null |
| private_subnet_a_cidr | CIDR block for the first private fixture subnet. | "10.0.1.0/24" |
| private_subnet_b_cidr | CIDR block for the second private fixture subnet. | "10.0.2.0/24" |
| public_subnet_a_cidr | CIDR block for the public fixture subnet (used for the NAT gateway). | "10.0.100.0/24" |
| availability_zone_a | First availability zone for fixture subnets. | "us-east-1a" |
| availability_zone_b | Second availability zone for fixture subnets. | "us-east-1b" |
| route_tables | Override route_tables. When null, the fixture uses a default private route table with NAT gateway. | null |
| tags | Additional tags applied to all resources. | test environment tags |

## Outputs

| Name | Description |
|------|-------------|
| route_table_ids | Map of route table name to route table ID. |
| route_table_ids_list | Ordered list of route table IDs. |
