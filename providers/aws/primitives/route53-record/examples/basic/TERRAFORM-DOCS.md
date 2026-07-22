# route53-record basic example -- terraform-docs reference

## Requirements

| Name | Version |
|------|---------|
| terraform | >= 1.15.5 |
| aws | >= 6.49.0 |

## Providers

| Name | Version |
|------|---------|
| aws | >= 6.49.0 |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| zone_id | ID of the hosted zone. | string | n/a | yes |
| name | DNS name of the record. | string | n/a | yes |
| type | DNS record type. Valid: A, AAAA, CAA, CNAME, DS, MX, NAPTR, NS, PTR, SOA, SPF, SRV, TXT. | string | "CNAME" | no |
| records | List of record values. | list(string) | n/a | yes |
| ttl | Time to live in seconds. | number | 60 | no |
| tags | Accepted for symmetry. Not applied (D3). | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| fqdn | The FQDN of the DNS record. |
| name | The DNS name of the record. |
| record_type | The DNS record type. |
