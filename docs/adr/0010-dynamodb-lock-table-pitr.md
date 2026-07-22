# 0010. DynamoDB lock table with PITR for state locking

- Status: Superseded by ADR-s3-native-state-locking
- Era: Initial IaC design

## Context

Terraform applies that run concurrently against the same remote state can race and
corrupt that state. The initial IaC design therefore needed a mechanism to serialize
state mutations across the state-bootstrap backend so that only one apply can hold the
state at a time.

## Decision

Provision a dedicated DynamoDB lock table, with point-in-time recovery (PITR) enabled,
alongside the S3 state backend. The table was declared as a `dynamodb_table` resource
and referenced by the Terraform backend configuration to coordinate state locks.

## Consequences

Every backend gains an extra backing resource that must be created, secured, tagged, and
paid for in addition to the S3 state bucket. The lock table was later replaced by
S3-native conditional-write locking, which removes the separate DynamoDB resource from
the state-bootstrap reference.

## Superseded by

This decision was superseded by [S3-native state locking](0017-s3-native-state-locking.md).
The DynamoDB lock table is no longer provisioned by the state-bootstrap reference; the
single locking mechanism is now S3-native conditional-write locking configured at the
remote-state root.

See the [ADR index](README.md).
