# route53-record -- basic example

This example demonstrates creating a CNAME DNS record for ACM certificate DNS
validation. The module is usable under `for_each` -- see the portal reference
module for a real-world `for_each` usage over `domain_validation_options`.

Note: `aws_route53_record` does not support tags (per decision D3). The `tags`
input is accepted for call-site symmetry but is not applied to the record.

## Usage

```hcl
module "validation_record" {
  source = "../../"

  zone_id = var.zone_id
  name    = "_abc123.example.com"
  type    = "CNAME"
  records = ["_val123.acm-validations.aws."]
  ttl     = 60
}
```

## What this example creates

- One `aws_route53_record` (CNAME type).

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `fqdn` | The FQDN of the DNS record. |
| `name` | The DNS name of the record. |
| `record_type` | The DNS record type. |
