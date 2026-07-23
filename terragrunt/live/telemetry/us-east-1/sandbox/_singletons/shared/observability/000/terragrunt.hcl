# live/telemetry/us-east-1/<env>/000/observability/000/terragrunt.hcl
#
# Service account observability unit.
# Provisions the central alerting sink for the usage-analytics stack: a KMS-encrypted
# SNS topic, cost-anomaly subscriber, AWS Budgets, and a CloudWatch dashboard via the
# references/observability reference module (AC-3, E1-F7-S2-T1). Sources exactly ONE
# module per AC-3: no primitive is sourced directly from this leaf.
#
# ACCOUNT GUARD (D2/D4):
# The root terragrunt.hcl generates the AWS provider with
#   allowed_account_ids = ["${local.aws_account_id}"]
# where aws_account_id is derived from the directory basename via account.hcl (D2).
# The account id is resolved from account.hcl. A wrong-profile apply will fail fast because
# the active caller identity will not match the allowed account (D4).
#
# OWNERSHIP BOUNDARY (D41, iac/04 section 3.8):
# This unit owns ONLY: KMS-encrypted SNS topic, topic subscriptions, cost-anomaly
# subscriber, and AWS Budgets. CloudWatch alarms owned by other units (collector-ingestion,
# data-lake, athena) are NOT created here. This unit subscribes those alarms'
# alarm_actions/ok_actions to the SNS topic_arn it owns via the alarms input, which
# carries the alarm names, metric/namespace, and dimension values from upstream outputs.
# Creating alarms or budgets owned by other units here is an ownership violation (D41).
#
# DEPENDENCY WIRING (D37/AC-3):
# - dependency.collector_ingestion: consumes ecs_cluster_name, ecs_service_name,
#   alb_arn_suffix, waf_web_acl_id, and firehose_stream_name from the collector-ingestion
#   unit so alarm dimensions are input-driven (never recomputed, D37).
# - dependency.data_lake: consumes firehose_stream_name for the Firehose alarm dimension
#   and cwl_transform_lambda_name for the cwl_split transform Lambda's FunctionName
#   dimension (load-test observability alarms 9-12).
# - dependency.athena: consumes workgroup_name for the Athena alarm dimension.
#
# SINGLE MODULE CONTRACT (AC-3):
# This leaf sources ONLY references/observability. No primitive is sourced directly.
# Grep the rendered config for a single source = to verify at refactor time.
#
# SNS TOPIC ENCRYPTION (AC-12):
# The SNS topic is encrypted with the KMS CMK alias (alias/telemetry-config) owned
# by the dns-prod-zone unit (iac/01 section 5.8). The key ARN is sourced from
# service.hcl -- never an inline literal in this leaf (AC-13, D8).
#
# ALARM WIRING (AC-12):
# Every alarm in the alarms input has alarm_actions and ok_actions populated with
# the observability SNS topic_arn (wired internally by references/observability).
# No named alarm fires to no one after apply.
#
# STATE PREREQUISITE (D40):
# Run tf-state-preflight before plan/apply to confirm the sandbox state backend
# exists in the resolved service account. The root remote_state block will fail closed
# if the bucket does not exist.
#
# Applied with AWS_PROFILE=sandbox credentials.
# spec 02 Section 4.2, ledger D2, D4, D8, D31, D37, D40, D41, D45, D47, AC-3,
# AC-12, AC-13, iac/04 section 3.8.

include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

include "envcommon" {
  path           = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/observability.hcl"
  expose         = true
  merge_strategy = "deep"
}

terraform {
  # Toggle-driven source (spec Section 4.3, AC-7, AC-8):
  # use_pinned_module_sources=false (sandbox): in-repo local source via get_repo_root().
  # use_pinned_module_sources=true (prod): pinned immutable git URL with ?ref= tag.
  source = local.account_vars.locals.use_pinned_module_sources ? "${include.root.locals.module_git_base}//providers/aws/references/observability?ref=providers/aws/references/observability/v1.0.2" : "${get_repo_root()}//providers/aws/references/observability"
}

# ---------------------------------------------------------------------------
# dependencies: upstream unit outputs consumed as inputs (D37/AC-3)
#
# mock_outputs_allowed_terraform_commands restricts mocks to plan and validate
# only -- apply and destroy always use real outputs (fail closed on apply, D31).
# ---------------------------------------------------------------------------

