output "parameter_arn" {
  description = "The Amazon Resource Name (ARN) of the SSM parameter."
  value       = aws_ssm_parameter.this.arn
}

output "parameter_name" {
  description = "The fully qualified name of the SSM parameter."
  value       = aws_ssm_parameter.this.name
}

output "parameter_version" {
  description = "The version of the SSM parameter."
  value       = aws_ssm_parameter.this.version
}
