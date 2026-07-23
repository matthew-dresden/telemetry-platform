# lambda -- tests overview

This directory contains Terratest functional tests for the lambda primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts function_arn output format, required outputs, resource count, and error paths for invalid package_type and invalid role ARN. |

## Running tests

From the module root:

```bash
make tf-test
```

## Coverage

Tests run against a live AWS account using real Terraform apply and destroy operations
against the sandbox account. The `basic` example performs a full apply->assert->destroy
cycle on every run via the FR-1 runner (`make tf-test`).

### S3 and Lambda zip fixture

The S3 bucket and artifact key are self-contained: `s3Fixture` generates a unique
bucket name via `generateUniqueBucketName` (prefix `tst-lambda-basic` plus a Unix
timestamp), creates the bucket, and uploads a minimal Python Lambda zip built
in-memory by `generateLambdaZip`. The artifact key is `lambda/test/index.zip`. A
`t.Cleanup` hook empties and deletes the bucket on both pass and fail to prevent
orphaned resources.

### IAM execution role fixture

The Lambda execution role is self-contained: `iamRoleFixture` creates an ephemeral
IAM role (name prefix `tst-lambda-exec` plus a nanosecond timestamp, under the
`/telemetry-platform-terratest/` path) with a `lambda.amazonaws.com` assume-role trust
policy and the AWS-managed `AWSLambdaBasicExecutionRole` policy attached. After
creation the fixture polls `GetRole` until the role propagates, then returns its ARN.
A `t.Cleanup` hook detaches the policy and deletes the role on both pass and fail.
No pre-provisioned role and no role-related environment variables are required; the
suite runs green in any clean account.

### Required environment variables

| Variable | Purpose | Format | Required? |
|----------|---------|--------|-----------|
| `AWS_DEFAULT_REGION` | AWS region for the target account | AWS region string (e.g. `us-east-1`) | Required (falls back to `AWS_REGION` if unset) |
| `AWS_REGION` | Alternate region variable accepted when `AWS_DEFAULT_REGION` is absent | AWS region string (e.g. `us-east-1`) | Required if `AWS_DEFAULT_REGION` is not set |
| `TERRATEST_RUN_ID` | Unique run identifier injected by the FR-1 runner for resource tagging and orphan-sweep scoping | Alphanumeric string | Injected automatically by `make tf-test` |
| `PROJECT_TAG` | Project tag value applied to all created resources | String (e.g. `telemetry-platform`) | Injected automatically by `make tf-test` |
