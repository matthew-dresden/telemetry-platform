<!-- BEGIN_TF_DOCS -->
# vpc-endpoint

Manages a set of VPC endpoints (Interface, Gateway, and GatewayLoadBalancer types) within a VPC. This is a NEW-LOCAL primitive (decision D6) because the upstream `vpc` shell does not create VPC endpoint resources.

## Resources managed

- `aws_vpc_endpoint` -- one per entry in the `endpoints` input list

## Usage

```hcl
module "vpc_endpoint" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/vpc-endpoint?ref=providers/aws/primitives/vpc-endpoint/v0.1.0"

  vpc_id = module.vpc.vpc_id
  endpoints = [
    {
      name               = "ssm"
      service_name       = "com.amazonaws.us-east-1.ssm"
      vpc_endpoint_type  = "Interface"
      subnet_ids         = module.subnet.subnet_ids_list
      security_group_ids = [module.sg.id]
      private_dns_enabled = true
    },
    {
      name              = "s3"
      service_name      = "com.amazonaws.us-east-1.s3"
      vpc_endpoint_type = "Gateway"
      route_table_ids   = module.route_table.route_table_ids_list
    },
  ]
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/interface` -- 1 Interface endpoint (SSM) with a fixture VPC, private subnet, and security group
- `examples/gateway` -- 1 Gateway endpoint (S3) with a fixture VPC and private route table

## Running tests

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/vpc-endpoint
```

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
| [aws_vpc_endpoint.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/vpc_endpoint) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_endpoints"></a> [endpoints](#input\_endpoints) | (Required) List of VPC endpoint definitions. Each entry must specify name, service\_name, and vpc\_endpoint\_type. Interface endpoints require non-empty subnet\_ids; Gateway endpoints require non-empty route\_table\_ids. | <pre>list(object({<br/>    name                = string<br/>    service_name        = string<br/>    vpc_endpoint_type   = string<br/>    subnet_ids          = optional(list(string), [])<br/>    route_table_ids     = optional(list(string), [])<br/>    security_group_ids  = optional(list(string), [])<br/>    private_dns_enabled = optional(bool, true)<br/>  }))</pre> | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"vpc-endpoint"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_vpc_id"></a> [vpc\_id](#input\_vpc\_id) | (Required) The ID of the VPC in which to create the endpoints. Must match the pattern ^vpc-. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_endpoint_dns_entries"></a> [endpoint\_dns\_entries](#output\_endpoint\_dns\_entries) | Map of endpoint name to DNS entries (list of maps with dns\_name and hosted\_zone\_id). |
| <a name="output_endpoint_ids"></a> [endpoint\_ids](#output\_endpoint\_ids) | Map of endpoint name to VPC endpoint ID. |

## Input validation

- `vpc_id` must match `^vpc-`. Plans fail if the pattern is not satisfied.
- `endpoints` must be non-empty. Plans fail with an empty list.
- Every `vpc_endpoint_type` must be one of `"Interface"`, `"Gateway"`, or `"GatewayLoadBalancer"`. Plans fail on any other value.
- `Interface` endpoints require at least one entry in `subnet_ids`. Plans fail if `subnet_ids` is empty for Interface type.
- `Gateway` endpoints require at least one entry in `route_table_ids`. Plans fail if `route_table_ids` is empty for Gateway type.
<!-- END_TF_DOCS -->