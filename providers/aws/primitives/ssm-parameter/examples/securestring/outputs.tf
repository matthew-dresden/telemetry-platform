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

output "kms_key_arn" {
  description = "The ARN of the fixture KMS CMK used to encrypt the SecureString parameter."
  value       = aws_kms_key.telemetry_config.arn
}
