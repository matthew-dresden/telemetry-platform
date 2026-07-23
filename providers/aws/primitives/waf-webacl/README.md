<!-- BEGIN_TF_DOCS -->
# waf-webacl

Manages a WAFv2 web ACL with configurable scope (CLOUDFRONT or REGIONAL), AWS
managed rule groups, an IP rate-based rule, and optional logging with
CMK-encrypted destination enforcement.

Used as the WAF layer for the telemetry-collector portal: a CLOUDFRONT-scope
ACL with AWSManagedRulesCommonRuleSet, AWSManagedRulesKnownBadInputsRuleSet,
and an IP rate-limit rule (default 2000 req/5 min per D44). The ACL ARN is
consumed by the cloudfront-distribution primitive as `web_acl_id`.

**Provider/region note:** CLOUDFRONT-scope WAF must be created in `us-east-1`.
The caller is responsible for configuring the AWS provider with `region =
"us-east-1"` when deploying CLOUDFRONT-scope ACLs (consistent with sibling
primitives such as acm-certificate).

**Per-sub-rule overrides (`rule_action_overrides`):** each `managed_rule_groups`
entry accepts an optional `rule_action_overrides` list. Every entry retargets a
SINGLE named sub-rule of the managed group to a specific action (`allow`,
`block`, `count`, `captcha`, or `challenge`) while the group-level
`override_action` continues to govern every other rule in the group. This is the
AWS-recommended way to neutralize one sub-rule without disabling the whole group
-- for example overriding only `AWSManagedRulesCommonRuleSet`'s
`SizeRestrictions_BODY` sub-rule to `count` so request bodies larger than the WAF
default body-inspection limit pass through (legitimate for large API/telemetry
payloads) while SQLi/XSS/path-traversal and the rest of the CommonRuleSet stay
enforced. The list defaults to empty (no sub-rule overrides).

**Logging note (decision D2 + docs/terragrunt-concepts.md):** when `logging_enabled = true`,
`log_kms_key_arn` must be supplied (a cross-variable validation fails at plan
time otherwise). There are two logging-destination modes:

- `create_log_group = true` (docs/terragrunt-concepts.md, the telemetry collector/portal mode):
  the module owns its OWN CloudWatch log group as the WAFv2 logging destination.
  AWS requires the log group name to start with the `aws-waf-logs-` prefix, so
  the module names it `aws-waf-logs-<name>`. The group is encrypted with
  `log_kms_key_arn` (the telemetry-data CMK, docs/terragrunt-concepts.md) and retained for
  `log_retention_in_days`. In this mode `log_destination_arns` must be empty.
- `create_log_group = false` (default): the caller supplies one or more external
  destinations via `log_destination_arns` (Kinesis Firehose, S3, or an existing
  CloudWatch log group). The `aws_wafv2_web_acl_logging_configuration` resource
  has no native KMS field; log encryption is enforced on that destination
  resource by its own configuration.

## Resources managed

- `aws_wafv2_web_acl` -- the WAFv2 web ACL with managed rule groups and
  IP rate-based rule
- `aws_cloudwatch_log_group` -- the module-owned `aws-waf-logs-*` log group
  (created only when `logging_enabled = true` AND `create_log_group = true`,
  CMK-encrypted, docs/terragrunt-concepts.md)
- `aws_wafv2_web_acl_logging_configuration` -- logging configuration
  (created only when `logging_enabled = true`)

## Usage

```hcl
module "waf" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/waf-webacl?ref=providers/aws/primitives/waf-webacl/v1.0.0"

  name           = "telemetry-portal-waf"
  scope          = "CLOUDFRONT"
  default_action = "allow"

  managed_rule_groups = [
    {
      name            = "AWSManagedRulesCommonRuleSet"
      vendor_name     = "AWS"
      priority        = 10
      override_action = "none"
    },
    {
      name            = "AWSManagedRulesKnownBadInputsRuleSet"
      vendor_name     = "AWS"
      priority        = 20
      override_action = "none"
    },
  ]

  rate_limit_per_ip = 2000

  # docs/terragrunt-concepts.md: the web ACL owns its own aws-waf-logs-* CloudWatch log group.
  logging_enabled       = true
  create_log_group      = true
  log_retention_in_days = 365
  log_kms_key_arn       = "arn:aws:kms:us-east-1:123456789012:key/my-key-id"

  tags = {
    Environment = "prod"
  }
}
```

## Examples

