"""Unit tests for scripts/otlp_e2e_loadgen.py.

No real network: the HTTPS sender is exercised through an injected connection
factory and the load generator through an injected sender / TCP prober. OTLP
protobuf + JSON encodings are constructed and parsed for real (no AWS).
"""

from __future__ import annotations

import json
import pathlib
import secrets
import socket
from typing import Any

import pytest

from scripts import constants
from scripts import otlp_e2e_loadgen as lg

RNG = secrets.SystemRandom()
TS = "2026-06-28T12:00:00Z"
# Every synthetic record now carries the single fixed reserved tool value; the run
# identity lives in each payload as $.run_id (constants.E2E_TOOL_VALUE == "e2e-smoke").
TOOL = "e2e-smoke"
RUN_ID = "testrun"


class FakeSender:
    """A sender that records calls and returns a fixed status."""

    def __init__(self, status: int = 200, reason: str = "OK") -> None:
        self._status = status
        self._reason = reason
        self.calls: list[tuple[str, int, str]] = []

    def post(self, path: str, body: bytes, content_type: str) -> tuple[int, str]:
        self.calls.append((path, len(body), content_type))
        return self._status, self._reason


# ---------------------------------------------------------------------------
# Record body + payload
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_record_body_shape() -> None:
    spec = lg.EventSpec(tool=TOOL, event_type="session_start", payload='{"a":1}', timestamp=TS)
    decoded = json.loads(lg.build_record_body(spec))
    assert decoded == {
        "timestamp": TS,
        "tool": TOOL,
        "event_type": "session_start",
        "payload": '{"a":1}',
    }


@pytest.mark.unit
def test_build_record_body_preserves_unicode() -> None:
    spec = lg.EventSpec(tool=TOOL, event_type="feature_usage", payload="🚀世界", timestamp=TS)
    body = lg.build_record_body(spec)
    assert "🚀世界" in body
    assert json.loads(body)["payload"] == "🚀世界"


@pytest.mark.unit
@pytest.mark.parametrize("variety", list(constants.E2E_PAYLOAD_VARIETIES))
def test_generate_payload_each_variety_is_valid_json(variety: str) -> None:
    payload = lg.generate_payload(variety, RNG, large_bytes=2048)
    json.loads(payload)  # must not raise


@pytest.mark.unit
def test_generate_payload_large_is_sized() -> None:
    payload = lg.generate_payload("large", RNG, large_bytes=10000)
    assert len(payload) >= 9000


@pytest.mark.unit
def test_generate_payload_control_chars_round_trip() -> None:
    payload = lg.generate_payload("control-chars", RNG, large_bytes=0)
    assert "\x00" in json.loads(payload)["raw"]


@pytest.mark.unit
def test_generate_payload_unknown_variety_raises() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError, match="unknown payload variety"):
        lg.generate_payload("bogus", RNG, large_bytes=0)


# ---------------------------------------------------------------------------
# embed_run_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("variety", list(constants.E2E_PAYLOAD_VARIETIES))
def test_embed_run_id_adds_top_level_run_id_for_every_variety(variety: str) -> None:
    # The verifier scopes a run with json_extract_scalar(payload, '$.run_id'), so
    # run_id must be a TOP-LEVEL key in the payload JSON for every payload variety.
    payload = lg.generate_payload(variety, RNG, large_bytes=2048)
    embedded = lg.embed_run_id(payload, RUN_ID)
    obj = json.loads(embedded)
    assert obj["run_id"] == RUN_ID
    # The original payload keys are preserved (run_id is added, nothing dropped).
    assert set(json.loads(payload)) <= set(obj)


@pytest.mark.unit
def test_embed_run_id_preserves_unicode() -> None:
    payload = lg.generate_payload("unicode", RNG, large_bytes=0)
    embedded = lg.embed_run_id(payload, RUN_ID)
    # Unicode survives the re-serialization (ensure_ascii=False), and run_id is added.
    assert json.loads(embedded)["run_id"] == RUN_ID
    assert json.loads(embedded)["emoji"] == json.loads(payload)["emoji"]


# ---------------------------------------------------------------------------
# _generate_run_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_run_id_suffix_is_never_all_digits() -> None:
    # The reconcile query (scripts/e2e_verify) binds the run id as a native Athena
    # ExecutionParameter; a purely <digits>-<digits> run id is inferred as bigint
    # arithmetic, so the query fails "varchar = bigint" TYPE_MISMATCH. The suffix
    # must always carry a non-digit so Athena binds the run id as varchar.
    # secrets.token_hex(3) is all-digits ~6% of draws, so sample enough to exercise
    # the redraw path.
    for _ in range(500):
        run_id = lg._generate_run_id()
        stamp, sep, suffix = run_id.partition("-")
        assert sep == "-", f"run id missing the stamp-suffix separator: {run_id!r}"
        assert stamp.isdigit(), f"stamp is not all-digits: {run_id!r}"
        assert len(suffix) == 6, f"unexpected suffix length: {run_id!r}"
        assert not suffix.isdigit(), f"all-digit suffix binds as bigint in Athena: {run_id!r}"
        assert any(c.isalpha() for c in run_id), f"run id has no alpha char: {run_id!r}"


