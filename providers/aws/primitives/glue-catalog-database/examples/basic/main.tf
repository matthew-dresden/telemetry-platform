variable "database_name" {
  type        = string
  description = "The name for the Glue catalog database."
}

variable "description" {
  type        = string
  description = "Description for the Glue catalog database."
  default     = "Telemetry usage analytics catalog"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to resources."
  default     = {}
}

module "example" {
  source = "../../"

  database_name = var.database_name
  description   = var.description
  create_table  = false

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "glue-catalog-database-module-basic-example"
    Owner       = "terraform"
  })
}

output "database_name" {
  description = "The name of the Glue catalog database."
  value       = module.example.database_name
}

output "database_arn" {
  description = "The ARN of the Glue catalog database."
  value       = module.example.database_arn
}

output "catalog_id" {
  description = "The account catalog ID."
  value       = module.example.catalog_id
}

output "table_name" {
  description = "The Glue table name (null when create_table is false)."
  value       = module.example.table_name
}
