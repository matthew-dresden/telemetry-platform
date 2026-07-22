# route53-zone -- tests overview

This directory contains Terratest functional tests for the route53-zone primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts aws_route53_zone resource count==1, name_servers==4, zone_id starts with Z, required outputs non-empty, and error path for invalid zone_name. |

## Running tests

From the repository root:

```bash
AWS_PROFILE=sandbox make tf-test MODULE_PATH=providers/aws/primitives/route53-zone
```

## Prerequisites

The live test suite requires:

1. **Sandbox AWS credentials**: The test runner must be authenticated to the sandbox account.
   Set `AWS_PROFILE=sandbox` (or equivalent `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   environment variables) before running `make tf-test`.
2. **FR-3 tagging variables**: `PROJECT_TAG` and `TERRATEST_RUN_ID` must be set. These are
   injected automatically by the FR-1 runner (`scripts/run_terratest.py`) when invoked via
   the repo-root `make tf-test MODULE_PATH=...` target. Running the Go test binary directly
   without these variables causes `t.Fatal` with an actionable error message.

## Coverage

The `basic` example undergoes a real apply, assertion, and destroy cycle executed by the FR-1
runner (`make tf-test MODULE_PATH=providers/aws/primitives/route53-zone` from the repo root).
Destroy is guaranteed on both pass and fail via `t.Cleanup`.

Each test run generates a unique hosted zone name using the pattern
`tt-<unix-timestamp>.example-terratest.net` to avoid collisions across concurrent or
sequential runs. The suffix is derived from `time.Now().Unix()` at test invocation time.

The framework-default idempotency re-plan runs automatically when `TERRATEST_IDEMPOTENCY=true`
is set in `test.config`.
