# iam-role -- basic example

This example demonstrates creating an IAM role with an ECS-tasks trust policy and one managed policy attachment.

## Usage

```hcl
module "iam_role" {
  source = "../../"

  name                    = "telemetry-ecs-execution"
  assume_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
  ]
}
```

## What this example creates

- One `aws_iam_role` with an ECS-tasks trust policy.
- One `aws_iam_role_policy_attachment` for the ECS task execution managed policy.

## Inputs

See `variables.tf` for all configurable inputs.

## Outputs

| Name | Description |
|------|-------------|
| `role_arn` | The Amazon Resource Name (ARN) of the IAM role. |
| `role_name` | The name of the IAM role. |
| `role_id` | The stable unique identifier for the IAM role. |
