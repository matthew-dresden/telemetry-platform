output "parameter_arn" {
  description = "The Amazon Resource Name (ARN) of the SSM parameter."
  value       = module.example.parameter_arn
}

output "parameter_name" {
  description = "The fully qualified name of the SSM parameter."
  value       = module.example.parameter_name
}

output "parameter_version" {
  description = "The version of the SSM parameter."
  value       = module.example.parameter_version
}
