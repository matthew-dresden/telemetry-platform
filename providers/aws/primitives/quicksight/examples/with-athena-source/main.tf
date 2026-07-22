variable "workgroup_name" {
  type        = string
  description = "Name of the Athena workgroup to use as the QuickSight data source."
  default     = "telemetry-analytics"
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

# QuickSight CreateDataSource requires aws_account_id to be the calling account.
# Derive it from caller identity so nothing is hard-coded and the data source is
# created in the account the test actually runs against.
data "aws_caller_identity" "current" {}

# NOTE: The Athena workgroup fixture is referenced from the caller's environment.
# A real deployment wires the athena-workgroup module output here. In the test
# scenario, the workgroup must exist in the target account before this example
# applies (QuickSight active-subscription preflight requirement).

module "quicksight" {
  source = "../../"

  aws_account_id = data.aws_caller_identity.current.account_id

  # Athena data source pointing at the cost-capped workgroup.
  athena_data_source = {
    data_source_id = "telemetry-athena-source"
    name           = "Telemetry Athena Data Source"
    workgroup_name = var.workgroup_name
    catalog        = "AwsDataCatalog"
    database       = "default"
  }

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "quicksight-module-with-athena-source-example"
    Owner       = "terraform"
  })
}

output "data_source_arn" {
  description = "The ARN of the Athena QuickSight data source."
  value       = module.quicksight.data_source_arn
}

output "data_source_id" {
  description = "The ID of the Athena QuickSight data source."
  value       = module.quicksight.data_source_id
}
