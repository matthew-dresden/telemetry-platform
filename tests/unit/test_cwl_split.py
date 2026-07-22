"""Unit tests for the data-lake cwl_split Firehose transform Lambda.

First-ever tests for providers/aws/references/data-lake/lambda/cwl_split/index.py. The
central invariant is NON-DISRUPTION: any message that follows the documented body contract
(a flat JSON object with a top-level ``tool`` -- example_cli and every existing tool) passes
through the reshape BYTE-IDENTICAL and is never reclassified. Structured OTLP claude-family
records (the collector's ``raw_log=false`` output, captured as golden fixtures from the pinned
ADOT v0.43.1 image) are reshaped into the same canonical ``{timestamp,tool,event_type,payload}``
envelope so the downstream Firehose jq + Glue schema are unchanged.

The reshape logic is validated against REAL golden fixtures in tests/unit/fixtures/claude/:
- structured_otlp_plugin_loaded.json: the exact bytes ADOT raw_log=false wrote to CloudWatch Logs
  for a real claude_code.plugin_loaded event (captured end-to-end through the pinned image).
- body_contract_example_body.json: the exact flat-JSON body a example_cli record carries.
- metrics/emf_*.json: the exact EMF log events the collector's ``awsemf`` exporter wrote to
  CloudWatch Logs for real Claude Code metric instruments (token usage per type, cost, session
  count, active time), captured end-to-end through the pinned ADOT image. The metrics reshape
  is exercised through ``_reshape_records`` (the list-returning entrypoint the handler calls);
  ``_reshape_message`` is unchanged and stays a scalar ``bytes`` passthrough/log classifier.
"""

from __future__ import annotations

import base64
import gzip
import importlib.util
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
INDEX_PY = (
    REPO_ROOT
    / "providers"
    / "aws"
    / "references"
    / "data-lake"
    / "lambda"
    / "cwl_split"
    / "index.py"
)
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "claude"
EMF_FIXTURES = FIXTURES / "metrics"

_DEFAULT_MAP = {
    "claude-code": "claude-code",
    "claude-cowork": "claude-cowork",
    "claude-office": "claude-office",
    "synthetic-claude-e2e": "e2e-smoke",
}


def load_index(env_overrides: dict[str, str] | None = None):
    """Load a FRESH copy of the Lambda module with env + a mocked firehose client.

    index.py reads DELIVERY_STREAM_NAME and SERVICE_TOOL_MAP at import time and builds a
    boto3 firehose client at module top; env is set and boto3.client is patched BEFORE
    exec_module so no AWS call is ever made. Returns (module, firehose_mock).
    """
    env = {
        "DELIVERY_STREAM_NAME": "test-stream",
        "AWS_DEFAULT_REGION": "us-east-1",
        "SERVICE_TOOL_MAP": json.dumps(_DEFAULT_MAP),
    }
    if env_overrides:
        env.update(env_overrides)
    firehose_mock = MagicMock()
    firehose_mock.put_record_batch.return_value = {"FailedPutCount": 0, "RequestResponses": []}
    spec = importlib.util.spec_from_file_location("cwl_split_index", INDEX_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    with (
        patch.dict("os.environ", env, clear=False),
        patch("boto3.client", return_value=firehose_mock),
    ):
        spec.loader.exec_module(mod)
    return mod, firehose_mock


def _gzip_cwl(messages: list[str], message_type: str = "DATA_MESSAGE") -> dict:
    """Build a Firehose input record wrapping a gzipped CWL subscription envelope."""
    envelope = {
        "messageType": message_type,
        "owner": "123",
        "logGroup": "/telemetry/x",
        "logStream": "s",
        "logEvents": [{"id": str(i), "timestamp": 0, "message": m} for i, m in enumerate(messages)],
    }
    raw = gzip.compress(json.dumps(envelope).encode("utf-8"))
    return {"recordId": "r0", "data": base64.b64encode(raw).decode("ascii")}


# ---------------------------------------------------------------------------
# Golden-fixture: example_cli byte-identical passthrough (the non-disruption invariant)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_example_cli_golden_body_passes_through_byte_identical() -> None:
    mod, _ = load_index()
    body = (FIXTURES / "body_contract_example_body.json").read_text().strip()
    out = mod._reshape_message(body)
    assert out == body.encode("utf-8"), "example_cli body must be re-ingested byte-for-byte"


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        '{"tool":"example-cli","event_type":"cli_command","payload":"{}"}',
        '{"tool":"example-cli","timestamp":"2026-01-01T00:00:00Z","event_type":"x","payload":"{\\"run_id\\":\\"r\\"}"}',
        '{"tool":"some-future-tool","attributes":{"x":1},"resource":{"y":2},"body":"z"}',
    ],
)
def test_any_top_level_tool_is_never_reshaped(body: str) -> None:
    """A top-level ``tool`` wins over every other shape -> byte-identical, never reshaped."""
    mod, _ = load_index()
    assert mod._reshape_message(body) == body.encode("utf-8")


