variable "function_name" {
  type        = string
  description = "(Required) Unique name for the Lambda function."
}

variable "description" {
  type        = string
  description = "(Optional) Description of the Lambda function."
  default     = ""
}

variable "runtime" {
  type        = string
  description = "(Required) Identifier of the Lambda runtime. Example: python3.12, nodejs20.x."
}

variable "handler" {
  type        = string
  description = "(Required) Function entrypoint in the code. Example: index.handler."
}

variable "package_type" {
  type        = string
  description = "(Required) Package type for the Lambda deployment artifact. Valid value: Zip."
  default     = "Zip"

  validation {
    condition     = var.package_type == "Zip"
    error_message = "package_type must be Zip. Only Zip package type is supported by this primitive."
  }
}

variable "s3_bucket" {
  type        = string
  description = "(Required) S3 bucket containing the Lambda deployment artifact."
}

variable "s3_key" {
  type        = string
  description = "(Required) S3 object key of the Lambda deployment artifact."
}

variable "source_code_hash" {
  type        = string
  description = "(Required) Base64-encoded SHA256 hash of the deployment package to detect changes."
}

variable "role" {
  type        = string
  description = "(Required) ARN of the IAM role to attach to the Lambda function. Must match ^arn:aws:iam:."

  validation {
    condition     = can(regex("^arn:aws:iam:", var.role))
    error_message = "role must be a valid IAM role ARN matching ^arn:aws:iam:."
  }
}

variable "environment_variables" {
  type        = map(string)
  description = "(Optional) Map of environment variables for the Lambda function. When empty, no environment block is emitted."
  default     = {}
}

variable "tracing_mode" {
  type        = string
  description = "(Optional) AWS X-Ray tracing mode for the function. Defaults to Active (secure-by-default) so requests are sampled and traced. Must be one of Active or PassThrough."
  default     = "Active"

  validation {
    condition     = contains(["Active", "PassThrough"], var.tracing_mode)
    error_message = "tracing_mode must be one of Active or PassThrough."
  }
}

variable "timeout" {
  type        = number
  description = "(Optional) Function execution timeout in seconds. Defaults to the AWS default of 3 (backward-compatible for existing callers). Callers whose handler makes external API calls (e.g. the portal embed-URL Lambda calling QuickSight + SSM) must raise this above 3 or the function times out before the API returns. Must be between 1 and 900."
  default     = 3

  validation {
    condition     = var.timeout >= 1 && var.timeout <= 900
    error_message = "timeout must be between 1 and 900 seconds (the AWS Lambda limit)."
  }
}

variable "memory_size" {
  type        = number
  description = "(Optional) Amount of memory in MB the function gets at runtime (also scales CPU). Defaults to the AWS default of 128 (backward-compatible). Must be between 128 and 10240."
  default     = 128

  validation {
    condition     = var.memory_size >= 128 && var.memory_size <= 10240
    error_message = "memory_size must be between 128 and 10240 MB (the AWS Lambda range)."
  }
}

variable "tags" {
  type        = map(string)
  description = "(Optional) Additional tags applied to all resources created by this module."
  default     = {}
}

variable "managed_by_tag" {
  type        = string
  description = "(Optional) Value for the ManagedBy tag."
  default     = "terraform"
}

variable "module_tag" {
  type        = string
  description = "(Optional) Value for the Module tag identifying the source module."
  default     = "lambda"
}
