# 0011. Parquet lake with Athena partition projection over an injected tool key

- Status: Accepted — the `tool` projection type is amended by [ADR 0030](0030-enum-projection-tool-partition.md)
- Era: Initial IaC design

## Context

The telemetry data lake must be queryable by tool and by date in Athena without operating a Glue crawler, and the tool dimension must not collide with the event data.

Firehose writes columnar Parquet objects to the lake bucket under a key layout that embeds two partitions: the tool (extracted by dynamic partitioning) and the record's arrival date. The on-disk layout is:

```text
s3://<bucket>/<prefix>/tool=<tool>/dt=<date>/...parquet
```

Two constraints shape the catalog design:

- Glue stores partition keys as additional descriptor columns. A name declared as both a data column and a partition key produces a table descriptor with duplicate columns, which Athena rejects with a duplicate-columns metadata error on every query. The tool value therefore has to come from the partition path, not from a data column.
- The table has no crawler and registers no partitions, so Athena has nothing to resolve partitions from at query time unless the partition layout is described declaratively.

The SerDe also has to match the columnar Parquet that Firehose writes. A JSON text SerDe reading binary Parquet returns every data column as NULL while the partition keys still resolve from the path, which can hide the failure.

## Decision

Store the lake as Parquet and resolve partitions with Glue partition projection over an injected tool key.

- The Glue table uses `ParquetHiveSerDe` with the MapredParquet input and output formats, matching the Parquet (SNAPPY) that Firehose's format conversion writes, so Athena reads the binary objects directly and returns the actual row values.
- `tool` and `dt` are declared as partition keys only and are disjoint from the data columns. The tool column is dropped from the data columns and read from the `tool=<x>/dt=<y>` path.
- Partition projection is enabled so partitions resolve from the S3 key layout without a crawler or registered partitions:

```hcl
"projection.enabled"        = "true"
"projection.tool.type"      = "injected"
"projection.dt.type"        = "date"
"projection.dt.format"      = <Firehose timestamp format>
"storage.location.template" = "s3://<bucket>/<prefix>/${tool}/${dt}"
```

- The `tool` partition uses the injected projection type: its set is unbounded and the value is supplied per query. (This projection type was later changed to a governed `enum` — see [ADR 0030](0030-enum-projection-tool-partition.md).)
- The `dt` partition uses a date projection whose format equals the Firehose timestamp format, with an input-driven range and interval.
- The `dt` format and the `storage.location.template` are derived from the Firehose prefix (the single source of truth for the on-disk layout), so the projection cannot drift from the path Firehose actually writes.

## Consequences

- Because the tool partition uses the injected projection type, every query must pin tool with an equality or `IN` predicate; an unbounded scan or a `LIKE` pattern on tool is rejected. Downstream consumers therefore issue an explicit `tool IN (...)` constraint rather than caching an unfiltered extract. ([ADR 0030](0030-enum-projection-tool-partition.md) later replaced the injected type with a governed `enum`, under which an unfiltered scan succeeds and pinning `tool` becomes a cost recommendation rather than a hard requirement.)
- There is no crawler to schedule or operate; partition resolution depends only on the key layout and the projection parameters.
- Deriving the projection format and storage location from the Firehose prefix keeps the catalog and the on-disk layout in lockstep.
- The catalog SerDe is coupled to the Parquet format Firehose writes; changing the write format requires changing the table SerDe in the same step.

## Amended by

[ADR 0030](0030-enum-projection-tool-partition.md) changes the `tool` partition's projection
type from `injected` to a governed `enum`. The reasons this ADR gave for injected projection
(crawler-free resolution, `tool` read from the path, Firehose prefix as the single source of
truth) all still hold; only the projection **type** and its "every query must pin `tool`"
consequence are superseded. The Parquet lake, SerDe coupling, and `dt` date projection described
here are unchanged.

See the [ADR index](README.md).
