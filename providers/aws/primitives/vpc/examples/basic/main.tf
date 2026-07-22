# Customer-managed KMS key for the VPC Flow Logs CloudWatch log group.
# Self-contained: created and destroyed with this example -- no external KMS ARN
# required. Models secure usage so the static scan sees the flow-log destination
# log group encrypted (clears trivy AWS-0017) rather than suppressing the finding.
resource "aws_kms_key" "flow_logs" {
  count                   = var.enable_flow_logs ? 1 : 0
  description             = "CMK for ${var.name} VPC Flow Logs CloudWatch log group (basic example)"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAdminAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = ["kms:*"]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsAccess"
        Effect = "Allow"
        Principal = {
          Service = "logs.${data.aws_region.current.name}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
    ]
  })

  tags = var.tags
}

# CloudWatch Log Group for VPC Flow Logs (KMS-encrypted with the CMK above).
resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  count             = var.enable_flow_logs ? 1 : 0
  name              = "/aws/vpc/flowlogs/${var.name}"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.flow_logs[0].arn
  tags              = var.tags
}

# IAM Role for VPC Flow Logs
resource "aws_iam_role" "vpc_flow_logs" {
  count = var.enable_flow_logs ? 1 : 0
  name  = "${var.name}-vpc-flow-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

# IAM Policy for VPC Flow Logs
resource "aws_iam_role_policy" "vpc_flow_logs" {
  count = var.enable_flow_logs ? 1 : 0
  name  = "${var.name}-vpc-flow-logs-policy"
  role  = aws_iam_role.vpc_flow_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Effect   = "Allow"
        Resource = "${aws_cloudwatch_log_group.vpc_flow_logs[0].arn}:*"
      }
    ]
  })
}

module "example" {
  source = "../../"

  name                                 = var.name
  cidr_block                           = var.cidr_block
  instance_tenancy                     = var.instance_tenancy
  enable_dns_support                   = var.enable_dns_support
  enable_dns_hostnames                 = var.enable_dns_hostnames
  enable_network_address_usage_metrics = var.enable_network_address_usage_metrics
  assign_generated_ipv6_cidr_block     = var.assign_generated_ipv6_cidr_block
  enable_flow_logs                     = var.enable_flow_logs
  flow_logs_iam_role_arn               = var.enable_flow_logs ? aws_iam_role.vpc_flow_logs[0].arn : null
  flow_logs_destination_arn            = var.enable_flow_logs ? aws_cloudwatch_log_group.vpc_flow_logs[0].arn : null
  flow_logs_traffic_type               = var.flow_logs_traffic_type
  tags                                 = var.tags
}