# ---------------------------------------------------------------------------
# plan_events
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_plan_events_count_and_cycle() -> None:
    specs = lg.plan_events("happy", 8, TOOL, RUN_ID, RNG, TS)
    assert len(specs) == 8
    assert all(s.tool == TOOL and s.timestamp == TS for s in specs)
    # event types cycle through the taxonomy
    assert specs[0].event_type == constants.E2E_EVENT_TYPES[0]
    assert specs[6].event_type == constants.E2E_EVENT_TYPES[0]


@pytest.mark.unit
def test_plan_events_variety_uses_varieties() -> None:
    specs = lg.plan_events("variety", len(constants.E2E_PAYLOAD_VARIETIES), TOOL, RUN_ID, RNG, TS)
    # the 5th record (index 4) is the 'large' variety -> noticeably bigger payload
    assert len(specs[4].payload) > len(specs[0].payload)


# ---------------------------------------------------------------------------
# OTLP request construction + encoding
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_export_request_one_record_per_spec() -> None:
    specs = lg.plan_events("happy", 3, TOOL, RUN_ID, RNG, TS)
    request = lg.build_export_request(specs)
    records = request.resource_logs[0].scope_logs[0].log_records
    assert len(records) == 3
    assert json.loads(records[0].body.string_value)["tool"] == TOOL


@pytest.mark.unit
def test_encode_request_protobuf_round_trips() -> None:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    specs = lg.plan_events("happy", 2, TOOL, RUN_ID, RNG, TS)
    body, content_type = lg.encode_request(lg.build_export_request(specs), "protobuf")
    assert content_type == constants.E2E_CONTENT_TYPE_PROTOBUF
    parsed = logs_service_pb2.ExportLogsServiceRequest()
    parsed.ParseFromString(body)
    assert len(parsed.resource_logs[0].scope_logs[0].log_records) == 2


@pytest.mark.unit
def test_encode_request_json_is_valid_otlp() -> None:
    specs = lg.plan_events("happy", 1, TOOL, RUN_ID, RNG, TS)
    body, content_type = lg.encode_request(lg.build_export_request(specs), "json")
    assert content_type == constants.E2E_CONTENT_TYPE_JSON
    assert "resourceLogs" in json.loads(body)


@pytest.mark.unit
def test_encode_request_unknown_raises() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError, match="unknown encoding"):
        lg.encode_request(lg.build_export_request([]), "yaml")


# ---------------------------------------------------------------------------
# chunk_into_requests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chunk_into_requests_single_plan_when_small() -> None:
    specs = lg.plan_events("happy", 5, TOOL, RUN_ID, RNG, TS)
    plans = lg.chunk_into_requests(specs, "protobuf", constants.E2E_MAX_BODY_BYTES)
    assert len(plans) == 1
    assert sum(len(p.specs) for p in plans) == 5
    assert plans[0].expected_statuses == (200,)


@pytest.mark.unit
def test_chunk_into_requests_splits_on_tiny_cap() -> None:
    specs = lg.plan_events("happy", 6, TOOL, RUN_ID, RNG, TS)
    plans = lg.chunk_into_requests(specs, "protobuf", max_bytes=200)
    assert len(plans) > 1
    assert sum(len(p.specs) for p in plans) == 6
    # every chunk that holds more than one record stays under the cap
    assert all(len(p.body) <= 200 or len(p.specs) == 1 for p in plans)


# ---------------------------------------------------------------------------
# conformance request builders
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_oversize_request_exceeds_cap() -> None:
    plan = lg.build_oversize_request(TOOL, "protobuf", TS)
    assert len(plan.body) > constants.E2E_MAX_BODY_BYTES
    assert plan.expected_statuses == (413,)
    assert plan.path == constants.E2E_OTLP_LOGS_PATH


@pytest.mark.unit
def test_build_bad_content_type_request() -> None:
    plan = lg.build_bad_content_type_request(TOOL, "protobuf", TS)
    assert plan.content_type == "text/plain"
    assert plan.expected_statuses == (415, 400)


@pytest.mark.unit
@pytest.mark.parametrize("encoding", ["protobuf", "json"])
def test_build_malformed_request(encoding: str) -> None:
    plan = lg.build_malformed_request(encoding)
    assert plan.expected_statuses == (400,)
    if encoding == "json":
        with pytest.raises(json.JSONDecodeError):
            json.loads(plan.body)
    else:
        from google.protobuf.message import DecodeError
        from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

        # garbage bytes must not parse as a valid OTLP request
        with pytest.raises(DecodeError):
            logs_service_pb2.ExportLogsServiceRequest().ParseFromString(plan.body)


