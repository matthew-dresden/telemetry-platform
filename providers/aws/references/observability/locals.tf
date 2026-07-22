locals {
  common_tags = merge(
    var.tags,
    {
      ManagedBy = var.managed_by_tag
      Module    = var.module_tag
    }
  )

  # Deterministic SNS topic ARN, composed from the active provider region/account and
  # var.topic_name. Built from primitive string inputs (NOT module.sns_topic outputs) so
  # the constructed policy can be passed into module.sns_topic as policy_json without a
  # create-time dependency cycle (the same module creates the topic the policy targets).
  account_id = data.aws_caller_identity.current.account_id
  # aws_region.region is the non-deprecated attribute in AWS provider 6.x (.name is deprecated).
  region        = data.aws_region.current.region
  sns_topic_arn = "arn:aws:sns:${local.region}:${local.account_id}:${var.topic_name}"

  # SNS topic access policy (D41). An aws_sns_topic_policy REPLACES the default topic
  # policy, so the owner statement MUST be retained or the account loses topic control.
  # The Cost Anomaly statement grants the costalerts service principal SNS:Publish, scoped
  # by aws:SourceAccount, which AWS Cost Anomaly Detection requires to deliver alerts to a
  # (CMK-encrypted) SNS topic. Built once here so the grant is automatic for every env that
  # composes this reference (DRY -- never pushed to leaves).
  default_sns_topic_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowOwnerAccountFullControl"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        # SNS topic (resource) policies only accept topic-scoped actions; the
        # wildcard "SNS:*" includes account-level actions and is rejected with
        # "Policy statement action out of service scope". This is the standard
        # owner action set from the AWS-managed default topic policy.
        Action = [
          "SNS:GetTopicAttributes",
          "SNS:SetTopicAttributes",
          "SNS:AddPermission",
          "SNS:RemovePermission",
          "SNS:DeleteTopic",
          "SNS:Subscribe",
          "SNS:ListSubscriptionsByTopic",
          "SNS:Publish",
        ]
        Resource = local.sns_topic_arn
      },
      {
        Sid    = "AllowCostAnomalyDetectionPublish"
        Effect = "Allow"
        Principal = {
          Service = "costalerts.amazonaws.com"
        }
        Action   = ["SNS:Publish"]
        Resource = local.sns_topic_arn
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
        }
      },
    ]
  }

  # Prefer an operator-supplied policy override when provided; otherwise apply the
  # constructed default that grants the cost-anomaly service principal publish access (D41).
  sns_topic_policy_json = var.sns_policy_json != null ? var.sns_policy_json : jsonencode(local.default_sns_topic_policy)

  # SNS alarm-topic subscribers (D41 single sink). The dedicated alarm_notification_email
  # input composes an email subscription so CloudWatch alarms and cost-anomaly alerts reach a
  # human, closing the gap where the topic had zero subscribers and every alarm fired into a
  # void. It is merged with any explicit sns_subscribers entries (e.g. SQS/Lambda/HTTPS
  # endpoints) so the dedicated email convenience and the general passthrough compose. The
  # email is placed FIRST so a single-email config keys the subscription at index 0. Duplicate
  # endpoints are removed so setting both alarm_notification_email and a matching sns_subscribers
  # entry yields exactly one subscription (no duplicate Subscribe for the same endpoint).
  alarm_email_subscribers = var.alarm_notification_email == null ? [] : [
    {
      protocol = "email"
      endpoint = var.alarm_notification_email
    }
  ]
  alarm_email_endpoints = [for s in local.alarm_email_subscribers : s.endpoint]
  sns_subscribers_effective = concat(
    local.alarm_email_subscribers,
    [for s in var.sns_subscribers : s if !contains(local.alarm_email_endpoints, s.endpoint)],
  )

  # Wire the composed SNS topic_arn into every alarm's alarm_actions and ok_actions (D41).
  # This eliminates the silent-alert path: no alarm can fire without routing to the SNS sink.
  alarms_wired = {
    for name, alarm in var.alarms : name => merge(alarm, {
      alarm_actions = [module.sns_topic.topic_arn]
      ok_actions    = [module.sns_topic.topic_arn]
    })
  }

  # Build budget notification blocks from the percentage-increment input list.
  # Each threshold is a percentage of budget_amount at which the budget alerts.
  # The budget retains its direct subscriber_email_addresses (D41 -- no SNS routing for budget).
  budget_notifications = [
    for t in var.budget_notification_thresholds : {
      comparison_operator        = t.comparison
      threshold                  = t.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = t.notification_type
      subscriber_email_addresses = var.budget_subscriber_email_addresses
      subscriber_sns_topic_arns  = []
    }
  ]

  # Cost-anomaly SNS subscriber -- always routes to the composed SNS topic_arn (D41).
  anomaly_subscribers = [
    {
      address = module.sns_topic.topic_arn
      type    = "SNS"
    }
  ]
}
