data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
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
  alb_name            = "${local.name}-alb"
  tg_name             = "${local.name}-tg"

  # Trust policy for ECS task execution role.
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

  # Container definition for the ADOT-shaped service.
  # Config is injected via SSM (AOT_CONFIG_CONTENT) at task start -- not baked into the image.
  container_definitions = jsonencode([
    {
      name      = local.service_name
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      cpu       = 512
      memory    = 1024
      essential = true
      portMappings = [
        {
          containerPort = 4318
          protocol      = "tcp"
        }
      ]
      environment = []
      secrets = [
        {
          name      = "AOT_CONFIG_CONTENT"
          valueFrom = "/telemetry/${var.env}/ingest/adot-config"
        }
      ]
    }
  ])

  # Full docs/terragrunt-concepts.md ingest SSM inventory per ADOT collector service.
  # adot-config is SecureString bound to the telemetry-config CMK per docs/terragrunt-concepts.md.
  ssm_parameters = {
    "adot-config" = {
      type       = "SecureString"
      value      = var.adot_config_content
      kms_key_id = aws_kms_key.telemetry_config.id
    }
    "waf-rate-limit" = {
      type  = "String"
      value = var.waf_rate_limit
    }
    "otlp-max-body-bytes" = {
      type  = "String"
      value = var.otlp_max_body_bytes
    }
    "public-client-id" = {
      type  = "String"
      value = var.public_client_id
    }
  }

  # Self-signed ACM certificate ARN for HTTPS listener fixture.
  # DNS-validated ACM certificates cannot be used in the sandbox environment because the test
  # domain is not registered in the public DNS tree, so ACM DNS validation cannot resolve.
  # Importing a self-signed cert into ACM provides an ISSUED certificate immediately without
  # requiring external DNS propagation. The tls provider generates the key and cert in-process.
  certificate_arn = aws_acm_certificate.fixture.arn
}

# Fixture: self-signed TLS certificate imported into ACM so the HTTPS listener has an ISSUED cert
# without requiring an externally registered domain or Route53 DNS validation.
# Following the pattern used by providers/aws/primitives/alb-listener/examples/https/main.tf.
resource "tls_private_key" "fixture" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "fixture" {
  private_key_pem = tls_private_key.fixture.private_key_pem

  subject {
    common_name  = "terratest-fixture.example.internal"
    organization = "Terratest Fixture"
  }

  validity_period_hours = 720 # 30 days

  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

resource "aws_acm_certificate" "fixture" {
  private_key      = tls_private_key.fixture.private_key_pem
  certificate_body = tls_self_signed_cert.fixture.cert_pem

  tags = merge(local.tags, { Name = "${local.name}-fixture-cert" })

  lifecycle {
    create_before_destroy = true
  }
}

# telemetry-config CMK -- used for SecureString SSM parameters per docs/terragrunt-concepts.md.
# The fixture creates this key so Terratest can assert kms_key_id is non-null on the adot-config param.
resource "aws_kms_key" "telemetry_config" {
  description             = "telemetry-config CMK for SSM SecureString parameters (fixture)"
  deletion_window_in_days = 7
  enable_key_rotation     = true

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
        Sid    = "AllowCloudWatchLogs"
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
      }
    ]
  })

  tags = local.tags
}

resource "aws_kms_alias" "telemetry_config" {
  name          = "alias/${local.name}-telemetry-config"
  target_key_id = aws_kms_key.telemetry_config.key_id
}

# The with-alb example provisions its own ECS cluster and execution role as fixtures.
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

# Minimal VPC fixtures for subnets and security groups.
resource "aws_vpc" "fixture" {
  cidr_block = "10.1.0.0/16"

  tags = merge(local.tags, { Name = "${local.name}-vpc" })
}

# VPC Flow Logs to CloudWatch Logs so VPC traffic is auditable.
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/vpc/${local.name}/flow-logs"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.telemetry_config.arn

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
  cidr_block        = "10.1.1.0/24"
  availability_zone = "${local.region}a"

  tags = merge(local.tags, { Name = "${local.name}-subnet-a" })
}

resource "aws_subnet" "b" {
  vpc_id            = aws_vpc.fixture.id
  cidr_block        = "10.1.2.0/24"
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

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb-sg"
  description = "Security group for ALB fixture"
  vpc_id      = aws_vpc.fixture.id

  ingress {
    description = "HTTPS from within the fixture VPC to the internal ALB"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.fixture.cidr_block]
  }

  egress {
    description = "Allow outbound traffic within the fixture VPC only (internal ALB to targets)"
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
  task_cpu      = 512
  task_memory   = 1024

  log_group_kms_key_arn = aws_kms_key.telemetry_config.arn

  subnet_ids         = [aws_subnet.a.id, aws_subnet.b.id]
  security_group_ids = [aws_security_group.service.id]
  vpc_id             = aws_vpc.fixture.id

  enable_autoscaling = true
  autoscaling = {
    min_capacity       = 1
    max_capacity       = 4
    cpu_target_percent = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }

  alb = {
    name                   = local.alb_name
    internal               = true
    alb_subnet_ids         = [aws_subnet.a.id, aws_subnet.b.id]
    alb_security_group_ids = [aws_security_group.alb.id]
    create_security_group  = false
    ingress_cidr_blocks    = []
    idle_timeout           = 60
    target_groups = [
      {
        name        = local.tg_name
        port        = 4318
        protocol    = "HTTP"
        target_type = "ip"
        health_check = {
          path                = "/health"
          port                = "traffic-port"
          protocol            = "HTTP"
          healthy_threshold   = 3
          unhealthy_threshold = 3
          interval            = 30
          timeout             = 5
          matcher             = "200"
        }
      }
    ]
  }

  alb_listeners = {
    listeners = [
      {
        name            = "https"
        port            = 443
        protocol        = "HTTPS"
        ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
        certificate_arn = local.certificate_arn
        default_action = {
          type             = "forward"
          target_group_arn = null
        }
      }
    ]
    listener_rules = []
  }

  ssm_parameters = local.ssm_parameters
  env            = var.env

  tags = local.tags

  depends_on = [module.cluster, module.execution_role, aws_kms_key.telemetry_config, aws_acm_certificate.fixture]
}

# -- Re-export outputs. --

output "service_arn" {
  description = "The ARN of the ECS service."
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
  description = "The DNS name of the Application Load Balancer."
  value       = module.example.alb_dns_name
}

output "alb_zone_id" {
  description = "The canonical hosted zone ID of the ALB."
  value       = module.example.alb_zone_id
}

output "target_group_arns" {
  description = "Map of target group name to ARN."
  value       = module.example.target_group_arns
}

output "https_listener_arn" {
  description = "The ARN of the HTTPS listener."
  value       = module.example.https_listener_arn
}

output "ssm_parameter_arns" {
  description = "Map of SSM parameter logical key to ARN."
  value       = module.example.ssm_parameter_arns
}

output "autoscaling_target_resource_id" {
  description = "The Application Auto Scaling resource ID."
  value       = module.example.autoscaling_target_resource_id
}
