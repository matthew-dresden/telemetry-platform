domain_name               = "telemetry-basic-example.example.com"
subject_alternative_names = ["www.telemetry-basic-example.example.com"]
validation_method         = "DNS"
key_algorithm             = "RSA_2048"
wait_for_validation       = false
tags = {
  Environment = "test"
  Purpose     = "acm-certificate-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
