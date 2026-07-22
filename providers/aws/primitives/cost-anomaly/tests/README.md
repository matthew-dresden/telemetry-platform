# cost-anomaly -- tests overview

This directory contains Terratest functional tests for the cost-anomaly primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `default/` | `examples/default` | Asserts monitor and subscription ARN patterns, resource counts, required outputs, Terraform version, idempotency, and error paths for invalid inputs. |

## Running tests

From the module root:

```bash
make tf-test
```

## Coverage

Tests must maintain Go coverage at or above the 90% floor configured in `monorepo-config.json`.

## Test account

Tests run against the QA AWS account via OIDC. The target account ID is supplied at runtime
via the `AWS_ACCOUNT_ID` environment variable and the OIDC role configured in the CI pipeline.

## Required environment variables

| Variable | Description |
|----------|-------------|
| `TF_VAR_sns_topic_arn` | SNS topic ARN wired as the anomaly subscription SNS subscriber |
| `TF_VAR_threshold_expression` | JSON cost-expression defining the spend anomaly threshold |
