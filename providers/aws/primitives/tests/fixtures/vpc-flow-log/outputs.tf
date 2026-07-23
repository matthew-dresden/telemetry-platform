output "vpc_id" {
  description = "ID of the fixture VPC."
  value       = aws_vpc.fixture.id
}

output "flow_log_role_arn" {
  description = "ARN of the IAM role used for VPC flow logs."
  value       = aws_iam_role.vpc_flow_log.arn
}

output "flow_log_kms_key_arn" {
  description = "ARN of the KMS key used to encrypt flow log CloudWatch log group."
  value       = aws_kms_key.flow_log.arn
}
