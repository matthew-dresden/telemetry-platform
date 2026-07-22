# 0009. Terragrunt state backend hardening

- Status: Accepted
- Era: Initial IaC design

## Context

The Terraform remote-state S3 bucket that backs the Terragrunt configuration tree was under-hardened: it lacked the encryption, versioning, public-access, and access-logging guarantees expected of a bucket holding sensitive infrastructure state.

A second risk came from how the bucket name was generated. The original scheme shortened long namespaces by taking a fixed-length leading substring of the namespace. Because that substring is lossy, two distinct namespaces that share a common prefix could collapse to the same name, risking collisions on a resource whose name must be globally unique.

## Decision

Harden the state backend bucket and make its name collision-free.

- Encrypt all state objects with a customer-managed KMS key that has key rotation enabled.
- Enable object versioning so prior state revisions are retained.
- Apply a full public-access block (block public ACLs and policies, ignore public ACLs, restrict public buckets).
- Send server access logs to a dedicated access-log bucket.
- Tag every resource consistently, including a managed-by tag.

Replace the lossy substring shortener with a name composed of a namespace prefix plus an eight-character hash suffix derived from the full namespace. The hash suffix guarantees uniqueness while keeping the name within the S3 sixty-three-character limit, so names generated in CI are deterministic and collision-free.

## Consequences

The state backend is secure: state is encrypted with a rotating key, versioned, access-logged, and shielded by a public-access block, with consistent tagging across its resources. Bucket names are unique by construction, eliminating the collision risk that the substring shortener introduced.

See the [ADR index](README.md).