@pytest.mark.unit
def test_build_wrong_signal_requests_target_traces_only() -> None:
    # /v1/metrics now carries structured-metrics records (ADR 0031) and is an ACCEPTED
    # signal, so /v1/traces is the ONLY unconfigured signal path the wrong-signal
    # negative test probes for a 404.
    plans = lg.build_wrong_signal_requests("protobuf")
    assert len(plans) == 1
    assert plans[0].path == constants.E2E_OTLP_TRACES_PATH
    assert plans[0].expected_statuses == (404,)
    assert plans[0].label == "wrong-signal-traces"


@pytest.mark.unit
def test_build_waf_trigger_request_embeds_attack_signatures() -> None:
    plan = lg.build_waf_trigger_request(TOOL, "json", TS)
    text = plan.body.decode("utf-8")
    assert "OR 1=1" in text
    assert "<script>" in text
    assert plan.expected_statuses == (403,)


@pytest.mark.unit
def test_build_rate_burst_requests_count_and_expected() -> None:
    plans = lg.build_rate_burst_requests(TOOL, "protobuf", TS, 5)
    assert len(plans) == 5
    assert all(p.expected_statuses == (200, 403, 429) for p in plans)


# ---------------------------------------------------------------------------
# build_structured_events_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_structured_events_request_shape() -> None:
    plan = lg.build_structured_events_request(TS, RUN_ID, "protobuf")
    assert plan.label == "structured-events-batch"
    assert plan.path == constants.E2E_OTLP_LOGS_PATH
    assert plan.expected_statuses == (200,)
    assert plan.content_type == constants.E2E_CONTENT_TYPE_PROTOBUF
    assert {s.event_type for s in plan.specs} == {"plugin_loaded", "mcp_server_connection"}
    assert all(s.tool == constants.E2E_TOOL_VALUE for s in plan.specs)


@pytest.mark.unit
def test_build_structured_events_request_protobuf_resource_and_scope() -> None:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    plan = lg.build_structured_events_request(TS, RUN_ID, "protobuf")
    parsed = logs_service_pb2.ExportLogsServiceRequest()
    parsed.ParseFromString(plan.body)
    resource_logs = parsed.resource_logs[0]
    resource_attrs = {a.key: a.value.string_value for a in resource_logs.resource.attributes}
    assert resource_attrs["service.name"] == constants.E2E_STRUCTURED_SERVICE_NAME
    scope_logs = resource_logs.scope_logs[0]
    assert scope_logs.scope.name == constants.E2E_STRUCTURED_SCOPE_NAME
    assert len(scope_logs.log_records) == 2


@pytest.mark.unit
def test_build_structured_events_request_bodies_are_event_names() -> None:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    plan = lg.build_structured_events_request(TS, RUN_ID, "protobuf")
    parsed = logs_service_pb2.ExportLogsServiceRequest()
    parsed.ParseFromString(plan.body)
    records = parsed.resource_logs[0].scope_logs[0].log_records
    bodies = {r.body.string_value for r in records}
    assert bodies == {"claude_code.plugin_loaded", "claude_code.mcp_server_connection"}


@pytest.mark.unit
def test_build_structured_events_request_every_record_carries_run_id_attribute() -> None:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    plan = lg.build_structured_events_request(TS, RUN_ID, "protobuf")
    parsed = logs_service_pb2.ExportLogsServiceRequest()
    parsed.ParseFromString(plan.body)
    records = parsed.resource_logs[0].scope_logs[0].log_records
    for record in records:
        attrs = {a.key: a.value.string_value for a in record.attributes}
        assert attrs["run_id"] == RUN_ID
        assert attrs["event.timestamp"] == TS
        assert attrs["event.name"] in ("plugin_loaded", "mcp_server_connection")


@pytest.mark.unit
def test_build_structured_events_request_plugin_loaded_attributes() -> None:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    plan = lg.build_structured_events_request(TS, RUN_ID, "protobuf")
    parsed = logs_service_pb2.ExportLogsServiceRequest()
    parsed.ParseFromString(plan.body)
    records = parsed.resource_logs[0].scope_logs[0].log_records
    plugin_record = next(r for r in records if r.body.string_value == "claude_code.plugin_loaded")
    attrs = {a.key: a.value.string_value for a in plugin_record.attributes}
    assert attrs["event.name"] == "plugin_loaded"
    assert attrs["marketplace.name"]
    assert attrs["plugin.scope"] == "user-local"
    assert attrs["enabled_via"] == "user-install"
    assert attrs["session.id"] == RUN_ID
    assert attrs["terminal.type"]


@pytest.mark.unit
def test_build_structured_events_request_mcp_server_connection_attributes() -> None:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

    plan = lg.build_structured_events_request(TS, RUN_ID, "protobuf")
    parsed = logs_service_pb2.ExportLogsServiceRequest()
    parsed.ParseFromString(plan.body)
    records = parsed.resource_logs[0].scope_logs[0].log_records
    mcp_record = next(
        r for r in records if r.body.string_value == "claude_code.mcp_server_connection"
    )
    attrs = {a.key: a.value for a in mcp_record.attributes}
    assert attrs["event.name"].string_value == "mcp_server_connection"
    assert attrs["server_name"].string_value
    assert attrs["transport_type"].string_value == "http"
    assert attrs["server_scope"].string_value == "user"
    assert attrs["status"].string_value == "connected"
    assert attrs["is_plugin"].bool_value is False


