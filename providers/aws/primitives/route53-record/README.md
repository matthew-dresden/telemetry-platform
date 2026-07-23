<!-- BEGIN_TF_DOCS -->
## Records XOR alias contract

This module accepts two mutually exclusive record shapes. Exactly one of
`records` or `alias` must be set; supplying both or neither is invalid and a
`lifecycle.precondition` fails the plan (fail-fast, no silent drop).

- **Static records path**: set `records` (list of values) and `ttl`. Used for
  CNAME, TXT, MX, and other record types that carry static values. `alias`
  must not be set.
- **Alias path**: set `alias` (object with `name`, `zone_id`,
  `evaluate_target_health`). Used for A/AAAA records pointing at AWS-managed
  targets such as CloudFront distributions, ELBs, and API Gateways. `records`
  and `ttl` must not be set.

The `alias` input and the `records` input are mutually exclusive (records XOR
alias). The terraform-docs Inputs table below documents every input.

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
| [aws_route53_record.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route53_record) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_alias"></a> [alias](#input\_alias) | (Optional) Alias target configuration. Mutually exclusive with records and ttl. Use when pointing the record at a CloudFront distribution, ELB, or other AWS alias target. Must not be set when records is provided. | <pre>object({<br/>    name                   = string<br/>    zone_id                = string<br/>    evaluate_target_health = bool<br/>  })</pre> | `null` | no |
| <a name="input_allow_overwrite"></a> [allow\_overwrite](#input\_allow\_overwrite) | (Optional) Allow Terraform to overwrite an existing record of the same name+type instead of failing. Set true only for records this unit is authoritative for (e.g. an NS delegation written into a parent zone), so a rebuild can re-point the record to a freshly created child zone idempotently. Default false: never clobber a record the unit does not own. | `bool` | `false` | no |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Accepted for call-site symmetry. Not applied to the record (aws\_route53\_record does not support tags, per D3). | `string` | `"terraform"` | no |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Accepted for call-site symmetry. Not applied to the record (aws\_route53\_record does not support tags, per D3). | `string` | `"route53-record"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) DNS name of the record. | `string` | n/a | yes |
| <a name="input_records"></a> [records](#input\_records) | (Optional) List of static record values. Required when alias is null. Must not be set when alias is provided. | `list(string)` | `null` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Accepted for call-site symmetry with other primitives. aws\_route53\_record does not support tags (per decision D3); this input is not applied to the record. | `map(string)` | `{}` | no |
| <a name="input_ttl"></a> [ttl](#input\_ttl) | (Optional) Time to live for the DNS record in seconds. Required when alias is null. Must be positive. Must not be set when alias is provided. | `number` | `null` | no |
| <a name="input_type"></a> [type](#input\_type) | (Required) DNS record type. Valid values: A, AAAA, CAA, CNAME, DS, MX, NAPTR, NS, PTR, SOA, SPF, SRV, TXT. | `string` | n/a | yes |
| <a name="input_zone_id"></a> [zone\_id](#input\_zone\_id) | (Required) ID of the hosted zone in which to create the record. | `string` | n/a | yes |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_accepted_inputs"></a> [accepted\_inputs](#output\_accepted\_inputs) | Inputs accepted at the module interface for call-site symmetry / toggles that this module does not consume internally; surfaced for caller introspection and to assert interface stability. |
| <a name="output_fqdn"></a> [fqdn](#output\_fqdn) | The FQDN of the DNS record. Consumed by aws\_acm\_certificate\_validation.validation\_record\_fqdns. |
| <a name="output_name"></a> [name](#output\_name) | The DNS name of the record. |
| <a name="output_record_type"></a> [record\_type](#output\_record\_type) | The DNS record type. |
<!-- END_TF_DOCS -->