# subnet/examples/basic -- terraform-docs reference

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
| module.vpc_fixture | module |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| vpc_cidr_block | CIDR block for the fixture VPC. | string | "10.0.0.0/16" | no |
| vpc_id | Override vpc_id (unused -- the fixture creates its own VPC). Present only to support validation error tests via ExtraVars. | string | null | no |
| subnets | Subnets to create. Defaults to 3 private subnets across 3 AZs. | list(object) | see terraform.tfvars | no |
| tags | Additional tags applied to all resources. | map(string) | see terraform.tfvars | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| subnet_ids | Map of subnet name to subnet ID. |
| subnet_ids_list | Ordered list of subnet IDs. |
| subnet_arns | Map of subnet name to subnet ARN. |
| availability_zones | Map of subnet name to availability zone. |
