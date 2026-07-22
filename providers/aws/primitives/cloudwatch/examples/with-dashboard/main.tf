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

  # Run-id-scoped uniqueness suffix (shared parallel-isolation idiom, D-2/D-4).
  # terratest injects a fresh per-run terratest_run_id (tt-<utc>-<rand>); the last
  # 6 chars are its random, collision-free tail. The inline SNS topic and KMS alias
  # below already embed ${var.terratest_run_id} directly; this local scopes the
  # remaining FIXED account/region-unique names this fixture creates -- the
  # CloudWatch dashboard and the two CloudWatch log groups (/telemetry/adot-collector,
  # /telemetry/alb-access) -- so concurrent CI runs of THIS module apply in the SAME
  # qa account without colliding. The metric references inside the dashboard widgets
  # and alarm dimensions (ClusterName=telemetry-collector, ServiceName=adot-collector)
  # point at an external ECS cluster/service this fixture does NOT create, so they are
  # left unchanged. The offline tfvars value "offline-validate" keeps the suffix
  # statically resolvable, so trivy still resolves names. substr/length are pure
  # plan-time functions.
  run_suffix = substr(var.terratest_run_id, length(var.terratest_run_id) - 6, 6)

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
}

# Inline SNS topic used as the alarm action sink for this fixture.
# No external TF_VAR_sns_topic_arn is required. Encrypted at rest with the
# self-contained customer-managed KMS key (models secure usage; clears AWS-0095).
resource "aws_sns_topic" "alarm_sink" {
  name              = "cw-dashboard-alarm-sink-${var.terratest_run_id}"
  kms_master_key_id = aws_kms_key.log_encryption.arn

  tags = var.tags
}

# Inline KMS key used to encrypt CloudWatch log groups for this fixture.
# No external TF_VAR_kms_key_arn is required.
resource "aws_kms_key" "log_encryption" {
  description             = "KMS key for CloudWatch log group encryption in with-dashboard fixture"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.cw_kms_policy

  tags = var.tags
}

resource "aws_kms_alias" "log_encryption" {
  name          = "alias/cw-dashboard-log-${var.terratest_run_id}"
  target_key_id = aws_kms_key.log_encryption.key_id
}

module "example" {
  source = "../../"

  alarms = {
    "high_cpu-${local.run_suffix}" = {
      comparison_operator = "GreaterThanThreshold"
      evaluation_periods  = 2
      metric_name         = "CPUUtilization"
      namespace           = "AWS/ECS"
      period              = 60
      statistic           = "Average"
      threshold           = 80
      alarm_description   = "CPU utilization exceeds 80 percent"
      dimensions = {
        ClusterName = "telemetry-collector"
        ServiceName = "adot-collector"
      }
      alarm_actions = [aws_sns_topic.alarm_sink.arn]
      ok_actions    = [aws_sns_topic.alarm_sink.arn]
    }
    "high_memory-${local.run_suffix}" = {
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
      alarm_actions = [aws_sns_topic.alarm_sink.arn]
      ok_actions    = [aws_sns_topic.alarm_sink.arn]
    }
  }

  log_groups = {
    adot_collector = {
      name              = "/telemetry/adot-collector-${local.run_suffix}"
      retention_in_days = 365
      kms_key_id        = aws_kms_key.log_encryption.arn
    }
    alb_access = {
      name              = "/telemetry/alb-access-${local.run_suffix}"
      retention_in_days = 365
      kms_key_id        = aws_kms_key.log_encryption.arn
    }
  }

  create_dashboard = true
  dashboard_name   = "${var.dashboard_name}-${local.run_suffix}"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", "telemetry-collector", "ServiceName", "adot-collector"]
          ]
          period = 60
          stat   = "Average"
          region = var.aws_region
          title  = "ADOT Collector CPU Utilization"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "MemoryUtilization", "ClusterName", "telemetry-collector", "ServiceName", "adot-collector"]
          ]
          period = 60
          stat   = "Average"
          region = var.aws_region
          title  = "ADOT Collector Memory Utilization"
        }
      }
    ]
  })

  tags = var.tags
}