dependency "collector_ingestion" {
  config_path = "../../../../${local.env_active}/collector-ingestion/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before collector-ingestion is applied.
  # Dimensions: ECS cluster/service, ALB arn-suffix, WAF web-acl id, Firehose stream.
  # Resource names derived from local.ns_prefix and local.svc_instance so a copied
  # leaf presents mocks for its own scope rather than sandbox literals (AC-13).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    ecs_cluster_name     = "mock-${local.ns_prefix}-collector-ingestion-${local.svc_instance}-cluster"
    ecs_service_name     = "mock-${local.ns_prefix}-collector-ingestion-${local.svc_instance}-adot"
    alb_arn_suffix       = "app/mock-collector-alb/0123456789abcdef"
    waf_web_acl_id       = "mock-collector-waf-web-acl-id"
    firehose_stream_name = "mock-${local.ns_prefix}-collector-ingestion-${local.svc_instance}-telemetry-events"
  }
}

dependency "data_lake" {
  config_path = "../../data-lake/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before data-lake is applied.
  # Resource name derived from local.ns_prefix and local.svc_instance (AC-13).
  # cwl_transform_lambda_name: the FunctionName dimension for the cwl_split transform
  # Lambda's AWS/Lambda alarms (D37) -- sourced from data-lake's cwl_transform_lambda_name
  # output (mirrors the firehose_stream_name wiring immediately below), never recomputed
  # or namespace-derived independently here.
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    firehose_stream_name      = "mock-${local.ns_prefix}-data-lake-${local.svc_instance}-telemetry-events"
    cwl_transform_lambda_name = "mock-${local.ns_prefix}-data-lake-${local.svc_instance}-cwl-split"
  }
}

dependency "athena" {
  config_path = "../../athena/${local.svc_instance}"

  # mock_outputs allow plan/validate to run before athena is applied.
  # Resource name derived from local.ns_prefix and local.svc_instance (AC-13).
  mock_outputs_allowed_terraform_commands = ["plan", "validate"]
  mock_outputs = {
    workgroup_name = "mock-${local.ns_prefix}-athena-${local.svc_instance}"
  }
}

# The WAF-blocked-requests alarm dimension uses collector-ingestion's waf_web_acl_id;
# observability declares no dependency beyond collector-ingestion, data-lake, and athena.

# ---------------------------------------------------------------------------
# locals: account identity, namespace, region, service-layer inputs (D31, D37, D45, D47)
# ---------------------------------------------------------------------------

locals {
  env_active = read_terragrunt_config("${get_terragrunt_dir()}/../../../../active.hcl").locals.active
  # Instance-relative local: resolves to the basename of this service-instance directory
  # (e.g. "000") so that every same-tier sibling dependency config_path interpolates
  # the owning instance index rather than a hardcoded literal (spec section 4.4,
  # AC-FUNC-001, AC-FUNC-002). Terragrunt evaluates locals before dependency blocks,
  # so local.svc_instance is valid inside config_path expressions (spec section 4.4).
  # Copying this folder to index "001" causes svc_instance to resolve to "001",
  # wiring all sibling deps at the new index with zero edits (spec section G5, AC-FUNC-003).
  svc_instance = basename(get_terragrunt_dir())

  # Account-level locals -- used for ARN construction (D2/D45).
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  # Region from root -- used for ARN construction (D47).
  region = include.root.locals.region

  # Namespace from root -- used to derive resource names (D37/D45).
  namespace = include.root.locals.namespace

  # ---------------------------------------------------------------------------
  # Namespace prefix for dependency mock resource name construction (AC-13, D31).
  # Composed from the product, region (cleaned), environment, and environment
  # instance segments -- identical across all services in this scope. Each
  # dependency's mock resource name appends its own service label and svc_instance
  # so mock values are scope-derived rather than sandbox literals.
  # Example sandbox value: "telemetry-useast1-sandbox-000"
  # ---------------------------------------------------------------------------
  ns_prefix = join("-", [
    include.root.locals.product_family,
    include.root.locals.region_clean,
    include.root.locals.environment_name,
    include.root.locals.environment_instance,
  ])

  # Service-layer locals from service.hcl -- all observability contract inputs (D8).
  # Declared in service.hcl so no inline literal appears in this leaf (AC-13, D8).
  service_vars = read_terragrunt_config(find_in_parent_folders("service.hcl"))

  sns_kms_key_id                    = local.service_vars.locals.sns_kms_key_id
  cost_anomaly_threshold_expression = local.service_vars.locals.cost_anomaly_threshold_expression
  budget_amount                     = local.service_vars.locals.budget_amount
  budget_notification_thresholds    = local.service_vars.locals.budget_notification_thresholds
  budget_subscriber_email_addresses = local.service_vars.locals.budget_subscriber_email_addresses
  sns_subscribers                   = local.service_vars.locals.sns_subscribers
  alarm_configs                     = local.service_vars.locals.alarm_configs
}

