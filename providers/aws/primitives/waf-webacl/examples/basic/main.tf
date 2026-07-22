data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # A caller-supplied CMK ARN is honoured only when it is a real, non-empty
  # value. The terratest harness sources log_kms_key_arn from the optional
  # WAF_LOG_KMS_KEY_ARN environment variable, which resolves to "" when unset;
  # an empty string is not a usable KMS ARN, so it is treated as absent.
  supplied_kms_key_arn = (
    var.log_kms_key_arn != null && var.log_kms_key_arn != "" ? var.log_kms_key_arn : null
  )

  # When the example must own a CloudWatch log group (create_log_group = true)
  # and no usable CMK ARN was supplied, the example provisions its own CMK so a
  # valid configuration can be applied end to end without external inputs. This
  # mirrors the kinesis-firehose example's self-contained KMS fixture.
  create_kms_fixture = (
    var.logging_enabled && var.create_log_group && local.supplied_kms_key_arn == null
  )

  effective_log_kms_key_arn = (
    local.supplied_kms_key_arn != null ? local.supplied_kms_key_arn : (
      local.create_kms_fixture ? aws_kms_key.logs[0].arn : null
    )
  )

  # KMS key policy granting root admin plus CloudWatch Logs service-principal
  # use of the CMK for the module-owned aws-waf-logs-* log group in this region.
  kms_key_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAdminAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = ["kms:*"]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsAccess"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:aws-waf-logs-${var.name}"
          }
        }
      },
    ]
  })
}

# Customer-managed KMS key fixture for encrypting the module-owned WAF log group
# at rest. Created only when the example owns its log group and the caller did
# not supply a usable CMK ARN.
resource "aws_kms_key" "logs" {
  count = local.create_kms_fixture ? 1 : 0

  description             = "CMK for ${var.name} WAF log group"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.kms_key_policy

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "waf-webacl-module-basic-example"
    Owner       = "terraform"
  })
}

resource "aws_kms_alias" "logs" {
  count = local.create_kms_fixture ? 1 : 0

  name          = "alias/aws-waf-logs-${var.name}"
  target_key_id = aws_kms_key.logs[0].key_id
}

module "example" {
  source = "../../"

  name           = var.name
  scope          = var.scope
  default_action = var.default_action

  managed_rule_groups = [
    {
      name            = "AWSManagedRulesCommonRuleSet"
      vendor_name     = "AWS"
      priority        = 10
      override_action = "none"
      # Exercise the per-sub-rule override path: neutralize only the
      # SizeRestrictions_BODY sub-rule to count while the rest of the
      # CommonRuleSet stays enforced (override_action stays none).
      rule_action_overrides = [
        {
          name          = "SizeRestrictions_BODY"
          action_to_use = "count"
        }
      ]
    },
    {
      name            = "AWSManagedRulesKnownBadInputsRuleSet"
      vendor_name     = "AWS"
      priority        = 20
      override_action = "none"
    },
  ]

  rate_limit_per_ip     = var.rate_limit_per_ip
  logging_enabled       = var.logging_enabled
  create_log_group      = var.create_log_group
  log_retention_in_days = var.log_retention_in_days
  log_destination_arns  = var.log_destination_arns
  log_kms_key_arn       = local.effective_log_kms_key_arn

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "waf-webacl-module-basic-example"
    Owner       = "terraform"
  })
}

output "web_acl_arn" {
  description = "The ARN of the WAFv2 web ACL."
  value       = module.example.web_acl_arn
}

output "web_acl_id" {
  description = "The unique identifier of the WAFv2 web ACL."
  value       = module.example.web_acl_id
}

output "web_acl_name" {
  description = "The name of the WAFv2 web ACL."
  value       = module.example.web_acl_name
}

output "log_group_name" {
  description = "Name of the module-owned CloudWatch log group (null unless create_log_group is true)."
  value       = module.example.log_group_name
}

output "log_group_arn" {
  description = "ARN of the module-owned CloudWatch log group (null unless create_log_group is true)."
  value       = module.example.log_group_arn
}

output "rule_action_overrides" {
  description = "Map of managed rule group name -> { sub-rule -> action } for the configured per-sub-rule overrides. Exposed so the terratest can assert the CommonRuleSet SizeRestrictions_BODY sub-rule is set to count."
  value       = module.example.rule_action_overrides
}
