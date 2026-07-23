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

# Derive the account ID from caller identity so nothing is hard-coded.
data "aws_caller_identity" "current" {}

module "quicksight" {
  source = "../../"

  aws_account_id = data.aws_caller_identity.current.account_id

  # No Athena data source in the basic example.
  athena_data_source = null

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "quicksight-module-basic-example"
    Owner       = "terraform"
  })
}

output "data_source_arn" {
  description = "The ARN of the optional Athena data source (empty in the basic example)."
  value       = module.quicksight.data_source_arn
}

output "data_source_id" {
  description = "The ID of the optional Athena data source (empty in the basic example)."
  value       = module.quicksight.data_source_id
}
