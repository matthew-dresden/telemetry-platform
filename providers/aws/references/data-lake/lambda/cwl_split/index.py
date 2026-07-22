"""Amazon Data Firehose transform for a CloudWatch Logs subscription source.

CloudWatch Logs subscription-filter records arrive GZIP-compressed and wrapped in the
``{messageType, owner, logGroup, logStream, subscriptionFilters, logEvents[]}`` envelope,
and a single subscription record batches MANY ``logEvents``. The AWS-native Firehose
``RecordDeAggregation`` processor is hard-capped at 500 sub-records per record, so a
delivery whose record carries more than 500 events is passed WHOLE to the
dynamic-partitioning ``MetadataExtraction`` jq engine, which rejects the multi-object blob
with "Non JSON record provided" (``DynamicPartitioning.MetadataExtractionFailed``) and routes
every event to ``errors/metadata-extraction-failed/``.

A Firehose transform Lambda is strictly 1:1 (each returned record must reuse its input
``recordId`` and the function cannot emit more records than it received), so this function
cannot split a record by returning N records. Instead it follows the AWS-sanctioned
re-ingestion pattern: it decompresses the envelope, extracts each ``logEvents[].message`` as
its own single-JSON record, and re-ingests those records into the SAME delivery stream via
``firehose:PutRecordBatch`` (chunked to the 500-record API limit), marking the original
aggregated record ``Dropped``. Re-ingested records are plain single-event JSON (not GZIP), so
on re-invocation they take the pass-through branch and flow 1:1 to ``MetadataExtraction`` +
Parquet conversion. Re-ingestion has no 500-record cap, so a CloudWatch Logs delivery of any
size is split into individual JSON records before ``MetadataExtraction``.

Message reshaping (multi-tool ingestion). Each extracted ``logEvents[].message`` is passed
through ``_reshape_records`` before re-ingestion, which returns a LIST of rows (usually one --
an EMF metrics event can declare several distinct metrics and yields one row per metric).
``_reshape_records`` delegates every non-EMF message UNCHANGED to ``_reshape_message`` (the
byte-identical passthrough classifier). Tools that follow the documented body contract (example-cli
and any tool that emits a flat JSON body with a top-level ``tool`` key) pass through
BYTE-IDENTICAL -- their message is re-ingested exactly as received. Structured OTLP log records
(e.g. Claude Code / Cowork / Office agents, exported by the collector's ``raw_log=false``
pipeline as ``{body, attributes, resource, scope}``) are reshaped into the same flat
``{timestamp, tool, event_type, payload}`` canonical envelope so the
downstream Firehose ``MetadataExtraction`` (jq ``{tool:.tool}``) and the Glue columns work
UNCHANGED: ``tool`` is mapped from ``resource.service.name`` via an input-driven map
(``SERVICE_TOOL_MAP``, derived from the tool registry), ``event_type`` is the event name, and
``payload`` is a JSON string carrying every attribute + resource field (flattened, dots
normalized to underscores) so no data is lost and every field is queryable in Athena (e.g.
``json_extract_scalar(payload,'$.marketplace_name')``). Structured-OTLP metrics, exported by
the collector's ``awsemf`` pipeline as flat EMF log events (``_aws`` + metric-name-keyed values
+ flattened resource/attributes), are reshaped by ``_reshape_emf`` into the SAME canonical
envelope -- one row per metric declared in ``_aws.CloudWatchMetrics``, with
``payload.otel_signal="metric"`` distinguishing a metric row from a structured LOG row. The
classifier is fail-closed to passthrough: anything that is not positively identified as a
structured record or an EMF metrics event -- above all any message carrying a top-level
``tool`` -- is re-ingested unchanged, so a body-contract tool can never be misclassified or
altered.

Fail-fast, no catch-all tool. ``SERVICE_TOOL_MAP`` has NO fallback: a structured/EMF record
whose ``resource.service.name`` (or, for EMF, the top-level ``service.name``) is not a key in
the map raises ``KeyError`` out of ``_reshape_claude``/``_reshape_emf``. In normal operation
this never happens -- the collector's structured pipeline routes ONLY the tool registry's
registered service names, which are exactly the map's keys (both derived from the same
``common/tool-registry.json``). The handler defends against a config-drift window (e.g. the
collector redeployed before the Lambda's env) with PER-RECORD isolation: each ``logEvents[]``
entry's ``_reshape_records`` call is wrapped in its own try/except, so one record's reshape
failure can never crash the whole CloudWatch Logs delivery and drop every co-batched record
(example-cli, other tools, other structured records) riding in the same subscription record. On any
exception the record's ORIGINAL bytes are re-ingested unchanged; a structured/EMF record
carries no top-level ``tool`` field, so Firehose's ``{tool:.tool}`` dynamic-partitioning JQ
evaluates to null and routes it to the monitored ``errors/`` prefix instead of ``raw/`` --
fail-fast and visible, never a silent catch-all ``tool`` value and never dropped.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3

# Required configuration -- fail fast (no fallback) if the stream name is unset.
_DELIVERY_STREAM_NAME = os.environ["DELIVERY_STREAM_NAME"]

# AWS PutRecordBatch hard limit: 500 records per call.
_PUT_RECORD_BATCH_MAX = 500
# Bounded retry budget for re-ingestion of records Firehose reports as failed.
_REINGEST_MAX_ATTEMPTS = int(os.environ.get("REINGEST_MAX_ATTEMPTS", "5"))

_CONTROL_MESSAGE = "CONTROL_MESSAGE"
_GZIP_MAGIC = b"\x1f\x8b"

# Structured-OTLP reshaping configuration, input-driven (no hardcoded tool identities).
# ``SERVICE_TOOL_MAP`` maps an OTLP ``resource.service.name`` to the lake ``tool`` partition
# value (e.g. {"claude-code":"claude-code"}), derived from the tool registry
# (common/tool-registry.json) at deploy time. NO FALLBACK: a structured/EMF record whose
# service.name is not a key in this map is never reshaped with a catch-all tool -- see the
# module docstring's "Fail-fast, no catch-all tool" section and the handler's per-record
# isolation. Absent/empty map is a safe default: only records positively identified as
# structured records are ever reshaped, and their service.name is preserved inside ``payload``
# regardless.
_SERVICE_TOOL_MAP: dict[str, str] = json.loads(os.environ.get("SERVICE_TOOL_MAP", "{}"))
# Non-null fallback so a reshaped record's event_type is always well-formed. Unrelated to the
# tool lookup, which has no fallback (see _SERVICE_TOOL_MAP above).
_EVENT_TYPE_FALLBACK = "unknown"

_firehose = boto3.client("firehose")


def _is_gzip(data: bytes) -> bool:
    return data[:2] == _GZIP_MAGIC


def _to_map(value: Any) -> dict[str, Any]:
    """Return an attribute/resource block as a flat ``{key: value}`` dict.

    The collector's ``raw_log=false`` exporter (ADOT v0.43.1, empirically confirmed) writes
    ``attributes`` and ``resource`` as flat JSON objects. Older/other exporters may write the
    OTLP list-of-KV form ``[{"key":k,"value":{"stringValue":v}}]``; both are tolerated so the
    parser is not brittle to the exporter shape. A non-dict/list value yields an empty map.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        flat: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, dict) or "key" not in item:
                continue
            raw = item.get("value")
            if isinstance(raw, dict) and raw:
                flat[item["key"]] = next(iter(raw.values()))
            else:
                flat[item["key"]] = raw
        return flat
    return {}


