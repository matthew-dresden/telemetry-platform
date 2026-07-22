variable "function_name" {
  type        = string
  description = "Unique name for the Lambda function."
}

variable "description" {
  type        = string
  description = "Description of the Lambda function."
  default     = "Example Lambda function for telemetry portal"
}

variable "runtime" {
  type        = string
  description = "Identifier of the Lambda runtime."
  default     = "python3.12"
}

variable "handler" {
  type        = string
  description = "Function entrypoint. Example: index.handler."
  default     = "index.handler"
}

variable "package_type" {
  type        = string
  description = "Package type. Must be Zip."
  default     = "Zip"
}

variable "s3_bucket" {
  type        = string
  description = "S3 bucket containing the Lambda deployment artifact."
}

variable "s3_key" {
  type        = string
  description = "S3 object key of the Lambda deployment artifact."
}

variable "source_code_hash" {
  type        = string
  description = "Base64-encoded SHA256 hash of the deployment package."
}

variable "role" {
  type        = string
  description = "ARN of the IAM role to attach to the Lambda function."
}

variable "environment_variables" {
  type        = map(string)
  description = "Map of environment variables for the Lambda function."
  default     = {}
}

variable "timeout" {
  type        = number
  description = "Function execution timeout in seconds."
  default     = 3
}

variable "memory_size" {
  type        = number
  description = "Amount of memory in MB the function gets at runtime."
  default     = 128
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

variable "project_tag" {
  type        = string
  description = "Project tag value applied to all resources via the provider default_tags block."
}

variable "terratest_run_id" {
  type        = string
  description = "Terratest run identifier applied to all resources via the provider default_tags block."
}
