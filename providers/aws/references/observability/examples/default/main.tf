data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); the last
  # 6 chars are its random, collision-free tail. Appending it to the base name lets
  # concurrent CI runs of THIS module apply in the SAME qa account without colliding
  # on a fixed name. Every account/region-unique resource here (SNS topic, CloudWatch
  # log group, the two KMS aliases, the dashboard, and the cost-anomaly
  # monitor/subscription) derives from local.name, so suffixing local.name cascades
  # to all of them. The offline tfvars value "offline-validate" keeps the suffix
  # statically resolvable, so trivy still resolves names. substr/length are pure
  # plan-time functions.
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)
  name       = "${var.name}-${local.run_suffix}"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  # SNS topic KMS key policy -- root admin access + CloudWatch publish permission.
  # All principals are input-driven ARNs (no wildcard principal per security rules).
  sns_kms_policy = jsonencode({
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
        Sid    = "CloudWatchPublish"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.account_id
          }
        }
      },
    ]
  })

  # CloudWatch log group KMS key policy -- root admin + CloudWatch log group encryption.
  log_kms_policy = jsonencode({
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
        Sid    = "CloudWatchLogsEncrypt"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:*"
          }
        }
      },
    ]
  })

  # Anomaly detection threshold expression (DIMENSIONAL, all services, GREATER_THAN impact).
  anomaly_threshold_expression = jsonencode({
    Dimensions = {
      Key          = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      Values       = ["100"]
      MatchOptions = ["GREATER_THAN_OR_EQUAL"]
    }
  })
}

# SNS topic KMS CMK -- dedicated key for the SNS topic SSE.
resource "aws_kms_key" "sns" {
  description             = "KMS key for SNS topic SSE in observability fixture"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.sns_kms_policy

  tags = local.tags
}

resource "aws_kms_alias" "sns" {
  name          = "alias/${local.name}-sns"
  target_key_id = aws_kms_key.sns.key_id
}

# CloudWatch log group KMS CMK -- dedicated key for log group encryption.
resource "aws_kms_key" "log" {
  description             = "KMS key for CloudWatch log group encryption in observability fixture"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.log_kms_policy

  tags = local.tags
}

resource "aws_kms_alias" "log" {
  name          = "alias/${local.name}-log"
  target_key_id = aws_kms_key.log.key_id
}

# Observability reference -- the module under test.
# Exercises all four composed primitives: sns-topic, cloudwatch, budget, cost-anomaly.
module "example" {
  source = "../../"

  # SNS topic (D41 single notification sink). alarm_notification_email composes the
  # email subscription that gives the alarm topic a human delivery path (the OBS-3
  # no-subscriber gap). The module merges it (deduped) with any sns_subscribers.
  topic_name               = "${local.name}-alerts"
  sns_kms_key_id           = aws_kms_key.sns.arn
  alarm_notification_email = var.budget_subscriber_email_addresses[0]

  # CloudWatch alarms -- alarm_actions/ok_actions are set internally to the SNS topic_arn (D41).
  # high-latency-p95 uses a percentile extended_statistic to exercise the percentile SLO
  # pass-through through the reference -> cloudwatch primitive (statistic must be null when
  # extended_statistic is set; the module requires EXACTLY ONE of the two).
  alarms = {
    "high-latency-p95-${local.run_suffix}" = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 3
      metric_name         = "TargetResponseTime"
      namespace           = "AWS/ApplicationELB"
      period              = 300
      extended_statistic  = "p95"
      threshold           = 2
      alarm_description   = "ALB p95 target response time exceeds 2s for three consecutive periods"
      dimensions          = {}
      treat_missing_data  = "notBreaching"
    }
    "high-error-rate-${local.run_suffix}" = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 1
      metric_name         = "HTTPCode_Target_5XX_Count"
      namespace           = "AWS/ApplicationELB"
      period              = 60
      statistic           = "Sum"
      threshold           = 10
      alarm_description   = "ALB 5xx error count > 10 in one period"
      dimensions          = {}
      # missing == healthy: no 5xx datapoints means no errors, not an outage.
      treat_missing_data = "notBreaching"
    }
    "task-down-${local.run_suffix}" = {
      comparison_operator = "LessThanThreshold"
      evaluation_periods  = 3
      metric_name         = "RunningTaskCount"
      namespace           = "AWS/ECS"
      period              = 60
      statistic           = "Average"
      threshold           = 1
      alarm_description   = "ECS running task count below desired -- service is down"
      dimensions          = {}
      # breaching: a stopped service stops emitting RunningTaskCount, so absent data
      # MUST alarm rather than sit silently in INSUFFICIENT_DATA (OBS-4a, task-down path).
      treat_missing_data = "breaching"
    }
  }

  # CloudWatch log groups
  log_groups = {
    platform = {
      name              = "/telemetry/${local.name}/platform"
      retention_in_days = 30
      kms_key_id        = aws_kms_key.log.arn
    }
  }

  # CloudWatch dashboard
  create_dashboard = true
  dashboard_name   = "${local.name}-observability"
  dashboard_body = jsonencode({
    widgets = []
  })

  # Budget with direct email subscribers (D41 -- budget does NOT use SNS)
  budget_amount                     = var.budget_amount
  budget_subscriber_email_addresses = var.budget_subscriber_email_addresses
  budget_notification_thresholds    = var.budget_notification_thresholds

  # Cost anomaly detection (D23) -- wired to SNS topic internally
  anomaly_monitor_name           = "${local.name}-monitor"
  anomaly_monitor_type           = "DIMENSIONAL"
  anomaly_subscription_name      = "${local.name}-subscription"
  anomaly_subscription_frequency = "IMMEDIATE"
  anomaly_threshold_expression   = local.anomaly_threshold_expression

  tags = local.tags

  depends_on = [aws_kms_key.sns, aws_kms_key.log]
}

# ---------------------------------------------------------------------------
# Outputs re-exported from the observability reference.
# sns_topic_arn is consumed here to prove the re-export is not dangling (AC-3).
# ---------------------------------------------------------------------------

output "sns_topic_arn" {
  description = "The SNS notification topic ARN from the observability reference (D41)."
  value       = module.example.sns_topic_arn
}

output "dashboard_name" {
  description = "The CloudWatch dashboard name from the observability reference."
  value       = module.example.dashboard_name
}

# Re-exports the configured alarm notification email so Terratest can assert the composed
# SNS email subscription endpoint matches the input (OBS-3 human-delivery-path coverage).
output "alarm_notification_email" {
  description = "The email address the observability reference subscribes to the SNS alarm topic (D41 human delivery path)."
  value       = var.budget_subscriber_email_addresses[0]
}