def _norm(key: str) -> str:
    """Normalize an attribute key to a dot-free token for clean Athena JSON paths."""
    return key.replace(".", "_")


# OTel RESOURCE attribute keys that an EMF event carries as flat top-level fields (via the
# collector's ``resource_to_telemetry_conversion``). These get a ``resource_`` prefix in
# ``_reshape_emf`` output, mirroring the ``resource_`` prefix ``_reshape_claude`` already applies
# to the OTLP ``resource`` block, so a metric row and a log row expose the same
# ``resource_service_name`` / ``resource_service_version`` fields. Fixed (not input-driven): this
# is the OTel SDK's own resource-attribute vocabulary, not a tool-specific identity.
_EMF_RESOURCE_KEYS = frozenset(
    {
        "service.name",
        "service.version",
        "host.arch",
        "host.name",
        "os.type",
        "os.version",
        "os.description",
    }
)
_EMF_RESOURCE_SDK_PREFIX = "telemetry.sdk."


def _is_emf_resource_key(key: str) -> bool:
    """True for an OTel RESOURCE attribute key flattened onto an EMF event."""
    return key in _EMF_RESOURCE_KEYS or key.startswith(_EMF_RESOURCE_SDK_PREFIX)


def _reshape_claude(obj: dict[str, Any], body: str) -> bytes:
    """Reshape one structured OTLP record into the canonical flat envelope.

    Raises ``KeyError`` if ``resource.service.name`` is not a key in ``_SERVICE_TOOL_MAP`` --
    NO fallback tool (see the module docstring). The caller (the handler's per-record loop in
    ``handler()``) isolates this failure to the single offending record so it can never crash
    the whole batch. Every OTHER derived field has a non-null fallback, so a successfully-mapped
    record is always a well-formed, queryable ``{timestamp, tool, event_type, payload}`` row.
    """
    attrs = _to_map(obj.get("attributes"))
    resource = _to_map(obj.get("resource"))
    service_name = str(resource.get("service.name", ""))
    tool = _SERVICE_TOOL_MAP[service_name]
    event_type = str(attrs.get("event.name") or body or _EVENT_TYPE_FALLBACK)
    timestamp = str(attrs.get("event.timestamp") or _record_timestamp_iso(obj) or _now_iso())

    payload: dict[str, Any] = {}
    for key, val in attrs.items():
        payload[_norm(key)] = val
    for key, val in resource.items():
        payload[f"resource_{_norm(key)}"] = val
    payload["otel_body"] = body
    scope = obj.get("scope") or obj.get("instrumentationScope")
    if isinstance(scope, dict):
        if scope.get("name") is not None:
            payload["otel_scope_name"] = scope.get("name")
        if scope.get("version") is not None:
            payload["otel_scope_version"] = scope.get("version")

    canonical = {
        "timestamp": timestamp,
        "tool": tool,
        "event_type": event_type,
        "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _emf_timestamp_iso(aws_block: dict[str, Any]) -> str:
    """ISO-8601 UTC from an EMF ``_aws.Timestamp`` (epoch milliseconds); non-numeric -> now().

    Mirrors ``_record_timestamp_iso``'s guard: only str/int/float can reach ``int()``, so the
    single-exception ``except ValueError`` can never need a second exception arm (the repo
    targets python3.14, whose unparenthesized multi-except is a SyntaxError on the python3.12
    Lambda runtime).
    """
    raw = aws_block.get("Timestamp")
    if not isinstance(raw, (str, int, float)):
        return _now_iso()
    try:
        millis = int(raw)
    except ValueError:
        return _now_iso()
    return datetime.fromtimestamp(millis / 1_000, tz=UTC).isoformat().replace("+00:00", "Z")


def _reshape_emf(obj: dict[str, Any]) -> list[bytes]:
    """Reshape one EMF (awsemf-exported) metrics event into one canonical row per metric.

    An EMF event (see the golden fixtures in ``tests/unit/fixtures/claude/metrics/``) is a FLAT
    JSON object: ``_aws.CloudWatchMetrics[*].Metrics[*]`` each declare one metric NAME whose
    VALUE lives in a top-level field keyed by that exact name (e.g.
    ``"claude_code.token.usage": 526``); every other top-level field is an OTel resource or
    attribute flattened onto the event by the collector's ``resource_to_telemetry_conversion``.
    A single EMF event can declare more than one DISTINCT metric, so this returns one row per
    distinct metric Name -- never dropping a datapoint. ``payload.otel_signal="metric"`` is the
    metric-vs-log discriminator (NOT the ``event_type`` value) so Athena can tell a metric row
    apart from a structured LOG row reshaped by ``_reshape_claude``.

    Raises ``KeyError`` if the event's ``service.name`` is not a key in ``_SERVICE_TOOL_MAP`` --
    NO fallback tool (see the module docstring and ``_reshape_claude``). The caller (the
    handler's per-record loop) isolates this failure to the single offending record.
    """
    aws_block = obj.get("_aws")
    aws_map = aws_block if isinstance(aws_block, dict) else {}
    timestamp = _emf_timestamp_iso(aws_map)
    scope = obj.get("OTelLib")

    # Dedupe by metric Name: awsemf's default dimension rollup can declare the SAME metric Name
    # in more than one ``CloudWatchMetrics`` dimension set, but there is exactly one top-level
    # VALUE per Name, so emitting one row per DISTINCT Name (first definition wins for Unit) is
    # lossless and correct -- appending every ``Metrics`` entry would emit duplicate rows with
    # identical values and double-count token/cost SUMs downstream.
    metric_by_name: dict[str, dict[str, Any]] = {}
    for cw_metrics in aws_map.get("CloudWatchMetrics") or []:
        if not isinstance(cw_metrics, dict):
            continue
        for metric in cw_metrics.get("Metrics") or []:
            if not isinstance(metric, dict):
                continue
            name = metric.get("Name")
            if not isinstance(name, str) or not name:
                continue
            metric_by_name.setdefault(name, metric)
    metric_names = set(metric_by_name)

    # Every top-level field except the EMF envelope keys and the metric-name value keys is an
    # OTel resource/attribute (identity/PII fields are fully retained per policy -- no redaction).
    base_payload: dict[str, Any] = {}
    for key, val in obj.items():
        if key in ("_aws", "OTelLib", "Version") or key in metric_names:
            continue
        norm_key = _norm(key)
        base_payload[f"resource_{norm_key}" if _is_emf_resource_key(key) else norm_key] = val
    if scope is not None:
        base_payload["otel_scope_name"] = scope

    # ``service.name`` can be any JSON value; ``str(...)`` mirrors ``_reshape_claude`` and keeps
    # a non-string (dict/list) from raising ``TypeError: unhashable type`` (a DIFFERENT
    # exception) on the dict lookup below -- so an unmapped-or-malformed service.name always
    # raises the SAME ``KeyError`` the handler's per-record isolation expects, whether or not
    # it's a string. NO FALLBACK: an unmapped service.name is never reshaped with a catch-all
    # tool (see the module docstring and _SERVICE_TOOL_MAP).
    tool = _SERVICE_TOOL_MAP[str(obj.get("service.name", ""))]

    rows: list[bytes] = []
    for metric in metric_by_name.values():
        name = metric["Name"]
        payload = dict(base_payload)
        payload["value"] = obj.get(name)
        payload["unit"] = metric.get("Unit")
        # awsemf converts cumulative monotonic OTel sums to per-interval deltas, so lake
        # consumers SUM these rows for totals, never MAX -- recorded explicitly so an Athena
        # query never has to assume it (see data-model.md).
        payload["metric_type"] = "SUM"
        payload["otel_signal"] = "metric"
        canonical = {
            "timestamp": timestamp,
            "tool": tool,
            "event_type": name,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }
        rows.append(
            json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    return rows


def _record_timestamp_iso(obj: dict[str, Any]) -> str | None:
    """Best-effort ISO-8601 from an OTLP log record's unix-nano timestamp, if present."""
    for key in ("timeUnixNano", "observedTimeUnixNano", "time_unix_nano", "time"):
        raw = obj.get(key)
        # Guard the type so int() can only raise ValueError (a numeric-looking string), never
        # TypeError -- a single-exception except keeps this parseable on the python3.12 Lambda
        # runtime (the repo targets 3.14, whose unparenthesized multi-except is a 3.12 SyntaxError).
        if not isinstance(raw, (str, int, float)):
            continue
        try:
            nanos = int(raw)
        except ValueError:
            continue
        return (
            datetime.fromtimestamp(nanos / 1_000_000_000, tz=UTC).isoformat().replace("+00:00", "Z")
        )
    return None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _reshape_message(message: str) -> bytes:
    """Reshape one ``logEvents[].message`` before re-ingestion.

    Fail-closed-to-passthrough classifier (order matters):
      1. not parseable JSON, or not a JSON object -> passthrough ORIGINAL bytes.
      2. object with a top-level ``tool`` key -> body-contract tool (example-cli, ...) -> passthrough
         ORIGINAL bytes, BYTE-IDENTICAL (never a JSON round-trip that could reorder/reformat).
      3. object with ``resource`` + ``attributes`` + a string ``body`` and NO top-level
         ``tool`` -> structured claude-family record -> reshape.
      4. anything else -> passthrough ORIGINAL bytes.
    """
    original = message.encode("utf-8")
    try:
        obj = json.loads(message)
    except ValueError:
        # json.JSONDecodeError subclasses ValueError; message is always a str (a CWL
        # logEvents[].message), so json.loads never raises TypeError here. A single-exception
        # except stays parseable on the python3.12 Lambda runtime (see _record_timestamp_iso).
        return original
    if not isinstance(obj, dict):
        return original
    if "tool" in obj:
        return original
    body = obj.get("body")
    if (
        isinstance(obj.get("resource"), (dict, list))
        and isinstance(obj.get("attributes"), (dict, list))
        and isinstance(body, str)
    ):
        return _reshape_claude(obj, body)
    return original


def _reshape_records(message: str) -> list[bytes]:
    """Reshape one ``logEvents[].message`` into a LIST of re-ingestion records.

    A single EMF (awsemf) metrics event can declare several DISTINCT metrics and therefore
    yields several canonical rows, so the caller must ``extend`` this list (never ``append`` a
    scalar). Every non-EMF message yields exactly one row and is delegated UNCHANGED to
    ``_reshape_message`` (the example-cli-sacred, byte-identical-passthrough classifier), so the log
    and passthrough behavior is untouched by the metrics work.

    An EMF metrics event is a dict with NO top-level ``tool`` (which would win as a
    body-contract passthrough) AND a top-level ``_aws`` dict whose ``CloudWatchMetrics`` is a
    list; such a record is routed to ``_reshape_emf``. Anything else is a single
    ``_reshape_message`` row. Non-EMF records are parsed twice (here and in ``_reshape_message``),
    an accepted cost of keeping ``_reshape_message`` behavior-identical to the pre-metrics module.

    Can raise ``KeyError`` (propagated, uncaught, from ``_reshape_claude``/``_reshape_emf``) for
    a structured/EMF record whose service.name is not in ``_SERVICE_TOOL_MAP`` -- NO fallback
    tool. The caller (``handler()``) wraps each ``logEvents[]`` entry's call to this function in
    its own try/except so one record's failure is isolated and cannot crash the whole batch.
    """
    try:
        obj = json.loads(message)
    except ValueError:
        return [_reshape_message(message)]
    if (
        isinstance(obj, dict)
        and "tool" not in obj
        and isinstance(obj.get("_aws"), dict)
        and isinstance(obj["_aws"].get("CloudWatchMetrics"), list)
    ):
        return _reshape_emf(obj)
    return [_reshape_message(message)]


def _reingest(records: list[bytes]) -> None:
    """Re-ingest single-event records into the delivery stream via PutRecordBatch.

    Retries only the records Firehose reports as failed, up to a bounded budget; raises
    after the budget is exhausted so the original record is reported ProcessingFailed
    (Firehose then retries it -- no silent data loss).
    """
    for start in range(0, len(records), _PUT_RECORD_BATCH_MAX):
        pending = [{"Data": payload} for payload in records[start : start + _PUT_RECORD_BATCH_MAX]]
        for attempt in range(_REINGEST_MAX_ATTEMPTS):
            response = _firehose.put_record_batch(
                DeliveryStreamName=_DELIVERY_STREAM_NAME, Records=pending
            )
            if response.get("FailedPutCount", 0) == 0:
                break
            results = response["RequestResponses"]
            pending = [pending[i] for i, res in enumerate(results) if res.get("ErrorCode")]
            if attempt == _REINGEST_MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    f"re-ingestion failed for {len(pending)} record(s) after "
                    f"{_REINGEST_MAX_ATTEMPTS} attempts to {_DELIVERY_STREAM_NAME}"
                )


def _diagnostic_service_name(message: str) -> str:
    """Best-effort ``service.name`` extraction from a raw message, for diagnostic logging ONLY.

    Never raises: this is called from inside the handler's per-record ``except`` block, so a
    second failure here must not mask the original one. Checks the structured-OTLP shape
    (``resource.service.name``) then the EMF shape (top-level ``service.name``); returns
    "unknown" if neither is present or the message is not parseable JSON.
    """
    try:
        obj = json.loads(message)
    except ValueError:
        return "unknown"
    if not isinstance(obj, dict):
        return "unknown"
    resource = obj.get("resource")
    if isinstance(resource, dict) and "service.name" in resource:
        return str(resource["service.name"])
    if "service.name" in obj:
        return str(obj["service.name"])
    return "unknown"


def handler(event, _context):
    """Firehose transform entrypoint. Returns one status per input record (1:1)."""
    output = []
    for record in event["records"]:
        record_id = record["recordId"]
        data = base64.b64decode(record["data"])

        if not _is_gzip(data):
            # A re-ingested single-event JSON record: pass through unchanged (1:1).
            output.append({"recordId": record_id, "result": "Ok", "data": record["data"]})
            continue

        envelope = json.loads(gzip.decompress(data))
        if envelope.get("messageType") == _CONTROL_MESSAGE:
            # CloudWatch Logs subscription control messages carry no log data.
            output.append({"recordId": record_id, "result": "Dropped"})
            continue

        events: list[bytes] = []
        for log_event in envelope.get("logEvents", []):
            # CloudWatch Logs guarantees logEvents[].message is a string; coerce defensively so
            # the except branch below (str.encode + the diagnostic) can never itself raise a
            # TypeError/AttributeError outside a try and crash the whole batch.
            message = str(log_event.get("message", ""))
            # PER-RECORD ISOLATION (fail-fast-to-errors, NOT a fallback tool). _reshape_records
            # raises KeyError for a structured/EMF record whose service.name is unmapped in
            # SERVICE_TOOL_MAP (no fallback -- see the module docstring). Scoping the try/except
            # to ONE logEvent, rather than wrapping the whole loop, means a single bad record can
            # never crash the entire CloudWatch Logs delivery and drop every co-batched record
            # (example-cli, other tools, other structured records) riding in the same subscription
            # record. On any exception the record's ORIGINAL bytes are re-ingested unchanged; a
            # structured/EMF record carries no top-level "tool" field, so Firehose's {tool:.tool}
            # MetadataExtraction evaluates to null and routes it to the monitored errors/ prefix
            # instead of raw/ -- fail-fast and visible, never a silent catch-all tool and never
            # dropped. In normal operation this branch is never taken: the collector's structured
            # pipeline routes ONLY the tool registry's registered service names, which are
            # exactly SERVICE_TOOL_MAP's keys (same registry). It defends a config-drift window
            # (e.g. the collector redeployed before the Lambda's env).
            try:
                events.extend(_reshape_records(message))
            except Exception as exc:  # per-record isolation -- see comment above
                print(
                    "cwl_split: reshape failed for service.name="
                    f"{_diagnostic_service_name(message)!r}: {exc!r}; re-ingesting original "
                    "bytes so the record routes to errors/ (no top-level tool) instead of "
                    "failing the batch"
                )
                events.append(message.encode("utf-8"))
        try:
            if events:
                _reingest(events)
        except Exception:  # surface as a transform failure so Firehose retries -- no silent loss
            output.append({"recordId": record_id, "result": "ProcessingFailed"})
            continue
        # The aggregated record's events were re-ingested individually; drop the original.
        output.append({"recordId": record_id, "result": "Dropped"})

    return {"records": output}
