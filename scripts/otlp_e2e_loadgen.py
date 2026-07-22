"""Standalone OTLP/HTTP load + conformance generator for the telemetry collector.

Invoked as::

    uv run python -m scripts.otlp_e2e_loadgen --env sandbox --mode happy --count 50
    make e2e-loadgen ENV=sandbox

The deployed collector is an ADOT OTLP/HTTP receiver on path ``/v1/logs`` (and,
for the Claude-metrics route below, ``/v1/metrics``; ``/v1/traces`` is still not
ingested -- see ADR 0031) with content-types ``application/x-protobuf`` and
``application/json``, a 4 MiB body cap, NO client auth (public), fronted by
CloudFront + WAF. Each OTLP ``LogRecord``'s ``Body`` must be a JSON *string*::

    {"timestamp": <ISO8601>, "tool": <str>, "event_type": <str>, "payload": <json-str>}

because the ``awscloudwatchlogs`` exporter writes ``log.Body().AsString()`` and
``raw_log=true`` keeps the ``.tool`` field top-level. The OTLP request itself is
an ``ExportLogsServiceRequest`` built from the ``opentelemetry-proto`` package;
the ``--encoding json`` form is produced via the canonical protobuf->JSON
mapping (``google.protobuf.json_format``).

One mode (``structured-events``) instead emulates the real ``claude_code``
telemetry event shape: data lives in the OTLP ``LogRecord`` *attributes* (a
structured key/value map), not in ``Body`` -- ``Body`` is just the event-name
string ``claude_code.<event>``. Those records carry a resource
``service.name`` that is a member of the collector's registry-derived
``structured_otlp_service_names`` allowlist (``terragrunt/common/tool-registry.json``),
so the collector routes them to a SECOND exporter configured with
``raw_log=false``; the data-lake ``cwl_split`` Lambda reshapes each one into
the same ``{timestamp, tool, event_type, payload}`` envelope every other mode
sends directly (``tool`` from ``service_tool_map[service.name]``,
``event_type`` from ``attributes["event.name"]``, ``payload`` from the
flattened attributes) before it lands in the same Glue table.

Another mode (``structured-metrics``) emulates the real ``claude_code`` telemetry
METRICS shape (ADR 0031): an OTLP ``ExportMetricsServiceRequest`` posted to
``/v1/metrics`` whose resource ``service.name`` is the same synthetic marker
(``constants.E2E_STRUCTURED_SERVICE_NAME``), so the collector's
``metrics/structured`` pipeline keeps it (the SAME registry-derived
``structured_otlp_service_names`` allowlist that gates ``structured-events``)
and exports it via the ``awsemf`` exporter as CloudWatch EMF log events into
the same telemetry ingest log group. The data-lake ``cwl_split`` Lambda's
``_reshape_emf`` then turns each EMF event into one canonical
``{timestamp, tool, event_type, payload}`` row per declared metric
(``event_type`` = the metric name, ``payload.otel_signal = "metric"``,
``tool`` from the same ``service_tool_map``), landing in the same Glue
table as every other mode.

This module imports NOTHING from any external CLI tool: the synthetic
usage-event taxonomy is derived purely from the Glue ``telemetry_events`` schema
(constants.E2E_EVENT_TYPES). Every synthetic record is tagged with the single
fixed reserved ``tool = "e2e-smoke"`` (constants.E2E_TOOL_VALUE), NOT a per-run
value: the Glue ``tool`` partition uses Athena enum projection over a governed,
fixed set of tool values, so a per-run tool value would fall outside the enum and
never be projected. Each record's payload instead embeds a ``run_id``
(``$.run_id``) so one run's records are identifiable within the shared
``tool=e2e-smoke`` partition.

Modes (each exercises one C-series conformance test)::

    happy               valid small batch                       -> 200
    volume              large valid batch                       -> 200
    variety             payload variety small/nested/unicode/
                        control-chars/large                     -> 200
    structured-events   synthetic STRUCTURED Claude-style log
                        records (data in attributes, Body =
                        event name) -- raw_log=false route +
                        cwl_split reshape                       -> 200
    structured-metrics  synthetic Claude-style OTLP Sum metrics
                        posted to /v1/metrics --
                        metrics/structured awsemf/EMF route +
                        cwl_split _reshape_emf                  -> 200
    oversize            single request body > 4 MiB             -> 413
    bad-content-type    valid body, wrong Content-Type          -> rejected (415/400)
    malformed           malformed proto/json bytes              -> 400
    wrong-signal        POST /v1/traces (only unconfigured
                        signal; /v1/metrics is ingested)        -> 404 (not ingested)
    grpc-probe          TCP connect to 4317                     -> no listener
    waf-trigger         SQLi/XSS pattern in body                -> 403
    rate-burst          > 2000 requests / 5 min from one IP     -> some 403/429

The run emits a sent-MANIFEST (``--manifest-out``) describing run-id, env,
endpoint, per-(event_type) counts (``counts_by_event``, scoped to this run by the
manifest ``run_id``), the fixed tool partition value (``tool_value``, always
``e2e-smoke``), expected dt partitions, sample record hashes for integrity
reconciliation, and a per-request HTTP status summary. The consumer-side verifier
(``scripts.e2e_verify``) consumes this manifest -- it reconciles the recorded
per-event counts against Athena with ``WHERE tool = 'e2e-smoke' AND
json_extract_scalar(payload, '$.run_id') = '<run-id>'`` (the Glue ``tool``
partition uses enum projection, so the table is queryable with no tool filter and
runs are separated by the payload ``run_id``, not by the tool value).

Exit codes::

    0 -- every request produced its expected status for the mode
    1 -- one or more requests deviated from the expected status
    2 -- usage error (unknown ENV / mode / malformed --resolve)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import pathlib
import secrets
import socket
import ssl
import sys
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scripts import constants, e2e_common

# Modes whose accepted records use the generic body-contract (LogRecord.Body =
# the JSON string {timestamp, tool, event_type, payload}) sent via
# build_export_request/plan_events/chunk_into_requests over the raw_log=true
# collector route. structured-events and structured-metrics ALSO land in the
# data lake and contribute queryable counts for verifier reconciliation, but
# each sends a materially different request shape -- structured-events a
# STRUCTURED log record (data in LogRecord attributes, Body = an event-name
# string) over the raw_log=false route + cwl_split reshape, structured-metrics
# an OTLP ExportMetricsServiceRequest over the metrics/structured awsemf/EMF
# route + cwl_split _reshape_emf -- so both are deliberately excluded here and
# dispatched to their own builders (build_structured_events_request /
# build_structured_metrics_request) in OtlpLoadGenerator.build_plans.
_DATA_MODES = frozenset({"happy", "volume", "variety"})

# Request paths whose ACCEPTED (200) records land in the data lake and therefore
# contribute manifest counts for verifier reconciliation. /v1/logs carries every
# body-contract and structured-events record; /v1/metrics carries structured-metrics
# records. Only the data-landing modes ever POST 200s to these paths; the wrong-signal
# mode targets /v1/traces (the sole unconfigured signal), which is absent here, so no
# non-data mode contributes to a manifest count.
_LANDING_PATHS = frozenset({constants.E2E_OTLP_LOGS_PATH, constants.E2E_OTLP_METRICS_PATH})


@dataclass(frozen=True)
class EventSpec:
    """A single synthetic usage event destined for one OTLP LogRecord."""

    tool: str
    event_type: str
    payload: str
    timestamp: str


@dataclass
class RequestPlan:
    """An encoded OTLP/HTTP request plus the records it carries."""

    label: str
    path: str
    content_type: str
    body: bytes
    specs: list[EventSpec] = field(default_factory=list)
    expected_statuses: tuple[int, ...] = ()


@dataclass
class RequestResult:
    """The outcome of sending one RequestPlan."""

    label: str
    path: str
    status: int
    reason: str
    expected_statuses: tuple[int, ...]
    specs: list[EventSpec] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the observed status is one of the expected statuses."""
        return self.status in self.expected_statuses


