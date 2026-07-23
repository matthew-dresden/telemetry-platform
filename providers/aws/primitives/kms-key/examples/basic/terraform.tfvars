alias_name               = "telemetry-basic-example"
description              = "Customer managed KMS key - basic example"
deletion_window_in_days  = 30
enable_key_rotation      = true
key_usage                = "ENCRYPT_DECRYPT"
customer_master_key_spec = "SYMMETRIC_DEFAULT"
multi_region             = false
tags = {
  Environment = "test"
  Purpose     = "kms-key-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
