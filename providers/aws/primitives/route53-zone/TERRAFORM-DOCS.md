# route53-zone -- terraform-docs reference

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
| aws_route53_zone.this | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| zone_name | (Required) DNS name for the hosted zone. Must match pattern ^[a-z0-9.-]+$. | string | n/a | yes |
| comment | (Optional) Comment for the hosted zone. | string | "Managed by terraform" | no |
| force_destroy | (Optional) Whether to destroy all records in the zone so the zone can be destroyed without error. Defaults to false; set to true only for ephemeral test zones. | bool | false | no |
| tags | (Optional) Additional tags applied to all resources created by this module. | map(string) | {} | no |
| managed_by_tag | (Optional) Value for the ManagedBy tag. | string | "terraform" | no |
| module_tag | (Optional) Value for the Module tag identifying the source module. | string | "route53-zone" | no |

## Outputs

| Name | Description |
|------|-------------|
| zone_id | The hosted zone ID. |
| name_servers | A list of name servers in associated (or default) delegation set. AWS always returns exactly four entries. |
| arn | The Amazon Resource Name (ARN) of the hosted zone. |
| zone_name | The DNS name of the hosted zone. |

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.12.1 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.0.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | ~> 6.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_route53_zone.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route53_zone) | resource |

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

| Name | Description |
| ---- | ----------- |
| <a name="output_arn"></a> [arn](#output\_arn) | The Amazon Resource Name (ARN) of the hosted zone. |
| <a name="output_name_servers"></a> [name\_servers](#output\_name\_servers) | A list of name servers in associated (or default) delegation set. AWS always returns exactly four entries. |
| <a name="output_zone_id"></a> [zone\_id](#output\_zone\_id) | The hosted zone ID. |
| <a name="output_zone_name"></a> [zone\_name](#output\_zone\_name) | The DNS name of the hosted zone. |
<!-- END_TF_DOCS -->