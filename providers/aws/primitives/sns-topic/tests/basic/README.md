# sns-topic basic tests

Tests for `examples/basic`: KMS-encrypted SNS topic with no subscriptions.

The example is self-contained -- it creates an inline `aws_kms_key` resource with an
explicit key policy (root admin + SNS service principal) derived at runtime from the
caller's account identity. No external KMS ARN or `TF_VAR_kms_key_arn` is required.

Asserts:
- `topic_arn` matches `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`
- `topic_name` is non-empty and equals the provisioned topic name
- `aws_sns_topic` count is 1 with KMS encryption
- `aws_sns_topic_subscription` count is 0
- Both required outputs (`topic_arn`, `topic_name`) are present
- Invalid `topic_name` (characters outside `^[A-Za-z0-9_-]+`) causes a plan failure
- Invalid subscriber protocol causes a plan failure
- Malformed `policy_json` causes a plan failure
