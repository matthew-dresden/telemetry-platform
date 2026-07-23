output "workgroup_id" {
  description = "The name of the Athena workgroup (used as its identifier)."
  value       = aws_athena_workgroup.this.id
}

output "workgroup_arn" {
  description = "The Amazon Resource Name (ARN) of the Athena workgroup."
  value       = aws_athena_workgroup.this.arn
}

output "workgroup_name" {
  description = "The name of the Athena workgroup."
  value       = aws_athena_workgroup.this.name
}
