name                 = "telemetry-basic-example-waf"
scope                = "CLOUDFRONT"
default_action       = "allow"
rate_limit_per_ip    = 2000
logging_enabled      = false
log_destination_arns = []
log_kms_key_arn      = null
tags = {
  Environment = "test"
  Purpose     = "waf-webacl-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
