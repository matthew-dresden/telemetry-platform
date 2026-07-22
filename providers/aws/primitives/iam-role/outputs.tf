output "role_arn" {
  description = "The Amazon Resource Name (ARN) of the IAM role."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "The name of the IAM role."
  value       = aws_iam_role.this.name
}

output "role_id" {
  description = "The stable and unique string identifying the IAM role."
  value       = aws_iam_role.this.unique_id
}
