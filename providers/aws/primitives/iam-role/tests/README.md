# iam-role -- tests overview

This directory contains Terratest functional tests for the iam-role primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts role ARN format, resource counts (1 role + 1 managed policy attachment), required outputs, and error paths for malformed trust JSON and over-length role name. |
| `with-inline/` | `examples/with-inline` | Asserts role creation with Firehose trust and exactly 2 inline policies, and error path for malformed inline policy JSON. |
| `common/` | both | Asserts Terraform version, required outputs, validate, fmt, and idempotency across both examples. |

## Running tests

From the module root:

```bash
make tf-test
```

## Coverage

Tests must maintain Go coverage at or above the 90% floor configured in `monorepo-config.json`.

## Test account

Tests run against QA account `333333333333` via OIDC.
