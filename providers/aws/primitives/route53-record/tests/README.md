# route53-record -- tests overview

This directory contains Terratest functional tests for the route53-record primitive module.

## Test suites

| Directory | Example | Description |
|-----------|---------|-------------|
| `basic/` | `examples/basic` | Asserts fqdn output format, required outputs, resource count, and error paths for invalid type and empty records. |

## Running tests

From the module root:

```bash
AWS_PROFILE=sandbox make tf-test
```

## Prerequisites

The live test suite requires only authenticated AWS credentials for the target account. Set
`AWS_PROFILE=sandbox` (or equivalent `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` environment
variables) before running `make tf-test`. No pre-provisioned hosted zone is required.

## Hosted zone fixture

The Route53 hosted zone is self-contained: `hostedZoneFixture` creates an ephemeral public
hosted zone (`tt-<nanosecond-timestamp>.example-terratest.net`) via the Route53
`CreateHostedZone` API and returns its zone ID and name. The nanosecond-unique apex label means
repeated or concurrent runs never collide, so the suite runs green in any clean account. A
`t.Cleanup` hook scrubs every non-default (non-NS, non-SOA) record and deletes the zone on both
pass and fail, leaving no orphaned resources.

## Coverage

Tests run against a live AWS account. The hosted zone is created and destroyed within the test
itself, so no external fixture provisioning is needed.

The `basic` example undergoes a real apply, assertion, and destroy cycle executed by the FR-1
runner (`make tf-test`). Because Go `t.Cleanup` runs in LIFO order and the zone fixture is
created before the example runs, the record's terraform destroy completes before the ephemeral
zone is deleted. Destroy is guaranteed on both pass and fail via `t.Cleanup`.
