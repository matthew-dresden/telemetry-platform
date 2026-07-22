variable "workgroup_name" {
  type        = string
  description = "The name of the Athena workgroup."
}

variable "result_s3_bucket" {
  type        = string
  description = "The S3 bucket name where Athena writes query results."
}

variable "result_kms_key_arn" {
  type        = string
  description = "ARN of the KMS key for SSE-KMS encryption of Athena query results."
}

variable "bytes_scanned_cutoff_per_query" {
  type        = number
  description = "Maximum bytes scanned per query. Must be >= 10485760."
  default     = 10485760
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

provider "aws" {
  default_tags {
    tags = {
      Project         = var.project_tag
      "terratest-run" = var.terratest_run_id
    }
  }
}

module "example" {
  source = "../../"

  workgroup_name                  = var.workgroup_name
  result_s3_bucket                = var.result_s3_bucket
  result_kms_key_arn              = var.result_kms_key_arn
  bytes_scanned_cutoff_per_query  = var.bytes_scanned_cutoff_per_query
  enforce_workgroup_configuration = true

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "athena-workgroup-module-default-example"
    Owner       = "terraform"
  })
}

output "workgroup_id" {
  description = "The Athena workgroup identifier."
  value       = module.example.workgroup_id
}

output "workgroup_arn" {
  description = "The ARN of the Athena workgroup."
  value       = module.example.workgroup_arn
}

output "workgroup_name" {
  description = "The name of the Athena workgroup."
  value       = module.example.workgroup_name
}
