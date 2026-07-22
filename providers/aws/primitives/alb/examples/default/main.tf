# Fixture: VPC for the ALB. Resource blocks are allowed in examples/ per no_resources_policy exclusion.
resource "aws_vpc" "fixture" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-vpc"
    Purpose = "terratest-fixture"
  })
}

# Fixture: VPC flow logs delivered to a KMS-encrypted CloudWatch log group.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_iam_policy_document" "flow_log_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "flow_log_cloudwatch" {
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
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "flow_log" {
  description             = "KMS key for ${var.name} VPC flow log CloudWatch log group"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.flow_log_kms.json

  tags = merge(var.tags, {
    Name    = "${var.name}-flow-log-kms"
    Purpose = "terratest-fixture"
  })
}

resource "aws_iam_role" "flow_log" {
  name               = "${var.name}-flow-log-role"
  assume_role_policy = data.aws_iam_policy_document.flow_log_assume.json

  tags = merge(var.tags, {
    Name    = "${var.name}-flow-log-role"
    Purpose = "terratest-fixture"
  })
}

resource "aws_iam_role_policy" "flow_log" {
  name   = "${var.name}-flow-log-policy"
  role   = aws_iam_role.flow_log.id
  policy = data.aws_iam_policy_document.flow_log_cloudwatch.json
}

resource "aws_cloudwatch_log_group" "flow_log" {
  name              = "/aws/vpc/${var.name}-flow-logs"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.flow_log.arn

  tags = merge(var.tags, {
    Name    = "${var.name}-vpc-flow-logs"
    Purpose = "terratest-fixture"
  })
}

resource "aws_flow_log" "fixture" {
  vpc_id          = aws_vpc.fixture.id
  traffic_type    = "ALL"
  iam_role_arn    = aws_iam_role.flow_log.arn
  log_destination = aws_cloudwatch_log_group.flow_log.arn
}

# Fixture: Two subnets across different AZs (ALB requires >= 2 subnets in different AZs).
resource "aws_subnet" "fixture_a" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = var.subnet_cidr_a
  availability_zone = var.availability_zone_a

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-subnet-a"
    Purpose = "terratest-fixture"
  })
}

resource "aws_subnet" "fixture_b" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = var.subnet_cidr_b
  availability_zone = var.availability_zone_b

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-subnet-b"
    Purpose = "terratest-fixture"
  })
}

module "example" {
  source = "../../"

  name                       = var.name
  internal                   = var.internal
  vpc_id                     = aws_vpc.fixture.id
  subnet_ids                 = var.subnet_ids != null ? var.subnet_ids : [aws_subnet.fixture_a.id, aws_subnet.fixture_b.id]
  create_security_group      = var.create_security_group
  ingress_cidr_blocks        = var.ingress_cidr_blocks
  idle_timeout               = var.idle_timeout
  enable_deletion_protection = var.enable_deletion_protection
  target_groups              = var.target_groups
  tags                       = var.tags
}
