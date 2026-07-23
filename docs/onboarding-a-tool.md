# Onboarding a new tool

This platform ingests usage telemetry from many tools into one shared data lake, partitioned by a
`tool` value and queryable in Athena. Onboarding a new tool — an AI agent, a CLI, a CI job, a dev
container, anything that can emit OpenTelemetry — is a **single edit** to one registry file:
[`terragrunt/common/tool-registry.json`](../terragrunt/common/tool-registry.json). Everything else
(the Glue `tool` enum, the collector's structured-OTLP routing, and the reshape's
`service.name → tool` map) is **derived** from that file, so you never configure a tool in more
than one place.

## Step 1 — decide the ingestion shape

A tool ingests via one of two shapes:

| Shape | The tool emits… | Use when |
|-------|-----------------|----------|
| **`body-contract`** | a single OTLP `LogRecord` whose **body is a JSON string** with a top-level `tool`: `{"timestamp","tool","event_type","payload"}` | you control the emitter and can format the body (kanon, most in-house CLIs, CI jobs, dev containers) |
| **`structured-otlp`** | **standard attribute-shaped OTLP** — logs whose body is the event name and whose data is in resource/log-record **attributes**, and/or OTLP **metrics** — identified by `resource.service.name` | the tool emits vanilla OpenTelemetry you don't control the shape of (Claude Code/Cowork/Office, other AI tools, any standard OTel SDK) |

Both land in the **same** `telemetry_events` table under your tool's `tool` value; the difference
is only how the record reaches it. See [architecture.md](architecture.md) and
[data-model.md](data-model.md) for the pipelines.

## Step 2 — add one registry entry

Edit `terragrunt/common/tool-registry.json` and add one object to `tools`:

**A body-contract tool (e.g. a dev container):**

```json
{ "tool": "devcontainer", "ingestion": "body-contract", "description": "Dev container usage events" }
```

**A structured-OTLP tool (e.g. an in-house AI agent):**

```json
{ "tool": "example-agent", "ingestion": "structured-otlp", "service_names": ["example.agent"], "description": "In-house AI agent" }
```

Fields:

- `tool` (required, unique) — the `tool` partition value your data lands under and queries filter on.
- `ingestion` (required) — `body-contract`, `structured-otlp`, or `both`.
- `service_names` (required for `structured-otlp`/`both`) — the OTLP `resource.service.name` value(s)
  the collector routes to your tool. Omit for `body-contract`.
- `description` (required) — a human note.

That is the whole change. Do **not** touch the Glue enum, the collector config, or the Lambda —
they read this file.

### What gets derived automatically

- **Glue `tool` enum** (`projection.tool.values`) gains your `tool` — so Athena can resolve the
  partition.
- **Collector structured-OTLP routing** (`structured_otlp_service_names`) gains your
  `service_names` — so the collector routes them to the structured pipeline + reshape.
- **Reshape map** (`service_tool_map`) gains `service.name → tool` — so a structured record lands
  under your `tool`.

## Step 3 — configure the emitter

- **body-contract:** point the tool's OTLP/HTTP log exporter at the collector and emit the flat
  body — see [sending-telemetry.md](sending-telemetry.md) ("Sending from an OTLP SDK").
- **structured-otlp:** point the tool's OTLP exporter (logs and/or metrics) at the collector with
  `resource.service.name` set to the value you registered. For Claude tools, that's the
  managed-settings rollout in [sending-telemetry.md](sending-telemetry.md).

Endpoints: `https://collector.telemetry.example.com` (prod),
`https://collector.sandbox.telemetry.example.com` (non-prod).

## Step 4 — deploy

The registry is env-agnostic (both environments read it verbatim). A change to it is a normal
config change: open a PR, and on merge the standard `terragrunt apply` reconciles the Glue enum
and rolls the collector (its config fingerprint triggers a new task revision automatically).
Validate in sandbox first, then prod, exactly as any config change.

## Step 5 — verify

Query the lake in the Athena workgroup (see [data-model.md](data-model.md)):

```sql
SELECT event_type, count(*) n
FROM telemetry_events
WHERE tool = '<your-tool>' AND dt >= date_format(current_date - interval '1' day, '%Y-%m-%d')
GROUP BY event_type ORDER BY n DESC;
```

## Fail-fast: unregistered tools go to `errors/`, never a catch-all

There is **no fallback tool**. If a structured-OTLP record arrives with a `service.name` that is
**not** in the registry, it is not routed or reshaped — it lands in the monitored `errors/` prefix
(fail-fast and visible) rather than being silently bucketed under a catch-all `tool`. Likewise a
flat-body record whose `tool` value is not in the enum is stored but not queryable until the value
is registered. This keeps the `tool` partition governed and surfaces un-onboarded tools loudly
instead of hiding them. Keep the `errors/` prefix clean: if data shows up there, either register
the tool or fix the emitter. See [troubleshooting.md](troubleshooting.md).

## Reference

- [tool-registry.json](../terragrunt/common/tool-registry.json) — the registry (source of truth)
- [sending-telemetry.md](sending-telemetry.md) — producer/emitter guide
- [data-model.md](data-model.md) — schema, `tool` enum projection, Athena queries
- [architecture.md](architecture.md) — collector + pipelines