@pytest.mark.unit
def test_build_structured_events_request_json_encoding() -> None:
    plan = lg.build_structured_events_request(TS, RUN_ID, "json")
    assert plan.content_type == constants.E2E_CONTENT_TYPE_JSON
    decoded = json.loads(plan.body)
    resource_attrs = {
        a["key"]: a["value"]["stringValue"]
        for a in decoded["resourceLogs"][0]["resource"]["attributes"]
    }
    assert resource_attrs["service.name"] == constants.E2E_STRUCTURED_SERVICE_NAME
    records = decoded["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    bodies = {r["body"]["stringValue"] for r in records}
    assert bodies == {"claude_code.plugin_loaded", "claude_code.mcp_server_connection"}
    for record in records:
        keys = {a["key"] for a in record["attributes"]}
        assert "run_id" in keys


# ---------------------------------------------------------------------------
# build_structured_metrics_request
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_structured_metrics_request_shape() -> None:
    plan = lg.build_structured_metrics_request(TS, RUN_ID, "protobuf")
    assert plan.label == "structured-metrics-batch"
    assert plan.path == constants.E2E_OTLP_METRICS_PATH
    assert plan.expected_statuses == (200,)
    assert plan.content_type == constants.E2E_CONTENT_TYPE_PROTOBUF
    assert {s.event_type for s in plan.specs} == set(constants.E2E_STRUCTURED_METRIC_NAMES)
    assert all(s.tool == constants.E2E_TOOL_VALUE for s in plan.specs)
    assert len(plan.specs) == 2


@pytest.mark.unit
def test_build_structured_metrics_request_protobuf_resource_and_scope() -> None:
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2

    plan = lg.build_structured_metrics_request(TS, RUN_ID, "protobuf")
    parsed = metrics_service_pb2.ExportMetricsServiceRequest()
    parsed.ParseFromString(plan.body)
    resource_metrics = parsed.resource_metrics[0]
    resource_attrs = {a.key: a.value.string_value for a in resource_metrics.resource.attributes}
    assert resource_attrs["service.name"] == constants.E2E_STRUCTURED_SERVICE_NAME
    scope_metrics = resource_metrics.scope_metrics[0]
    assert scope_metrics.scope.name == constants.E2E_STRUCTURED_METRICS_SCOPE_NAME
    assert len(scope_metrics.metrics) == 2


@pytest.mark.unit
def test_build_structured_metrics_request_metric_names_are_synthetic_not_real() -> None:
    plan = lg.build_structured_metrics_request(TS, RUN_ID, "protobuf")
    names = {s.event_type for s in plan.specs}
    assert names == set(constants.E2E_STRUCTURED_METRIC_NAMES)
    for name in names:
        assert name.startswith("synthetic.")
        assert not name.startswith("claude_code.")


@pytest.mark.unit
def test_build_structured_metrics_request_metrics_are_monotonic_delta_sums() -> None:
    # BUG FIX (#286): a one-shot CUMULATIVE Sum never yields an awsemf delta, so no EMF
    # event -- and no lake row -- is ever produced. DELTA (still monotonic) is required so
    # awsemf emits the datapoint's value directly on this single export.
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
    from opentelemetry.proto.metrics.v1 import metrics_pb2

    plan = lg.build_structured_metrics_request(TS, RUN_ID, "protobuf")
    parsed = metrics_service_pb2.ExportMetricsServiceRequest()
    parsed.ParseFromString(plan.body)
    metrics = parsed.resource_metrics[0].scope_metrics[0].metrics
    for metric in metrics:
        assert metric.WhichOneof("data") == "sum"
        assert metric.sum.is_monotonic is True
        assert metric.sum.aggregation_temporality == metrics_pb2.AGGREGATION_TEMPORALITY_DELTA
        assert len(metric.sum.data_points) == 1


@pytest.mark.unit
def test_build_structured_metrics_request_every_datapoint_carries_run_id_attribute() -> None:
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2

    plan = lg.build_structured_metrics_request(TS, RUN_ID, "protobuf")
    parsed = metrics_service_pb2.ExportMetricsServiceRequest()
    parsed.ParseFromString(plan.body)
    metrics = parsed.resource_metrics[0].scope_metrics[0].metrics
    for metric in metrics:
        data_point = metric.sum.data_points[0]
        attrs = {a.key: a.value.string_value for a in data_point.attributes}
        assert attrs["run_id"] == RUN_ID
        assert attrs["session.id"] == RUN_ID
        assert data_point.as_int > 0


@pytest.mark.unit
def test_build_structured_metrics_request_datapoint_markers_differ_between_metrics() -> None:
    # awsemf groups same-export-call datapoints that share an identical attribute set
    # into ONE combined EMF log event; each metric's synthetic_marker must therefore be
    # unique so the two metrics resolve to different dimension sets and land as two
    # SEPARATE EMF/CWL log events (see build_structured_metrics_request docstring).
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2

    plan = lg.build_structured_metrics_request(TS, RUN_ID, "protobuf")
    parsed = metrics_service_pb2.ExportMetricsServiceRequest()
    parsed.ParseFromString(plan.body)
    metrics = parsed.resource_metrics[0].scope_metrics[0].metrics
    markers = set()
    for metric in metrics:
        data_point = metric.sum.data_points[0]
        attrs = {a.key: a.value.string_value for a in data_point.attributes}
        markers.add(attrs["synthetic_marker"])
    assert len(markers) == 2


@pytest.mark.unit
def test_build_structured_metrics_request_json_encoding() -> None:
    plan = lg.build_structured_metrics_request(TS, RUN_ID, "json")
    assert plan.content_type == constants.E2E_CONTENT_TYPE_JSON
    decoded = json.loads(plan.body)
    resource_attrs = {
        a["key"]: a["value"]["stringValue"]
        for a in decoded["resourceMetrics"][0]["resource"]["attributes"]
    }
    assert resource_attrs["service.name"] == constants.E2E_STRUCTURED_SERVICE_NAME
    metrics = decoded["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    names = {m["name"] for m in metrics}
    assert names == set(constants.E2E_STRUCTURED_METRIC_NAMES)
    for metric in metrics:
        data_point = metric["sum"]["dataPoints"][0]
        keys = {a["key"] for a in data_point["attributes"]}
        assert "run_id" in keys


# ---------------------------------------------------------------------------
# --resolve / --connect-to parsers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_resolve_host_ip() -> None:
    assert lg.parse_resolve("collector.example:1.2.3.4", "collector.example") == "1.2.3.4"


@pytest.mark.unit
def test_parse_resolve_host_port_ip() -> None:
    assert lg.parse_resolve("collector.example:443:1.2.3.4", "collector.example") == "1.2.3.4"


@pytest.mark.unit
def test_parse_resolve_non_matching_host_returns_none() -> None:
    assert lg.parse_resolve("other.example:1.2.3.4", "collector.example") is None


@pytest.mark.unit
def test_parse_resolve_bad_form_raises() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError, match="--resolve"):
        lg.parse_resolve("only-one-field", "h")


@pytest.mark.unit
def test_parse_connect_to_forms() -> None:
    assert lg.parse_connect_to("1.2.3.4") == "1.2.3.4"
    assert lg.parse_connect_to("host:443:5.6.7.8:443") == "5.6.7.8"
    assert lg.parse_connect_to("") is None


@pytest.mark.unit
def test_parse_connect_to_bad_form_raises() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError, match="--connect-to"):
        lg.parse_connect_to("a:b:c")


