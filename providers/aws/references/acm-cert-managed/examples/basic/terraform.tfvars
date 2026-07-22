domain_name               = "telemetry-basic-example.example.com"
subject_alternative_names = []
validation_method         = "DNS"
key_algorithm             = "RSA_2048"
wait_for_validation       = false
tags = {
  Environment = "test"
  Purpose     = "acm-cert-managed-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
