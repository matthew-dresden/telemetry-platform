name        = "/telemetry/prod/ingest/adot-config"
type        = "SecureString"
description = "ADOT collector config -- securestring example"
tier        = "Standard"

tags = {
  Environment = "test"
  Purpose     = "ssm-parameter-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