# ---------------------------------------------------------------------------
# connection factory + sender
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_connection_factory_returns_pinned_when_ip_supplied() -> None:
    conn = lg._default_connection_factory("collector.example", 443, "1.2.3.4", 5.0)
    assert isinstance(conn, lg._PinnedHTTPSConnection)
    assert conn._connect_ip == "1.2.3.4"


@pytest.mark.unit
def test_default_connection_factory_plain_when_no_ip() -> None:
    conn = lg._default_connection_factory("collector.example", 443, None, 5.0)
    assert isinstance(conn, lg.http.client.HTTPSConnection)
    assert not isinstance(conn, lg._PinnedHTTPSConnection)


class _FakeResponse:
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason

    def read(self) -> bytes:
        return b""


class _FakeConn:
    def __init__(
        self, status: int = 200, reason: str = "OK", raise_on_request: bool = False
    ) -> None:
        self._status = status
        self._reason = reason
        self._raise = raise_on_request
        self.requested: dict[str, Any] = {}
        self.closed = False

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        if self._raise:
            raise OSError("boom")
        self.requested = {"method": method, "path": path, "body": body, "headers": headers}

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(self._status, self._reason)

    def close(self) -> None:
        self.closed = True


@pytest.mark.unit
def test_sender_post_returns_status_and_closes() -> None:
    conn = _FakeConn(202, "Accepted")
    sender = lg.OtlpHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=5.0,
        connection_factory=lambda *a: conn,
    )
    status, reason = sender.post("/v1/logs", b"abc", "application/json")
    assert (status, reason) == (202, "Accepted")
    assert conn.requested["headers"]["Content-Type"] == "application/json"
    assert conn.requested["headers"]["Content-Length"] == "3"
    assert conn.closed is True


@pytest.mark.unit
def test_sender_post_always_sends_default_user_agent() -> None:
    # The collector WAF blocks User-Agent-less requests, so a UA must always be
    # present on every built request.
    conn = _FakeConn(200, "OK")
    sender = lg.OtlpHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=5.0,
        connection_factory=lambda *a: conn,
    )
    sender.post("/v1/logs", b"abc", "application/x-protobuf")
    user_agent = conn.requested["headers"]["User-Agent"]
    assert user_agent == constants.E2E_USER_AGENT
    assert user_agent.startswith(f"{constants.E2E_USER_AGENT_PRODUCT}/")


