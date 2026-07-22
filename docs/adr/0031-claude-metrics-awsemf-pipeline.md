# 0031. Claude-tool metrics via a dedicated awsemf pipeline

- Status: Accepted
- Era: Claude-tool ingestion

## Context

Claude tools (Claude Code, Cowork, Office) emit OTLP metrics — session, token, and cost
counters — to `/v1/metrics` in addition to the logs signal [ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)
already routes and reshapes. Until now the collector returned `404` for that signal:
[ADR 0003](0003-logs-only-otlp-pipeline.md) constrained the platform to logs only, and
[ADR 0029](0029-dual-logs-pipeline-claude-reshape.md) extended that decision to onboard
Claude's attribute-shaped log records but explicitly left the metrics signal deferred —
clients had to set `OTEL_METRICS_EXPORTER=none` so the SDK's metrics exporter would not error
against the missing receiver.

The goal was to land Claude tools' metrics in the same queryable lake as their logs — joinable
on `session_id` / `user_email` — without disturbing either logs pipeline (`raw_log=true` for
kanon and other flat-body producers, `raw_log=false` for structured Claude logs) or the
`cwl_split` Lambda's existing reshape behavior for either.

## Decision

Enable the OTLP metrics signal through a third, isolated ADOT pipeline, `metrics/claude`:

- Receivers `[otlp]` — adding a `metrics` pipeline to the rendered collector config is what
  makes the OTLP/HTTP receiver serve `/v1/metrics` (previously `404`). No traces pipeline is
  added, so `/v1/traces` stays `404`.
- Processors `[memory_limiter/metrics, filter/keep_claude_metrics, batch/metrics]` — dedicated
  `memory_limiter/metrics` and `batch/metrics` instances, never the logs pipelines' shared
  limiter, so a metrics burst cannot trip the shared limiter and drop kanon or Claude logs.
  `filter/keep_claude_metrics` reuses the same `claude_filter_keep_condition` OTTL expression
  the logs `filter/keep_claude` processor uses, evaluated in the metrics datapoint context, so
  the identical input-driven Claude service-name list gates both pipelines: an empty list
  renders the keep condition `false` for both, leaving the metrics pipeline fully inert until
  an environment opts in, exactly like the logs route.
- Exporters `[awsemf]` — the `awsemf` exporter writes CloudWatch EMF log events to a dedicated
  stream (`adot-collector-claude-metrics-emf`) inside the **same** ingest log group the logs
  pipelines already write to, so the existing group-scoped CloudWatch Logs subscription filter
  picks them up with no new log group and no new Firehose delivery stream.
  `resource_to_telemetry_conversion = true` carries `service.name` and other resource
  attributes into the EMF payload, so the `cwl_split` reshape can still map `service.name` to
  the `tool` value. `dimension_rollup_option = "NoDimensionRollup"` with a metric declaration
  of `dimensions = [[]]` emits one coarse, zero-dimension CloudWatch metric per instrument
  name — keeping CloudWatch Metrics cardinality and cost flat — while every high-cardinality
  attribute (`type`, `model`, `user.email`, `session.id`, …) still survives as an EMF log
  field, which is what the lake actually reads. The namespace is the input-driven
  `claude_metrics_emf_namespace` variable, not hardcoded.

`cwl_split` gains a third record shape. `_reshape_message` (flat-body producers) and the
existing Claude log reshape are unchanged; a new `_reshape_emf` branch recognizes an EMF event
(a dict with no top-level `tool` and a top-level `_aws` block carrying `CloudWatchMetrics`) and
emits one row per distinct metric name: `tool` resolves from `service.name` through the same
service-to-tool map the log reshape already uses (including its `claude-other` fallback),
`event_type` is the full instrument name (for example `claude_code.token.usage`), and `payload`
carries the metric's `value`, `unit`, `metric_type = "SUM"`, `otel_signal = "metric"` (the
metric-versus-log discriminator — log rows never carry this key), and every attribute flattened
dot-to-underscore with OTel resource keys `resource_`-prefixed, identical in shape to the log
reshape's attribute flatten.

