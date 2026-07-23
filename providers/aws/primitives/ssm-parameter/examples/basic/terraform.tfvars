name        = "/telemetry/test/ingest/waf-rate-limit"
type        = "String"
value       = "100"
description = "WAF rate limit config -- basic example"
tier        = "Standard"

tags = {
  Environment = "test"
  Purpose     = "ssm-parameter-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
