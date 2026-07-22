zone_name     = "telemetry-basic-example.example.com"
comment       = "Basic example hosted zone - managed by terraform"
force_destroy = true
tags = {
  Environment = "test"
  Purpose     = "route53-zone-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