Two alternatives were considered and rejected:

- An `awss3` exporter writing metrics directly to S3. Rejected: it bypasses the Firehose
  delivery, Glue schema, and Parquet conversion the rest of the lake depends on, so metric rows
  would need a second, parallel catalog and query path instead of landing in the existing
  `telemetry_events` table.
- An `awscloudwatch` metrics-only exporter (CloudWatch Metrics without EMF log events).
  Rejected: it publishes aggregated metric values to CloudWatch Metrics but never emits a
  corresponding log event, so nothing reaches CloudWatch Logs, the Firehose bridge, or the
  lake — it would give an operator a CloudWatch dashboard but not a queryable, joinable Athena
  row.

## Consequences

Metric rows land in the same `telemetry_events` table as every other record, under the same
governed `tool = claude-code` enum value the structured Claude logs pipeline already added
([ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)) — no new `tool` enum value is needed.
`payload.$.otel_signal = 'metric'` is the query-time discriminator between a metric row and a
log row from the same tool.

The `awsemf` exporter converts Claude Code's cumulative monotonic OTel sums into per-interval
deltas before emitting them, so every landed metric row is a delta, not a running total; Athena
consumers must `SUM(payload.$.value)` for a total and must never `MAX` it, or usage is silently
under-reported. See [data-model.md](../data-model.md#claude-tool-metric-records) for the row
shape and query examples.

The attribute flatten for metrics is unbounded, matching
[ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)'s decision for logs: identity attributes
(`user_email`, `session_id`, `user_id`, `organization_id`) are captured and retained under the
same data-classification and retention policy that already governs the reshaped Claude logs —
this is not a new PII category, and PII is retained rather than stripped because these are
first-party tools reporting into the platform's own lake.

`POST /v1/metrics` now returns `200`; `POST /v1/traces` still returns `404` — no traces
pipeline exists. This ADR extends [ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)'s
Claude-tool ingestion work to a second signal, and it further amends
[ADR 0003](0003-logs-only-otlp-pipeline.md)'s logs-only signal scope: the platform no longer
ingests exactly one OTLP signal. [ADR 0003](0003-logs-only-otlp-pipeline.md) and
[ADR 0029](0029-dual-logs-pipeline-claude-reshape.md) each gain an "Amended by" pointer to this
ADR, the same way [ADR 0011](0011-parquet-lake-athena-partition-projection.md) points forward
to [ADR 0030](0030-enum-projection-tool-partition.md). Traces remain the one OTLP signal this
platform does not accept.

See [data-model.md](../data-model.md#claude-tool-metric-records) and
[sending-telemetry.md](../sending-telemetry.md#sending-claude-tool-telemetry-claude-code-cowork-office)
for the producer- and consumer-facing detail this decision produces.

**Generalized by the tool-registry refactor.** `filter/keep_claude_metrics` is renamed
`filter/keep_structured_metrics` and the `metrics/claude` pipeline is renamed
`metrics/structured`, but both still reuse the SAME registry-derived keep condition the logs
route uses (see [ADR 0029](0029-dual-logs-pipeline-claude-reshape.md)'s amendment note); the
hardcoded input-driven Claude service-name list this ADR describes is now derived from the
single [tool registry](../../terragrunt/common/tool-registry.json), and the `claude-other`
fallback the reshape's service-to-tool map fell back to has been **removed** (an unmapped
`service.name` now fails that one EMF record fast to `errors/` instead). The `awsemf`/EMF
pipeline architecture, the CloudWatch namespace variable name (`claude_metrics_emf_namespace`,
kept as-is to avoid a namespace migration), and the `_reshape_emf` mechanism this ADR
establishes are otherwise unchanged. See [onboarding-a-tool.md](../onboarding-a-tool.md).

See the [ADR index](README.md).
