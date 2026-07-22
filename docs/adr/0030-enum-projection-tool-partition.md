# 0030. Governed enum projection for the tool partition

- Status: Accepted
- Era: Prod queryability

## Context

[ADR 0011](0011-parquet-lake-athena-partition-projection.md) resolved the lake's partitions
with Athena partition projection and chose the **injected** projection type for the `tool`
partition: its value set is unbounded and supplied per query. That choice carried a hard
constraint — every query had to pin `tool` with an equality or `IN` predicate, and an unbounded
scan or a `LIKE`/range predicate on `tool` was rejected at query time with
`CONSTRAINT_VIOLATION`.

This constraint surfaced as a real blocker once the lake was queried in anger. A bare
`SELECT count(*)` with no `tool` predicate — the natural first query an analyst or a dashboard
issues — failed rather than returning results, and downstream BI extract caches could not hold an
unfiltered result; every consumer was forced into an explicit per-query `tool IN (...)` constraint.
Every consumer had to know the set of `tool` values out of band before it could read anything,
and onboarding a new producer required teaching each query about the new value rather than
declaring it once.

## Decision

Switch the `tool` partition from the injected projection type to a **governed enum** projection.
The table declares `projection.tool.type = "enum"` with `projection.tool.values` set from an
input-driven variable (`glue_partition_projection_tool_values`) — the single governed list of
`tool` partition values: `e2e-smoke`, `kanon`, and (added later by
[ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)) `claude-code`, `claude-cowork`,
`claude-office`, `claude-other`.

Under enum projection Athena knows the full value set from the catalog, so an unfiltered
`SELECT` **succeeds** — it resolves and scans every governed partition — and pinning `tool` with
`=` or `IN` becomes a cost / scan-pruning **recommendation** rather than a hard requirement.
Onboarding a new producer is a declarative one-line change: add its `tool` value to the governed
list. The `dt` date projection and the derivation of the projection format and
`storage.location.template` from the Firehose prefix are unchanged from
[ADR 0011](0011-parquet-lake-athena-partition-projection.md).

Two alternatives were considered:

- Keep the injected projection and educate every consumer to always pin `tool`. Rejected: it
  makes the most common exploratory query fail by default, blocks unfiltered downstream extracts, and
  pushes value-set knowledge into every query instead of declaring it once in the catalog.
- Register real partitions with a Glue crawler. Rejected: [ADR 0011](0011-parquet-lake-athena-partition-projection.md)
  deliberately avoids operating a crawler; enum projection keeps partitions resolving from the
  key layout with no crawler while still bounding the value set.

## Consequences

An unfiltered `SELECT` over the table now returns rows instead of failing, so dashboards and
exploratory queries work without a mandatory `tool` predicate; pinning `tool` remains the way to
bound bytes scanned. Downstream extract caches can hold unfiltered results again.

The `tool` value set is now **governed and bounded**: only values in
`glue_partition_projection_tool_values` resolve, so a `tool` value written to S3 that is not in
the governed list is simply not queryable until the list is extended. The list therefore must be
kept in sync with the producers actually emitting — adding a new tool is the declarative act of
adding its value (as [ADR 0029](0029-dual-logs-pipeline-claude-reshape.md) did for the
`claude-*` values).

This decision **amends** [ADR 0011](0011-parquet-lake-athena-partition-projection.md): the
`projection.tool.type` and its "must pin `tool`" consequence are superseded by the enum
projection described here; the rest of ADR 0011 (Parquet lake, crawler-free projection, Firehose
prefix as the single source of truth) stands unchanged. This ADR records a decision already
implemented in the deployed lake; it is written to close the governance gap of that change
lacking its own record.

**Generalized by the tool-registry refactor.** `glue_partition_projection_tool_values` is no
longer maintained as a hand-edited list in terragrunt inputs; it is now **derived** from the
single [tool registry](../../terragrunt/common/tool-registry.json) (every registered tool's
`tool` value), and the `claude-other` catch-all value this ADR lists has been **removed** — see
[ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)'s amendment note and
[onboarding-a-tool.md](../onboarding-a-tool.md). The enum-projection mechanism this ADR
establishes is unchanged; only where the governed value list is authored has moved.

See the [ADR index](README.md).
