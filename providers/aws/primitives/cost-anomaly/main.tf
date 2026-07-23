resource "aws_ce_anomaly_monitor" "this" {
  name                  = var.monitor_name
  monitor_type          = var.monitor_type
  monitor_dimension     = var.monitor_type == "DIMENSIONAL" ? "SERVICE" : null
  monitor_specification = var.monitor_type == "CUSTOM" ? var.monitor_specification : null

  tags = local.common_tags
}

resource "aws_ce_anomaly_subscription" "this" {
  name      = var.subscription_name
  frequency = var.subscription_frequency

  monitor_arn_list = [
    aws_ce_anomaly_monitor.this.arn,
  ]

  dynamic "subscriber" {
    for_each = var.subscribers
    content {
      address = subscriber.value.address
      type    = subscriber.value.type
    }
  }

  threshold_expression {
    dimension {
      key           = jsondecode(var.threshold_expression)["Dimensions"]["Key"]
      values        = jsondecode(var.threshold_expression)["Dimensions"]["Values"]
      match_options = jsondecode(var.threshold_expression)["Dimensions"]["MatchOptions"]
    }
  }
}
