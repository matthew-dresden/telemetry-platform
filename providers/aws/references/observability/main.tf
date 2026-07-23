# Active provider identity, used by locals.tf to compose the deterministic SNS topic ARN
# (arn:aws:sns:<region>:<account_id>:<topic_name>) and scope the cost-anomaly publish grant
# by aws:SourceAccount. Derived from the provider so region/account are never hardcoded (D47).
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# SNS topic (D41) -- the single notification sink for CloudWatch alarms and
# the cost-anomaly SNS subscriber. Its topic_arn is wired into every alarm's
# alarm_actions/ok_actions by locals.tf so no alarm fires silently.
#
# policy_json carries the access policy composed in locals.tf: an owner-account
# statement (an aws_sns_topic_policy REPLACES the default policy) plus a grant of
# SNS:Publish to costalerts.amazonaws.com so AWS Cost Anomaly Detection can publish
# to this CMK-encrypted topic (D41). var.sns_policy_json overrides the default when set.
# The policy references only the deterministic ARN string (not module.sns_topic outputs),
# so there is no create-time dependency cycle.
# ---------------------------------------------------------------------------
module "sns_topic" {
  source = var.sns_topic_source

  topic_name = var.topic_name
  kms_key_id = var.sns_kms_key_id
  # Composed in locals.tf: the dedicated alarm_notification_email email subscription merged
  # (deduped by endpoint) with any explicit sns_subscribers, so the alarm topic always has a
  # human delivery path when an alarm email is configured (D41 -- no subscriber-less topic).
  subscribers = local.sns_subscribers_effective
  policy_json = local.sns_topic_policy_json

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "sns-topic"
}

# ---------------------------------------------------------------------------
# CloudWatch (alarms + dashboard + log groups).
# locals.tf wires the composed SNS topic_arn into every alarm's
# alarm_actions and ok_actions (D41 -- sole SNS sink, no silent alert path).
# ---------------------------------------------------------------------------
module "cloudwatch" {
  source = var.cloudwatch_source

  alarms           = local.alarms_wired
  log_groups       = var.log_groups
  create_dashboard = var.create_dashboard
  dashboard_name   = var.dashboard_name
  dashboard_body   = var.dashboard_body

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "cloudwatch"

  depends_on = [module.sns_topic]
}

# ---------------------------------------------------------------------------
# Budget (REUSED upstream primitive from terraform-modules v1.1.0).
# The budget keeps its direct subscriber_email_addresses (D41 -- budget does
# NOT route through the SNS topic; only alarms and cost-anomaly use the SNS sink).
# budget_notification_thresholds are percentage increments of budget_amount
# (docs/terragrunt-concepts.md).
# ---------------------------------------------------------------------------
module "budget" {
  source = "git::https://github.com/matthew-dresden/terraform-modules.git//providers/aws/primitives/budget?ref=providers/aws/primitives/budget/v1.1.0"

  budgets = {
    observability = {
      name         = var.dashboard_name
      budget_type  = "COST"
      limit_amount = var.budget_amount
      time_unit    = "MONTHLY"
      notification = local.budget_notifications
    }
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Cost anomaly detection (D23) -- wired to the composed SNS topic_arn (D41)
# via locals.tf. This is the SOLE cost-anomaly resource; no other reference
# composes aws_ce_anomaly_monitor or aws_ce_anomaly_subscription.
# ---------------------------------------------------------------------------
module "cost_anomaly" {
  source = var.cost_anomaly_source

  monitor_name           = var.anomaly_monitor_name
  monitor_type           = var.anomaly_monitor_type
  subscription_name      = var.anomaly_subscription_name
  subscription_frequency = var.anomaly_subscription_frequency
  threshold_expression   = var.anomaly_threshold_expression
  subscribers            = local.anomaly_subscribers

  tags           = local.common_tags
  managed_by_tag = var.managed_by_tag
  module_tag     = "cost-anomaly"

  depends_on = [module.sns_topic]
}
