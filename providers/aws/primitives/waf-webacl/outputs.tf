output "web_acl_arn" {
  description = "The Amazon Resource Name (ARN) of the WAFv2 web ACL. Consumed by CloudFront as web_acl_id."
  value       = aws_wafv2_web_acl.this.arn
}

output "web_acl_id" {
  description = "The unique identifier of the WAFv2 web ACL."
  value       = aws_wafv2_web_acl.this.id
}

output "web_acl_name" {
  description = "The name of the WAFv2 web ACL."
  value       = aws_wafv2_web_acl.this.name
}

output "web_acl_capacity" {
  description = "The web ACL capacity units (WCU) currently being used by this web ACL."
  value       = aws_wafv2_web_acl.this.capacity
}

output "log_group_name" {
  description = "Name of the module-owned CloudWatch log group used as the WAFv2 logging destination (docs/terragrunt-concepts.md). Null when create_log_group is false."
  value       = var.logging_enabled && var.create_log_group ? aws_cloudwatch_log_group.waf[0].name : null
}

output "log_group_arn" {
  description = "ARN of the module-owned CloudWatch log group used as the WAFv2 logging destination (docs/terragrunt-concepts.md). Null when create_log_group is false."
  value       = var.logging_enabled && var.create_log_group ? aws_cloudwatch_log_group.waf[0].arn : null
}

output "rule_action_overrides" {
  description = "Map of managed rule group name -> { sub-rule name -> action_to_use } for every per-sub-rule rule_action_override configured on that group. Derived from the single managed_rule_groups input that also feeds the web ACL's managed_rule_group_statement blocks, so it never drifts from the applied resource. Groups with no overrides map to an empty object. Surfaced for caller introspection and Terratest assertions (e.g. confirming AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY sub-rule is set to count)."
  value = {
    for rg in var.managed_rule_groups : rg.name => {
      for o in rg.rule_action_overrides : o.name => o.action_to_use
    }
  }
}
