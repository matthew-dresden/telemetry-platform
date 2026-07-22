# kms-key -- tests overview

This directory contains Terratest functional tests for the kms-key primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts key ARN format, alias prefix, resource counts, and error paths for invalid inputs. |
| `with-policy/` | `examples/with-policy` | Asserts key creation with custom policy and multi-region enabled. |
| `common/` | both | Asserts Terraform version, required outputs, validate, fmt, and idempotency across all examples. |

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
