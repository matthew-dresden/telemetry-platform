output "function_arn" {
  description = "The Amazon Resource Name (ARN) of the Lambda function."
  value       = aws_lambda_function.this.arn
}

output "function_name" {
  description = "The name of the Lambda function."
  value       = aws_lambda_function.this.function_name
}

output "invoke_arn" {
  description = "The ARN to be used for invoking the Lambda function from API Gateway."
  value       = aws_lambda_function.this.invoke_arn
}

output "qualified_arn" {
  description = "The qualified ARN of the Lambda function including the version."
  value       = aws_lambda_function.this.qualified_arn
}

output "timeout" {
  description = "The function execution timeout in seconds."
  value       = aws_lambda_function.this.timeout
}

output "memory_size" {
  description = "The amount of memory in MB the function gets at runtime."
  value       = aws_lambda_function.this.memory_size
}
