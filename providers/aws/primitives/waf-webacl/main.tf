# WAFv2 Web ACL (CLOUDFRONT or REGIONAL scope).
# CLOUDFRONT-scope WAF must be created in us-east-1; the provider/region
# is the caller's responsibility (consistent with sibling primitives).
resource "aws_wafv2_web_acl" "this" {
  name  = var.name
  scope = var.scope

  dynamic "default_action" {
    for_each = var.default_action == "allow" ? [1] : []
    content {
      allow {}
    }
  }

  dynamic "default_action" {
    for_each = var.default_action == "block" ? [1] : []
    content {
      block {}
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = true
  }

  # AWS managed rule groups (one rule block per entry in managed_rule_groups).
  dynamic "rule" {
    for_each = var.managed_rule_groups
    content {
      name     = rule.value.name
      priority = rule.value.priority

      dynamic "override_action" {
        for_each = rule.value.override_action == "none" ? [1] : []
        content {
          none {}
        }
      }

      dynamic "override_action" {
        for_each = rule.value.override_action == "count" ? [1] : []
        content {
          count {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = rule.value.name
          vendor_name = rule.value.vendor_name

          # Per-sub-rule action overrides. Each entry retargets one named rule
          # inside the managed group (e.g. AWSManagedRulesCommonRuleSet's
          # SizeRestrictions_BODY) to a specific action while the group-level
          # override_action continues to govern every other rule. action_to_use
          # is validated to one of allow/block/count/captcha/challenge in
          # variables.tf, so exactly one of the nested action blocks renders.
          dynamic "rule_action_override" {
            for_each = rule.value.rule_action_overrides
            content {
              name = rule_action_override.value.name

              action_to_use {
                dynamic "allow" {
                  for_each = rule_action_override.value.action_to_use == "allow" ? [1] : []
                  content {}
                }
                dynamic "block" {
                  for_each = rule_action_override.value.action_to_use == "block" ? [1] : []
                  content {}
                }
                dynamic "count" {
                  for_each = rule_action_override.value.action_to_use == "count" ? [1] : []
                  content {}
                }
                dynamic "captcha" {
                  for_each = rule_action_override.value.action_to_use == "captcha" ? [1] : []
                  content {}
                }
                dynamic "challenge" {
                  for_each = rule_action_override.value.action_to_use == "challenge" ? [1] : []
                  content {}
                }
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = rule.value.name
        sampled_requests_enabled   = true
      }
    }
  }

  # IP rate-based rule (requests per 5-minute window per IP).
  rule {
    name     = "RateLimitPerIP"
    priority = 100

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit_per_ip
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimitPerIP"
      sampled_requests_enabled   = true
    }
  }

  tags = local.common_tags
}

# Module-owned CloudWatch log group used as the WAFv2 logging destination when
# create_log_group = true (docs/terragrunt-concepts.md). AWS requires the log group name to start
# with the 'aws-waf-logs-' prefix; the module enforces that prefix so callers do
# not have to. The group is encrypted with the telemetry-data CMK (log_kms_key_arn,
# docs/terragrunt-concepts.md) and retained for log_retention_in_days.
resource "aws_cloudwatch_log_group" "waf" {
  count = var.logging_enabled && var.create_log_group ? 1 : 0

  name              = "aws-waf-logs-${var.name}"
  retention_in_days = var.log_retention_in_days
  kms_key_id        = var.log_kms_key_arn

  tags = local.common_tags
}

# Optional WAFv2 logging configuration (created when logging_enabled = true).
# Decision D2: log_kms_key_arn is required when logging is enabled; enforcement
# is a cross-variable validation in variables.tf.
#
# Logging destination (docs/terragrunt-concepts.md): when create_log_group = true the module-owned
# CloudWatch log group is the sole destination (its ARN without the trailing ':*'
# wildcard, as WAFv2 requires); otherwise the externally-supplied
# log_destination_arns are used. The aws_wafv2_web_acl_logging_configuration
# resource has no native KMS field; CMK encryption of log data is enforced on the
# destination resource itself (the module-owned log group above, or an external
# destination such as a Kinesis Firehose delivery stream configured with the CMK).
resource "aws_wafv2_web_acl_logging_configuration" "this" {
  count = var.logging_enabled ? 1 : 0

  resource_arn = aws_wafv2_web_acl.this.arn
  log_destination_configs = var.create_log_group ? [
    trimsuffix(aws_cloudwatch_log_group.waf[0].arn, ":*")
  ] : var.log_destination_arns

  logging_filter {
    default_behavior = "KEEP"

    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"
      condition {
        action_condition {
          action = "BLOCK"
        }
      }
    }
  }
}
