# waf-webacl -- tests overview

This directory contains Terratest functional tests for the waf-webacl primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts web_acl_arn output format, required outputs, resource counts (one ACL, no logging when disabled), and error paths for invalid scope and default_action values. |

## Running tests

From the module root:

```bash
make tf-test
```

## Coverage

Tests run against a live AWS account and require the WAF to be provisioned in us-east-1
for CLOUDFRONT scope. The provider/region is configured via the example's versions.tf.

## Test account

Tests run against the QA AWS account via OIDC. The role ARN and region are configured
in the CI pipeline. Real-AWS apply/destroy is deferred to a separate operator deploy
backlog (per spec decision D4).
