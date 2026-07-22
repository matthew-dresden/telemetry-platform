# subnet -- terraform-docs reference

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
| aws_subnet.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| vpc_id | (Required) The ID of the VPC in which to create the subnets. Must match the pattern ^vpc-. | string | n/a | yes |
| subnets | (Required) List of subnet definitions. Each entry must specify name, cidr_block, availability_zone, and optionally map_public_ip_on_launch. | list(object) | n/a | yes |
| tags | (Optional) Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | (Optional) Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | (Optional) Value for the Module tag identifying the source module. | string | "subnet" | no |

## Outputs

| Name | Description |
|------|-------------|
| subnet_ids | Map of subnet name to subnet ID. |
| subnet_ids_list | Ordered list of subnet IDs (for ALB/ECS subnet_ids inputs). |
| subnet_arns | Map of subnet name to subnet ARN. |
| availability_zones | Map of subnet name to availability zone. |
