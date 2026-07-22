# -- VPC Flow Log supporting resources --
# These resources exist in the example fixture because the reference module
# root cannot declare resource blocks (no_resources_policy). The IAM role
# and CloudWatch log group ARNs are passed as inputs to vpc-network so that
# VPC Flow Logs are enabled and AWS-0178 is satisfied without suppression.

data "aws_iam_policy_document" "vpc_flow_log_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "vpc_flow_log_cloudwatch" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "flow_log_kms" {
  statement {
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "flow_log" {
  description             = "KMS key for ${local.name} VPC flow log CloudWatch log group"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.flow_log_kms.json

  tags = merge(local.tags, {
    Name = "${local.name}-flow-log-kms"
  })
}

resource "aws_iam_role" "vpc_flow_log" {
  name               = "${local.name}-vpc-flow-log-role"
  assume_role_policy = data.aws_iam_policy_document.vpc_flow_log_assume.json

  tags = merge(local.tags, {
    Name = "${local.name}-vpc-flow-log-role"
  })
}

resource "aws_iam_role_policy" "vpc_flow_log" {
  name   = "${local.name}-vpc-flow-log-policy"
  role   = aws_iam_role.vpc_flow_log.id
  policy = data.aws_iam_policy_document.vpc_flow_log_cloudwatch.json
}

resource "aws_cloudwatch_log_group" "vpc_flow_log" {
  name              = "/aws/vpc/${local.name}-flow-logs"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.flow_log.arn

  tags = merge(local.tags, {
    Name = "${local.name}-vpc-flow-logs"
  })
}