@pytest.mark.unit
def test_sender_post_uses_overridden_user_agent() -> None:
    conn = _FakeConn(200, "OK")
    sender = lg.OtlpHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=5.0,
        user_agent="custom-agent/9.9",
        connection_factory=lambda *a: conn,
    )
    sender.post("/v1/logs", b"x", "application/x-protobuf")
    assert conn.requested["headers"]["User-Agent"] == "custom-agent/9.9"


@pytest.mark.unit
def test_sender_post_transport_error_is_surfaced() -> None:
    conn = _FakeConn(raise_on_request=True)
    sender = lg.OtlpHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=5.0,
        connection_factory=lambda *a: conn,
    )
    status, reason = sender.post("/v1/logs", b"x", "application/json")
    assert status == 0
    assert "transport-error" in reason
    assert conn.closed is True


# ---------------------------------------------------------------------------
# default_tcp_prober
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_tcp_prober_no_listener() -> None:
    # Port 1 is reserved/unused on the loopback host -> connection refused.
    present, detail = lg.default_tcp_prober("127.0.0.1", 1, timeout=1.0)
    assert present is False
    assert "no TCP listener" in detail


@pytest.mark.unit
def test_default_tcp_prober_listener_present() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        present, detail = lg.default_tcp_prober("127.0.0.1", port, timeout=2.0)
        assert present is True
        assert "answered" in detail
    finally:
        listener.close()


# ---------------------------------------------------------------------------
# OtlpLoadGenerator
# ---------------------------------------------------------------------------


def _make_generator(
    mode: str, sender: Any, *, count: int = 12, encoding: str = "protobuf"
) -> lg.OtlpLoadGenerator:
    return lg.OtlpLoadGenerator(
        env="sandbox",
        endpoint_host="collector.sandbox.example",
        port=443,
        connect_ip=None,
        encoding=encoding,
        count=count,
        concurrency=2,
        run_id="testrun",
        mode=mode,
        sender=sender,
    )


@pytest.mark.unit
def test_generator_rejects_unknown_mode() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError, match="unknown --mode"):
        _make_generator("teleport", FakeSender())


@pytest.mark.unit
def test_generator_rejects_unknown_encoding() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError, match="unknown --encoding"):
        lg.OtlpLoadGenerator(
            env="sandbox",
            endpoint_host="h",
            port=443,
            connect_ip=None,
            encoding="yaml",
            count=1,
            concurrency=1,
            run_id="r",
            mode="happy",
            sender=FakeSender(),
        )


@pytest.mark.unit
def test_generator_happy_run_populates_manifest() -> None:
    sender = FakeSender(200)
    manifest, code = _make_generator("happy", sender).run()
    assert code == 0
    assert manifest["total_records"] == 12
    # Every record carries the fixed reserved tool value (not a per-run value).
    assert manifest["tool_value"] == TOOL
    # The run identity lives in the manifest run_id (embedded in each payload).
    assert manifest["run_id"] == RUN_ID
    assert manifest["http_status_summary"]["200"] >= 1
    # Counts are keyed by event_type only; the run is scoped by run_id, and the
    # tool is the single fixed value, so tool is no longer part of the count key.
    assert set(manifest["counts_by_event"]) == set(constants.E2E_EVENT_TYPES)
    assert 0 < len(manifest["sample_records"]) <= 5
    # Sample records carry the run id and the fixed tool value.
    assert all(s["run_id"] == RUN_ID for s in manifest["sample_records"])
    assert all(s["tool"] == TOOL for s in manifest["sample_records"])
    assert manifest["expected_dt_partitions"] == [manifest["started_at"][:10]]


@pytest.mark.unit
def test_generator_happy_run_embeds_run_id_in_every_payload() -> None:
    # Each accepted record's payload must carry $.run_id so the verifier can scope
    # this run's records within the shared tool=e2e-smoke partition.
    gen = _make_generator("happy", FakeSender(200), count=6)
    specs = lg.plan_events("happy", 6, TOOL, RUN_ID, secrets.SystemRandom(), TS)
    assert all(json.loads(s.payload)["run_id"] == RUN_ID for s in specs)
    # And the orchestrator uses the same run id it was constructed with.
    assert gen._run_id == RUN_ID


@pytest.mark.unit
def test_generator_deviation_returns_exit_1() -> None:
    # happy mode expects 200; a 500 is a deviation.
    manifest, code = _make_generator("happy", FakeSender(500)).run()
    assert code == 1
    assert manifest["total_records"] == 0  # nothing accepted
    assert manifest["counts_by_event"] == {}  # no accepted records -> no counts


@pytest.mark.unit
def test_generator_oversize_run_ok_on_413() -> None:
    manifest, code = _make_generator("oversize", FakeSender(413), count=1).run()
    assert code == 0
    assert manifest["http_status_summary"]["413"] == 1


