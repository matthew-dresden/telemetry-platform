name = "telemetry-ecs-execution-basic"

assume_role_policy_json = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

description = "ECS tasks execution role -- basic example"

managed_policy_arns = [
  "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
]

tags = {
  Environment = "test"
  Purpose     = "iam-role-module-testing"
  Owner       = "terraform"
}

project_tag      = "telemetry-platform"
terratest_run_id = "offline-validate"
