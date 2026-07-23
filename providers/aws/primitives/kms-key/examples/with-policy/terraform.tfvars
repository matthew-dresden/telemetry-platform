alias_name               = "telemetry-with-policy-example"
description              = "Customer managed KMS key - with-policy example"
deletion_window_in_days  = 7
enable_key_rotation      = true
key_usage                = "ENCRYPT_DECRYPT"
customer_master_key_spec = "SYMMETRIC_DEFAULT"
multi_region             = true
# account_id is supplied at runtime via TF_VAR_account_id environment variable
tags = {
  Environment = "test"
  Purpose     = "kms-key-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