# ---------------------------------------------------------------------------
# Record body + payload construction (pure, fully unit-testable)
# ---------------------------------------------------------------------------


def build_record_body(spec: EventSpec) -> str:
    """Serialize an EventSpec to the JSON-string LogRecord Body the stack expects.

    The body is itself a JSON *string* (the value of ``LogRecord.body``'s
    ``stringValue``) carrying the four top-level fields the Glue
    ``telemetry_events`` schema reads.

    Args:
        spec: The synthetic event.

    Returns:
        ``json.dumps`` of ``{timestamp, tool, event_type, payload}`` with
        ``ensure_ascii=False`` so unicode payloads round-trip byte-faithfully.
    """
    return json.dumps(
        {
            "timestamp": spec.timestamp,
            "tool": spec.tool,
            "event_type": spec.event_type,
            "payload": spec.payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def generate_payload(variety: str, rng: secrets.SystemRandom, large_bytes: int) -> str:
    """Return a synthetic inner-payload JSON string for the given variety.

    Args:
        variety: One of constants.E2E_PAYLOAD_VARIETIES.
        rng: A SystemRandom instance (injected for deterministic-shape tests).
        large_bytes: Target byte length for the ``large`` variety.

    Returns:
        A JSON string suitable for the ``payload`` field.

    Raises:
        E2EUsageError: When the variety is unknown.
    """
    nonce = rng.randrange(1_000_000)
    if variety == "small":
        return json.dumps({"k": "v", "n": nonce}, separators=(",", ":"))
    if variety == "nested":
        return json.dumps(
            {
                "args": ["--flag", "value", str(nonce)],
                "meta": {"duration_ms": nonce % 5000, "nested": {"depth": 3, "ok": True}},
                "tags": [{"k": "env", "v": "synthetic"}, {"k": "n", "v": nonce}],
            },
            separators=(",", ":"),
        )
    if variety == "unicode":
        return json.dumps(
            {"msg": f"héllo-世界-{nonce}", "emoji": "🚀📈", "rtl": "مرحبا"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if variety == "control-chars":
        return json.dumps(
            {"raw": "tab\tnewline\nnull\x00bell\x07end", "n": nonce},
            separators=(",", ":"),
        )
    if variety == "large":
        filler = ("x" * 512 + str(nonce)) * max(1, large_bytes // 515)
        return json.dumps({"blob": filler[:large_bytes], "n": nonce}, separators=(",", ":"))
    raise e2e_common.E2EUsageError(
        f"ERROR: unknown payload variety {variety!r}: must be one of "
        f"{list(constants.E2E_PAYLOAD_VARIETIES)!r}"
    )


def embed_run_id(payload_json: str, run_id: str) -> str:
    """Return the payload JSON string with a top-level ``run_id`` key added.

    Every synthetic record shares the fixed ``tool = "e2e-smoke"`` partition, so
    the run identity lives in the payload instead: the verifier scopes a run's
    records with ``json_extract_scalar(payload, '$.run_id') = '<run-id>'``. Every
    payload ``generate_payload`` produces is a JSON object, so ``run_id`` is
    inserted as a top-level field; ``ensure_ascii=False`` keeps unicode payloads
    byte-faithful.

    Args:
        payload_json: A JSON-object string produced by ``generate_payload``.
        run_id: The run identifier to embed as ``$.run_id``.

    Returns:
        The payload JSON string carrying a top-level ``run_id`` field.
    """
    obj = json.loads(payload_json)
    obj["run_id"] = run_id
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def plan_events(
    mode: str,
    count: int,
    tool: str,
    run_id: str,
    rng: secrets.SystemRandom,
    timestamp: str,
) -> list[EventSpec]:
    """Build the list of synthetic events for a data-plane mode.

    Every event carries the fixed reserved ``tool`` partition value; the run
    identity is embedded in each payload as ``run_id`` (``$.run_id``) so the
    verifier can reconcile this run's records within the shared tool partition.

    Args:
        mode: A data mode (happy, volume, variety).
        count: Number of events to generate.
        tool: The fixed reserved tool value (``e2e-smoke``) every record carries.
        run_id: The run identifier embedded in each event payload as ``$.run_id``.
        rng: SystemRandom for value variation.
        timestamp: ISO-8601 timestamp stamped on every event.

    Returns:
        A list of ``count`` EventSpec instances.
    """
    event_types = constants.E2E_EVENT_TYPES
    varieties = constants.E2E_PAYLOAD_VARIETIES
    specs: list[EventSpec] = []
    for i in range(count):
        event_type = event_types[i % len(event_types)]
        if mode == "variety":
            variety = varieties[i % len(varieties)]
            # Keep per-record "large" payloads modest in variety mode so a single
            # request still fits under the 4 MiB cap and yields a 200.
            payload = generate_payload(variety, rng, large_bytes=64 * 1024)
        else:
            payload = generate_payload("small", rng, large_bytes=0)
        specs.append(
            EventSpec(
                tool=tool,
                event_type=event_type,
                payload=embed_run_id(payload, run_id),
                timestamp=timestamp,
            )
        )
    return specs


# ---------------------------------------------------------------------------
# OTLP protobuf request construction + encoding (pure)
# ---------------------------------------------------------------------------


def build_export_request(specs: list[EventSpec]) -> Any:
    """Build an OTLP ExportLogsServiceRequest carrying one LogRecord per spec.

    Args:
        specs: The synthetic events to embed.

    Returns:
        An ``ExportLogsServiceRequest`` protobuf message.
    """
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
    from opentelemetry.proto.common.v1 import common_pb2

    request = logs_service_pb2.ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    resource_logs.resource.attributes.add(
        key="service.name",
        value=common_pb2.AnyValue(string_value=constants.E2E_TOOL_VALUE),
    )
    scope_logs = resource_logs.scope_logs.add()
    scope_logs.scope.name = constants.E2E_TOOL_VALUE
    for spec in specs:
        log_record = scope_logs.log_records.add()
        log_record.body.string_value = build_record_body(spec)
    return request


def encode_request(request: Any, encoding: str) -> tuple[bytes, str]:
    """Encode an OTLP request to wire bytes and return the matching content type.

    Args:
        request: An ExportLogsServiceRequest protobuf message.
        encoding: ``protobuf`` or ``json``.

    Returns:
        ``(body_bytes, content_type)``.

    Raises:
        E2EUsageError: When the encoding is unknown.
    """
    if encoding == "protobuf":
        return request.SerializeToString(), constants.E2E_CONTENT_TYPE_PROTOBUF
    if encoding == "json":
        from google.protobuf import json_format

        body = json_format.MessageToJson(request, indent=0).encode("utf-8")
        return body, constants.E2E_CONTENT_TYPE_JSON
    raise e2e_common.E2EUsageError(
        f"ERROR: unknown encoding {encoding!r}: must be 'protobuf' or 'json'"
    )


def chunk_into_requests(
    specs: list[EventSpec],
    encoding: str,
    max_bytes: int,
) -> list[RequestPlan]:
    """Encode events into one or more requests that each fit under ``max_bytes``.

    Args:
        specs: The events to send.
        encoding: ``protobuf`` or ``json``.
        max_bytes: The per-request body-size ceiling (the 4 MiB receiver cap).

    Returns:
        A list of RequestPlan, each with an encoded body strictly below
        ``max_bytes``. A single record that cannot fit is sent on its own (the
        receiver will reject it; the load generator never silently drops data).
    """
    plans: list[RequestPlan] = []
    current: list[EventSpec] = []
    for spec in specs:
        candidate = [*current, spec]
        body, content_type = encode_request(build_export_request(candidate), encoding)
        if len(body) > max_bytes and current:
            prev_body, prev_ct = encode_request(build_export_request(current), encoding)
            plans.append(
                RequestPlan(
                    label=f"logs-batch-{len(plans)}",
                    path=constants.E2E_OTLP_LOGS_PATH,
                    content_type=prev_ct,
                    body=prev_body,
                    specs=list(current),
                    expected_statuses=(200,),
                )
            )
            current = [spec]
        else:
            current = candidate
    if current:
        body, content_type = encode_request(build_export_request(current), encoding)
        plans.append(
            RequestPlan(
                label=f"logs-batch-{len(plans)}",
                path=constants.E2E_OTLP_LOGS_PATH,
                content_type=content_type,
                body=body,
                specs=list(current),
                expected_statuses=(200,),
            )
        )
    return plans


# Fixed, obviously-synthetic attribute values for the two structured-events
# EventSpecs below. Real values (a genuine host arch, terminal type, etc.)
# would risk being mistaken for real usage by anyone reading the raw Athena
# payload; these markers keep every structured-events record legible as
# harness output at a glance, matching the "e2e-smoke"/"synthetic-*" naming
# already used elsewhere in this module.
_CLAUDE_EVENTS_RESOURCE_OS_TYPE = "linux"
_CLAUDE_EVENTS_RESOURCE_HOST_ARCH = "x86_64"
_CLAUDE_EVENTS_PLUGIN_NAME = "e2e-smoke-plugin"
_CLAUDE_EVENTS_MARKETPLACE_NAME = "e2e-smoke-marketplace"
_CLAUDE_EVENTS_TERMINAL_TYPE = "e2e-harness"
_CLAUDE_EVENTS_SERVER_NAME = "e2e-smoke-server"


def build_structured_events_request(timestamp: str, run_id: str, encoding: str) -> RequestPlan:
    """Build one OTLP request carrying two structured Claude-style LogRecords.

    Emulates the real ``claude_code`` telemetry event shape captured from
    claude v2.1.210 (data in LogRecord *attributes*, ``Body`` = the event-name
    string ``claude_code.<event>``), which is structurally different from
    every other mode's body-contract records built by ``build_export_request``.
    The resource ``service.name`` is the synthetic marker
    ``constants.E2E_STRUCTURED_SERVICE_NAME``; the tool registry
    (``terragrunt/common/tool-registry.json``) lists it as the "e2e-smoke"
    entry's service_names member, so the registry-derived collector-ingestion
    ``structured_otlp_service_names`` allowlist includes it and the collector
    routes it to the ``raw_log=false`` exporter, and the registry-derived
    data-lake ``service_tool_map`` maps it to ``constants.E2E_TOOL_VALUE`` so
    the ``cwl_split`` reshape lands the records under the shared reconcile
    tool partition.

    Each record carries a ``run_id`` attribute -- the claude event shape has no
    JSON-string ``payload`` field to embed a run id in (unlike the other
    modes' ``embed_run_id``), so the run identity is embedded directly as an
    OTLP attribute instead. After reshape, an attribute with no dot in its key
    becomes a top-level ``payload`` field unchanged, so
    ``json_extract_scalar(payload, '$.run_id')`` still resolves it -- the same
    reconciliation query every other mode uses, unmodified.

    Args:
        timestamp: ISO-8601 timestamp stamped as the ``event.timestamp``
            attribute on every record.
        run_id: The run identifier, embedded as a ``run_id`` attribute on
            every record.
        encoding: ``protobuf`` or ``json``.

    Returns:
        A single RequestPlan carrying two EventSpecs (one per event, used only
        for manifest count-keying -- see ``OtlpLoadGenerator._populate_manifest``)
        expected to return HTTP 200.
    """
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
    from opentelemetry.proto.common.v1 import common_pb2

    def _add_attr(container: Any, key: str, value: bool | str) -> None:
        if isinstance(value, bool):
            container.attributes.add(key=key, value=common_pb2.AnyValue(bool_value=value))
        else:
            container.attributes.add(key=key, value=common_pb2.AnyValue(string_value=str(value)))

    request = logs_service_pb2.ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    resource_attrs: dict[str, str] = {
        "service.name": constants.E2E_STRUCTURED_SERVICE_NAME,
        "service.version": "e2e",
        "os.type": _CLAUDE_EVENTS_RESOURCE_OS_TYPE,
        "host.arch": _CLAUDE_EVENTS_RESOURCE_HOST_ARCH,
    }
    for key, value in resource_attrs.items():
        _add_attr(resource_logs.resource, key, value)
    scope_logs = resource_logs.scope_logs.add()
    scope_logs.scope.name = constants.E2E_STRUCTURED_SCOPE_NAME

    # (event.name, event-specific attributes) for the two real event shapes.
    events: list[tuple[str, dict[str, bool | str]]] = [
        (
            "plugin_loaded",
            {
                "plugin.name": _CLAUDE_EVENTS_PLUGIN_NAME,
                "marketplace.name": _CLAUDE_EVENTS_MARKETPLACE_NAME,
                "plugin.scope": "user-local",
                "enabled_via": "user-install",
                "session.id": run_id,
                "terminal.type": _CLAUDE_EVENTS_TERMINAL_TYPE,
            },
        ),
        (
            "mcp_server_connection",
            {
                "server_name": _CLAUDE_EVENTS_SERVER_NAME,
                "transport_type": "http",
                "server_scope": "user",
                "status": "connected",
                "is_plugin": False,
                "session.id": run_id,
            },
        ),
    ]

    specs: list[EventSpec] = []
    for event_name, event_attrs in events:
        log_record = scope_logs.log_records.add()
        log_record.body.string_value = f"claude_code.{event_name}"
        all_attrs: dict[str, bool | str] = {
            **event_attrs,
            "event.name": event_name,
            "event.timestamp": timestamp,
            "run_id": run_id,
        }
        for attr_key, attr_value in all_attrs.items():
            _add_attr(log_record, attr_key, attr_value)
        specs.append(
            EventSpec(
                tool=constants.E2E_TOOL_VALUE,
                event_type=event_name,
                payload=json.dumps(all_attrs, ensure_ascii=False, separators=(",", ":")),
                timestamp=timestamp,
            )
        )

    body, content_type = encode_request(request, encoding)
    return RequestPlan(
        label="structured-events-batch",
        path=constants.E2E_OTLP_LOGS_PATH,
        content_type=content_type,
        body=body,
        specs=specs,
        expected_statuses=(200,),
    )


# Fixed, obviously-synthetic markers for the structured-metrics NumberDataPoints below.
# Distinct names from the structured-events markers above (own constant names) so a raw
# Athena payload reader can immediately tell a metric row's synthetic origin apart from a
# log row's.
_CLAUDE_METRICS_MARKER_PREFIX = "e2e-smoke-metric"
_CLAUDE_METRICS_TERMINAL_TYPE = "e2e-harness"
_CLAUDE_METRICS_VALUE = 1
_CLAUDE_METRICS_UNIT = "1"


def build_structured_metrics_request(timestamp: str, run_id: str, encoding: str) -> RequestPlan:
    """Build one OTLP request carrying two synthetic Claude-style Sum metrics.

    Emulates the real Claude-tool telemetry METRICS shape (ADR 0031): the collector's
    ``metrics/structured`` pipeline accepts an OTLP ``ExportMetricsServiceRequest`` on
    ``/v1/metrics``, keeps only datapoints whose resource ``service.name`` is a member of
    the registry-derived ``structured_otlp_service_names`` allowlist (the SAME allowlist
    ``build_structured_events_request`` relies on for the logs route --
    ``constants.E2E_STRUCTURED_SERVICE_NAME`` is a member via the tool registry's
    "e2e-smoke" entry), and exports them via ``awsemf`` as CloudWatch EMF log events into
    the SAME telemetry ingest log group. The data-lake ``cwl_split`` Lambda's
    ``_reshape_emf`` then turns each EMF event into one canonical
    ``{timestamp, tool, event_type, payload}`` row per declared metric, with
    ``payload.otel_signal = "metric"`` as the metric-vs-log discriminator and ``tool``
    resolved via the SAME registry-derived ``service_tool_map`` (service.name ->
    ``constants.E2E_TOOL_VALUE``).

    Each metric is a monotonic ``Sum`` with **DELTA** aggregation temporality (NOT
    cumulative -- see the temporality note below) with exactly ONE ``NumberDataPoint``
    carrying an integer value and datapoint attributes -- including ``run_id`` (which
    survives the EMF reshape as a flattened, dot-free ``payload.run_id`` field, exactly
    like a structured-events attribute) plus a couple obviously-synthetic markers, so a
    run's metric rows are reconcilable by the SAME
    ``json_extract_scalar(payload, '$.run_id')`` query the verifier already uses for logs.
    The two datapoints deliberately carry DIFFERENT ``synthetic_marker`` values (an
    index-suffixed marker per metric): the ``awsemf`` exporter groups same-export-call
    datapoints that share an identical dimension/attribute set into ONE combined EMF log
    event, and giving each metric a distinct marker guarantees the two metrics resolve to
    different dimension sets and therefore land as two SEPARATE EMF/CWL log events -- so the
    raw CWL event count matches ``total_records`` (2), exactly like every other
    data-landing mode's one-record-to-one-CWL-event mapping that ``e2e_verify._check_cwl``
    assumes.

    Temporality (BUG FIX, #286): a real Claude Code counter is cumulative and the
    ``awsemf`` exporter converts cumulative Sums to per-interval deltas by diffing
    consecutive exports -- but this load generator sends exactly ONE export per run, so a
    CUMULATIVE datapoint only ever sets the exporter's baseline and never yields a delta,
    meaning no EMF event is ever produced (verified empirically: sandbox metrics streams
    showed zero ``synthetic.*`` rows). Sending **DELTA** aggregation temporality instead
    (still monotonic) makes ``awsemf`` emit the datapoint's value directly on this single
    export, so each of the two metrics produces its own EMF event -- 2 EMF events -> 2 lake
    rows -> the verifier's ``athena:reconcile expected=2 found=2``.

    Args:
        timestamp: ISO-8601 timestamp; stamped as each NumberDataPoint's
            ``time_unix_nano``/``start_time_unix_nano`` and used for the manifest sample
            records. The EMF ``_aws.Timestamp`` CloudWatch Logs ultimately sees comes from
            the collector's own export time, not this value.
        run_id: The run identifier, embedded as a ``run_id`` (and ``session.id``, mirroring
            the real SDK's attribute name) attribute on every NumberDataPoint.
        encoding: ``protobuf`` or ``json``.

    Returns:
        A single RequestPlan carrying two EventSpecs (one per metric, used only for
        manifest count-keying -- see ``OtlpLoadGenerator._populate_manifest``) expected to
        return HTTP 200.
    """
    import datetime

    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
    from opentelemetry.proto.common.v1 import common_pb2
    from opentelemetry.proto.metrics.v1 import metrics_pb2

    def _add_attr(container: Any, key: str, value: str) -> None:
        container.attributes.add(key=key, value=common_pb2.AnyValue(string_value=str(value)))

    time_unix_nano = int(
        datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=datetime.UTC)
        .timestamp()
        * 1_000_000_000
    )

    request = metrics_service_pb2.ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_attrs: dict[str, str] = {
        "service.name": constants.E2E_STRUCTURED_SERVICE_NAME,
        "service.version": "e2e",
    }
    for key, value in resource_attrs.items():
        _add_attr(resource_metrics.resource, key, value)
    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.scope.name = constants.E2E_STRUCTURED_METRICS_SCOPE_NAME

    specs: list[EventSpec] = []
    for index, metric_name in enumerate(constants.E2E_STRUCTURED_METRIC_NAMES, start=1):
        metric = scope_metrics.metrics.add()
        metric.name = metric_name
        metric.unit = _CLAUDE_METRICS_UNIT
        metric.sum.is_monotonic = True
        # BUG FIX (#286): DELTA, not CUMULATIVE -- a one-shot cumulative Sum only sets the
        # awsemf exporter's baseline and never yields a delta, so no EMF event is produced.
        # See the temporality note in this function's docstring.
        metric.sum.aggregation_temporality = metrics_pb2.AGGREGATION_TEMPORALITY_DELTA
        data_point = metric.sum.data_points.add()
        data_point.as_int = _CLAUDE_METRICS_VALUE
        data_point.start_time_unix_nano = time_unix_nano
        data_point.time_unix_nano = time_unix_nano
        dp_attrs: dict[str, str] = {
            "session.id": run_id,
            "run_id": run_id,
            "terminal.type": _CLAUDE_METRICS_TERMINAL_TYPE,
            "synthetic_marker": f"{_CLAUDE_METRICS_MARKER_PREFIX}-{index}",
        }
        for attr_key, attr_value in dp_attrs.items():
            _add_attr(data_point, attr_key, attr_value)
        specs.append(
            EventSpec(
                tool=constants.E2E_TOOL_VALUE,
                event_type=metric_name,
                payload=json.dumps(
                    {**dp_attrs, "value": _CLAUDE_METRICS_VALUE, "otel_signal": "metric"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                timestamp=timestamp,
            )
        )

    body, content_type = encode_request(request, encoding)
    return RequestPlan(
        label="structured-metrics-batch",
        path=constants.E2E_OTLP_METRICS_PATH,
        content_type=content_type,
        body=body,
        specs=specs,
        expected_statuses=(200,),
    )


def build_oversize_request(marker: str, encoding: str, timestamp: str) -> RequestPlan:
    """Build a single request whose encoded body strictly exceeds the 4 MiB cap.

    Args:
        marker: The fixed reserved tool value (``e2e-smoke``) records carry.
        encoding: ``protobuf`` or ``json``.
        timestamp: ISO-8601 timestamp for the record.

    Returns:
        A RequestPlan expected to be rejected with HTTP 413.
    """
    rng = secrets.SystemRandom()
    over = constants.E2E_MAX_BODY_BYTES + 256 * 1024
    spec = EventSpec(
        tool=marker,
        event_type="perf_sample",
        payload=generate_payload("large", rng, large_bytes=over),
        timestamp=timestamp,
    )
    body, content_type = encode_request(build_export_request([spec]), encoding)
    return RequestPlan(
        label="oversize",
        path=constants.E2E_OTLP_LOGS_PATH,
        content_type=content_type,
        body=body,
        specs=[],
        expected_statuses=(413,),
    )


def build_bad_content_type_request(marker: str, encoding: str, timestamp: str) -> RequestPlan:
    """Build a structurally valid request sent with a wrong Content-Type header.

    Args:
        marker: The fixed reserved tool value (``e2e-smoke``) records carry.
        encoding: ``protobuf`` or ``json`` (used to build a valid body).
        timestamp: ISO-8601 timestamp for the record.

    Returns:
        A RequestPlan with a ``text/plain`` content type, expected to be
        rejected (415 Unsupported Media Type, or 400).
    """
    rng = secrets.SystemRandom()
    spec = EventSpec(
        tool=marker,
        event_type="session_start",
        payload=generate_payload("small", rng, large_bytes=0),
        timestamp=timestamp,
    )
    body, _ct = encode_request(build_export_request([spec]), encoding)
    return RequestPlan(
        label="bad-content-type",
        path=constants.E2E_OTLP_LOGS_PATH,
        content_type="text/plain",
        body=body,
        specs=[],
        expected_statuses=(415, 400),
    )


def build_malformed_request(encoding: str) -> RequestPlan:
    """Build a request whose body is malformed for the declared content type.

    Args:
        encoding: ``protobuf`` or ``json``.

    Returns:
        A RequestPlan with garbage bytes (protobuf) or invalid JSON (json) under
        the correct Content-Type, expected to be rejected with HTTP 400.
    """
    if encoding == "json":
        body = b'{"resourceLogs": [ this is not valid json'
        content_type = constants.E2E_CONTENT_TYPE_JSON
    else:
        body = bytes([0xFF, 0xFE, 0xFD, 0xFC]) + secrets.token_bytes(64)
        content_type = constants.E2E_CONTENT_TYPE_PROTOBUF
    return RequestPlan(
        label="malformed",
        path=constants.E2E_OTLP_LOGS_PATH,
        content_type=content_type,
        body=body,
        specs=[],
        expected_statuses=(400,),
    )


def build_wrong_signal_requests(encoding: str) -> list[RequestPlan]:
    """Build a request to the traces signal path (the one signal not ingested).

    The collector ingests logs (``/v1/logs``) and, since the metrics/structured
    awsemf pipeline (ADR 0031), metrics (``/v1/metrics``); ``/v1/traces`` is the
    ONLY OTLP signal path still unconfigured, so the OTLP/HTTP receiver returns
    404 for it. (``/v1/metrics`` is no longer a wrong signal -- it carries the
    structured-metrics records built by ``build_structured_metrics_request`` --
    so it is deliberately NOT probed here.)

    Args:
        encoding: ``protobuf`` or ``json`` (an empty valid OTLP envelope is used).

    Returns:
        One RequestPlan targeting ``/v1/traces``, expected to return HTTP 404.
    """
    body, content_type = encode_request(build_export_request([]), encoding)
    return [
        RequestPlan(
            label="wrong-signal-traces",
            path=constants.E2E_OTLP_TRACES_PATH,
            content_type=content_type,
            body=body,
            specs=[],
            expected_statuses=(404,),
        ),
    ]


def build_waf_trigger_request(marker: str, encoding: str, timestamp: str) -> RequestPlan:
    """Build a request whose payload embeds SQLi + XSS attack patterns.

    The AWS managed WAF rule groups fronting the collector inspect the request
    body; a recognizable SQLi/XSS signature must be blocked with HTTP 403 before
    it reaches the receiver.

    Args:
        marker: The fixed reserved tool value (``e2e-smoke``) records carry.
        encoding: ``protobuf`` or ``json``.
        timestamp: ISO-8601 timestamp for the record.

    Returns:
        A RequestPlan expected to be blocked with HTTP 403.
    """
    attack = "' OR 1=1;-- <script>alert(document.cookie)</script> UNION SELECT * FROM users"
    spec = EventSpec(
        tool=marker,
        event_type="command_execution",
        payload=json.dumps({"injection": attack}, separators=(",", ":")),
        timestamp=timestamp,
    )
    body, content_type = encode_request(build_export_request([spec]), encoding)
    return RequestPlan(
        label="waf-trigger",
        path=constants.E2E_OTLP_LOGS_PATH,
        content_type=content_type,
        body=body,
        specs=[],
        expected_statuses=(403,),
    )


def build_rate_burst_requests(
    marker: str, encoding: str, timestamp: str, count: int
) -> list[RequestPlan]:
    """Build ``count`` small requests to exceed the per-IP WAF rate limit.

    Args:
        marker: The fixed reserved tool value (``e2e-smoke``) records carry.
        encoding: ``protobuf`` or ``json``.
        timestamp: ISO-8601 timestamp for the records.
        count: Number of requests to fire (should exceed the 5-minute limit).

    Returns:
        A list of RequestPlan; each accepts 200 OR 403/429 (rate-limited) so the
        run only fails if *no* request is rate-limited once the limit is passed.
    """
    rng = secrets.SystemRandom()
    plans: list[RequestPlan] = []
    for i in range(count):
        spec = EventSpec(
            tool=marker,
            event_type="feature_usage",
            payload=generate_payload("small", rng, large_bytes=0),
            timestamp=timestamp,
        )
        body, content_type = encode_request(build_export_request([spec]), encoding)
        plans.append(
            RequestPlan(
                label=f"rate-burst-{i}",
                path=constants.E2E_OTLP_LOGS_PATH,
                content_type=content_type,
                body=body,
                specs=[],
                expected_statuses=(200, 403, 429),
            )
        )
    return plans


# ---------------------------------------------------------------------------
# --resolve / --connect-to IP-pinning parsers
# ---------------------------------------------------------------------------


def parse_resolve(value: str, target_host: str) -> str | None:
    """Parse a curl-style ``--resolve`` argument and return the pinned IP.

    Accepts ``HOST:IP`` and ``HOST:PORT:IP`` forms. Returns the IP only when the
    HOST matches ``target_host`` (so an unrelated entry is ignored).

    Args:
        value: The ``--resolve`` argument value.
        target_host: The host the load generator is targeting.

    Returns:
        The pinned IP string, or None when the entry does not match.

    Raises:
        E2EUsageError: When the value is not a valid HOST[:PORT]:IP triple/pair.
    """
    parts = value.split(":")
    if len(parts) == 2:
        host, ip = parts
    elif len(parts) == 3:
        host, _port, ip = parts
    else:
        raise e2e_common.E2EUsageError(
            f"ERROR: --resolve must be HOST:IP or HOST:PORT:IP, got {value!r}"
        )
    if host != target_host:
        return None
    return ip


def parse_connect_to(value: str) -> str | None:
    """Parse a curl-style ``--connect-to`` argument and return the connect IP.

    Accepts the full ``HOST:PORT:CONNECT_HOST:CONNECT_PORT`` form and the
    shorthand ``CONNECT_HOST`` form. Returns the connect host/IP to dial while
    SNI + Host stay the original target host.

    Args:
        value: The ``--connect-to`` argument value.

    Returns:
        The connect host/IP, or None when blank.

    Raises:
        E2EUsageError: When the value has an unsupported field count.
    """
    parts = value.split(":")
    if len(parts) == 1:
        return parts[0] or None
    if len(parts) == 4:
        return parts[2] or None
    raise e2e_common.E2EUsageError(
        f"ERROR: --connect-to must be HOST:PORT:CONNECT_HOST:CONNECT_PORT or CONNECT_HOST, "
        f"got {value!r}"
    )


# ---------------------------------------------------------------------------
# HTTPS sender with optional IP pinning (keeps SNI = target host)
# ---------------------------------------------------------------------------


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPSConnection that dials a pinned IP while keeping SNI = ``host``.

    Used to send traffic at a specific CloudFront/ALB edge IP while public DNS
    re-propagates, without losing the SNI / Host routing the edge requires.
    """

    def __init__(
        self, host: str, port: int, connect_ip: str, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._connect_ip = connect_ip
        self._ssl_context = context

    def connect(self) -> None:  # pragma: no cover - exercised only against a live edge
        sock = socket.create_connection((self._connect_ip, self.port), self.timeout)
        self.sock = self._ssl_context.wrap_socket(sock, server_hostname=self.host)


ConnectionFactory = Callable[[str, int, str | None, float], http.client.HTTPSConnection]


def _default_connection_factory(
    host: str,
    port: int,
    connect_ip: str | None,
    timeout: float,
) -> http.client.HTTPSConnection:
    """Build an HTTPSConnection, pinning ``connect_ip`` when supplied."""
    context = ssl.create_default_context()
    if connect_ip:
        return _PinnedHTTPSConnection(host, port, connect_ip, timeout, context)
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=context)


class OtlpHttpSender:
    """Stateless OTLP/HTTP sender. Each ``post`` opens its own connection.

    Opening a fresh connection per request keeps the sender safe to share across
    a thread pool (HTTPS connections are not thread-safe to reuse).
    """

    def __init__(
        self,
        host: str,
        port: int,
        connect_ip: str | None,
        timeout: float,
        user_agent: str = constants.E2E_USER_AGENT,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_ip = connect_ip
        self._timeout = timeout
        self._user_agent = user_agent
        self._factory: ConnectionFactory = connection_factory or _default_connection_factory

    def post(self, path: str, body: bytes, content_type: str) -> tuple[int, str]:
        """POST ``body`` to ``https://<host><path>`` and return ``(status, reason)``.

        A realistic ``User-Agent`` header is always sent: the collector WAF
        (AWSManagedRulesCommonRuleSet rule ``NoUserAgent_HEADER``) blocks requests
        that carry no User-Agent, so a UA-less request never reaches the receiver.

        Args:
            path: The request path (e.g. ``/v1/logs``).
            body: The encoded request body.
            content_type: The Content-Type header value.

        Returns:
            ``(http_status, reason)``. A transport failure is surfaced as status
            0 with the exception text as the reason (never masked).
        """
        conn = self._factory(self._host, self._port, self._connect_ip, self._timeout)
        try:
            conn.request(
                "POST",
                path,
                body=body,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "User-Agent": self._user_agent,
                },
            )
            response = conn.getresponse()
            status = response.status
            reason = response.reason
            response.read()
            return status, reason
        except (OSError, http.client.HTTPException) as exc:
            return 0, f"transport-error: {exc}"
        finally:
            conn.close()


def default_tcp_prober(host: str, port: int, timeout: float) -> tuple[bool, str]:
    """Probe whether a TCP listener answers on ``host:port``.

    Args:
        host: Target host or IP.
        port: Target TCP port.
        timeout: Connection timeout in seconds.

    Returns:
        ``(listener_present, detail)``. ``listener_present`` is True only when
        the TCP handshake completes.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP listener answered on {host}:{port}"
    except OSError as exc:
        return False, f"no TCP listener on {host}:{port}: {exc}"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class OtlpLoadGenerator:
    """Builds the per-mode request plan, dispatches it, and emits the manifest."""

    def __init__(
        self,
        env: str,
        endpoint_host: str,
        port: int,
        connect_ip: str | None,
        encoding: str,
        count: int,
        concurrency: int,
        run_id: str,
        mode: str,
        sender: OtlpHttpSender,
        tcp_prober: Callable[[str, int, float], tuple[bool, str]] | None = None,
        timeout: float | None = None,
    ) -> None:
        if mode not in constants.E2E_MODES:
            raise e2e_common.E2EUsageError(
                f"ERROR: unknown --mode {mode!r}: must be one of {list(constants.E2E_MODES)!r}"
            )
        if encoding not in ("protobuf", "json"):
            raise e2e_common.E2EUsageError(
                f"ERROR: unknown --encoding {encoding!r}: must be 'protobuf' or 'json'"
            )
        self._env = env
        self._host = endpoint_host
        self._port = port
        self._connect_ip = connect_ip
        self._encoding = encoding
        self._count = count
        self._concurrency = max(1, concurrency)
        self._run_id = run_id
        self._mode = mode
        self._sender = sender
        self._tcp_prober = tcp_prober or default_tcp_prober
        self._timeout = timeout if timeout is not None else float(constants.E2E_HTTP_TIMEOUT)
        # The fixed reserved tool value every record carries (``e2e-smoke``); runs
        # are separated by the payload ``run_id``, not by this shared partition.
        self._tool = e2e_common.tool_marker(run_id)

    def build_plans(self, timestamp: str) -> list[RequestPlan]:
        """Build the list of RequestPlan for the configured mode."""
        rng = secrets.SystemRandom()
        if self._mode in _DATA_MODES:
            specs = plan_events(self._mode, self._count, self._tool, self._run_id, rng, timestamp)
            return chunk_into_requests(specs, self._encoding, constants.E2E_MAX_BODY_BYTES)
        if self._mode == "structured-events":
            return [build_structured_events_request(timestamp, self._run_id, self._encoding)]
        if self._mode == "structured-metrics":
            return [build_structured_metrics_request(timestamp, self._run_id, self._encoding)]
        if self._mode == "oversize":
            return [build_oversize_request(self._tool, self._encoding, timestamp)]
        if self._mode == "bad-content-type":
            return [build_bad_content_type_request(self._tool, self._encoding, timestamp)]
        if self._mode == "malformed":
            return [build_malformed_request(self._encoding)]
        if self._mode == "wrong-signal":
            return build_wrong_signal_requests(self._encoding)
        if self._mode == "waf-trigger":
            return [build_waf_trigger_request(self._tool, self._encoding, timestamp)]
        # rate-burst -- fire more than the per-IP limit so at least one is blocked.
        burst = max(self._count, constants.E2E_WAF_RATE_LIMIT_PER_5MIN + 1)
        return build_rate_burst_requests(self._tool, self._encoding, timestamp, burst)

    def _send_one(self, plan: RequestPlan) -> RequestResult:
        status, reason = self._sender.post(plan.path, plan.body, plan.content_type)
        return RequestResult(
            label=plan.label,
            path=plan.path,
            status=status,
            reason=reason,
            expected_statuses=plan.expected_statuses,
            specs=plan.specs,
        )

    def run(self) -> tuple[dict[str, Any], int]:
        """Execute the mode and return ``(manifest, exit_code)``.

        Returns:
            The sent-manifest dict and a process exit code (0 = every request
            met its expected status for the mode).
        """
        started_at = e2e_common.utc_now_iso()

        # grpc-probe is a pure TCP negative test (no HTTP request plan).
        if self._mode == "grpc-probe":
            present, detail = self._tcp_prober(
                self._connect_ip or self._host, constants.E2E_GRPC_PORT, self._timeout
            )
            finished_at = e2e_common.utc_now_iso()
            manifest = self._base_manifest(started_at, finished_at)
            manifest["grpc_probe"] = {"listener_present": present, "detail": detail}
            ok = not present
            print(
                f"{'OK' if ok else 'FAIL'} grpc-probe: {detail} "
                f"(expected: no listener on {constants.E2E_GRPC_PORT})"
            )
            if not ok:
                print(
                    f"ERROR: a gRPC listener answered on {constants.E2E_GRPC_PORT}; "
                    "the public edge must expose OTLP/HTTP only.",
                    file=sys.stderr,
                )
            return manifest, 0 if ok else 1

        plans = self.build_plans(started_at)
        results: list[RequestResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            results = list(pool.map(self._send_one, plans))

        finished_at = e2e_common.utc_now_iso()
        manifest = self._base_manifest(started_at, finished_at)
        self._populate_manifest(manifest, results)

        deviations = [r for r in results if not r.ok]
        for result in results:
            verdict = "OK" if result.ok else "FAIL"
            print(
                f"{verdict} {result.label} {result.path} -> {result.status} {result.reason} "
                f"(expected {list(result.expected_statuses)})"
            )
        if deviations:
            for result in deviations:
                print(
                    f"ERROR: request {result.label} returned {result.status} "
                    f"({result.reason}); expected one of {list(result.expected_statuses)}",
                    file=sys.stderr,
                )
            return manifest, 1
        return manifest, 0

    def _base_manifest(self, started_at: str, finished_at: str) -> dict[str, Any]:
        return {
            "run_id": self._run_id,
            "env": self._env,
            "endpoint_host": self._host,
            "connect_ip": self._connect_ip,
            "port": self._port,
            "encoding": self._encoding,
            "mode": self._mode,
            "tool_value": self._tool,
            "started_at": started_at,
            "finished_at": finished_at,
            "expected_dt_partitions": [e2e_common.dt_partition(started_at)],
            "counts_by_event": {},
            "total_records": 0,
            "sample_records": [],
            "http_status_summary": {},
        }

    def _populate_manifest(self, manifest: dict[str, Any], results: list[RequestResult]) -> None:
        status_summary: dict[str, int] = {}
        counts: dict[str, int] = {}
        samples: list[dict[str, Any]] = []
        total = 0
        for result in results:
            key = str(result.status)
            status_summary[key] = status_summary.get(key, 0) + 1
            # Only records the receiver accepted (200) on a data-landing path land in S3.
            if result.status == 200 and result.path in _LANDING_PATHS:
                for spec in result.specs:
                    # Every accepted record shares tool=e2e-smoke; this run's counts
                    # are keyed by event_type and scoped to the run by the manifest
                    # run_id (embedded in each record's payload as $.run_id).
                    counts[spec.event_type] = counts.get(spec.event_type, 0) + 1
                    total += 1
                    if len(samples) < 5:
                        # build_record_body always renders the generic
                        # {timestamp, tool, event_type, payload} envelope. For
                        # structured-events and structured-metrics, that envelope is NOT
                        # the literal wire record sent (a structured-events Body is
                        # "claude_code.<event>"; a structured-metrics record is an OTLP
                        # NumberDataPoint, not a LogRecord at all -- see
                        # build_structured_events_request / build_structured_metrics_request);
                        # body_sha256 there hashes an informational reconstruction of
                        # the record, not the bytes on the wire. payload_sha256 is
                        # accurate for every mode: it always hashes spec.payload verbatim.
                        body = build_record_body(spec)
                        samples.append(
                            {
                                "tool": spec.tool,
                                "run_id": self._run_id,
                                "event_type": spec.event_type,
                                "timestamp": spec.timestamp,
                                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                                "payload_sha256": hashlib.sha256(
                                    spec.payload.encode("utf-8")
                                ).hexdigest(),
                            }
                        )
        manifest["http_status_summary"] = status_summary
        # Per-(event_type) counts for this run. The verifier reconciles these against
        # Athena with WHERE tool='e2e-smoke' AND json_extract_scalar(payload,
        # '$.run_id')='<run-id>' -- runs are separated by the payload run_id, not by
        # the shared enum-projected tool partition.
        manifest["counts_by_event"] = counts
        manifest["total_records"] = total
        manifest["sample_records"] = samples


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _generate_run_id() -> str:
    """Return a fresh run id (``<UTC-compact>-<hex>``).

    Thin wrapper over ``e2e_common.generate_run_id`` (shared with
    ``scripts.perf_loadgen``) -- see that function's docstring for the
    non-digit-suffix requirement (Athena varchar binding).
    """
    return e2e_common.generate_run_id()


def normalize_endpoint_host(value: str) -> str:
    """Return the bare collector HOST from a ``--endpoint`` value (host OR full URL).

    ``--endpoint`` overrides the domains.json-derived collector host. Callers may
    pass either a bare host (``d123.cloudfront.net``) or a full URL
    (``https://d123.cloudfront.net/v1/logs`` -- the ``collector_endpoint`` the
    perf-test workflow derives from the CloudFront default domain, which is the
    same value the readiness probe POSTs to). When a URL is given, only its host
    is used: the load driver always appends the OTLP signal path
    (``/v1/logs`` / ``/v1/metrics``) itself and takes the port from ``--port``, so
    any path/scheme in the URL is intentionally ignored. A bare host (no
    ``://``) is returned verbatim, so existing bare-host callers are unaffected.

    Args:
        value: The raw ``--endpoint`` value (bare host or full URL).

    Returns:
        The bare host to connect to.

    Raises:
        e2e_common.E2EUsageError: When a URL is given but carries no host.
    """
    if "://" not in value:
        return value
    host = urllib.parse.urlparse(value).hostname
    if not host:
        raise e2e_common.E2EUsageError(
            f"ERROR: --endpoint URL {value!r} has no host to connect to."
        )
    return host


def _resolve_endpoint_host(args: argparse.Namespace) -> str:
    """Resolve the target collector host from --endpoint or domains.json."""
    if args.endpoint:
        return normalize_endpoint_host(str(args.endpoint))
    domains = e2e_common.load_json(e2e_common.DOMAINS_JSON_PATH)
    endpoints = e2e_common.resolve_endpoints(args.env, domains)
    return endpoints["collector_pretty_fqdn"]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the load generator CLI."""
    parser = argparse.ArgumentParser(
        prog="scripts.otlp_e2e_loadgen",
        description="Standalone OTLP/HTTP load + conformance generator for the collector.",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment to target (resolves the published collector FQDN from domains.json).",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help=(
            "Override the collector host (default: collector.<pretty_apex> for --env). Accepts a "
            "bare host or a full https:// URL (only the host is used; the OTLP signal path and "
            "--port are applied by the driver)."
        ),
    )
    parser.add_argument(
        "--resolve",
        default=None,
        help="curl-style HOST:IP (or HOST:PORT:IP) to pin the edge IP while keeping SNI.",
    )
    parser.add_argument(
        "--connect-to",
        dest="connect_to",
        default=None,
        help="curl-style HOST:PORT:CONNECT_HOST:CONNECT_PORT (or CONNECT_HOST) to pin the edge IP.",
    )
    parser.add_argument(
        "--encoding",
        default="protobuf",
        choices=("protobuf", "json"),
        help="OTLP/HTTP encoding (default: protobuf).",
    )
    parser.add_argument("--count", type=int, default=50, help="Number of records/requests.")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent senders.")
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help=(
            "Run id (default: generated). Records carry the fixed tool value "
            "'e2e-smoke'; this run id is embedded in every payload ($.run_id) so the "
            "verifier can reconcile this run within the shared tool partition."
        ),
    )
    parser.add_argument(
        "--mode",
        default="happy",
        choices=list(constants.E2E_MODES),
        help="Conformance mode (default: happy).",
    )
    parser.add_argument(
        "--manifest-out",
        dest="manifest_out",
        default=None,
        help="Path to write the sent-manifest JSON (default: stdout only).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=constants.E2E_HTTPS_PORT,
        help=f"HTTPS port (default: {constants.E2E_HTTPS_PORT}).",
    )
    parser.add_argument(
        "--user-agent",
        dest="user_agent",
        default=constants.E2E_USER_AGENT,
        help=(
            "User-Agent header sent on every request "
            f"(default: {constants.E2E_USER_AGENT}). The collector WAF blocks "
            "User-Agent-less requests, so this is always sent."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the OTLP load generator."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        host = _resolve_endpoint_host(args)
        connect_ip: str | None = None
        if args.resolve:
            connect_ip = parse_resolve(args.resolve, host)
        if args.connect_to:
            connect_ip = parse_connect_to(args.connect_to)
        run_id = args.run_id or _generate_run_id()
        sender = OtlpHttpSender(
            host=host,
            port=args.port,
            connect_ip=connect_ip,
            timeout=float(constants.E2E_HTTP_TIMEOUT),
            user_agent=args.user_agent,
        )
        generator = OtlpLoadGenerator(
            env=args.env,
            endpoint_host=host,
            port=args.port,
            connect_ip=connect_ip,
            encoding=args.encoding,
            count=args.count,
            concurrency=args.concurrency,
            run_id=run_id,
            mode=args.mode,
            sender=sender,
        )
    except e2e_common.E2EUsageError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    manifest, exit_code = generator.run()

    if args.manifest_out:
        pathlib.Path(args.manifest_out).write_text(json.dumps(manifest, indent=2))
        print(f"manifest written to {args.manifest_out}")
    else:
        print(json.dumps(manifest, indent=2))

    sys.exit(exit_code)


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
