terraform {
  required_version = ">= 1.15.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.49.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project         = var.project_tag
      "terratest-run" = var.terratest_run_id
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # KMS key policy granting root admin access, CloudWatch Logs encryption access,
  # and SNS service access (so the same CMK encrypts the alarm-sink SNS topic at rest).
  cw_kms_policy = jsonencode({
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
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
      {
        Sid    = "SNSServiceAccess"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchAlarmsPublish"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
    ]
  })

  # When override_alarm_actions is true, use empty lists to trigger the D41 validation error.
  alarm_actions = var.override_alarm_actions ? [] : [aws_sns_topic.alarm_sink.arn]
  ok_actions    = var.override_alarm_actions ? [] : [aws_sns_topic.alarm_sink.arn]

  # When override_retention_days is set, use that value to trigger validation error.
  adot_retention = var.override_retention_days != null ? var.override_retention_days : 365
  alb_retention  = var.override_retention_days != null ? var.override_retention_days : 365

  # Statistic selection for the high_cpu alarm.
  # - default: base statistic "Average", no extended_statistic.
  # - override_use_extended_statistic: percentile p99 via extended_statistic, statistic null
  #   (the module requires EXACTLY ONE of statistic / extended_statistic).
  # - override_both_statistics: sets BOTH to trigger the mutual-exclusion validation error.
  high_cpu_statistic = var.override_both_statistics ? "Average" : (
    var.override_use_extended_statistic ? null : "Average"
  )
  high_cpu_extended_statistic = var.override_both_statistics ? "p99" : (
    var.override_use_extended_statistic ? "p99" : null
  )
}

# Inline SNS topic used as the alarm action sink for this fixture.
# No external TF_VAR_sns_topic_arn is required. Encrypted at rest with the
# self-contained customer-managed KMS key (models secure usage; clears AWS-0095).
resource "aws_sns_topic" "alarm_sink" {
  name              = "cw-basic-alarm-sink-${var.terratest_run_id}"
  kms_master_key_id = aws_kms_key.log_encryption.arn

  tags = var.tags
}

# Inline KMS key used to encrypt CloudWatch log groups for this fixture.
# No external TF_VAR_kms_key_arn is required.
resource "aws_kms_key" "log_encryption" {
  description             = "KMS key for CloudWatch log group encryption in basic fixture"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.cw_kms_policy

  tags = var.tags
}

resource "aws_kms_alias" "log_encryption" {
  name          = "alias/cw-basic-log-${var.terratest_run_id}"
  target_key_id = aws_kms_key.log_encryption.key_id
}

module "example" {
  source = "../../"

  alarms = {
    high_cpu = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 2
      metric_name         = "CPUUtilization"
      namespace           = "AWS/ECS"
      period              = 60
      statistic           = local.high_cpu_statistic
      extended_statistic  = local.high_cpu_extended_statistic
      threshold           = 80
      alarm_description   = "CPU utilization exceeds 80 percent"
      dimensions = {
        ClusterName = "telemetry-collector"
        ServiceName = "adot-collector"
      }
      alarm_actions = local.alarm_actions
      ok_actions    = local.ok_actions
    }
    high_memory = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 2
      metric_name         = "MemoryUtilization"
      namespace           = "AWS/ECS"
      period              = 60
      statistic           = "Average"
      threshold           = 85
      alarm_description   = "Memory utilization exceeds 85 percent"
      dimensions = {
        ClusterName = "telemetry-collector"
        ServiceName = "adot-collector"
      }
      alarm_actions = local.alarm_actions
      ok_actions    = local.ok_actions
    }
  }

  log_groups = {
    adot_collector = {
      name              = "/telemetry/adot-collector"
      retention_in_days = local.adot_retention
      kms_key_id        = aws_kms_key.log_encryption.arn
    }
    alb_access = {
      name              = "/telemetry/alb-access"
      retention_in_days = local.alb_retention
      kms_key_id        = aws_kms_key.log_encryption.arn
    }
  }

  create_dashboard = false

  tags = var.tags
}
