# 0003. Logs-only telemetry pipeline

- Status: Accepted — amended by [ADR 0029](0029-dual-logs-pipeline-claude-reshape.md) and
  [ADR 0031](0031-claude-metrics-awsemf-pipeline.md)
- Era: Initial IaC design

## Context

OTLP defines three signal types: logs, metrics, and traces. The platform exists
to capture discrete usage events from client tools, and each such event maps
cleanly onto a single OTLP log record.

Supporting metrics or traces alongside logs would introduce additional record
shapes and additional collector pipelines. Each new signal type needs its own
receiver path, its own exporter, and its own downstream store, and the catalog
schema would have to accommodate more than one record layout. Accepting OTLP over
gRPC in addition to HTTP would likewise add a second receiver surface to operate
and secure for no gain to the usage-analytics goal.

## Decision

Every usage event is modeled as a single OTLP log record carried over one HTTP
logs *signal*. The collector declares a `logs` pipeline (OTLP/HTTP receiver to
memory-limiter and batch processors to the CloudWatch Logs exporter) for this
purpose, and the design deliberately excludes:

- any `metrics` pipeline,
- any metrics endpoint or remote-write exporter,
- any OTLP gRPC receiver (OTLP/HTTP only).

## Consequences

A single, uniform record shape flows end to end for producers that follow the flat
body contract described here, from the client SDK through CloudWatch Logs,
Firehose, and Parquet to Athena. The `tool` partition key and the
`timestamp`, `event_type`, and `payload` columns populate consistently because
there is exactly one layout for those producers to map.

There is one receiver surface and one exporter to operate and secure rather than
several. Adding metrics or traces later is a separate, explicit decision: it would
require new pipelines, a new downstream store, and catalog changes rather than an
incremental extension of this one. For the end-to-end flow this record shape
travels, see [architecture.md](../architecture.md).

## Amended by

This decision originally scoped the platform to exactly one OTLP signal (logs), with no
metrics or trace receiver and no gRPC listener. Two later ADRs narrow that scope:

[ADR 0029](0029-dual-logs-pipeline-claude-reshape.md) refines the "single,
uniform record shape" consequence above for Claude tool telemetry (Claude Code,
Cowork, Office): those tools emit an attribute-shaped OTLP record rather than
this ADR's flat body contract, so the platform routes them through a second
`logs` pipeline and reshapes them into the same four-field envelope in the
`cwl_split` Lambda. kanon and other flat-body producers are unaffected and
continue to travel the single pipeline this ADR describes, byte-for-byte
unchanged. The signal itself was still logs only, now carried by two pipelines.

[ADR 0031](0031-claude-metrics-awsemf-pipeline.md) further amends the logs-only signal scope
itself: Claude tools' OTLP **metrics** signal (session, token, and cost counters) is now also
ingested, through a dedicated `metrics/claude` pipeline exported as CloudWatch EMF and reshaped
into the same event envelope and the same `telemetry_events` table. `POST /v1/metrics` now
returns `200`. This ADR's exclusion of a traces receiver and a gRPC listener is unchanged:
`POST /v1/traces` still returns `404` and there is no OTLP gRPC receiver.

See the [ADR index](README.md).