# ---------------------------------------------------------------------------
# Golden-fixture: claude raw_log=false reshape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_claude_golden_record_reshapes_correctly() -> None:
    mod, _ = load_index()
    msg = (FIXTURES / "structured_otlp_plugin_loaded.json").read_text().strip()
    env = json.loads(mod._reshape_message(msg))
    assert env["tool"] == "claude-code"
    assert env["event_type"] == "plugin_loaded"
    assert env["timestamp"] == "2026-07-17T18:24:46.938Z"
    assert "tool" in env, "canonical envelope must carry a top-level tool for Firehose {tool:.tool}"
    payload = json.loads(env["payload"])
    # operator's target fields, queryable with clean dot-free Athena paths
    assert payload["plugin_name"] == "demo-telemetry-plugin"
    assert payload["marketplace_name"] == "telemetry-fixture-marketplace"
    assert payload["user_email"] == "matthew@dresdencraft.com"
    assert payload["session_id"] == "442ac0ae-9ac7-4209-9860-298522169abf"
    assert payload["skill_path_count"] == 1
    assert payload["resource_service_name"] == "claude-code"
    assert payload["otel_scope_name"] == "com.anthropic.claude_code.events"
    assert payload["otel_body"] == "claude_code.plugin_loaded"


@pytest.mark.unit
def test_synthetic_claude_run_id_reconcile_shape() -> None:
    """Synthetic e2e mode: service maps to e2e-smoke; run_id attr lands at payload.$.run_id."""
    mod, _ = load_index()
    msg = json.dumps(
        {
            "body": "claude_code.plugin_loaded",
            "attributes": {
                "event.name": "plugin_loaded",
                "event.timestamp": "2026-07-17T00:00:00Z",
                "run_id": "abc123",
                "marketplace.name": "mk",
            },
            "resource": {"service.name": "synthetic-claude-e2e"},
            "scope": {"name": "x"},
        }
    )
    env = json.loads(mod._reshape_message(msg))
    assert env["tool"] == "e2e-smoke"
    assert json.loads(env["payload"])["run_id"] == "abc123"


@pytest.mark.unit
def test_unmapped_structured_service_raises_keyerror_no_fallback_tool() -> None:
    """NO-FALLBACK fail-fast: a structured record whose resource.service.name is not a key in
    SERVICE_TOOL_MAP must raise KeyError -- it is never assigned a catch-all tool. The caller
    (handler()'s per-record loop, tested separately below) is responsible for isolating this to
    the single offending record and re-ingesting its original bytes instead."""
    mod, _ = load_index()
    msg = json.dumps(
        {
            "body": "claude_code.api_request",
            "attributes": {"event.name": "api_request"},
            "resource": {"service.name": "claude-brand-new-surface"},
        }
    )
    with pytest.raises(KeyError):
        mod._reshape_message(msg)


