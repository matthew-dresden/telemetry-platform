# ssm-parameter -- tests overview

This directory contains Terratest functional tests for the ssm-parameter primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts parameter ARN format, resource count, parameter_version >= 1, required metadata outputs, idempotency, and error paths for invalid type and SecureString without kms_key_id. |
| `securestring/` | `examples/securestring` | Asserts SecureString creation, CMK binding (via kms_key_arn metadata output), section-4 path convention, and idempotency. Never reads the secret value. |
| `common/` | both | Asserts Terraform version, required metadata outputs (`parameter_arn`, `parameter_name`, `parameter_version`), validate, and idempotency across both examples. |

## Running tests

From the repo root:

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/ssm-parameter
```

## Coverage

Tests must maintain Go coverage at or above the 90% floor configured in `monorepo-config.json`.

## Test account

Tests run against the sandbox account via `AWS_PROFILE=sandbox`.

## Security note

No test in this suite reads the `value` output (which does not exist) or calls any AWS API to retrieve the parameter value. Tests assert only metadata: ARN, name, version, and KMS key ARN.
