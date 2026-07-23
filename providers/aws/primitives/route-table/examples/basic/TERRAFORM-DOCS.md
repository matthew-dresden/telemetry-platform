# route-table/examples/basic -- terraform-docs reference

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
| aws_subnet.private_a | resource |
| aws_subnet.private_b | resource |
| aws_subnet.public_a | resource |
| aws_internet_gateway.fixture | resource |
| aws_eip.nat | resource |
| aws_nat_gateway.fixture | resource |
| module.vpc_fixture | module |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| vpc_cidr_block | CIDR block for the fixture VPC. | string | "10.0.0.0/16" | no |
| vpc_id | Override vpc_id (unused -- the fixture creates its own VPC). Present only to support validation error tests via ExtraVars. | string | null | no |
| private_subnet_a_cidr | CIDR block for the first private fixture subnet. | string | "10.0.1.0/24" | no |
| private_subnet_b_cidr | CIDR block for the second private fixture subnet. | string | "10.0.2.0/24" | no |
| public_subnet_a_cidr | CIDR block for the public fixture subnet (used for the NAT gateway). | string | "10.0.100.0/24" | no |
| availability_zone_a | First availability zone for fixture subnets. | string | "us-east-1a" | no |
| availability_zone_b | Second availability zone for fixture subnets. | string | "us-east-1b" | no |
| route_tables | Override route_tables. When null, the fixture uses a default private route table with NAT gateway. | list(object) | null | no |
| tags | Additional tags applied to all resources. | map(string) | see terraform.tfvars | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| route_table_ids | Map of route table name to route table ID. |
| route_table_ids_list | Ordered list of route table IDs. |