# ---------------------------------------------------------------------------
# inputs: observability reference module (AC-3, D37, D41, D45, D47)
#
# Naming inputs (topic_name, dashboard_name, anomaly_monitor_name,
# anomaly_subscription_name, anomaly_subscription_frequency) are merged from
# _envcommon/observability.hcl (via the "envcommon" include above). Leaf-level
# inputs here extend the shared template with sandbox-specific upstream values.
#
# SINGLE SINK CONTRACT (D41, AC-12):
# The references/observability module wires topic_arn into every alarm's
# alarm_actions and ok_actions internally. This leaf supplies the alarms list
# with all fifteen named alarms (metric/namespace/dimensions from dependency outputs)
# so the module ensures no alarm fires to no one.
#
# BUDGET WIRING (iac/04 section 3.8, AC-12):
# budget_amount + budget_notification_thresholds + budget_subscriber_email_addresses
# all sourced from service.hcl -- never inline literals in this leaf (AC-13, D8).
#
# DASHBOARD TOGGLE (spec section 4.5, AC-FUNC-002):
# create_dashboard is a deployment-unique value sourced from terraform.tfvars.
# Terraform auto-loads terraform.tfvars after Terragrunt copies the unit dir (spec 1.1).
#
# The tags input uses include.root.locals.common_tags (not local.common_tags)
# because the root include block uses expose = true (spec 02).
# ---------------------------------------------------------------------------

