output "state_kms_key_arn" {
  description = "The ARN of the KMS CMK used to encrypt the state bucket and access-log bucket."
  value       = module.state_kms_key.key_arn
}

output "access_log_bucket" {
  description = "The name of the S3 bucket that receives server access logs from the artifact (state) bucket."
  value       = module.access_log_bucket.bucket_id
}

output "artifact_bucket_name" {
  description = "The name of the S3 bucket that stores Terraform state files."
  value       = module.artifact_bucket.bucket_id
}
