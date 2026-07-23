<!-- BEGIN_TF_DOCS -->
# route53-zone

Manages a single AWS Route53 public hosted zone, exposing the zone ID, name servers list, ARN, and zone name for use by downstream modules.

Used for creating hosted zones for the telemetry-collector platform DNS infrastructure. The upstream route53-record primitive cannot create hosted zones (decision D7), so this dedicated primitive is required.

## Resources managed

- `aws_route53_zone` -- the public hosted zone

## Usage

```hcl
module "route53_zone" {
  source = "git::https://github.com/matthew-dresden/telemetry-platform.git//providers/aws/primitives/route53-zone?ref=providers/aws/primitives/route53-zone/v0.1.0"

  zone_name = "example.com"
  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- public hosted zone with force_destroy enabled for ephemeral test environments

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
| [aws_route53_zone.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route53_zone) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_comment"></a> [comment](#input\_comment) | (Optional) Comment for the hosted zone. | `string` | `"Managed by terraform"` | no |
| <a name="input_force_destroy"></a> [force\_destroy](#input\_force\_destroy) | (Optional) Whether to destroy all records in the zone so the zone can be destroyed without error. Defaults to false; set to true only for ephemeral test zones. | `bool` | `false` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"route53-zone"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |
| <a name="input_zone_name"></a> [zone\_name](#input\_zone\_name) | (Required) DNS name for the hosted zone. Must match pattern ^[a-z0-9.-]+$. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_arn"></a> [arn](#output\_arn) | The Amazon Resource Name (ARN) of the hosted zone. |
| <a name="output_name_servers"></a> [name\_servers](#output\_name\_servers) | A list of name servers in associated (or default) delegation set. AWS always returns exactly four entries. |
| <a name="output_zone_id"></a> [zone\_id](#output\_zone\_id) | The hosted zone ID. |
| <a name="output_zone_name"></a> [zone\_name](#output\_zone\_name) | The DNS name of the hosted zone. |

## Input validation

- `zone_name` must match `^[a-z0-9.-]+$`. Plans fail if the pattern is not satisfied.
- `force_destroy` defaults to false. Set to true only for ephemeral test zones to avoid accidental record deletion in production.
<!-- END_TF_DOCS -->