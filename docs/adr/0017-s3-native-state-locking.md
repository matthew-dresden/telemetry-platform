# 0017. S3-native conditional-write state locking (use_lockfile)

- Status: Accepted
- Era: Copyable-tree refactor

## Context

Serializing concurrent Terraform applies against the same remote state previously
required a dedicated DynamoDB lock table provisioned alongside the S3 state bucket.
That table was a second backing resource per backend that had to be created, secured,
tagged, and paid for in addition to the state bucket itself.

Terraform and OpenTofu version 1.10 and later support S3-native state locking, where
the backend serializes mutations with a conditional-write lock file held in the same
S3 bucket as the state. The pinned toolchain satisfies that minimum version, so the
separate lock table is no longer needed to coordinate state locks.

## Decision

Enable S3-native conditional-write locking by setting `use_lockfile = true` on the
remote-state S3 backend at the Terragrunt root, and remove the DynamoDB lock table
from the state-bootstrap reference.

```hcl
remote_state {
  config = {
    use_lockfile = true
  }
}
```

S3-native locking becomes the single mechanism that serializes state mutations across
the backend; there is no longer a `dynamodb_table` resource in the state-bootstrap
configuration.

## Consequences

Each backend drops its separate DynamoDB lock table, so there is one fewer backing
resource to provision, secure, tag, and pay for. State locking now lives entirely in
the S3 backend. The remaining state-backend hardening — customer-managed KMS
encryption, object versioning, the public-access block, enforced TLS, and access
logging — is unchanged and stays in force.

## Supersedes

This decision supersedes [DynamoDB lock table with PITR for state locking](0010-dynamodb-lock-table-pitr.md).

See the [ADR index](README.md).
