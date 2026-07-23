module "example" {
  source = "../../"

  function_name         = var.function_name
  description           = var.description
  runtime               = var.runtime
  handler               = var.handler
  package_type          = var.package_type
  s3_bucket             = var.s3_bucket
  s3_key                = var.s3_key
  source_code_hash      = var.source_code_hash
  role                  = var.role
  environment_variables = var.environment_variables
  timeout               = var.timeout
  memory_size           = var.memory_size

  tags = merge(var.tags, {
    Environment = "test"
    Purpose     = "lambda-module-basic-example"
    Owner       = "terraform"
  })
}

output "function_arn" {
  description = "The ARN of the Lambda function."
  value       = module.example.function_arn
}

output "function_name" {
  description = "The name of the Lambda function."
  value       = module.example.function_name
}

output "invoke_arn" {
  description = "The ARN for invoking the Lambda function from API Gateway."
  value       = module.example.invoke_arn
}

output "qualified_arn" {
  description = "The qualified ARN of the Lambda function including the version."
  value       = module.example.qualified_arn
}

output "timeout" {
  description = "The function execution timeout in seconds."
  value       = module.example.timeout
}

output "memory_size" {
  description = "The amount of memory in MB the function gets at runtime."
  value       = module.example.memory_size
}
