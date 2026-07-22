<!-- BEGIN_TF_DOCS -->
# nat-gateway

Manages a set of NAT gateways with their associated Elastic IPs across availability zones. This is a NEW-LOCAL primitive (decision D6) because the upstream `vpc` shell does not create NAT gateway or Elastic IP resources.

## Resources managed

- `aws_eip` -- one per entry in the `nat_gateways` input list (for public NAT gateways)
- `aws_nat_gateway` -- one per entry in the `nat_gateways` input list

## Usage

```hcl
module "nat_gateway" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/nat-gateway?ref=providers/aws/primitives/nat-gateway/v0.1.0"

  nat_gateways = [
    {
      name             = "nat-a"
      public_subnet_id = module.subnet.subnet_ids["public-a"]
    },
    {
      name             = "nat-b"
      public_subnet_id = module.subnet.subnet_ids["public-b"]
    },
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- 1 public NAT gateway in 1 public subnet with a fixture VPC and IGW

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
| [aws_eip.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/eip) | resource |
| [aws_nat_gateway.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/nat_gateway) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"nat-gateway"` | no |
| <a name="input_nat_gateways"></a> [nat\_gateways](#input\_nat\_gateways) | (Required) List of NAT gateway definitions. Each entry specifies name, public\_subnet\_id, and optionally connectivity\_type (public or private). One NAT gateway per entry for HA across AZs. | <pre>list(object({<br/>    name              = string<br/>    public_subnet_id  = string<br/>    connectivity_type = optional(string, "public")<br/>  }))</pre> | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_elastic_ips"></a> [elastic\_ips](#output\_elastic\_ips) | Map of NAT gateway name to Elastic IP public address. |
| <a name="output_nat_gateway_ids"></a> [nat\_gateway\_ids](#output\_nat\_gateway\_ids) | Map of NAT gateway name to NAT gateway ID. |
| <a name="output_nat_gateway_ids_list"></a> [nat\_gateway\_ids\_list](#output\_nat\_gateway\_ids\_list) | Ordered list of NAT gateway IDs. |

## Input validation

- `nat_gateways` must be non-empty. Plans fail with an empty list.
- Every `connectivity_type` in `nat_gateways` must be either `"public"` or `"private"`. Plans fail on any other value.
- Every `public_subnet_id` in `nat_gateways` must start with `"subnet-"`. Plans fail if the pattern is not satisfied.
<!-- END_TF_DOCS -->