# 0029. Dual logs pipeline with a Claude-tool reshape in cwl_split

- Status: Accepted — the metrics-deferred consequence below is amended by
  [ADR 0031](0031-claude-metrics-awsemf-pipeline.md)
- Era: Claude-tool ingestion

## Context

Anthropic's Claude tools (Claude Code, Cowork, Office) emit OpenTelemetry natively, but not in
the flat body contract this platform expects. Every producer up to this point — kanon and other
in-house CLIs — sends a single OTLP `LogRecord` whose body is a JSON string carrying
`{timestamp, tool, event_type, payload}` ([ADR 0003](0003-logs-only-otlp-pipeline.md)). Claude
tools instead emit attribute-shaped OTLP: the `LogRecord` body is the event name itself (for
example `plugin_loaded`), and the event's data lives in the log-record and resource attributes
(`plugin.name`, `mcp.server_name`, `user.email`, `model`, and dozens more), not in a JSON body
object.

Ingesting Claude tools through the existing raw pipeline unmodified would either drop the event
content (the body string alone carries no usable payload) or require changing the raw exporter
to accommodate the new shape, which would put kanon's byte-identical raw path at risk. The
platform needed to onboard Claude Code, Cowork, and Office telemetry without disturbing the
flat-body contract that kanon and every other existing producer rely on.

## Decision

Route on `resource.attributes["service.name"]` at the collector, through a second `logs`
pipeline within the same OTLP logs signal — [ADR 0003](0003-logs-only-otlp-pipeline.md)'s
logs-only decision is unchanged; the signal is still logs only, now carried by two pipelines.

A new `filter/keep_claude` processor matches records whose `service.name` is in a configured
list (`claude-code`, `claude-cowork`, `claude-office`, plus the synthetic
`synthetic-claude-e2e` used for end-to-end tests) and routes them to a `raw_log=false` exporter
that writes to a distinct CloudWatch Logs stream. Every other record — including anything that
does not match the Claude service-name list — continues through the existing `raw_log=true`
exporter unchanged, so kanon and any future flat-body producer is untouched by construction: the
classifier fails closed to the original passthrough behavior rather than to the new reshape
path.

The `cwl_split` transform Lambda ([ADR 0023](0023-cwl-split-transform-lambda.md)), which already
re-ingests oversized CloudWatch Logs deliveries as individual records, gains a second
responsibility: when a record arrives on the structured stream, it reshapes the attribute-shaped
OTLP record into the same four-field envelope the raw pipeline already produces — `tool` from
`service.name` (mapped `claude-code` to `claude-code`, `claude-cowork` to `claude-cowork`,
`claude-office` to `claude-office`; any other routed value falls back to `claude-other`),
`event_type` from the record's event name, and `payload` from every log-record attribute
flattened (dotted keys become underscores) plus the resource attributes, `resource_`-prefixed,
along with `otel_body` and `otel_scope_*` fields. Records on the raw stream skip the reshape step
entirely and pass through byte-for-byte, exactly as before.

Three approaches were considered:

- Client-side reshaping via Claude's managed settings. Rejected: managed settings can redirect
  where telemetry goes (the OTLP endpoint, protocol, and enabled exporters — see
  [sending-telemetry.md](../sending-telemetry.md)) but cannot change what shape the client
  emits; Claude Code's OTLP output format is not itself configurable.
- An OTTL transform embedded in the collector config. Rejected: reshaping dozens of attributes
  into a nested JSON payload string in OTTL is exotic and hard to unit-test in isolation from a
  running collector, and it would put the reshape logic in the harder-to-test rendered-config
  layer rather than in code with its own test suite.
- Reshaping in the `cwl_split` Lambda (chosen). The Lambda already owns record-shape
  transformation for CloudWatch Logs delivery ([ADR 0023](0023-cwl-split-transform-lambda.md)),
  has its own unit-test suite, and keeps the collector configuration itself simple (route by
  `service.name` only); kanon's path through the Lambda is untouched because it never reaches
  the Claude branch.

## Consequences

The Glue `tool` partition's governed enum gains the `claude-*` values (`claude-code`,
`claude-cowork`, `claude-office`, `claude-other`) alongside the existing `e2e-smoke` and `kanon`
values, extending the enum projection on the `tool` partition (see
[data-model.md](../data-model.md)).

The attribute flatten is unbounded: it captures every attribute Claude tools attach to a record,
including identity attributes such as `user.email` when a session is authenticated. This is
intentional — usage telemetry is the platform's purpose, and captured identity attributes are
usage data subject to the data lake's existing data-classification and retention controls, not a
new category of data the platform did not already anticipate handling.

Only the logs signal is ingested; Claude tools also emit OTLP metrics (session/token/cost
counters) to `/v1/metrics`, which the collector still returns `404` for. Ingesting that signal
remained deferred future work at the time of this decision,
consistent with [ADR 0003](0003-logs-only-otlp-pipeline.md)'s logs-only decision, which this ADR
extends but does not revisit. (The metrics pipeline was added later — see
[ADR 0031](0031-claude-metrics-awsemf-pipeline.md).)

## Amended by

[ADR 0031](0031-claude-metrics-awsemf-pipeline.md) closes the gap the Consequences section
above left open: Claude tools' OTLP metrics signal, deferred here, is now ingested through a
dedicated `metrics/claude` pipeline and reshaped into the same event envelope this ADR
established for logs, landing in the same `telemetry_events` table this ADR's `claude-*` tool
values already govern. The dual-logs-pipeline routing and the `cwl_split` reshape this ADR
describes for the logs signal are unchanged.

**Generalized by the tool-registry refactor.** The `filter/keep_claude` routing list and the
`service.name -> tool` reshape map this ADR describes were hardcoded and Claude-specific; they
are now both **derived** from one input-driven registry
([`terragrunt/common/tool-registry.json`](../../terragrunt/common/tool-registry.json)) so any
tool — not only Claude's — onboards by adding one registry entry (see
[onboarding-a-tool.md](../onboarding-a-tool.md)). The `claude-other` fallback this ADR
describes has also been **removed**: an unmapped routed `service.name` now fails that record
fast to the monitored `errors/` prefix instead of a catch-all tool. The dual-pipeline routing
mechanism and the `cwl_split` reshape architecture this ADR establishes are otherwise
unchanged; only the naming and the source of the routing/mapping data were generalized.

See the [ADR index](README.md).
