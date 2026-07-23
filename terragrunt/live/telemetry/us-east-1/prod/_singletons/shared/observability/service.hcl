# live/telemetry/us-east-1/sandbox/000/observability/service.hcl
#
# Service layer for the sandbox observability unit.
# Basename resolves to "observability" per the canonical layer idiom (spec 02 Section 1.3).
#
# This unit provisions the central alerting sink for the usage-analytics stack:
# a KMS-encrypted SNS topic, cost-anomaly subscriber, AWS Budgets, and a CloudWatch
# dashboard, via a single call to the references/observability reference module
# (AC-3, E1-F7-S2-T1). The reference module is the canonical composition point;
# no primitive is sourced directly from this unit (AC-3).
#
# Ownership boundary (iac/04 section 3.8, D41):
#   This unit owns ONLY: KMS-encrypted SNS topic, topic subscriptions, cost-anomaly
#   subscriber, and AWS Budgets resources. CloudWatch alarms owned by other units
#   (collector-ingestion, data-lake, athena) are NOT created here. The
#   observability unit only subscribes those alarms' alarm_actions/ok_actions to the
#   SNS topic_arn it owns.
#
# Module source: providers/aws/references/observability (spec 02 Section 3.5).
# Pre-tag local source is used during initial rollout; replace with an immutable
# ?ref=<semver> tag once the module is published and promoted to stable.
#
# Dependencies:
#   - references/observability module (E1-F7-S2-T1) must be present in the repo
#     at providers/aws/references/observability before this unit can be applied.
#   - sandbox collector-ingestion unit (E6-F2-S1-T2) must be applied and expose
#     ecs_cluster_name, ecs_service_name, alb_arn_suffix, waf_web_acl_id, and
#     firehose_stream_name for alarm dimension inputs.
#   - sandbox data-lake unit (E6-F2-S1-T1) must be applied and expose
#     firehose_stream_name for the Firehose alarm dimensions and
#     cwl_transform_lambda_name for the cwl_split transform Lambda's FunctionName
#     alarm dimension (load-test observability alarms 9-12).
#   - athena unit must be applied and expose
#     workgroup_name for the Athena alarm dimensions.
#   - state-bootstrap unit (E4-F1-S1-T1) must have been applied so the remote
#     state backend and lock table exist (D40).
#   - _envcommon/observability.hcl must define topic_name, dashboard_name,
#     anomaly_monitor_name, anomaly_subscription_name,
#     anomaly_subscription_frequency, sns_kms_key_id, cost_anomaly_threshold_expression,
#     budget_amount, budget_notification_thresholds, budget_email_subscribers,
#     and alarm_configs (D37).
#
# The _envcommon/observability.hcl shared template is included by the leaf terragrunt.hcl
# (not here at the service layer). No _envcommon include is used at this layer.
#
# spec 02 Section 4.2, ledger D2, D4, D8, D31, D37, D40, D41, D45, D47, AC-3,
# AC-12, AC-13, iac/04 section 3.8.