@pytest.mark.unit
def test_reshape_tolerates_list_of_kv_attribute_form() -> None:
    """Defensive: some exporters write attributes/resource as OTLP list-of-KV, not a flat map."""
    mod, _ = load_index()
    msg = json.dumps(
        {
            "body": "claude_code.api_request",
            "attributes": [
                {"key": "event.name", "value": {"stringValue": "api_request"}},
                {"key": "model", "value": {"stringValue": "claude-haiku"}},
            ],
            "resource": [{"key": "service.name", "value": {"stringValue": "claude-code"}}],
        }
    )
    env = json.loads(mod._reshape_message(msg))
    assert env["tool"] == "claude-code"
    assert env["event_type"] == "api_request"
    assert json.loads(env["payload"])["model"] == "claude-haiku"


@pytest.mark.unit
def test_event_type_and_timestamp_fallbacks() -> None:
    """Missing event.name/event.timestamp -> non-null fallbacks (body, then unknown/now)."""
    mod, _ = load_index()
    msg = json.dumps(
        {
            "body": "claude_code.some_event",
            "attributes": {},
            "resource": {"service.name": "claude-code"},
        }
    )
    env = json.loads(mod._reshape_message(msg))
    assert env["event_type"] == "claude_code.some_event"  # falls back to body
    assert env["timestamp"], "timestamp must never be empty"


@pytest.mark.unit
def test_timestamp_falls_back_to_record_unix_nano() -> None:
    """No event.timestamp attr -> derive ISO from the record's timeUnixNano."""
    mod, _ = load_index()
    msg = json.dumps(
        {
            "body": "claude_code.api_request",
            "timeUnixNano": "1784312686938000000",
            "attributes": {"event.name": "api_request"},
            "resource": {"service.name": "claude-code"},
        }
    )
    env = json.loads(mod._reshape_message(msg))
    assert env["timestamp"].startswith("2026-07-17T"), env["timestamp"]
    assert env["timestamp"].endswith("Z")


