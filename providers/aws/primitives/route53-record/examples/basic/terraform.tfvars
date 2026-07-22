zone_id = "Z1234567890ABCDEFGHIJ"
name    = "_1234567890abcdef1234.telemetry-basic-example.example.com"
type    = "CNAME"
records = ["_abcdef1234567890.acm-validations.aws."]
ttl     = 60
tags = {
  Environment = "test"
  Purpose     = "route53-record-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