@pytest.mark.unit
def test_generator_grpc_probe_no_listener_passes() -> None:
    def prober(host: str, port: int, timeout: float) -> tuple[bool, str]:
        return False, "no listener"

    gen = lg.OtlpLoadGenerator(
        env="qa",
        endpoint_host="127.0.0.1",
        port=443,
        connect_ip=None,
        encoding="protobuf",
        count=1,
        concurrency=1,
        run_id="r",
        mode="grpc-probe",
        sender=FakeSender(),
        tcp_prober=prober,
    )
    manifest, code = gen.run()
    assert code == 0
    assert manifest["grpc_probe"]["listener_present"] is False


@pytest.mark.unit
def test_generator_grpc_probe_listener_present_fails() -> None:
    def prober(host: str, port: int, timeout: float) -> tuple[bool, str]:
        return True, "listener answered"

    gen = lg.OtlpLoadGenerator(
        env="qa",
        endpoint_host="h",
        port=443,
        connect_ip=None,
        encoding="protobuf",
        count=1,
        concurrency=1,
        run_id="r",
        mode="grpc-probe",
        sender=FakeSender(),
        tcp_prober=prober,
    )
    _manifest, code = gen.run()
    assert code == 1


@pytest.mark.unit
def test_generator_rate_burst_builds_many_plans() -> None:
    gen = _make_generator("rate-burst", FakeSender(200), count=3)
    plans = gen.build_plans(TS)
    assert len(plans) >= constants.E2E_WAF_RATE_LIMIT_PER_5MIN + 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("oversize", (413,)),
        ("bad-content-type", (415, 400)),
        ("malformed", (400,)),
        ("waf-trigger", (403,)),
    ],
)
def test_generator_build_plans_conformance_modes(mode: str, expected: tuple[int, ...]) -> None:
    plans = _make_generator(mode, FakeSender(), count=1).build_plans(TS)
    assert len(plans) == 1
    assert plans[0].expected_statuses == expected


@pytest.mark.unit
def test_generator_build_plans_wrong_signal() -> None:
    # /v1/metrics now carries structured-metrics records (ADR 0031), so /v1/traces is the
    # ONLY unconfigured signal path left: the wrong-signal mode probes just that one,
    # expecting 404.
    plans = _make_generator("wrong-signal", FakeSender(), count=1).build_plans(TS)
    assert len(plans) == 1
    assert plans[0].path == constants.E2E_OTLP_TRACES_PATH
    assert plans[0].expected_statuses == (404,)


@pytest.mark.unit
def test_structured_events_mode_is_registered() -> None:
    assert "structured-events" in constants.E2E_MODES


@pytest.mark.unit
def test_generator_build_plans_structured_events_dispatches_single_plan() -> None:
    plans = _make_generator("structured-events", FakeSender(200), count=1).build_plans(TS)
    assert len(plans) == 1
    assert plans[0].label == "structured-events-batch"
    assert plans[0].expected_statuses == (200,)
    assert len(plans[0].specs) == 2


@pytest.mark.unit
def test_generator_structured_events_run_populates_manifest() -> None:
    manifest, code = _make_generator("structured-events", FakeSender(200), count=1).run()
    assert code == 0
    assert manifest["mode"] == "structured-events"
    assert manifest["tool_value"] == TOOL
    assert manifest["run_id"] == RUN_ID
    assert manifest["counts_by_event"] == {"plugin_loaded": 1, "mcp_server_connection": 1}
    assert manifest["total_records"] == 2
    assert manifest["http_status_summary"]["200"] == 1


@pytest.mark.unit
def test_generator_structured_events_deviation_returns_exit_1() -> None:
    manifest, code = _make_generator("structured-events", FakeSender(500), count=1).run()
    assert code == 1
    assert manifest["counts_by_event"] == {}
    assert manifest["total_records"] == 0


@pytest.mark.unit
def test_structured_metrics_mode_is_registered() -> None:
    assert "structured-metrics" in constants.E2E_MODES


@pytest.mark.unit
def test_generator_build_plans_structured_metrics_dispatches_single_plan() -> None:
    plans = _make_generator("structured-metrics", FakeSender(200), count=1).build_plans(TS)
    assert len(plans) == 1
    assert plans[0].label == "structured-metrics-batch"
    assert plans[0].path == constants.E2E_OTLP_METRICS_PATH
    assert plans[0].expected_statuses == (200,)
    assert len(plans[0].specs) == 2


@pytest.mark.unit
def test_generator_structured_metrics_run_populates_manifest() -> None:
    manifest, code = _make_generator("structured-metrics", FakeSender(200), count=1).run()
    assert code == 0
    assert manifest["mode"] == "structured-metrics"
    # Every synthetic record shares the fixed reserved tool value (e2e-smoke), not a
    # per-run value -- same reconcile-by-run_id contract as every other mode.
    assert manifest["tool_value"] == TOOL
    assert manifest["run_id"] == RUN_ID
    assert manifest["counts_by_event"] == {
        constants.E2E_STRUCTURED_METRIC_NAMES[0]: 1,
        constants.E2E_STRUCTURED_METRIC_NAMES[1]: 1,
    }
    assert manifest["total_records"] == 2
    assert manifest["http_status_summary"]["200"] == 1


