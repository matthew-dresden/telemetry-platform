data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  region     = data.aws_region.current.name
  account_id = data.aws_caller_identity.current.account_id
  name       = var.name

  # Fully-qualified 6-field namespace (product-region-env-envinstance-service-serviceinstance)
  # passed to the reference so it derives SET-SCOPED SSM parameter paths. The unique per-run
  # fixture name is embedded as the service field so serial runs never collide on the path.
  namespace = "telemetry-useast1-qa-000-${local.name}-000"

  tags = merge(var.tags, {
    Purpose = "terratest-fixture"
  })

  # Derive resource names from the name input so nothing is hard-coded.
  cluster_name        = "${local.name}-cluster"
  execution_role_name = "${local.name}-exec-role"
  task_role_name      = "${local.name}-task-role"
  service_name        = local.name

  # Task role trust policy allowing ecs-tasks to assume the execution role.
  execution_role_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  # Minimal container definition referencing SSM for config (not baked into the image).
  container_definitions = jsonencode([
    {
      name         = local.service_name
      image        = "public.ecr.aws/amazonlinux/amazonlinux:2"
      cpu          = 256
      memory       = 512
      essential    = true
      environment  = []
      portMappings = []
    }
  ])

  # Single SSM parameter for the basic example -- one String param for testing.
  ssm_parameters = {
    "public-client-id" = {
      type  = "String"
      value = "${local.name}-client"
    }
  }
}

# The basic example provisions its own ECS cluster and execution role as fixtures
# because the ecs-app-cluster reference depends on E1-F4-S2-T1 (separate task).
# Using primitives directly here avoids a circular dependency in the fixture.

module "execution_role" {
  source = "../../../../primitives/iam-role"

  name                    = local.execution_role_name
  assume_role_policy_json = local.execution_role_assume_policy
  description             = "Shared ECS task execution role for fixture cluster ${local.cluster_name}"
  managed_policy_arns     = ["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"]

  tags = local.tags
}

module "cluster" {
  source = "../../../../primitives/ecs-cluster"

  name                               = local.cluster_name
  capacity_providers                 = ["FARGATE"]
  default_capacity_provider_strategy = []
  enable_container_insights          = true

  tags = local.tags
}

# CMK encrypting the ECS service CloudWatch log group (and the VPC flow-logs group)
# at rest. The key policy must grant CloudWatch Logs (logs.<region>.amazonaws.com)
# permission to use the key, scoped by the kms:EncryptionContext:aws:logs:arn
# condition to this account/region's log groups; without it CreateLogGroup with a
# kms_key_id fails with AccessDeniedException ("KMS key ... is not allowed to be used").
resource "aws_kms_key" "logs" {
  description             = "CMK for ${local.name} ECS service log group encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAdminAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = ["kms:*"]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsAccess"
        Effect = "Allow"
        Principal = {
          Service = "logs.${local.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:log-group:*"
          }
        }
      },
    ]
  })

  tags = local.tags
}

# Minimal VPC fixtures for subnet and security group.
resource "aws_vpc" "fixture" {
  cidr_block = "10.0.0.0/16"

  tags = merge(local.tags, { Name = "${local.name}-vpc" })
}

# VPC Flow Logs to CloudWatch Logs so VPC traffic is auditable.
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/vpc/${local.name}/flow-logs"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.logs.arn

  tags = local.tags
}

resource "aws_iam_role" "flow_logs" {
  name = "${local.name}-vpc-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "vpc-flow-logs.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = local.tags
}

resource "aws_flow_log" "fixture" {
  iam_role_arn    = aws_iam_role.flow_logs.arn
  log_destination = aws_cloudwatch_log_group.flow_logs.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.fixture.id

  tags = merge(local.tags, { Name = "${local.name}-flow-log" })
}

resource "aws_subnet" "a" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "${local.region}a"

  tags = merge(local.tags, { Name = "${local.name}-subnet-a" })
}

resource "aws_subnet" "b" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "${local.region}b"

  tags = merge(local.tags, { Name = "${local.name}-subnet-b" })
}

resource "aws_security_group" "service" {
  name        = "${local.name}-svc-sg"
  description = "Security group for ECS service fixture"
  vpc_id      = aws_vpc.fixture.id

  egress {
    description = "Allow outbound traffic within the fixture VPC only"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.fixture.cidr_block]
  }

  tags = local.tags
}

module "example" {
  source = "../../"

  namespace             = local.namespace
  service_name          = local.service_name
  cluster_arn           = module.cluster.cluster_arn
  execution_role_arn    = module.execution_role.role_arn
  task_role_name        = local.task_role_name
  container_definitions = local.container_definitions

  desired_count = 1
  task_cpu      = 256
  task_memory   = 512

  log_group_kms_key_arn = aws_kms_key.logs.arn

  subnet_ids         = [aws_subnet.a.id, aws_subnet.b.id]
  security_group_ids = [aws_security_group.service.id]

  ssm_parameters = local.ssm_parameters
  env            = var.env

  # No ALB in the basic example.
  alb           = null
  alb_listeners = null

  tags = local.tags

  depends_on = [module.cluster, module.execution_role]
}

# -- Re-export outputs to prove the wiring is not dangling. --

output "service_arn" {
  description = "The ARN of the ECS service (proves service_arn re-export is non-dangling)."
  value       = module.example.service_arn
}

output "service_name" {
  description = "The name of the ECS service (proves service_name re-export is non-dangling)."
  value       = module.example.service_name
}

output "task_definition_arn" {
  description = "The ARN of the active task definition revision."
  value       = module.example.task_definition_arn
}

output "task_role_arn" {
  description = "The ARN of the per-service ECS task IAM role."
  value       = module.example.task_role_arn
}

output "alb_dns_name" {
  description = "The DNS name of the ALB. Null in the basic example."
  value       = module.example.alb_dns_name
}

output "alb_zone_id" {
  description = "The canonical hosted zone ID of the ALB. Null in the basic example."
  value       = module.example.alb_zone_id
}

output "target_group_arns" {
  description = "Map of target group name to ARN. Empty in the basic example."
  value       = module.example.target_group_arns
}

output "https_listener_arn" {
  description = "The ARN of the HTTPS listener. Null in the basic example."
  value       = module.example.https_listener_arn
}

output "ssm_parameter_arns" {
  description = "Map of SSM parameter logical key to ARN."
  value       = module.example.ssm_parameter_arns
}
