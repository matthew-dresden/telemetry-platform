data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # KMS key policy -- root admin + SNS service principal access.
  kms_key_policy = jsonencode({
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
        Sid    = "SNSServiceAccess"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
    ]
  })

  topic_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowPublish"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action   = "SNS:Publish"
        Resource = module.example.topic_arn
      }
    ]
  })
}

# Customer-managed KMS key for SNS server-side encryption.
# Self-contained: created and destroyed with this example -- no external KMS ARN required.
resource "aws_kms_key" "sns" {
  description             = "CMK for ${var.topic_name} SNS server-side encryption (with-subscriptions example)"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = local.kms_key_policy

  tags = {
    Environment = "test"
    Purpose     = "sns-topic-module-with-subscriptions-example"
    Owner       = "terraform"
  }
}

module "example" {
  source = "../../"

  topic_name = var.topic_name
  kms_key_id = aws_kms_key.sns.arn
  subscribers = [
    {
      protocol = "email"
      endpoint = var.subscriber_email
    }
  ]
  policy_json = local.topic_policy
  tags        = var.tags
}

variable "topic_name" {
  type        = string
  description = "SNS topic name for the with-subscriptions example."
}

variable "subscriber_email" {
  type        = string
  description = "Email address for the SNS topic subscription."
  default     = "terratest@example.com"
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all resources."
  default     = {}
}

output "topic_arn" {
  description = "The ARN of the SNS topic."
  value       = module.example.topic_arn
}

output "topic_name" {
  description = "The name of the SNS topic."
  value       = module.example.topic_name
}
