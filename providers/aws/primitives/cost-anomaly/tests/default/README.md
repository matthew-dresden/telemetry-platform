# cost-anomaly default tests

Tests for `examples/default`: DIMENSIONAL anomaly monitor with DAILY frequency and an SNS subscriber.

Asserts:
- `aws_ce_anomaly_monitor` count is 1
- `aws_ce_anomaly_subscription` count is 1 with a non-empty subscriber wired to the SNS topic ARN
- `monitor_arn` matches `^arn:aws:ce::`
- `subscription_arn` matches `^arn:aws:ce::`
- Both required outputs (`monitor_arn`, `subscription_arn`) are present
- Terraform version satisfies the pinned constraint (1.15.5)
- Apply is idempotent
- Empty `monitor_name` causes a plan failure
- Empty `subscribers` list causes a plan failure
- Malformed `threshold_expression` causes a plan failure
