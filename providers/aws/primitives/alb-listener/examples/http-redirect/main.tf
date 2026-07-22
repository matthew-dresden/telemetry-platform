# Fixture: VPC. Resource blocks are allowed in examples/ per no_resources_policy exclusion.
resource "aws_vpc" "fixture" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-vpc"
    Purpose = "terratest-fixture"
  })
}

# Fixture: VPC Flow Logs. Models secure usage so the static scan sees the VPC with
# flow logging enabled (clears trivy AWS-0178). Self-contained: the flow-log
# destination, encryption key, and IAM role are all created and destroyed with this
# example -- no external resources required.

# Customer-managed KMS key for the VPC Flow Logs CloudWatch log group. Encrypting the
# log group (clears trivy AWS-0017) rather than suppressing the finding.
resource "aws_kms_key" "fixture_flow_logs" {
  description             = "CMK for ${var.name} VPC Flow Logs CloudWatch log group (fixture)"
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

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-flow-logs-cmk"
    Purpose = "terratest-fixture"
  })
}

# CloudWatch Log Group for VPC Flow Logs (KMS-encrypted with the CMK above).
resource "aws_cloudwatch_log_group" "fixture_flow_logs" {
  name              = "/aws/vpc/flowlogs/${var.name}-fixture"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.fixture_flow_logs.arn

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-flow-logs"
    Purpose = "terratest-fixture"
  })
}

# IAM Role assumed by the VPC Flow Logs service to deliver logs.
resource "aws_iam_role" "fixture_flow_logs" {
  name = "${var.name}-fixture-flow-logs-role"

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

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-flow-logs-role"
    Purpose = "terratest-fixture"
  })
}

# IAM Policy granting the flow-log role permission to write to the log group.
resource "aws_iam_role_policy" "fixture_flow_logs" {
  name = "${var.name}-fixture-flow-logs-policy"
  role = aws_iam_role.fixture_flow_logs.id

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
        Resource = "${aws_cloudwatch_log_group.fixture_flow_logs.arn}:*"
      }
    ]
  })
}

# Flow log capturing all traffic for the fixture VPC.
resource "aws_flow_log" "fixture" {
  vpc_id          = aws_vpc.fixture.id
  iam_role_arn    = aws_iam_role.fixture_flow_logs.arn
  log_destination = aws_cloudwatch_log_group.fixture_flow_logs.arn
  traffic_type    = "ALL"

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-flow-log"
    Purpose = "terratest-fixture"
  })
}

# Fixture: Two subnets across different AZs (ALB requires >= 2).
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

# Fixture: Security group for the ALB.
resource "aws_security_group" "fixture_alb" {
  name        = "${var.name}-fixture-alb-sg"
  description = "Fixture security group for ALB listener test"
  vpc_id      = aws_vpc.fixture.id

  # Internal ALB: restrict egress to TCP within the fixture VPC so health checks can
  # reach the targets, while clearing trivy AWS-0104 (no unrestricted egress to any IP).
  egress {
    description = "Allow TCP egress to targets within the fixture VPC"
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.fixture.cidr_block]
  }

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-alb-sg"
    Purpose = "terratest-fixture"
  })
}

# Fixture: Internal ALB.
resource "aws_lb" "fixture" {
  name               = var.name
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.fixture_alb.id]
  subnets            = [aws_subnet.fixture_a.id, aws_subnet.fixture_b.id]

  enable_deletion_protection = false
  drop_invalid_header_fields = true

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-alb"
    Purpose = "terratest-fixture"
  })
}

# Fixture: Target group that the HTTPS listener forwards to.
resource "aws_lb_target_group" "fixture" {
  name        = "${var.name}-tg"
  port        = var.target_group_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.fixture.id

  health_check {
    path = "/healthz"
  }

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-tg"
    Purpose = "terratest-fixture"
  })
}

# Fixture: Generate a self-signed TLS certificate for the HTTPS listener.
# DNS-validated ACM certificates cannot be used in this sandbox environment because
# the domain example-terratest.net is not registered in the public DNS tree,
# so ACM's DNS validation infrastructure cannot resolve the Route53 CNAME records.
# Importing a self-signed cert into ACM provides an ISSUED cert immediately without
# requiring external DNS propagation.
resource "tls_private_key" "fixture" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "fixture" {
  private_key_pem = tls_private_key.fixture.private_key_pem

  subject {
    common_name  = var.certificate_domain
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

  tags = merge(var.tags, {
    Name    = "${var.name}-fixture-cert"
    Purpose = "terratest-fixture"
  })

  lifecycle {
    create_before_destroy = true
  }
}

module "example" {
  source = "../../"

  load_balancer_arn = aws_lb.fixture.arn

  listeners = [
    {
      name     = "http-redirect"
      port     = 80
      protocol = "HTTP"
      default_action = {
        type = "redirect"
        redirect = {
          port        = "443"
          protocol    = "HTTPS"
          status_code = "HTTP_301"
        }
      }
    },
    {
      name            = "https"
      port            = 443
      protocol        = "HTTPS"
      ssl_policy      = var.ssl_policy
      certificate_arn = aws_acm_certificate.fixture.arn
      default_action = {
        type             = "forward"
        target_group_arn = aws_lb_target_group.fixture.arn
      }
    }
  ]

  tags = var.tags
}