locals {
  # service resolves to the directory basename, i.e. "observability".
  # Consumed by the root terragrunt.hcl remote_state key derivation.
  service = basename(get_terragrunt_dir())

  # ---------------------------------------------------------------------------
  # Hierarchy locals: account id, region (D45/D47).
  # All ARN-derived values in this file use these locals so no hardcoded account
  # id, region, or environment-specific string appears (D31/D45). Mirrors the prod
  # observability service.hcl so the sandbox account id/region flow through.
  # ---------------------------------------------------------------------------
  account_vars   = read_terragrunt_config(find_in_parent_folders("account.hcl"))
  aws_account_id = local.account_vars.locals.aws_account_id

  region = read_terragrunt_config(find_in_parent_folders("region.hcl")).locals.aws_region

  # Environment name (sandbox/prod/...) resolved from environment.hcl by folder basename,
  # so the alarm_name prefix is env-derived and copy-folder safe: "sandbox-" in the sandbox
  # env, "prod-" in the prod env, with NO hardcoded env literal (D31, copyability).
  environment = read_terragrunt_config(find_in_parent_folders("environment.hcl")).locals.environment

  # ---------------------------------------------------------------------------
  # SNS topic KMS encryption (iac/01 section 5.8, AC-3, D8).
  # The SNS topic is encrypted with the telemetry-config CMK alias owned by the
  # dns-prod-zone unit. The KMS alias ARN is derived from region + account_id so
  # no inline literal appears in this file (AC-13, D8).
  # The alias/telemetry-config CMK is the platform config-tier key (iac/04 section 3.8).
  # ---------------------------------------------------------------------------
  telemetry_config_cmk_alias = "alias/telemetry-config"
  sns_kms_key_id             = "arn:aws:kms:${local.region}:${local.aws_account_id}:${local.telemetry_config_cmk_alias}"

  # ---------------------------------------------------------------------------
  # Cost-anomaly alert threshold (iac/04 section 3.8).
  # Expression format matches the AWS Cost Anomaly Detection API.
  # The threshold is input-driven from this service.hcl (AC-13, D8).
  # ---------------------------------------------------------------------------
  cost_anomaly_threshold_expression = jsonencode({
    Dimensions = {
      Key          = "ANOMALY_TOTAL_IMPACT_PERCENTAGE"
      Values       = ["20"]
      MatchOptions = ["GREATER_THAN_OR_EQUAL"]
    }
  })

  # ---------------------------------------------------------------------------
  # Budget inputs (iac/04 section 3.8, AC-5, D8).
  # budget_amount: sourced from OBSERVABILITY_BUDGET_AMOUNT environment variable.
  #   get_env() fails fast with an actionable error if the variable is absent,
  #   enforcing the missing-value fail-fast contract (D8, CLAUDE.md).
  #   The CI apply workflow sets this before invoking terragrunt apply.
  # budget_notification_thresholds: percentage increments of budget_amount.
  #   Three thresholds -- 50%, 80%, 100% -- notify before and at budget exhaustion.
  # budget_subscriber_email_addresses: sourced from OBSERVABILITY_BUDGET_EMAIL env var.
  #   get_env() fails fast if absent. The CI workflow sets this from secrets.
  # ---------------------------------------------------------------------------
  budget_amount = tonumber(get_env("OBSERVABILITY_BUDGET_AMOUNT"))

  budget_notification_thresholds = [
    { threshold = 50 },
    { threshold = 80 },
    { threshold = 100 },
  ]

  budget_subscriber_email_addresses = split(",", get_env("OBSERVABILITY_BUDGET_EMAIL"))

  # SNS topic subscribers (D41 single sink, OBS-3): email-subscribe the same owner
  # address(es) as the budget so CloudWatch alarms and cost-anomaly notifications
  # reach a human instead of firing into a subscriber-less topic. Each entry
  # becomes a PendingConfirmation subscription the operator confirms via the AWS
  # email link. Derived from the same OBSERVABILITY_BUDGET_EMAIL source so no
  # inline literal appears here (AC-13); the budget itself keeps its direct
  # subscribers and does not route through SNS (D41).
  #
  # OPERATOR ACTION (OBS-3 root cause): an SNS *email* subscription is created
  # PendingConfirmation. The recipient MUST click the confirmation link AWS emails
  # promptly after apply, or NO alarm/anomaly notifications are delivered. AWS
  # auto-deletes an unconfirmed subscription after ~3 days, and Terraform does NOT
  # recreate a still-pending subscription on re-apply -- which is why the live topic
  # showed zero subscriptions despite this wiring. Confirm right after the apply that
  # creates the subscription. (The observability reference module also exposes a
  # dedicated alarm_notification_email input from v1.1.0; this leaf can adopt it on the
  # prod re-pin, but the subscription it composes is identical and still PendingConfirmation.)
  sns_subscribers = [
    for addr in local.budget_subscriber_email_addresses : {
      protocol = "email"
      endpoint = addr
    }
  ]

  # ---------------------------------------------------------------------------
  # Alarm configuration list (iac/04 section 3.8, AC-4, D8).
  # Fifteen named CloudWatch alarm metric/namespace/threshold configs: the original
  # six (ECS RunningTaskCount, ALB 5XX, ALB TargetResponseTime p95, WAF BlockedRequests,
  # Firehose DeliveryToS3.DataFreshness, Athena ProcessedBytes) plus nine load-test
  # observability alarms added so a ~2000-concurrent-session load test is fully
  # measurable (alarms 7-15): ECS CPUUtilization/MemoryUtilization (the collector now
  # autoscales, so CPU/memory pressure are the primary autoscaling signals), the
  # cwl_split Firehose-transform Lambda's Errors/Throttles/Duration-p95/
  # ConcurrentExecutions (the Lambda re-ingests every CloudWatch Logs event as its own
  # PutRecordBatch record, so it is the amplification point most exposed to
  # high-volume re-ingestion), Firehose ThrottledRecords (delivery-stream backpressure),
  # and ALB RejectedConnectionCount/TargetConnectionErrorCount (ALB-side connection
  # exhaustion distinct from the existing 5XX/latency alarms).
  # (OBS-4b removed the original seventh, adot-memory-limiter-drops: the collector is
  # logs-only per D27.3 so the ADOT memory_limiter never publishes
  # otelcol_processor_refused_metric_points -- the alarm had no datapoints and could
  # never transition, so it was dead noise. It is not restored here.)
  # Dimension values are NOT set here -- they come from upstream dependency outputs
  # in the leaf terragrunt.hcl (D37). The alarm_configs list carries only the
  # alarm_name, metric_name, namespace, statistic, comparison_operator, threshold,
  # period, and evaluation_periods (all input-driven, no inline literals, AC-13).
  # The leaf merges each config with its dimension values sourced from dependency outputs.
  # The alarm_name prefix is env-derived from local.environment ("sandbox-" in the sandbox
  # env, "prod-" in the prod env); all other fields are identical across envs (D31). This is
  # what makes a single service.hcl copy-folder safe: no per-env hardcoded prefix.
  # ---------------------------------------------------------------------------
  alarm_configs = [
    {
      # Alarm 1: ECS RunningTaskCount below desired (AWS/ECS)
      alarm_name          = "${local.environment}-ecs-running-task-count-low"
      metric_name         = "RunningTaskCount"
      namespace           = "AWS/ECS"
      statistic           = "Average"
      comparison_operator = "LessThanThreshold"
      threshold           = 1
      period              = 60
      evaluation_periods  = 3
      # breaching (OBS-4a): a stopped ECS service stops emitting RunningTaskCount, so a
      # task-down outage produces NO datapoints. With the default "missing" the alarm would
      # sit in INSUFFICIENT_DATA and never fire; "breaching" makes absent data alarm.
      treat_missing_data = "breaching"
    },
    {
      # Alarm 2: ALB HTTP 5XX surge (AWS/ApplicationELB)
      alarm_name          = "${local.environment}-alb-5xx-surge"
      metric_name         = "HTTPCode_Target_5XX_Count"
      namespace           = "AWS/ApplicationELB"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 10
      period              = 60
      evaluation_periods  = 3
      # notBreaching (OBS-4a): no 5XX datapoints means no errors, not an outage -- absent
      # data is healthy, so it must NOT alarm.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 3: ALB TargetResponseTime p95 (AWS/ApplicationELB).
      # p95 is a percentile statistic -- aws_cloudwatch_metric_alarm requires it to be
      # supplied via extended_statistic, not statistic (which only accepts the five base
      # statistics). statistic is set to null so EXACTLY ONE of the two is present.
      alarm_name          = "${local.environment}-alb-target-response-time-p95"
      metric_name         = "TargetResponseTime"
      namespace           = "AWS/ApplicationELB"
      statistic           = null
      extended_statistic  = "p95"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 2
      period              = 300
      evaluation_periods  = 3
      # notBreaching (OBS-4a): with no requests there are no latency datapoints; idle traffic
      # is healthy and must not raise a latency alarm.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 4: WAF BlockedRequests spike (AWS/WAFV2)
      alarm_name          = "${local.environment}-waf-blocked-requests-spike"
      metric_name         = "BlockedRequests"
      namespace           = "AWS/WAFV2"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 100
      period              = 300
      evaluation_periods  = 3
      # notBreaching (OBS-4a): no BlockedRequests datapoints means nothing was blocked --
      # healthy, so absent data must not alarm.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 5: Firehose DeliveryToS3.DataFreshness / failed-records (AWS/Firehose)
      alarm_name          = "${local.environment}-firehose-delivery-freshness"
      metric_name         = "DeliveryToS3.DataFreshness"
      namespace           = "AWS/Firehose"
      statistic           = "Maximum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 900
      period              = 60
      evaluation_periods  = 3
      # breaching (OBS-4a): if Firehose stalls/stops delivering, DeliveryToS3.DataFreshness
      # stops being published, so a stalled stream produces NO datapoints. The default
      # "missing" would keep it in INSUFFICIENT_DATA; "breaching" makes absent data alarm.
      treat_missing_data = "breaching"
    },
    {
      # Alarm 6: Athena bytes-scanned near cap (AWS/Athena, athena_workgroup_name dim)
      alarm_name          = "${local.environment}-athena-scan-bytes-near-cap"
      metric_name         = "ProcessedBytes"
      namespace           = "AWS/Athena"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 1000000000000
      period              = 86400
      evaluation_periods  = 1
      # notBreaching (OBS-4a): no ProcessedBytes datapoints means no large scans ran --
      # healthy, so absent data must not alarm.
      treat_missing_data = "notBreaching"
    },
    # -------------------------------------------------------------------------
    # Load-test observability alarms 7-15 (spec 02 Section 4.2 extension): make the
    # ~2000-concurrent-session collector load test fully measurable. Dimension values
    # continue to come from upstream dependency outputs in the leaf terragrunt.hcl
    # (D37) -- ECS/ALB dims from collector-ingestion, Lambda dim from data-lake's new
    # cwl_transform_lambda_name output, Firehose dim from data-lake's firehose_stream_name.
    # -------------------------------------------------------------------------
    {
      # Alarm 7: ECS CPUUtilization high (AWS/ECS, ClusterName+ServiceName dims).
      # The collector now autoscales, so CPU pressure is a primary autoscaling signal.
      alarm_name          = "${local.environment}-ecs-cpu-utilization-high"
      metric_name         = "CPUUtilization"
      namespace           = "AWS/ECS"
      statistic           = "Average"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 80
      period              = 60
      evaluation_periods  = 3
      # notBreaching (OBS-4a): a stopped/absent task publishes no CPUUtilization
      # datapoints; that outage is already covered (breaching) by the ecs-running-task-
      # count-low alarm above, so this alarm must not double-alarm on absent data.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 8: ECS MemoryUtilization high (AWS/ECS, ClusterName+ServiceName dims).
      alarm_name          = "${local.environment}-ecs-memory-utilization-high"
      metric_name         = "MemoryUtilization"
      namespace           = "AWS/ECS"
      statistic           = "Average"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 80
      period              = 60
      evaluation_periods  = 3
      # notBreaching (OBS-4a): same rationale as CPUUtilization above -- task-down is
      # already covered by ecs-running-task-count-low.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 9: cwl_split transform Lambda Errors (AWS/Lambda, FunctionName dim).
      # The Lambda re-ingests every CloudWatch Logs event as its own PutRecordBatch
      # record, so it is the amplification point most exposed under high-volume
      # re-ingestion (e.g. ~2000 concurrent sessions).
      alarm_name          = "${local.environment}-cwl-split-lambda-errors"
      metric_name         = "Errors"
      namespace           = "AWS/Lambda"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 0
      period              = 60
      evaluation_periods  = 1
      # notBreaching (OBS-4a): no invocations means no Errors datapoints -- healthy,
      # so absent data must not alarm.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 10: cwl_split transform Lambda Throttles (AWS/Lambda, FunctionName dim).
      alarm_name          = "${local.environment}-cwl-split-lambda-throttles"
      metric_name         = "Throttles"
      namespace           = "AWS/Lambda"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 0
      period              = 60
      evaluation_periods  = 1
      # notBreaching (OBS-4a): no invocations means no Throttles datapoints -- healthy.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 11: cwl_split transform Lambda Duration p95 (AWS/Lambda, FunctionName dim).
      # p95 is a percentile statistic -- supplied via extended_statistic, statistic null,
      # matching the alb-target-response-time-p95 pattern above. Threshold 60000ms (60s)
      # is half of the module's cwl_transform_lambda_timeout default (120s, see
      # providers/aws/references/data-lake/variables.tf), giving an early warning before
      # invocations approach the configured timeout.
      alarm_name          = "${local.environment}-cwl-split-lambda-duration-p95"
      metric_name         = "Duration"
      namespace           = "AWS/Lambda"
      statistic           = null
      extended_statistic  = "p95"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 60000
      period              = 300
      evaluation_periods  = 3
      # notBreaching (OBS-4a): no invocations means no Duration datapoints -- healthy.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 12: cwl_split transform Lambda ConcurrentExecutions (AWS/Lambda,
      # FunctionName dim). Threshold 800 is set below the AWS default per-region
      # unreserved-concurrency pool (1000) so operators get advance warning before
      # this function's concurrency contends for the shared account-level pool
      # (the leaf does not set cwl_transform_lambda_reserved_concurrent_executions,
      # so the function currently draws from that shared unreserved pool).
      alarm_name          = "${local.environment}-cwl-split-lambda-concurrent-executions-high"
      metric_name         = "ConcurrentExecutions"
      namespace           = "AWS/Lambda"
      statistic           = "Maximum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 800
      period              = 60
      evaluation_periods  = 3
      # notBreaching (OBS-4a): no invocations means zero concurrency -- healthy.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 13: Firehose ThrottledRecords (AWS/Firehose, DeliveryStreamName dim).
      # Signals delivery-stream backpressure distinct from the existing
      # firehose-delivery-freshness alarm (which watches staleness, not throttling).
      alarm_name          = "${local.environment}-firehose-throttled-records"
      metric_name         = "ThrottledRecords"
      namespace           = "AWS/Firehose"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 0
      period              = 60
      evaluation_periods  = 1
      # notBreaching (OBS-4a): no ThrottledRecords datapoints means nothing was
      # throttled -- healthy, so absent data must not alarm.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 14: ALB RejectedConnectionCount (AWS/ApplicationELB, LoadBalancer dim).
      # Connection-exhaustion signal distinct from the existing 5XX/latency alarms.
      alarm_name          = "${local.environment}-alb-rejected-connections"
      metric_name         = "RejectedConnectionCount"
      namespace           = "AWS/ApplicationELB"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 0
      period              = 60
      evaluation_periods  = 1
      # notBreaching (OBS-4a): no rejected connections means nothing was refused --
      # healthy, so absent data must not alarm.
      treat_missing_data = "notBreaching"
    },
    {
      # Alarm 15: ALB TargetConnectionErrorCount (AWS/ApplicationELB, LoadBalancer dim).
      alarm_name          = "${local.environment}-alb-target-connection-errors"
      metric_name         = "TargetConnectionErrorCount"
      namespace           = "AWS/ApplicationELB"
      statistic           = "Sum"
      comparison_operator = "GreaterThanThreshold"
      threshold           = 0
      period              = 60
      evaluation_periods  = 1
      # notBreaching (OBS-4a): no target-connection errors means healthy targets --
      # absent data must not alarm.
      treat_missing_data = "notBreaching"
    },
  ]
}
