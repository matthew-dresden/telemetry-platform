<!-- BEGIN_TF_DOCS -->
# subnet

Manages a set of subnets (public or private) across availability zones within a VPC. This is a NEW-LOCAL primitive (decision D6) because the upstream `vpc` shell does not create subnet resources.

## Resources managed

- `aws_subnet` -- one per entry in the `subnets` input list

## Usage

```hcl
module "subnet" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/subnet?ref=providers/aws/primitives/subnet/v0.1.0"

  vpc_id = module.vpc.vpc_id
  subnets = [
    {
      name              = "private-a"
      cidr_block        = "10.0.1.0/24"
      availability_zone = "us-east-1a"
    },
    {
      name              = "private-b"
      cidr_block        = "10.0.2.0/24"
      availability_zone = "us-east-1b"
    },
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- 3 private subnets across 3 AZs with a fixture VPC

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
| [aws_subnet.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/subnet) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"subnet"` | no |
| <a name="input_subnets"></a> [subnets](#input\_subnets) | (Required) List of subnet definitions. Each entry must specify name, cidr\_block, availability\_zone, and optionally map\_public\_ip\_on\_launch. | <pre>list(object({<br/>    name                    = string<br/>    cidr_block              = string<br/>    availability_zone       = string<br/>    map_public_ip_on_launch = optional(bool, false)<br/>  }))</pre> | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_vpc_id"></a> [vpc\_id](#input\_vpc\_id) | (Required) The ID of the VPC in which to create the subnets. Must match the pattern ^vpc-. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_availability_zones"></a> [availability\_zones](#output\_availability\_zones) | Map of subnet name to availability zone. |
| <a name="output_subnet_arns"></a> [subnet\_arns](#output\_subnet\_arns) | Map of subnet name to subnet ARN. |
| <a name="output_subnet_ids"></a> [subnet\_ids](#output\_subnet\_ids) | Map of subnet name to subnet ID. |
| <a name="output_subnet_ids_list"></a> [subnet\_ids\_list](#output\_subnet\_ids\_list) | Ordered list of subnet IDs (for ALB/ECS subnet\_ids inputs). |

## Input validation

- `vpc_id` must match `^vpc-`. Plans fail if the pattern is not satisfied.
- `subnets` must be non-empty. Plans fail with an empty list.
- Every `cidr_block` in `subnets` must be a valid CIDR block validated by `can(cidrhost(...))`.
<!-- END_TF_DOCS -->