# s3-bucket -- tests overview

This directory contains Terratest functional tests for the s3-bucket primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts bucket ARN format, public access block count, encryption config present, required outputs, and error paths for invalid inputs (invalid transition class, SSE without KMS key, expiration before transition, incomplete block-public-access). |
| `data-lake/` | `examples/data-lake` | Asserts lifecycle configuration resource count is 1, lifecycle days and storage class match terraform.tfvars, and idempotency. |
| `common/` | both | Asserts Terraform version, required outputs, no public-access bucket resource, and idempotency across both examples. Required because the module ships more than one example. |

## Running tests

From the repo root:

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/s3-bucket
```

## Coverage

Tests must maintain Go coverage at or above the 90% floor configured in `monorepo-config.json`.

## Test account

Tests run against the sandbox AWS account. Both examples (`basic` and `data-lake`) are self-contained:
each example creates its own KMS CMK inline (with a 7-day deletion window) and destroys it via
`terraform destroy` at the end of the test run. No external KMS key ARN is required.
