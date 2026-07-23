# 0001. Record architecture decisions

- Status: Accepted
- Era: Initial IaC design

## Context

telemetry-platform is built from many interlocking architectural choices: the shape of
the ingest pipeline, the AWS account topology, the Terraform module taxonomy, the
state backend, the naming scheme, and the way the ingest edge is secured. These
decisions are made over the life of the platform, they constrain one another, and
some of them are later replaced. Without a durable record, the reasoning behind a
choice is lost once the people who made it move on, and a later change risks
reopening a question that was already settled or silently contradicting an earlier
constraint.

We need a single, version-controlled place that captures each significant
architectural decision, the context that forced it, and the consequences it
imposes, so that the decision and its rationale travel with the code.

## Decision

Record every significant architecture decision as a numbered Architecture Decision
Record (ADR), using the format described by Michael Nygard.

- ADRs live in `docs/adr/` and are named `NNNN-<slug>.md`, where `NNNN` is a
  zero-padded sequence number and `<slug>` is a short hyphenated title.
- Each ADR is a short markdown document with a `# NNNN. Title` heading, a `Status`
  and an `Era` field, and `Context`, `Decision`, and `Consequences` sections. The
  `Status` is `Accepted` or `Superseded`; the `Era` groups the decision into the
  phase of the platform's evolution in which it was made.
- ADRs are immutable once accepted. When a decision changes, a new ADR is added and
  the original is marked `Superseded` with a forward link to its successor; the
  original text is not rewritten or deleted.
- The [ADR index](README.md) lists every ADR in order with its title, era, and
  status, and is updated whenever an ADR is added or its status changes.
- This ADR is itself recorded in that format as a worked example.

## Consequences

The architectural history of the platform is captured as a numbered, append-only
series that can be read in order. A reader can see not only the current design but
the path that produced it, including the decisions that were tried and replaced.

- Adding or changing a decision means writing or amending an ADR and updating the
  index in the same change, so the record stays in sync with the system it
  describes.
- Because superseded decisions are retained rather than removed, the index grows
  monotonically and a stale link is never left dangling; a replaced decision is
  found by following the forward link from the record it supersedes.
- The `Status` and `Era` fields make it possible to read only the currently
  accepted set, or to read a single phase of the platform's evolution, without
  losing the historical record.

See the [ADR index](README.md).
