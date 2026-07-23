locals {
  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })
}

module "example" {
  source = "../../"

  bucket_prefix = var.bucket_prefix
  kms_alias     = var.kms_alias

  tags           = local.tags
  managed_by_tag = var.managed_by_tag
  module_tag     = var.module_tag
}

output "state_kms_key_arn" {
  description = "The ARN of the KMS CMK used for state encryption."
  value       = module.example.state_kms_key_arn
}

output "access_log_bucket" {
  description = "The name of the S3 access-log bucket."
  value       = module.example.access_log_bucket
}

output "artifact_bucket_name" {
  description = "The name of the S3 artifact (state) bucket."
  value       = module.example.artifact_bucket_name
}
