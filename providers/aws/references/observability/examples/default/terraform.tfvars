name          = "telemetry-obs-test"
budget_amount = 500

budget_subscriber_email_addresses = [
  "ops@example.com",
]

budget_notification_thresholds = [
  { threshold = 80, notification_type = "ACTUAL", comparison = "GREATER_THAN" },
  { threshold = 100, notification_type = "ACTUAL", comparison = "GREATER_THAN" },
]

tags = {
  Environment = "test"
  Purpose     = "observability-module-testing"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
