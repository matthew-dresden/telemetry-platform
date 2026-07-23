# sns-topic with-subscriptions tests

Tests for `examples/with-subscriptions`: KMS-encrypted SNS topic with an email subscription and a topic policy.

The example is self-contained -- it creates an inline `aws_kms_key` resource with an
explicit key policy (root admin + SNS service principal) derived at runtime from the
caller's account identity. The subscriber email defaults to `terratest@example.com` in
`terraform.tfvars`. No external KMS ARN or email address environment variable is required.

Asserts:
- `aws_sns_topic_subscription` count is 1
- `aws_sns_topic_policy` count is 1
- Apply is idempotent
