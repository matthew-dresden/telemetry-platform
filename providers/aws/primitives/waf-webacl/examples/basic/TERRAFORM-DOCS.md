# waf-webacl basic example -- terraform-docs reference

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
| name | Name for the WAFv2 web ACL. | string | n/a | yes |
| scope | Scope. Valid values: CLOUDFRONT, REGIONAL. | string | "CLOUDFRONT" | no |
| default_action | Default action. Valid values: allow, block. | string | "allow" | no |
| rate_limit_per_ip | Rate-based rule limit per IP per 5-minute window. | number | 2000 | no |
| logging_enabled | Whether to attach a logging configuration. | bool | false | no |
| log_destination_arns | Log destination ARNs. Must contain at least one entry for a valid AWS deployment when logging_enabled is true; not enforced by the module's cross-variable validation. | list(string) | [] | no |
| log_kms_key_arn | KMS key ARN for log encryption. Required when logging_enabled is true (D2). | string | null | no |
| tags | Additional tags. | map(string) | {} | no |
| project_tag | Project tag value applied to all resources via the provider default_tags block. | string | n/a | yes |
| terratest_run_id | Terratest run identifier applied to all resources via the provider default_tags block. | string | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| web_acl_arn | The ARN of the WAFv2 web ACL. |
| web_acl_id | The unique identifier of the WAFv2 web ACL. |
| web_acl_name | The name of the WAFv2 web ACL. |
