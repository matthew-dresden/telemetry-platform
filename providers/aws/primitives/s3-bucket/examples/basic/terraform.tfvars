bucket_name        = "telemetry-basic-example-bucket"
versioning_enabled = true
bucket_key_enabled = true

tags = {
  Environment = "test"
  Purpose     = "s3-bucket-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
