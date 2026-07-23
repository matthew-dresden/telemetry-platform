bucket_name              = "telemetry-data-lake-example-bucket"
versioning_enabled       = true
bucket_key_enabled       = true
force_destroy            = true
transition_days          = 365
transition_storage_class = "GLACIER"
expiration_days          = 730

tags = {
  Environment = "test"
  Purpose     = "s3-bucket-module-data-lake-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
