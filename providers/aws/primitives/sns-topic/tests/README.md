# sns-topic -- tests overview

This directory contains Terratest functional tests for the sns-topic primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts topic_arn pattern, topic_name output, KMS encryption, zero subscriptions, required outputs, and error paths. |
| `with-subscriptions/` | `examples/with-subscriptions` | Asserts aws_sns_topic_subscription count is 1 and aws_sns_topic_policy count is 1. |
| `common/` | both | Asserts Terraform version, required outputs, validate, and idempotency across both examples. |

## Running tests

From the repo root:

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/sns-topic
```

## Self-contained examples

Both examples create an inline `aws_kms_key` resource scoped to the caller's AWS account
via `data "aws_caller_identity" "current"`. No external KMS key ARN is required.
The `with-subscriptions` example defaults to `subscriber_email = "terratest@example.com"`.

## Coverage

Tests must maintain Go coverage at or above the 90% floor configured in `monorepo-config.json`.

## Test account

Tests run against the sandbox AWS account. Credentials are supplied via `AWS_PROFILE=sandbox`.

## Required environment variables

| Variable | Description |
|----------|-------------|
| `PROJECT_TAG` | Project tag value injected into all resources (supplied by the FR-1 runner) |
| `TERRATEST_RUN_ID` | Unique run identifier injected into all resources (supplied by the FR-1 runner) |
