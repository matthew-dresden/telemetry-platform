# Example: ECS service without ALB, desired_count 1, autoscaling disabled.
# This is the minimal deployment scenario -- a single ADOT collector task on Fargate.
# Container configuration (ADOT config) is referenced from SSM at runtime, not baked into the image.
# Resource blocks are allowed in examples/ per no_resources_policy exclusion.

data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

# Fixture: IAM execution role -- allows ECS to pull image and write to CloudWatch Logs.
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "execution_role_policy" {
  statement {
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "ssm:GetParameters",
      "ssm:GetParameter",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "task_role_policy" {
  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-exec-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = merge(var.tags, {
    Name    = "${var.name}-exec-role"
    Purpose = "terratest-fixture"
  })
}

resource "aws_iam_role_policy" "execution" {
  name   = "${var.name}-exec-policy"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_role_policy.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json

  tags = merge(var.tags, {
    Name    = "${var.name}-task-role"
    Purpose = "terratest-fixture"
  })
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.name}-task-policy"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_role_policy.json
}

# Fixture: minimal VPC with two subnets for ECS networking.
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

resource "aws_subnet" "fixture_a" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = var.subnet_cidr_a
  availability_zone = data.aws_availability_zones.available.names[0]

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-subnet-a"
    Purpose = "terratest-fixture"
  })
}

resource "aws_subnet" "fixture_b" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = var.subnet_cidr_b
  availability_zone = data.aws_availability_zones.available.names[1]

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-subnet-b"
    Purpose = "terratest-fixture"
  })
}

resource "aws_security_group" "fixture" {
  name        = "${var.name}-fixture-sg"
  description = "Security group for ECS service fixture"
  vpc_id      = aws_vpc.fixture.id

  egress {
    description = "Allow outbound traffic within the fixture VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
  }

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-sg"
    Purpose = "terratest-fixture"
  })
}

# Fixture: ECS cluster to host the service.
resource "aws_ecs_cluster" "fixture" {
  name = "${var.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(var.tags, {
    Name    = "${var.name}-cluster"
    Purpose = "terratest-fixture"
  })
}

resource "aws_ecs_cluster_capacity_providers" "fixture" {
  cluster_name       = aws_ecs_cluster.fixture.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# SSM parameter for ADOT config -- per D44, config is referenced from SSM, not baked into the image.
resource "aws_ssm_parameter" "adot_config" {
  name  = "/${var.name}/adot/config"
  type  = "String"
  value = var.adot_config_value

  tags = merge(var.tags, {
    Name    = "/${var.name}/adot/config"
    Purpose = "terratest-fixture"
  })
}

# The container definition references ADOT config from SSM via environment variable, not baked.
locals {
  container_definitions = jsonencode([
    {
      name      = "adot-collector"
      image     = var.adot_image
      essential = true
      cpu       = 0
      environment = [
        {
          name  = "AOT_CONFIG_CONTENT"
          value = aws_ssm_parameter.adot_config.name
        }
      ]
      secrets = [
        {
          name      = "ADOT_CONFIG"
          valueFrom = aws_ssm_parameter.adot_config.arn
        }
      ]
      portMappings = [
        {
          containerPort = 4318
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/${var.name}"
          "awslogs-region"        = data.aws_region.current.region
          "awslogs-stream-prefix" = "adot"
        }
      }
    }
  ])
}

module "example" {
  source = "../../"

  name               = var.name
  cluster_arn        = aws_ecs_cluster.fixture.arn
  task_cpu           = var.task_cpu
  task_memory        = var.task_memory
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  container_definitions = local.container_definitions

  desired_count      = var.desired_count
  subnet_ids         = length(var.override_subnets) > 0 ? var.override_subnets : [aws_subnet.fixture_a.id, aws_subnet.fixture_b.id]
  security_group_ids = [aws_security_group.fixture.id]
  assign_public_ip   = false

  enable_autoscaling = var.enable_autoscaling

  create_log_group         = var.create_log_group
  log_group_retention_days = var.log_group_retention_days

  tags = var.tags

  depends_on = [aws_ecs_cluster_capacity_providers.fixture]
}
