# subnet -- examples/basic

This example creates 3 private subnets across 3 availability zones within a fixture VPC.

## What this example does

1. Creates an `aws_vpc` fixture with a configurable CIDR block.
2. Passes 3 private subnet definitions to the `subnet` module.
3. Exports the resulting `subnet_ids`, `subnet_ids_list`, `subnet_arns`, and `availability_zones` outputs.

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
| vpc_cidr_block | CIDR block for the fixture VPC. | 10.0.0.0/16 |
| subnets | List of subnet definitions. | 3 private subnets across us-east-1a/b/c |
| tags | Additional tags. | test environment tags |

## Outputs

| Name | Description |
|------|-------------|
| subnet_ids | Map of subnet name to subnet ID. |
| subnet_ids_list | Ordered list of subnet IDs. |
| subnet_arns | Map of subnet name to subnet ARN. |
| availability_zones | Map of subnet name to AZ. |