- `examples/basic` -- CLOUDFRONT-scope, allow default action, two AWS managed
  rule groups, rate limit 2000. Logging is disabled by default; set
  `logging_enabled = true` + `create_log_group = true` + `log_kms_key_arn` to
  exercise the module-owned `aws-waf-logs-*` log group destination (docs/terragrunt-concepts.md).

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
| [aws_cloudwatch_log_group.waf](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_log_group) | resource |
| [aws_wafv2_web_acl.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/wafv2_web_acl) | resource |
| [aws_wafv2_web_acl_logging_configuration.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/wafv2_web_acl_logging_configuration) | resource |

## Inputs

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_create_log_group"></a> [create\_log\_group](#input\_create\_log\_group) | (Optional) When true, the module creates its OWN CloudWatch log group as the WAFv2 logging destination (docs/terragrunt-concepts.md). AWS requires the log group name to start with the 'aws-waf-logs-' prefix, so the module names it 'aws-waf-logs-<name>'. The group is encrypted with log\_kms\_key\_arn (telemetry-data CMK, docs/terragrunt-concepts.md) and retained for log\_retention\_in\_days. When false, the caller must supply log\_destination\_arns. Defaults to false. | `bool` | `false` | no |
| <a name="input_default_action"></a> [default\_action](#input\_default\_action) | (Required) Default action for the WAFv2 web ACL. Valid values: allow, block. | `string` | n/a | yes |
| <a name="input_log_destination_arns"></a> [log\_destination\_arns](#input\_log\_destination\_arns) | (Optional) List of EXTERNAL CloudWatch Logs, Kinesis Data Firehose, or S3 log destination ARNs for the WAFv2 logging configuration. Used only when create\_log\_group = false. When create\_log\_group = true the module owns its CloudWatch log group and this must be left empty (the module-created group is the sole destination, docs/terragrunt-concepts.md). | `list(string)` | `[]` | no |
| <a name="input_log_kms_key_arn"></a> [log\_kms\_key\_arn](#input\_log\_kms\_key\_arn) | (Optional) ARN of the KMS key used to enforce CMK encryption on the log destination. Required when logging\_enabled is true (decision D2). Must match ^arn:aws:kms: when provided. When create\_log\_group = true, this CMK encrypts the module-owned CloudWatch log group at rest (docs/terragrunt-concepts.md telemetry-data CMK). When supplying an external log\_destination\_arns, encryption is enforced on that destination resource (e.g., Kinesis Firehose) by its own configuration. The WAFv2 resources have no native KMS field. | `string` | `null` | no |
| <a name="input_log_retention_in_days"></a> [log\_retention\_in\_days](#input\_log\_retention\_in\_days) | (Optional) Retention in days for the module-owned CloudWatch log group when create\_log\_group = true. Must be a value from the valid CloudWatch retention set. Defaults to 365 (docs/terragrunt-concepts.md). | `number` | `365` | no |
| <a name="input_logging_enabled"></a> [logging\_enabled](#input\_logging\_enabled) | (Required) Whether to attach a WAFv2 logging configuration. When true, log\_kms\_key\_arn must be supplied (decision D2), and the log destination must be available either by setting create\_log\_group = true (the module owns its own CloudWatch log group per docs/terragrunt-concepts.md) or by supplying at least one external log\_destination\_arns entry. | `bool` | n/a | yes |
| <a name="input_managed_by_tag"></a> [managed\_by\_tag](#input\_managed\_by\_tag) | (Optional) Value for the ManagedBy tag. | `string` | `"terraform"` | no |
| <a name="input_managed_rule_groups"></a> [managed\_rule\_groups](#input\_managed\_rule\_groups) | (Required) List of AWS managed rule groups to attach. Each entry specifies name, vendor\_name, priority, override\_action (none or count), and an optional rule\_action\_overrides list. The group-level override\_action governs the whole group; each rule\_action\_overrides entry instead retargets a SINGLE named sub-rule of the managed group to a specific action (allow, block, count, captcha, or challenge) WITHOUT changing the rest of the group. This is the AWS-recommended way to neutralize one noisy sub-rule while every other rule in the group stays enforced -- for example overriding only AWSManagedRulesCommonRuleSet's SizeRestrictions\_BODY sub-rule to count so request bodies larger than the WAF default body-inspection limit are not blocked, while SQLi/XSS/etc. remain in effect. Defaults to an empty list (no sub-rule overrides). | <pre>list(object({<br/>    name            = string<br/>    vendor_name     = string<br/>    priority        = number<br/>    override_action = string<br/>    rule_action_overrides = optional(list(object({<br/>      name          = string<br/>      action_to_use = string<br/>    })), [])<br/>  }))</pre> | n/a | yes |
| <a name="input_module_tag"></a> [module\_tag](#input\_module\_tag) | (Optional) Value for the Module tag identifying the source module. | `string` | `"waf-webacl"` | no |
| <a name="input_name"></a> [name](#input\_name) | (Required) Name for the WAFv2 web ACL. | `string` | n/a | yes |
| <a name="input_rate_limit_per_ip"></a> [rate\_limit\_per\_ip](#input\_rate\_limit\_per\_ip) | (Required) Rate-based rule limit: maximum requests per 5-minute window per IP. Must be a positive integer. | `number` | n/a | yes |
| <a name="input_scope"></a> [scope](#input\_scope) | (Required) Scope of the WAFv2 web ACL. Valid values: CLOUDFRONT, REGIONAL. | `string` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | (Optional) Additional tags applied to all resources created by this module. | `map(string)` | `{}` | no |

## Outputs

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_log_group_arn"></a> [log\_group\_arn](#output\_log\_group\_arn) | ARN of the module-owned CloudWatch log group used as the WAFv2 logging destination (docs/terragrunt-concepts.md). Null when create\_log\_group is false. |
| <a name="output_log_group_name"></a> [log\_group\_name](#output\_log\_group\_name) | Name of the module-owned CloudWatch log group used as the WAFv2 logging destination (docs/terragrunt-concepts.md). Null when create\_log\_group is false. |
| <a name="output_rule_action_overrides"></a> [rule\_action\_overrides](#output\_rule\_action\_overrides) | Map of managed rule group name -> { sub-rule name -> action\_to\_use } for every per-sub-rule rule\_action\_override configured on that group. Derived from the single managed\_rule\_groups input that also feeds the web ACL's managed\_rule\_group\_statement blocks, so it never drifts from the applied resource. Groups with no overrides map to an empty object. Surfaced for caller introspection and Terratest assertions (e.g. confirming AWSManagedRulesCommonRuleSet's SizeRestrictions\_BODY sub-rule is set to count). |
| <a name="output_web_acl_arn"></a> [web\_acl\_arn](#output\_web\_acl\_arn) | The Amazon Resource Name (ARN) of the WAFv2 web ACL. Consumed by CloudFront as web\_acl\_id. |
| <a name="output_web_acl_capacity"></a> [web\_acl\_capacity](#output\_web\_acl\_capacity) | The web ACL capacity units (WCU) currently being used by this web ACL. |
| <a name="output_web_acl_id"></a> [web\_acl\_id](#output\_web\_acl\_id) | The unique identifier of the WAFv2 web ACL. |
| <a name="output_web_acl_name"></a> [web\_acl\_name](#output\_web\_acl\_name) | The name of the WAFv2 web ACL. |

## Input validation

- `scope` must be one of `CLOUDFRONT` or `REGIONAL`. Plans fail for any other value.
- `default_action` must be one of `allow` or `block`. Plans fail for any other value.
- `managed_rule_groups[*].override_action` must be one of `none` or `count`. Plans fail for invalid values.
- `managed_rule_groups[*].rule_action_overrides[*].action_to_use` must be one of `allow`, `block`, `count`, `captcha`, or `challenge`. Plans fail for invalid values.
- `rate_limit_per_ip` must be a positive integer. Plans fail for zero or negative values.
- `logging_enabled = true` requires a non-null `log_kms_key_arn` (cross-variable validation; decision D2). Plans fail when `log_kms_key_arn` is null and `logging_enabled = true`.
- `logging_enabled = true` requires a destination: either `create_log_group = true` (module-owned log group) or a non-empty `log_destination_arns`. Plans fail when neither is provided.
- `create_log_group = true` and a non-empty `log_destination_arns` are mutually exclusive. Plans fail when both are set.
- `log_kms_key_arn`, when provided, must match `^arn:aws:kms:`. Plans fail for non-KMS ARNs.
- `log_retention_in_days` must be one of the valid CloudWatch retention values. Plans fail otherwise.
- `log_destination_arns` entries must be valid AWS ARNs matching `^arn:aws:`. Plans fail for invalid ARNs.

## Decision D2 -- encrypted logging

When `logging_enabled = true`, a non-null `log_kms_key_arn` is required. A
cross-variable validation in `variables.tf` enforces this at plan time:
providing `logging_enabled = true` with `log_kms_key_arn = null` fails the plan
with a clear error. `log_destination_arns` is not enforced by the cross-variable
validation so that portal callers (which compose the destination ARNs at
outer-stack level) are not blocked at module-composition time; at least one
destination entry is required by the AWS provider for a valid deployment.

The `aws_wafv2_web_acl_logging_configuration` resource has no native KMS field,
so the WAFv2 resource itself does not encrypt logs. Log encryption is enforced
on the destination resource (e.g., a Kinesis Firehose delivery stream or S3
bucket) by configuring it with the CMK identified by `log_kms_key_arn`. The
caller is responsible for configuring the destination with that key.
<!-- END_TF_DOCS -->