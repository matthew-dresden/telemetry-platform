# 0005. Two-tier module taxonomy: primitives and references

- Status: Accepted
- Era: Initial IaC design

## Context

The deployment defines its infrastructure as Terraform modules under `providers/aws/`.
If every deployable unit composed AWS resources directly, the same wiring — provider
plumbing, resource blocks, and naming for shared concerns such as KMS keys, S3 buckets,
and ECS services — would be repeated in each unit. Per-unit composition duplicates that
wiring and gives no clear place for the difference between owning a resource and assembling
resources into a surface.

A taxonomy is needed that separates the ownership of individual AWS resources from the
composition of those resources into deployable surfaces, without proliferating layers.

## Decision

Modules are organized into exactly two tiers, with no third "collection" tier:

- A **primitive** (`providers/aws/primitives/<name>/`) wraps a single AWS concern — for
  example `kms-key`, `s3-bucket`, `ecs-service`, or `cloudfront-distribution`. It owns the
  `resource` blocks for that concern and composes no other modules.
- A **reference** (`providers/aws/references/<name>/`) is a composition unit. It declares no
  `resource` blocks of its own; it wires primitives (and external modules) together into a
  deployable surface such as `collector-ingestion`, `data-lake`, or `observability`.

Each Terragrunt leaf sources exactly one reference module — one module per unit. References
source their child modules through `const`-typed `*_source` variables whose default is an
in-repo relative path.

## Consequences

The boundary between resource ownership and composition is explicit: primitives are the only
place `resource` blocks live, and references are the only place primitives are assembled.
Primitives are reusable across many references, and references stay thin wiring units. Adding
a deployable surface means adding one reference that composes existing primitives, rather than
re-declaring resources. The flat two-tier shape keeps the taxonomy easy to reason about, at
the cost of disallowing intermediate grouping tiers; surfaces that share structure express it
by reusing the same primitives rather than by introducing a new layer.

See the [ADR index](README.md).