@pytest.mark.unit
def test_generator_structured_metrics_deviation_returns_exit_1() -> None:
    manifest, code = _make_generator("structured-metrics", FakeSender(500), count=1).run()
    assert code == 1
    assert manifest["counts_by_event"] == {}
    assert manifest["total_records"] == 0


# ---------------------------------------------------------------------------
# CLI helpers + main
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_run_id_shape() -> None:
    run_id = lg._generate_run_id()
    stamp, _, suffix = run_id.partition("-")
    assert len(stamp) == 14 and stamp.isdigit()
    assert len(suffix) == 6


@pytest.mark.unit
def test_resolve_endpoint_host_uses_endpoint_override() -> None:
    args = lg.argparse.Namespace(endpoint="my.host", env="sandbox")
    assert lg._resolve_endpoint_host(args) == "my.host"


@pytest.mark.unit
def test_resolve_endpoint_host_defaults_to_domains() -> None:
    args = lg.argparse.Namespace(endpoint=None, env="sandbox")
    host = lg._resolve_endpoint_host(args)
    assert host.startswith("collector.")


@pytest.mark.unit
def test_normalize_endpoint_host_passes_bare_host_through() -> None:
    assert lg.normalize_endpoint_host("d123abc.cloudfront.net") == "d123abc.cloudfront.net"


@pytest.mark.unit
def test_normalize_endpoint_host_extracts_host_from_url() -> None:
    # The perf-test workflow passes the collector_endpoint URL; only the host is used.
    assert (
        lg.normalize_endpoint_host("https://d123abc.cloudfront.net/v1/logs")
        == "d123abc.cloudfront.net"
    )


@pytest.mark.unit
def test_resolve_endpoint_host_extracts_host_from_url_endpoint() -> None:
    args = lg.argparse.Namespace(endpoint="https://d123abc.cloudfront.net/v1/logs", env="sandbox")
    assert lg._resolve_endpoint_host(args) == "d123abc.cloudfront.net"


@pytest.mark.unit
def test_normalize_endpoint_host_rejects_hostless_url() -> None:
    with pytest.raises(lg.e2e_common.E2EUsageError):
        lg.normalize_endpoint_host("https:///v1/logs")


@pytest.mark.unit
def test_main_grpc_probe_offline_exits_zero(tmp_path: pathlib.Path) -> None:
    manifest_out = tmp_path / "m.json"
    with pytest.raises(SystemExit) as exc:
        lg.main(
            [
                "--env",
                "qa",
                "--mode",
                "grpc-probe",
                "--endpoint",
                "127.0.0.1",
                "--port",
                "1",
                "--manifest-out",
                str(manifest_out),
            ]
        )
    assert exc.value.code == 0
    written = json.loads(manifest_out.read_text())
    assert written["mode"] == "grpc-probe"
    assert written["grpc_probe"]["listener_present"] is False


@pytest.mark.unit
def test_main_usage_error_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    # A --resolve with a bad form raises E2EUsageError -> exit 2.
    with pytest.raises(SystemExit) as exc:
        lg.main(["--env", "sandbox", "--endpoint", "h", "--resolve", "single-token"])
    assert exc.value.code == 2


@pytest.mark.unit
def test_main_data_mode_with_injected_generator(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _StubGen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self) -> tuple[dict[str, Any], int]:
            return {"mode": "happy", "total_records": 3}, 0

    monkeypatch.setattr(lg, "OtlpLoadGenerator", _StubGen)
    monkeypatch.setattr(lg, "OtlpHttpSender", lambda **kwargs: object())
    with pytest.raises(SystemExit) as exc:
        lg.main(
            [
                "--env",
                "sandbox",
                "--endpoint",
                "collector.example",
                "--mode",
                "happy",
                "--connect-to",
                "1.2.3.4",
            ]
        )
    assert exc.value.code == 0
    assert "total_records" in capsys.readouterr().out


@pytest.mark.unit
def test_main_passes_user_agent_override_to_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _StubGen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self) -> tuple[dict[str, Any], int]:
            return {"mode": "happy"}, 0

    def _capture_sender(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(lg, "OtlpLoadGenerator", _StubGen)
    monkeypatch.setattr(lg, "OtlpHttpSender", _capture_sender)
    with pytest.raises(SystemExit):
        lg.main(
            [
                "--env",
                "sandbox",
                "--endpoint",
                "collector.example",
                "--mode",
                "happy",
                "--user-agent",
                "my-loadgen/1.2.3",
            ]
        )
    assert captured["user_agent"] == "my-loadgen/1.2.3"


@pytest.mark.unit
def test_main_defaults_user_agent_to_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _StubGen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self) -> tuple[dict[str, Any], int]:
            return {"mode": "happy"}, 0

    def _capture_sender(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(lg, "OtlpLoadGenerator", _StubGen)
    monkeypatch.setattr(lg, "OtlpHttpSender", _capture_sender)
    with pytest.raises(SystemExit):
        lg.main(["--env", "sandbox", "--endpoint", "collector.example", "--mode", "happy"])
    assert captured["user_agent"] == constants.E2E_USER_AGENT