inputs = merge({
  # SNS topic KMS encryption: alias ARN from service.hcl (iac/04 section 3.8, D8).
  # Never an inline literal in this leaf (AC-13).
  sns_kms_key_id = local.sns_kms_key_id

  # Cost-anomaly alert threshold (iac/04 section 3.8, D8).
  # Input-driven from service.hcl -- never an inline literal (AC-13).
  anomaly_threshold_expression = local.cost_anomaly_threshold_expression

  # Budget inputs (iac/04 section 3.8, AC-12, D8).
  # All sourced from service.hcl -- never inline literals in this leaf (AC-13).
  budget_amount                     = local.budget_amount
  budget_notification_thresholds    = local.budget_notification_thresholds
  budget_subscriber_email_addresses = local.budget_subscriber_email_addresses

  # SNS topic email subscribers (D41 single sink, AC-13): sourced from service.hcl,
  # derived from the same owner email as the budget. Creates PendingConfirmation
  # subscriptions the operator confirms so alarms and cost-anomaly alerts reach a human.
  sns_subscribers = local.sns_subscribers

  # ---------------------------------------------------------------------------
  # Fifteen named alarms with metric/namespace/dimensions from upstream outputs
  # (AC-12, D37): the original six plus nine load-test observability alarms (7-15)
  # covering the now-autoscaling ECS collector, the cwl_split Firehose-transform
  # Lambda, Firehose backpressure, and ALB connection health -- see service.hcl for
  # the full alarm-by-alarm rationale.
  # (OBS-4b removed the original seventh, adot-memory-limiter-drops: the logs-only
  # collector (D27.3) never publishes otelcol_processor_refused_metric_points, so the
  # alarm had no datapoints and could never transition -- a permanently no-data alarm
  # dropped per operator decision. It is not restored here.)
  # The alarm_configs list (from service.hcl via local.alarm_configs) carries
  # metric_name, namespace, statistic, comparison_operator, threshold, period,
  # evaluation_periods, and treat_missing_data -- all input-driven (OBS-4a: task-down and
  # firehose-freshness use "breaching" so a stopped service / stalled stream alarms even
  # with no datapoints; the rest use "notBreaching" where missing data means healthy).
  # The dimensions block is populated here
  # from upstream dependency outputs so no dimension literal appears in service.hcl
  # or this leaf (AC-12, D37). Each alarm's alarm_actions and ok_actions are wired
  # to topic_arn by references/observability internally; no named alarm fires to no
  # one (AC-12).
  #
  # Built directly in inputs (not locals) because terragrunt only resolves
  # dependency.<name>.outputs at input-evaluation time -- a locals block cannot
  # reference dependency outputs (terragrunt v1.0.7: "dependency is not defined").
  # ---------------------------------------------------------------------------
  alarms = {
    ecs_running_task_count = merge(local.alarm_configs[0], {
      # ECS RunningTaskCount -- cluster and service dimensions from collector-ingestion.
      dimensions = {
        ClusterName = dependency.collector_ingestion.outputs.ecs_cluster_name
        ServiceName = dependency.collector_ingestion.outputs.ecs_service_name
      }
    })
    alb_5xx_count = merge(local.alarm_configs[1], {
      # ALB HTTPCode_Target_5XX_Count -- ALB arn-suffix from collector-ingestion.
      dimensions = {
        LoadBalancer = dependency.collector_ingestion.outputs.alb_arn_suffix
      }
    })
    alb_target_response_time_p95 = merge(local.alarm_configs[2], {
      # ALB TargetResponseTime p95 -- ALB arn-suffix from collector-ingestion.
      dimensions = {
        LoadBalancer = dependency.collector_ingestion.outputs.alb_arn_suffix
      }
    })
    waf_blocked_requests = merge(local.alarm_configs[3], {
      # WAF BlockedRequests -- WebACL id and region from collector-ingestion.
      dimensions = {
        WebACL = dependency.collector_ingestion.outputs.waf_web_acl_id
        Rule   = "ALL"
        Region = local.region
      }
    })
    firehose_data_freshness = merge(local.alarm_configs[4], {
      # Firehose DeliveryToS3.DataFreshness -- stream name from data-lake.
      dimensions = {
        DeliveryStreamName = dependency.data_lake.outputs.firehose_stream_name
      }
    })
    athena_bytes_scanned = merge(local.alarm_configs[5], {
      # Athena bytes scanned -- workgroup from the athena unit.
      dimensions = {
        WorkGroup = dependency.athena.outputs.workgroup_name
      }
    })
    # -------------------------------------------------------------------------
    # Load-test observability alarms 7-15: make the ~2000-concurrent-session collector
    # load test fully measurable (spec 02 Section 4.2 extension). Dimensions sourced
    # from the same upstream dependency outputs as the six alarms above (D37).
    # -------------------------------------------------------------------------
    ecs_cpu_utilization = merge(local.alarm_configs[6], {
      # ECS CPUUtilization -- cluster and service dimensions from collector-ingestion.
      dimensions = {
        ClusterName = dependency.collector_ingestion.outputs.ecs_cluster_name
        ServiceName = dependency.collector_ingestion.outputs.ecs_service_name
      }
    })
    ecs_memory_utilization = merge(local.alarm_configs[7], {
      # ECS MemoryUtilization -- cluster and service dimensions from collector-ingestion.
      dimensions = {
        ClusterName = dependency.collector_ingestion.outputs.ecs_cluster_name
        ServiceName = dependency.collector_ingestion.outputs.ecs_service_name
      }
    })
    cwl_split_lambda_errors = merge(local.alarm_configs[8], {
      # cwl_split transform Lambda Errors -- function name from data-lake.
      dimensions = {
        FunctionName = dependency.data_lake.outputs.cwl_transform_lambda_name
      }
    })
    cwl_split_lambda_throttles = merge(local.alarm_configs[9], {
      # cwl_split transform Lambda Throttles -- function name from data-lake.
      dimensions = {
        FunctionName = dependency.data_lake.outputs.cwl_transform_lambda_name
      }
    })
    cwl_split_lambda_duration_p95 = merge(local.alarm_configs[10], {
      # cwl_split transform Lambda Duration p95 -- function name from data-lake.
      dimensions = {
        FunctionName = dependency.data_lake.outputs.cwl_transform_lambda_name
      }
    })
    cwl_split_lambda_concurrent_executions = merge(local.alarm_configs[11], {
      # cwl_split transform Lambda ConcurrentExecutions -- function name from data-lake.
      dimensions = {
        FunctionName = dependency.data_lake.outputs.cwl_transform_lambda_name
      }
    })
    firehose_throttled_records = merge(local.alarm_configs[12], {
      # Firehose ThrottledRecords -- stream name from data-lake.
      dimensions = {
        DeliveryStreamName = dependency.data_lake.outputs.firehose_stream_name
      }
    })
    alb_rejected_connections = merge(local.alarm_configs[13], {
      # ALB RejectedConnectionCount -- ALB arn-suffix from collector-ingestion.
      dimensions = {
        LoadBalancer = dependency.collector_ingestion.outputs.alb_arn_suffix
      }
    })
    alb_target_connection_errors = merge(local.alarm_configs[14], {
      # ALB TargetConnectionErrorCount -- ALB arn-suffix from collector-ingestion.
      dimensions = {
        LoadBalancer = dependency.collector_ingestion.outputs.alb_arn_suffix
      }
    })
  }

  # ---------------------------------------------------------------------------
  # CloudWatch dashboard (iac/04 section 3.8, AC-12).
  # create_dashboard is a deployment-unique value sourced from terraform.tfvars
  # (spec section 4.5, AC-FUNC-002). Terraform auto-loads terraform.tfvars after
  # Terragrunt copies the unit directory into the module working directory (spec section 1.1).
  # To retune dashboard provisioning for this deployment, edit terraform.tfvars only.
  # dashboard_name from _envcommon (namespace-scoped, non-null after apply).
  # dashboard_body is the JSON document covering ALB/ECS/Firehose/WAF health.
  # Dimension values are sourced from dependency outputs -- never hardcoded (D37).
  # Built directly in inputs for the same dependency-resolution reason as alarms.
  # ---------------------------------------------------------------------------
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ALB 5XX Error Rate"
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count",
            "LoadBalancer", dependency.collector_ingestion.outputs.alb_arn_suffix]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ALB Target Response Time p95"
          period = 300
          stat   = "p95"
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime",
            "LoadBalancer", dependency.collector_ingestion.outputs.alb_arn_suffix]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ECS Running Task Count"
          period = 60
          stat   = "Average"
          view   = "timeSeries"
          metrics = [
            ["AWS/ECS", "RunningTaskCount",
              "ClusterName", dependency.collector_ingestion.outputs.ecs_cluster_name,
            "ServiceName", dependency.collector_ingestion.outputs.ecs_service_name]
          ]
        }
      },
      # OBS-4b: the "ADOT Memory Limiter Drops" widget was removed alongside the
      # adot-memory-limiter-drops alarm. It plotted telemetry/adot
      # otelcol_processor_refused_metric_points, which the logs-only collector (D27.3)
      # never publishes -- a permanently no-data tile carrying no signal.
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "Firehose Delivery Freshness"
          period = 60
          stat   = "Maximum"
          view   = "timeSeries"
          metrics = [
            ["AWS/Firehose", "DeliveryToS3.DataFreshness",
            "DeliveryStreamName", dependency.data_lake.outputs.firehose_stream_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "WAF Blocked Requests"
          period = 300
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            ["AWS/WAFV2", "BlockedRequests",
              "WebACL", dependency.collector_ingestion.outputs.waf_web_acl_id,
              "Rule", "ALL",
            "Region", local.region]
          ]
        }
      },
      # -----------------------------------------------------------------------
      # Load-test observability widgets (spec 02 Section 4.2 extension): make the
      # ~2000-concurrent-session collector load test fully measurable. Dimension
      # values are sourced from the same dependency outputs as the alarms above
      # (D37) -- never hardcoded.
      # -----------------------------------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ECS CPU Utilization"
          period = 60
          stat   = "Average"
          view   = "timeSeries"
          metrics = [
            ["AWS/ECS", "CPUUtilization",
              "ClusterName", dependency.collector_ingestion.outputs.ecs_cluster_name,
            "ServiceName", dependency.collector_ingestion.outputs.ecs_service_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ECS Memory Utilization"
          period = 60
          stat   = "Average"
          view   = "timeSeries"
          metrics = [
            ["AWS/ECS", "MemoryUtilization",
              "ClusterName", dependency.collector_ingestion.outputs.ecs_cluster_name,
            "ServiceName", dependency.collector_ingestion.outputs.ecs_service_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "cwl_split Lambda Duration p95"
          period = 300
          stat   = "p95"
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Duration",
            "FunctionName", dependency.data_lake.outputs.cwl_transform_lambda_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "cwl_split Lambda Errors"
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Errors",
            "FunctionName", dependency.data_lake.outputs.cwl_transform_lambda_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 30
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "cwl_split Lambda Throttles"
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Throttles",
            "FunctionName", dependency.data_lake.outputs.cwl_transform_lambda_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 30
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "cwl_split Lambda Concurrent Executions"
          period = 60
          stat   = "Maximum"
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "ConcurrentExecutions",
            "FunctionName", dependency.data_lake.outputs.cwl_transform_lambda_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 36
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "Firehose Incoming Records"
          period = 60
          # Sum (not Maximum): IncomingRecords is a per-period record-count metric, so Sum
          # gives the true records-per-period volume; the load test's peak throughput (the
          # "high-watermark") is then visible as the tallest point on the resulting timeSeries.
          stat = "Sum"
          view = "timeSeries"
          metrics = [
            ["AWS/Firehose", "IncomingRecords",
            "DeliveryStreamName", dependency.data_lake.outputs.firehose_stream_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 36
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "Firehose Delivered Records"
          period = 60
          # Sum: DeliveryToS3.Records is a per-period record-count metric, matching the
          # IncomingRecords widget's statistic so the two are directly comparable.
          stat = "Sum"
          view = "timeSeries"
          metrics = [
            ["AWS/Firehose", "DeliveryToS3.Records",
            "DeliveryStreamName", dependency.data_lake.outputs.firehose_stream_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 42
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "Firehose Throttled Records"
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            ["AWS/Firehose", "ThrottledRecords",
            "DeliveryStreamName", dependency.data_lake.outputs.firehose_stream_name]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 42
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ALB Request Count"
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "RequestCount",
            "LoadBalancer", dependency.collector_ingestion.outputs.alb_arn_suffix]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 48
        width  = 12
        height = 6
        properties = {
          region = local.region
          title  = "ALB Active Connection Count"
          period = 60
          stat   = "Average"
          view   = "timeSeries"
          metrics = [
            ["AWS/ApplicationELB", "ActiveConnectionCount",
            "LoadBalancer", dependency.collector_ingestion.outputs.alb_arn_suffix]
          ]
        }
      },
    ]
  })

  # Tags from the root include (expose=true pattern -- not local.common_tags).
  tags = include.root.locals.common_tags
  },
  # ---------------------------------------------------------------------------
  # Child module source overrides (AC-7, AC-8, spec Section 4.3, Section 5).
  # When use_pinned_module_sources=true (prod): every in-repo child source is set to
  # its pinned git URL so the composed module tree is fully pinned.
  # When use_pinned_module_sources=false (sandbox/QA/root): the *_source keys are
  # OMITTED entirely so the module's relative-path defaults apply (spec Section 4.3,
  # Leaf behavior toggle=false: "Does NOT set any *_source inputs"). They MUST be
  # omitted -- not set to null -- because a terraform child module `source` argument
  # is a const-typed variable; passing an explicit null overrides the relative-path
  # default and crashes `terraform init` ("panic: value is null" in module install).
  # A conditional merge adds the keys only in the pinned (prod) context.
  # ---------------------------------------------------------------------------
  local.account_vars.locals.use_pinned_module_sources ? {
    sns_topic_source    = "${include.root.locals.module_git_base}//providers/aws/primitives/sns-topic?ref=providers/aws/primitives/sns-topic/v1.0.1"
    cloudwatch_source   = "${include.root.locals.module_git_base}//providers/aws/primitives/cloudwatch?ref=providers/aws/primitives/cloudwatch/v1.0.1"
    cost_anomaly_source = "${include.root.locals.module_git_base}//providers/aws/primitives/cost-anomaly?ref=providers/aws/primitives/cost-anomaly/v1.0.1"
  } : {}
)
