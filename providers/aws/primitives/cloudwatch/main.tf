resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = var.alarms

  alarm_name          = each.key
  comparison_operator = each.value.comparison_operator
  evaluation_periods  = each.value.evaluation_periods
  metric_name         = each.value.metric_name
  namespace           = each.value.namespace
  period              = each.value.period
  # aws_cloudwatch_metric_alarm accepts EXACTLY ONE of statistic / extended_statistic.
  # When extended_statistic is set (e.g. a p95 percentile SLO), statistic must be null
  # and vice versa; the alarms variable validation enforces that exactly one is provided.
  statistic          = each.value.extended_statistic != null ? null : each.value.statistic
  extended_statistic = each.value.extended_statistic
  threshold          = each.value.threshold
  alarm_description  = each.value.alarm_description
  dimensions         = each.value.dimensions
  alarm_actions      = each.value.alarm_actions
  ok_actions         = each.value.ok_actions
  treat_missing_data = each.value.treat_missing_data

  tags = local.common_tags
}

resource "aws_cloudwatch_dashboard" "this" {
  count = var.create_dashboard ? 1 : 0

  dashboard_name = var.dashboard_name
  dashboard_body = var.dashboard_body
}

resource "aws_cloudwatch_log_group" "this" {
  for_each = var.log_groups

  name              = each.value.name
  retention_in_days = each.value.retention_in_days
  kms_key_id        = each.value.kms_key_id

  tags = local.common_tags
}