@pytest.mark.unit
def test_record_timestamp_iso_ignores_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-numeric unix-nano value is skipped (ValueError caught), not crashed."""
    mod, _ = load_index()
    assert mod._record_timestamp_iso({"timeUnixNano": "not-a-number"}) is None
    assert mod._record_timestamp_iso({}) is None


# ---------------------------------------------------------------------------
# Fail-closed passthrough for anything not positively a structured claude record
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "not json at all",
        "[1,2,3]",
        '"a bare string"',
        "42",
        '{"attributes":{},"resource":{}}',  # missing string body
        '{"body":"x","attributes":{}}',  # missing resource
        '{"body":"x","resource":{}}',  # missing attributes
        '{"body":123,"attributes":{},"resource":{}}',  # body not a string
    ],
)
def test_non_claude_shapes_pass_through_byte_identical(message: str) -> None:
    mod, _ = load_index()
    assert mod._reshape_message(message) == message.encode("utf-8")


# ---------------------------------------------------------------------------
# _reshape_records(): the list-returning entrypoint routes non-EMF unchanged to
# _reshape_message and EMF metrics events to _reshape_emf (one row per distinct metric).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reshape_records_delegates_non_emf_to_reshape_message_unchanged() -> None:
    """A example_cli message and a claude LOG message must round-trip through _reshape_records
    identically to _reshape_message, wrapped in a single-element list."""
    mod, _ = load_index()
    example_cli = '{"tool":"example-cli","event_type":"cli_command","payload":"{}"}'
    claude = (FIXTURES / "structured_otlp_plugin_loaded.json").read_text().strip()
    assert mod._reshape_records(example_cli) == [mod._reshape_message(example_cli)]
    assert mod._reshape_records(example_cli) == [example_cli.encode("utf-8")]
    assert mod._reshape_records(claude) == [mod._reshape_message(claude)]


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "not json at all",
        "[1,2,3]",
        "42",
        '{"attributes":{},"resource":{}}',
    ],
)
def test_reshape_records_non_emf_passthrough_is_single_element(message: str) -> None:
    mod, _ = load_index()
    assert mod._reshape_records(message) == [mod._reshape_message(message)]


# ---------------------------------------------------------------------------
# Golden-fixture: EMF (awsemf) Claude metrics reshape (via _reshape_records)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_emf_golden_token_usage_input_reshapes_correctly() -> None:
    mod, _ = load_index()
    msg = (EMF_FIXTURES / "emf_token_usage_input.json").read_text()
    rows = mod._reshape_records(msg)
    assert len(rows) == 1, "one distinct metric declared -> one row"
    env = json.loads(rows[0])
    assert env["tool"] == "claude-code"
    assert env["event_type"] == "claude_code.token.usage"
    assert env["timestamp"] == "2026-07-18T22:52:03.379000Z"
    payload = json.loads(env["payload"])
    assert payload["value"] == 526
    assert payload["unit"] == "tokens"
    assert payload["type"] == "input"
    assert payload["otel_signal"] == "metric"
    assert payload["metric_type"] == "SUM"
    assert payload["user_email"] == "matthew@dresdencraft.com"
    assert payload["resource_service_name"] == "claude-code"
    assert payload["model"] == "claude-haiku-4-5-20251001"
    assert payload["otel_scope_name"] == "com.anthropic.claude_code"
    # the metric-name value key must not survive as a stray flattened field
    assert "claude_code_token_usage" not in payload
    assert "claude_code.token.usage" not in payload


@pytest.mark.unit
def test_emf_golden_cost_usage_value_is_float() -> None:
    mod, _ = load_index()
    msg = (EMF_FIXTURES / "emf_cost_usage.json").read_text()
    env = json.loads(mod._reshape_records(msg)[0])
    assert env["event_type"] == "claude_code.cost.usage"
    payload = json.loads(env["payload"])
    assert payload["value"] == pytest.approx(0.0005909999999999999)
    assert isinstance(payload["value"], float)
    assert payload["unit"] == "USD"
    assert payload["otel_signal"] == "metric"


@pytest.mark.unit
def test_emf_golden_session_count_value_is_one() -> None:
    mod, _ = load_index()
    msg = (EMF_FIXTURES / "emf_session_count.json").read_text()
    env = json.loads(mod._reshape_records(msg)[0])
    assert env["event_type"] == "claude_code.session.count"
    payload = json.loads(env["payload"])
    assert payload["value"] == 1
    assert payload["start_type"] == "fresh"
    assert payload["otel_signal"] == "metric"


@pytest.mark.unit
@pytest.mark.parametrize(
    "fixture_name,event_type,expected_type",
    [
        ("emf_token_usage_output.json", "claude_code.token.usage", "output"),
        ("emf_token_usage_cacheCreation.json", "claude_code.token.usage", "cacheCreation"),
        ("emf_token_usage_cacheRead.json", "claude_code.token.usage", "cacheRead"),
        ("emf_active_time_total_cli.json", "claude_code.active_time.total", "cli"),
    ],
)
def test_emf_golden_remaining_fixtures_reshape(
    fixture_name: str, event_type: str, expected_type: str
) -> None:
    mod, _ = load_index()
    msg = (EMF_FIXTURES / fixture_name).read_text()
    env = json.loads(mod._reshape_records(msg)[0])
    assert env["tool"] == "claude-code"
    assert env["event_type"] == event_type
    payload = json.loads(env["payload"])
    assert payload["type"] == expected_type
    assert payload["otel_signal"] == "metric"


@pytest.mark.unit
def test_emf_multi_distinct_metric_event_yields_one_row_per_metric() -> None:
    """A single ``_aws`` event declaring K DISTINCT metrics must yield K canonical rows."""
    mod, _ = load_index()
    obj = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    # add a second CloudWatchMetrics dimension set declaring a DIFFERENT metric name + its value.
    obj["_aws"]["CloudWatchMetrics"].append(
        {
            "Namespace": "Telemetry/ClaudeCode",
            "Dimensions": [[]],
            "Metrics": [{"Name": "claude_code.cost.usage", "Unit": "USD", "StorageResolution": 60}],
        }
    )
    obj["claude_code.cost.usage"] = 0.001
    rows = mod._reshape_records(json.dumps(obj))
    assert len(rows) == 2
    envs = [json.loads(r) for r in rows]
    assert {e["event_type"] for e in envs} == {
        "claude_code.token.usage",
        "claude_code.cost.usage",
    }
    for env in envs:
        payload = json.loads(env["payload"])
        assert payload["otel_signal"] == "metric"
        # neither metric-name value key leaks into any row's payload
        assert "claude_code_token_usage" not in payload
        assert "claude_code_cost_usage" not in payload
    token_row = next(e for e in envs if e["event_type"] == "claude_code.token.usage")
    cost_row = next(e for e in envs if e["event_type"] == "claude_code.cost.usage")
    assert json.loads(token_row["payload"])["value"] == 526
    assert json.loads(cost_row["payload"])["value"] == 0.001


@pytest.mark.unit
def test_emf_duplicate_metric_name_across_dimension_sets_yields_one_row() -> None:
    """FIX-2 regression: awsemf's default rollup can declare the SAME metric Name in more than
    one CloudWatchMetrics dimension set. There is one shared top-level value per Name, so the
    reshape must emit exactly ONE row -- appending both would double-count the SUM downstream."""
    mod, _ = load_index()
    obj = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    # a SECOND dimension set re-declaring the SAME metric name (rollup shape), same value field.
    obj["_aws"]["CloudWatchMetrics"].append(
        {
            "Namespace": "Telemetry/ClaudeCode",
            "Dimensions": [["type"]],
            "Metrics": [
                {"Name": "claude_code.token.usage", "Unit": "tokens", "StorageResolution": 60}
            ],
        }
    )
    rows = mod._reshape_records(json.dumps(obj))
    assert len(rows) == 1, "duplicate metric Name must collapse to a single row (no double-count)"
    env = json.loads(rows[0])
    assert env["event_type"] == "claude_code.token.usage"
    assert json.loads(env["payload"])["value"] == 526


@pytest.mark.unit
def test_emf_unmapped_service_name_raises_keyerror_no_fallback_tool() -> None:
    """NO-FALLBACK fail-fast: an EMF metrics event whose service.name is not a key in
    SERVICE_TOOL_MAP must raise KeyError -- it is never assigned a catch-all tool."""
    mod, _ = load_index()
    obj = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    obj["service.name"] = "claude-brand-new-metrics-surface"
    with pytest.raises(KeyError):
        mod._reshape_records(json.dumps(obj))


@pytest.mark.unit
@pytest.mark.parametrize("bad_service_name", [{"stringValue": "x"}, ["claude-code"]])
def test_emf_non_string_service_name_raises_keyerror_not_typeerror(bad_service_name) -> None:
    """FIX-1 regression, updated for the no-fallback fail-fast design: a non-string (dict/list)
    ``service.name`` must raise ``KeyError`` -- the SAME exception the handler's per-record
    isolation expects for every unmapped/malformed service name -- and NEVER ``TypeError:
    unhashable type`` (a DIFFERENT exception that the handler's per-record try/except still
    catches, but that would defeat the intent of a single, predictable failure mode)."""
    mod, _ = load_index()
    obj = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    obj["service.name"] = bad_service_name
    with pytest.raises(KeyError):
        mod._reshape_records(json.dumps(obj))


@pytest.mark.unit
def test_emf_missing_timestamp_falls_back_to_now() -> None:
    mod, _ = load_index()
    obj = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    obj["_aws"]["Timestamp"] = "not-a-number"
    env = json.loads(mod._reshape_records(json.dumps(obj))[0])
    assert env["timestamp"], "timestamp must never be empty"
    assert not env["timestamp"].startswith("2026-07-18T22:52:03")


@pytest.mark.unit
def test_emf_never_carries_a_tool_dimension_would_break_example_cli_passthrough() -> None:
    """Guard the design invariant: awsemf must never emit a top-level ``tool`` field, or a
    metric event would be misclassified as a example_cli-style body-contract passthrough."""
    for fixture in EMF_FIXTURES.glob("emf_*.json"):
        obj = json.loads(fixture.read_text())
        assert "tool" not in obj, f"{fixture.name} unexpectedly carries a top-level 'tool' key"


# ---------------------------------------------------------------------------
# handler(): end-to-end through the CWL envelope + re-ingestion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_handler_mixed_example_cli_and_claude_reingests_and_drops_original() -> None:
    mod, firehose = load_index()
    example_cli = '{"tool":"example-cli","event_type":"cli_command","payload":"{}"}'
    claude = (FIXTURES / "structured_otlp_plugin_loaded.json").read_text().strip()
    record = _gzip_cwl([example_cli, claude])
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "Dropped"}]
    # one PutRecordBatch with two records: example_cli byte-identical + claude reshaped
    firehose.put_record_batch.assert_called_once()
    sent = [r["Data"] for r in firehose.put_record_batch.call_args.kwargs["Records"]]
    assert sent[0] == example_cli.encode("utf-8"), "example_cli re-ingested byte-identical"
    assert json.loads(sent[1])["tool"] == "claude-code", "claude reshaped"


@pytest.mark.unit
def test_handler_mixed_example_cli_claude_log_and_emf_metrics_flattens_correctly() -> None:
    """A batch of example_cli + a claude LOG record + 2 distinct EMF metric events must flatten to 4
    rows: the EMF branch returns a list per message, so the handler must ``extend`` (not
    ``append``) -- this is the regression guard for that invariant."""
    mod, firehose = load_index()
    example_cli = '{"tool":"example-cli","event_type":"cli_command","payload":"{}"}'
    claude_log = (FIXTURES / "structured_otlp_plugin_loaded.json").read_text().strip()
    emf_input = (EMF_FIXTURES / "emf_token_usage_input.json").read_text()
    emf_cost = (EMF_FIXTURES / "emf_cost_usage.json").read_text()
    record = _gzip_cwl([example_cli, claude_log, emf_input, emf_cost])
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "Dropped"}]
    firehose.put_record_batch.assert_called_once()
    sent = [r["Data"] for r in firehose.put_record_batch.call_args.kwargs["Records"]]
    assert len(sent) == 4, "4 input messages -> 4 flattened rows (1 each, no metric fan-out here)"
    assert sent[0] == example_cli.encode("utf-8"), "example_cli re-ingested byte-identical"
    decoded = [json.loads(s) for s in sent[1:]]
    assert decoded[0]["tool"] == "claude-code" and decoded[0]["event_type"] == "plugin_loaded"
    assert decoded[1]["event_type"] == "claude_code.token.usage"
    assert json.loads(decoded[1]["payload"])["otel_signal"] == "metric"
    assert decoded[2]["event_type"] == "claude_code.cost.usage"
    assert json.loads(decoded[2]["payload"])["otel_signal"] == "metric"


@pytest.mark.unit
def test_handler_poison_emf_service_name_still_delivers_cobatched_example_cli() -> None:
    """FIX-1 end-to-end, updated for the no-fallback fail-fast design: a co-batched example_cli
    logEvent MUST still be delivered even when a poison EMF record (non-string
    ``service.name``) rides in the same CWL record. PER-RECORD ISOLATION (added with the
    fallback removal) catches the KeyError _reshape_records raises for the poison record and
    re-ingests its ORIGINAL bytes unchanged -- never a fallback tool, never crashing the batch
    and dropping the example_cli record."""
    mod, firehose = load_index()
    example_cli = '{"tool":"example-cli","event_type":"cli_command","payload":"{}"}'
    poison = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    poison["service.name"] = {"unhashable": "object"}
    poison_json = json.dumps(poison)
    record = _gzip_cwl([example_cli, poison_json])
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "Dropped"}]
    firehose.put_record_batch.assert_called_once()
    sent = [r["Data"] for r in firehose.put_record_batch.call_args.kwargs["Records"]]
    assert sent[0] == example_cli.encode("utf-8"), "co-batched example_cli must survive the poison EMF record"
    assert sent[1] == poison_json.encode("utf-8"), (
        "poison EMF record must be re-ingested UNCHANGED (original bytes), not reshaped with a "
        "fallback tool -- it carries no top-level 'tool' so Firehose routes it to errors/"
    )
    assert "tool" not in json.loads(sent[1])


@pytest.mark.unit
def test_handler_unmapped_records_reingest_original_bytes_to_errors_path() -> None:
    """CRITICAL per-record isolation proof (no-fallback fail-fast design, required by the
    tool-registry refactor): a structured record AND an EMF metrics record whose
    resource.service.name/service.name is NOT a key in SERVICE_TOOL_MAP must each be
    re-ingested as their ORIGINAL bytes -- never assigned a fallback tool, never raising out of
    handler(). Unchanged bytes carry no top-level 'tool', so Firehose's {tool:.tool}
    MetadataExtraction evaluates to null and routes them to the monitored errors/ prefix instead
    of raw/. A co-batched example_cli record riding in the SAME CWL subscription record must still
    deliver byte-identical: neither unmapped record may crash the batch or drop anything else
    riding with it."""
    mod, firehose = load_index()
    example_cli = '{"tool":"example-cli","event_type":"cli_command","payload":"{}"}'
    unmapped_structured = json.dumps(
        {
            "body": "some_tool.some_event",
            "attributes": {"event.name": "some_event"},
            "resource": {"service.name": "totally-unregistered-service"},
        }
    )
    unmapped_emf_obj = json.loads((EMF_FIXTURES / "emf_token_usage_input.json").read_text())
    unmapped_emf_obj["service.name"] = "another-unregistered-service"
    unmapped_emf = json.dumps(unmapped_emf_obj)

    record = _gzip_cwl([example_cli, unmapped_structured, unmapped_emf])
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "Dropped"}]

    firehose.put_record_batch.assert_called_once()
    sent = [r["Data"] for r in firehose.put_record_batch.call_args.kwargs["Records"]]
    assert len(sent) == 3, "3 input messages -> 3 re-ingested records (no reshape fan-out here)"

    assert sent[0] == example_cli.encode("utf-8"), (
        "co-batched example_cli must survive both unmapped records riding in the same CWL record"
    )

    assert sent[1] == unmapped_structured.encode("utf-8"), (
        "unmapped structured record must be re-ingested UNCHANGED, never given a fallback tool"
    )
    assert "tool" not in json.loads(sent[1]), (
        "unmapped structured record's original bytes carry no top-level 'tool', so Firehose "
        "routes it to errors/ instead of raw/"
    )

    assert sent[2] == unmapped_emf.encode("utf-8"), (
        "unmapped EMF record must be re-ingested UNCHANGED, never given a fallback tool"
    )
    assert "tool" not in json.loads(sent[2]), (
        "unmapped EMF record's original bytes carry no top-level 'tool', so Firehose routes it "
        "to errors/ instead of raw/"
    )


@pytest.mark.unit
def test_handler_control_message_dropped_no_reingest() -> None:
    mod, firehose = load_index()
    record = _gzip_cwl(["irrelevant"], message_type="CONTROL_MESSAGE")
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "Dropped"}]
    firehose.put_record_batch.assert_not_called()


@pytest.mark.unit
def test_handler_non_gzip_reingested_record_passes_through() -> None:
    mod, firehose = load_index()
    single = json.dumps({"tool": "example-cli", "event_type": "x", "payload": "{}"}).encode("utf-8")
    record = {"recordId": "r1", "data": base64.b64encode(single).decode("ascii")}
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [
        {"recordId": "r1", "result": "Ok", "data": base64.b64encode(single).decode("ascii")}
    ]
    firehose.put_record_batch.assert_not_called()


@pytest.mark.unit
def test_handler_over_500_events_chunks_reingest() -> None:
    mod, firehose = load_index()
    msgs = [
        json.dumps({"tool": "example-cli", "event_type": "e", "payload": "{}", "n": i})
        for i in range(1100)
    ]
    record = _gzip_cwl(msgs)
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "Dropped"}]
    # 1100 records -> ceil(1100/500) = 3 PutRecordBatch calls
    assert firehose.put_record_batch.call_count == 3


@pytest.mark.unit
def test_reingest_retries_then_succeeds() -> None:
    mod, firehose = load_index()
    firehose.put_record_batch.side_effect = [
        {"FailedPutCount": 1, "RequestResponses": [{}, {"ErrorCode": "ServiceUnavailable"}]},
        {"FailedPutCount": 0, "RequestResponses": [{}]},
    ]
    mod._reingest([b'{"tool":"example-cli"}', b'{"tool":"example-cli"}'])
    assert firehose.put_record_batch.call_count == 2


@pytest.mark.unit
def test_reingest_exhausted_budget_raises() -> None:
    mod, firehose = load_index({"REINGEST_MAX_ATTEMPTS": "3"})
    firehose.put_record_batch.return_value = {
        "FailedPutCount": 1,
        "RequestResponses": [{"ErrorCode": "ServiceUnavailable"}],
    }
    with pytest.raises(RuntimeError, match="re-ingestion failed"):
        mod._reingest([b'{"tool":"example-cli"}'])
    assert firehose.put_record_batch.call_count == 3


@pytest.mark.unit
def test_handler_reingest_failure_surfaces_processing_failed() -> None:
    mod, firehose = load_index({"REINGEST_MAX_ATTEMPTS": "1"})
    firehose.put_record_batch.return_value = {
        "FailedPutCount": 1,
        "RequestResponses": [{"ErrorCode": "ServiceUnavailable"}],
    }
    record = _gzip_cwl(['{"tool":"example-cli","event_type":"x","payload":"{}"}'])
    result = mod.handler({"records": [record]}, None)
    assert result["records"] == [{"recordId": "r0", "result": "ProcessingFailed"}]


@pytest.mark.unit
def test_lambda_source_parses_under_deploy_runtime_grammar() -> None:
    """The Lambda must be parseable under its ACTUAL deploy runtime (python3.12).

    The repo targets python3.14 (ruff/mypy), whose PEP 758 unparenthesized multi-``except`` and
    other 3.13/3.14-only grammar are SyntaxErrors on the python3.12 Lambda runtime
    (cwl_transform_lambda_runtime, variables.tf). This guard compiles the source with the 3.12
    feature version so a devcontainer/CI on 3.14 cannot let runtime-incompatible syntax ship.
    """
    import ast

    source = INDEX_PY.read_text()
    ast.parse(source, feature_version=(3, 12))


@pytest.mark.unit
def test_delivery_stream_name_required_fail_fast() -> None:
    """Missing DELIVERY_STREAM_NAME fails fast at import (no silent fallback)."""
    spec = importlib.util.spec_from_file_location("cwl_split_index_nostream", INDEX_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    with (
        patch.dict("os.environ", {"AWS_DEFAULT_REGION": "us-east-1"}, clear=True),
        patch("boto3.client", return_value=MagicMock()),
        pytest.raises(KeyError),
    ):
        spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Static regression guard: the fallback tool mechanism (superseded by the tool-registry
# generalization's no-fallback fail-fast design) must never reappear in index.py.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_index_source_has_no_fallback_tool_or_claude_prefixed_names() -> None:
    """Grep-style regression guard: no fallback-tool mechanism or CLAUDE_*-prefixed env var
    name may reappear in index.py -- SERVICE_TOOL_MAP has NO fallback (see the module
    docstring's "Fail-fast, no catch-all tool" section)."""
    source = INDEX_PY.read_text()
    forbidden = (
        "_TOOL_FALLBACK",
        "claude-other",
        "CLAUDE_SERVICE_TOOL_MAP",
        "CLAUDE_TOOL_FALLBACK",
    )
    for token in forbidden:
        assert token not in source, (
            f"{token!r} must not appear in index.py (no-fallback fail-fast design, "
            "tool-registry generalization)"
        )
