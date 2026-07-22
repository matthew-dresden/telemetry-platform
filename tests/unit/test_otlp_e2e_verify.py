"""Unit tests for scripts/e2e_verify.py.

Every AWS client is a MagicMock returning canned describe/list responses; no
network or AWS call is made. Polling budgets are tiny so timeout paths are fast.
"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock

import botocore.exceptions
import pytest

from scripts import e2e_verify

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ACCOUNTS = json.loads((REPO_ROOT / "terragrunt" / "common" / "accounts.json").read_text())
DOMAINS = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())

# Every synthetic record now carries the single fixed reserved tool value; a run is
# scoped by the run_id embedded in each payload ($.run_id), not by a per-run tool.
TOOL = "e2e-smoke"
RUN_ID = "testrun"
MANIFEST: dict[str, Any] = {
    "run_id": RUN_ID,
    "tool_value": TOOL,
    "started_at": "2026-06-28T00:00:00Z",
    "finished_at": "2026-06-28T01:00:00Z",
    "total_records": 12,
    # Counts are keyed by event_type only (tool is fixed, run scoped by run_id).
    "counts_by_event": {
        "session_start": 6,
        "command_execution": 6,
    },
    "expected_dt_partitions": ["2026-06-28"],
}


def _make_verifier(
    clients: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    env: str = "sandbox",
) -> e2e_verify.E2EVerifier:
    registry = clients or {}
    session = MagicMock()
    session.client.side_effect = lambda name: registry.setdefault(name, MagicMock())
    boto3_module = MagicMock()
    boto3_module.Session.return_value = session
    return e2e_verify.E2EVerifier(
        env=env,
        manifest=manifest or MANIFEST,
        accounts_data=ACCOUNTS,
        domains_data=DOMAINS,
        boto3_module=boto3_module,
        poll_timeout=0.05,
        poll_interval=0.001,
        aws_region="us-east-1",
    )


def _statuses(probes: list[dict[str, Any]]) -> dict[str, str]:
    return {p["name"]: p["status"] for p in probes}


def _client_error(code: str, op: str = "Op") -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError({"Error": {"Code": code}}, op)


# ---------------------------------------------------------------------------
# _safe_identifier
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safe_identifier_accepts_table() -> None:
    assert e2e_verify._safe_identifier("telemetry_events") == "telemetry_events"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["a;b", "tbl'--", "a b", "a-b", "a.b"])
def test_safe_identifier_rejects_injection(bad: str) -> None:
    with pytest.raises(e2e_verify.e2e_common.E2EError, match="unsafe identifier"):
        e2e_verify._safe_identifier(bad)


# ---------------------------------------------------------------------------
# init / region
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_init_resolves_account() -> None:
    verifier = _make_verifier()
    assert verifier._account_id == "222222222222"
    assert verifier._profile == "sandbox"


@pytest.mark.unit
def test_client_uses_named_profile_by_default() -> None:
    """By default the session binds the env-named AWS profile (local dev)."""
    session = MagicMock()
    boto3_module = MagicMock()
    boto3_module.Session.return_value = session
    verifier = e2e_verify.E2EVerifier(
        env="sandbox",
        manifest=MANIFEST,
        accounts_data=ACCOUNTS,
        domains_data=DOMAINS,
        boto3_module=boto3_module,
        aws_region="us-east-1",
    )
    verifier._client("s3")
    boto3_module.Session.assert_called_once_with(profile_name="sandbox")


@pytest.mark.unit
def test_client_uses_ambient_session_when_flagged() -> None:
    """With ambient_credentials=True the session uses the default/OIDC chain (no profile)."""
    session = MagicMock()
    boto3_module = MagicMock()
    boto3_module.Session.return_value = session
    verifier = e2e_verify.E2EVerifier(
        env="sandbox",
        manifest=MANIFEST,
        accounts_data=ACCOUNTS,
        domains_data=DOMAINS,
        boto3_module=boto3_module,
        aws_region="us-east-1",
        ambient_credentials=True,
    )
    verifier._client("s3")
    boto3_module.Session.assert_called_once_with()
    _, kwargs = boto3_module.Session.call_args
    assert "profile_name" not in kwargs, "ambient mode must not bind a named profile"


@pytest.mark.unit
def test_init_unknown_env_raises() -> None:
    with pytest.raises(e2e_verify.e2e_common.E2EUsageError):
        _make_verifier(env="staging")


@pytest.mark.unit
def test_get_region_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _make_verifier()
    verifier._aws_region = None
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    assert verifier._get_region() == "eu-west-1"


@pytest.mark.unit
def test_get_region_injected_wins() -> None:
    verifier = _make_verifier()
    assert verifier._get_region() == "us-east-1"


@pytest.mark.unit
def test_get_region_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _make_verifier()
    verifier._aws_region = None
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(e2e_verify.e2e_common.E2EUsageError, match="AWS_DEFAULT_REGION"):
        verifier._get_region()


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_log_group_matches_env_and_suffix() -> None:
    logs = MagicMock()
    logs.describe_log_groups.return_value = {
        "logGroups": [
            {"logGroupName": "/telemetry/telemetry-useast1-prod-000-ci-000/ingest/otlp-logs"},
            {"logGroupName": "/telemetry/telemetry-useast1-sandbox-000-ci-000/ingest/otlp-logs"},
            {"logGroupName": "/telemetry/other/health"},
        ]
    }
    verifier = _make_verifier()
    assert verifier._discover_log_group(logs) == (
        "/telemetry/telemetry-useast1-sandbox-000-ci-000/ingest/otlp-logs"
    )


@pytest.mark.unit
def test_discover_log_group_none() -> None:
    logs = MagicMock()
    logs.describe_log_groups.return_value = {"logGroups": []}
    assert _make_verifier()._discover_log_group(logs) is None


@pytest.mark.unit
def test_discover_firehose_extracts_bucket_and_prefix() -> None:
    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {
        "DeliveryStreamNames": ["unrelated", "sandbox-telemetry-stream"]
    }
    firehose.describe_delivery_stream.return_value = {
        "DeliveryStreamDescription": {
            "Destinations": [
                {
                    "ExtendedS3DestinationDescription": {
                        "BucketARN": "arn:aws:s3:::my-data-lake",
                        "Prefix": "raw/tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp}/",
                        "ErrorOutputPrefix": "errors/!{firehose:error-output-type}/",
                    }
                }
            ]
        }
    }
    discovered = _make_verifier()._discover_firehose(firehose)
    assert discovered == {
        "stream": "sandbox-telemetry-stream",
        "bucket": "my-data-lake",
        "prefix": "raw/tool=!{partitionKeyFromQuery:tool}/dt=!{timestamp}/",
        "error_prefix": "errors/!{firehose:error-output-type}/",
    }


@pytest.mark.unit
def test_discover_firehose_none_when_no_stream() -> None:
    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {"DeliveryStreamNames": ["unrelated"]}
    assert _make_verifier()._discover_firehose(firehose) is None


@pytest.mark.unit
def test_discover_firehose_no_destinations() -> None:
    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {"DeliveryStreamNames": ["telemetry-x"]}
    firehose.describe_delivery_stream.return_value = {"DeliveryStreamDescription": {}}
    discovered = _make_verifier()._discover_firehose(firehose)
    assert discovered == {"stream": "telemetry-x", "bucket": "", "prefix": "", "error_prefix": ""}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("raw/tool=!{partitionKeyFromQuery:tool}/dt=!{x}/", f"raw/tool={TOOL}/"),
        ("raw/!{x}/", f"raw/tool={TOOL}/"),
        ("rawdata/", f"rawdata/tool={TOOL}/"),
    ],
)
def test_s3_listing_prefix(prefix: str, expected: str) -> None:
    assert e2e_verify.E2EVerifier.s3_listing_prefix(prefix, TOOL) == expected


@pytest.mark.unit
def test_error_listing_prefix() -> None:
    assert e2e_verify.E2EVerifier._error_listing_prefix("errors/!{x}/") == "errors/"
    assert e2e_verify.E2EVerifier._error_listing_prefix("errors/") == "errors/"


@pytest.mark.unit
def test_discover_embed_function() -> None:
    lambda_client = MagicMock()
    lambda_client.list_functions.return_value = {
        "Functions": [{"FunctionName": "x"}, {"FunctionName": "sandbox-portal-embed-url"}]
    }
    assert _make_verifier()._discover_embed_function(lambda_client) == "sandbox-portal-embed-url"


# ---------------------------------------------------------------------------
# CWL
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_cwl_success() -> None:
    logs = MagicMock()
    logs.describe_log_groups.return_value = {
        "logGroups": [{"logGroupName": "/telemetry/x-sandbox-y/ingest/otlp-logs"}]
    }
    logs.filter_log_events.return_value = {"events": [{}] * 12}
    verifier = _make_verifier({"logs": logs})
    probes: list[dict[str, Any]] = []
    verifier._check_cwl(probes)
    statuses = _statuses(probes)
    assert statuses["cwl:log-group"] == "OK"
    assert statuses["cwl:received"] == "OK"


@pytest.mark.unit
def test_check_cwl_no_group_fails() -> None:
    logs = MagicMock()
    logs.describe_log_groups.return_value = {"logGroups": []}
    probes: list[dict[str, Any]] = []
    _make_verifier({"logs": logs})._check_cwl(probes)
    assert _statuses(probes)["cwl:log-group"] == "FAIL"


@pytest.mark.unit
def test_check_cwl_short_received_fails() -> None:
    logs = MagicMock()
    logs.describe_log_groups.return_value = {
        "logGroups": [{"logGroupName": "/telemetry/x-sandbox-y/ingest/otlp-logs"}]
    }
    logs.filter_log_events.return_value = {"events": [{}]}  # only 1 of 12
    probes: list[dict[str, Any]] = []
    _make_verifier({"logs": logs})._check_cwl(probes)
    assert _statuses(probes)["cwl:received"] == "FAIL"


@pytest.mark.unit
def test_check_cwl_counts_across_paginated_pages() -> None:
    # filter_log_events caps each response at one page and returns a nextToken when
    # more events remain. The verifier must SUM across all pages, not read only the
    # first, or a multi-page result under-counts and false-FAILs (issue #85). The
    # manifest expects 12; the events arrive across three pages of 5 + 5 + 2.
    logs = MagicMock()
    logs.describe_log_groups.return_value = {
        "logGroups": [{"logGroupName": "/telemetry/x-sandbox-y/ingest/otlp-logs"}]
    }
    logs.filter_log_events.side_effect = [
        {"events": [{}] * 5, "nextToken": "p2"},
        {"events": [{}] * 5, "nextToken": "p3"},
        {"events": [{}] * 2},  # no nextToken -> last page
    ]
    verifier = _make_verifier({"logs": logs})
    probes: list[dict[str, Any]] = []
    verifier._check_cwl(probes)
    statuses = _statuses(probes)
    assert statuses["cwl:received"] == "OK"
    # 12 events were counted only because every page was followed.
    received = next(p for p in probes if p["name"] == "cwl:received")
    assert "12 event(s)" in received["detail"]
    # The second and third calls carried the page's nextToken (pagination followed).
    assert logs.filter_log_events.call_count == 3
    assert logs.filter_log_events.call_args_list[1].kwargs["nextToken"] == "p2"
    assert logs.filter_log_events.call_args_list[2].kwargs["nextToken"] == "p3"


@pytest.mark.unit
def test_count_run_events_single_page_no_token() -> None:
    logs = MagicMock()
    logs.filter_log_events.return_value = {"events": [{}, {}, {}]}  # no nextToken
    verifier = _make_verifier()
    assert verifier._count_run_events(logs, "/telemetry/g/ingest/otlp-logs") == 3
    assert logs.filter_log_events.call_count == 1
    # The CWL filter scopes to this run's unique run_id, not the shared tool value.
    assert logs.filter_log_events.call_args.kwargs["filterPattern"] == f'"{RUN_ID}"'


# ---------------------------------------------------------------------------
# Firehose
# ---------------------------------------------------------------------------


def _firehose_client() -> MagicMock:
    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {"DeliveryStreamNames": ["sandbox-telemetry"]}
    firehose.describe_delivery_stream.return_value = {
        "DeliveryStreamDescription": {
            "Destinations": [
                {
                    "ExtendedS3DestinationDescription": {
                        "BucketARN": "arn:aws:s3:::lake",
                        "Prefix": "raw/tool=!{x}/",
                        "ErrorOutputPrefix": "errors/!{x}/",
                    }
                }
            ]
        }
    }
    return firehose


@pytest.mark.unit
def test_check_firehose_metrics_advanced() -> None:
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {"Datapoints": [{"Sum": 12.0}]}
    verifier = _make_verifier({"firehose": _firehose_client(), "cloudwatch": cw})
    probes: list[dict[str, Any]] = []
    discovered = verifier._check_firehose(probes)
    assert discovered is not None and discovered["bucket"] == "lake"
    statuses = _statuses(probes)
    assert statuses["firehose:stream"] == "OK"
    assert statuses["firehose:metric:IncomingRecords"] == "OK"


@pytest.mark.unit
def test_check_firehose_zero_metrics_is_info() -> None:
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {"Datapoints": []}
    verifier = _make_verifier({"firehose": _firehose_client(), "cloudwatch": cw})
    probes: list[dict[str, Any]] = []
    verifier._check_firehose(probes)
    assert _statuses(probes)["firehose:metric:DeliveryToS3.Records"] == "INFO"


@pytest.mark.unit
def test_check_firehose_no_stream_fails() -> None:
    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {"DeliveryStreamNames": []}
    verifier = _make_verifier({"firehose": firehose})
    probes: list[dict[str, Any]] = []
    assert verifier._check_firehose(probes) is None
    assert _statuses(probes)["firehose:stream"] == "FAIL"


@pytest.mark.unit
def test_metric_window_widens_when_start_equals_end() -> None:
    # A fast run records started_at == finished_at at 1s resolution. The window
    # must still be valid (StartTime strictly before EndTime, start floored to
    # the minute) so CloudWatch does not raise InvalidParameterValue.
    same = "2026-06-28T12:00:45Z"
    start, end = e2e_verify.E2EVerifier._metric_window(same, same)
    assert start < end
    assert start.second == 0 and start.microsecond == 0
    assert (end - start).total_seconds() >= 60
    assert start.tzinfo is not None and end.tzinfo is not None


@pytest.mark.unit
def test_metric_window_preserves_wide_finish() -> None:
    start, end = e2e_verify.E2EVerifier._metric_window(
        "2026-06-28T12:00:45Z", "2026-06-28T12:09:50Z"
    )
    assert start.minute == 0 and start.second == 0
    assert end == datetime.datetime(2026, 6, 28, 12, 9, 50, tzinfo=datetime.UTC)


@pytest.mark.unit
def test_check_firehose_fast_run_passes_valid_window_to_cloudwatch() -> None:
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {"Datapoints": [{"Sum": 12.0}]}
    manifest = {**MANIFEST, "started_at": "2026-06-28T12:00:30Z"}
    manifest["finished_at"] = manifest["started_at"]  # fast run, equal timestamps
    verifier = _make_verifier({"firehose": _firehose_client(), "cloudwatch": cw}, manifest=manifest)
    probes: list[dict[str, Any]] = []
    verifier._check_firehose(probes)
    _args, kwargs = cw.get_metric_statistics.call_args
    assert kwargs["StartTime"] < kwargs["EndTime"]


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------


def _s3_ok() -> MagicMock:
    s3 = MagicMock()
    s3.list_objects_v2.side_effect = [
        {"Contents": [{"Key": f"raw/tool={TOOL}/dt=2026-06-28/part-0.parquet"}]},  # objects
        {"Contents": []},  # errors
    ]
    s3.head_object.return_value = {
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "arn:aws:kms:us-east-1:111:key/abc",
    }
    return s3


@pytest.mark.unit
def test_check_s3_success() -> None:
    verifier = _make_verifier({"s3": _s3_ok()})
    probes: list[dict[str, Any]] = []
    verifier._check_s3(
        probes, {"bucket": "lake", "prefix": "raw/tool=!{x}/", "error_prefix": "errors/!{x}/"}
    )
    statuses = _statuses(probes)
    assert statuses["s3:objects"] == "OK"
    assert statuses["s3:encryption"] == "OK"
    assert statuses["s3:errors"] == "OK"


@pytest.mark.unit
def test_check_s3_missing_bucket_fails() -> None:
    verifier = _make_verifier({"s3": MagicMock()})
    probes: list[dict[str, Any]] = []
    verifier._check_s3(probes, {"bucket": "", "prefix": "", "error_prefix": ""})
    assert _statuses(probes)["s3:bucket"] == "FAIL"


@pytest.mark.unit
def test_check_s3_no_objects_fails() -> None:
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {"Contents": []}
    verifier = _make_verifier({"s3": s3})
    probes: list[dict[str, Any]] = []
    verifier._check_s3(probes, {"bucket": "lake", "prefix": "raw/", "error_prefix": "errors/"})
    assert _statuses(probes)["s3:objects"] == "FAIL"


@pytest.mark.unit
def test_check_s3_wrong_encryption_fails() -> None:
    s3 = MagicMock()
    s3.list_objects_v2.side_effect = [
        {"Contents": [{"Key": "raw/part.parquet"}]},
        {"Contents": []},
    ]
    s3.head_object.return_value = {"ServerSideEncryption": "AES256"}
    verifier = _make_verifier({"s3": s3})
    probes: list[dict[str, Any]] = []
    verifier._check_s3(probes, {"bucket": "lake", "prefix": "raw/", "error_prefix": "errors/"})
    assert _statuses(probes)["s3:encryption"] == "FAIL"


@pytest.mark.unit
def test_check_s3_fresh_errors_fail() -> None:
    s3 = MagicMock()
    s3.list_objects_v2.side_effect = [
        {"Contents": [{"Key": "raw/part.parquet"}]},
        {"Contents": [{"Key": "errors/x", "LastModified": "2026-06-28T00:30:00Z"}]},
    ]
    s3.head_object.return_value = {
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "key/abc",
    }
    verifier = _make_verifier({"s3": s3})
    probes: list[dict[str, Any]] = []
    verifier._check_s3(probes, {"bucket": "lake", "prefix": "raw/", "error_prefix": "errors/"})
    assert _statuses(probes)["s3:errors"] == "FAIL"


# ---------------------------------------------------------------------------
# Glue
# ---------------------------------------------------------------------------


_DEPLOYED_GLUE_TABLE = "telemetry_useast1_sandbox_shared_data_lake_000_events"


def _telemetry_table(
    name: str = _DEPLOYED_GLUE_TABLE,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    table: dict[str, Any] = {
        "Name": name,
        "StorageDescriptor": {
            "Location": "s3://lake/raw/",
            # tool is a partition key only, not a data column (BUG-3 fix).
            "Columns": [
                {"Name": "timestamp"},
                {"Name": "event_type"},
                {"Name": "payload"},
            ],
        },
        "PartitionKeys": [{"Name": "tool"}, {"Name": "dt"}],
    }
    if parameters is not None:
        table["Parameters"] = parameters
    return table


# The deployed data-lake table parameters: the tool partition is ENUM projection over
# a governed values list (no physical partition in the Glue catalog), dt is date
# projection.
_ENUM_PROJECTION_PARAMETERS = {
    "projection.enabled": "true",
    "projection.tool.type": "enum",
    "projection.tool.values": "e2e-smoke,example-cli",
    "projection.dt.type": "date",
}


def _glue_schema_match(
    partitions: list[dict[str, Any]] | None = None,
    parameters: dict[str, Any] | None = None,
) -> MagicMock:
    glue = MagicMock()
    glue.get_databases.return_value = {"DatabaseList": [{"Name": "telemetry_db"}]}
    glue.get_tables.return_value = {"TableList": [_telemetry_table(parameters=parameters)]}
    glue.get_partitions.return_value = {
        "Partitions": partitions if partitions is not None else [{"Values": [TOOL, "2026-06-28"]}]
    }
    return glue


@pytest.mark.unit
def test_check_glue_success() -> None:
    glue = _glue_schema_match()
    verifier = _make_verifier({"glue": glue})
    probes: list[dict[str, Any]] = []
    discovered = verifier._check_glue(probes)
    assert discovered is not None
    assert discovered["database"] == "telemetry_db"
    # The namespace-derived table name is discovered by schema, never hardcoded.
    assert discovered["table"] == _DEPLOYED_GLUE_TABLE
    statuses = _statuses(probes)
    assert statuses["glue:table"] == "OK"
    assert statuses["glue:partitions"] == "OK"
    # Partitions are queried against the DISCOVERED table name.
    _args, kwargs = glue.get_partitions.call_args
    assert kwargs["TableName"] == _DEPLOYED_GLUE_TABLE


@pytest.mark.unit
def test_check_glue_no_table_fails() -> None:
    glue = MagicMock()
    glue.get_databases.return_value = {"DatabaseList": [{"Name": "db"}]}
    glue.get_tables.return_value = {"TableList": []}
    verifier = _make_verifier({"glue": glue})
    probes: list[dict[str, Any]] = []
    assert verifier._check_glue(probes) is None
    assert _statuses(probes)["glue:table"] == "FAIL"


@pytest.mark.unit
def test_check_glue_no_partitions_fails() -> None:
    glue = _glue_schema_match(partitions=[])
    verifier = _make_verifier({"glue": glue})
    probes: list[dict[str, Any]] = []
    verifier._check_glue(probes)
    assert _statuses(probes)["glue:partitions"] == "FAIL"


@pytest.mark.unit
def test_check_glue_enum_projection_does_not_call_get_partitions() -> None:
    # The deployed table uses Athena ENUM projection on the tool partition, so the
    # Glue catalog has NO physical tool=<value> partition: get_partitions returns []
    # by design. The check must NOT false-FAIL (issue #85) -- it must report OK WITHOUT
    # requiring get_partitions; presence is proven later by the Athena reconciliation.
    glue = _glue_schema_match(partitions=[], parameters=_ENUM_PROJECTION_PARAMETERS)
    verifier = _make_verifier({"glue": glue})
    probes: list[dict[str, Any]] = []
    discovered = verifier._check_glue(probes)
    assert discovered is not None
    statuses = _statuses(probes)
    assert statuses["glue:table"] == "OK"
    # OK despite zero physical partitions, BECAUSE the partition is enum projection.
    assert statuses["glue:partitions"] == "OK"
    # The enum values list is surfaced in the probe detail for evidence.
    partitions_probe = next(p for p in probes if p["name"] == "glue:partitions")
    assert "enum projection" in partitions_probe["detail"]
    assert "e2e-smoke,example-cli" in partitions_probe["detail"]
    # get_partitions is never called for a projection-managed partition.
    glue.get_partitions.assert_not_called()


@pytest.mark.unit
def test_check_glue_physical_partitions_still_required_without_projection() -> None:
    # A table that does NOT use partition projection still requires a physical
    # tool=<value> partition: an empty get_partitions result is a real FAIL.
    glue = _glue_schema_match(partitions=[], parameters={"classification": "parquet"})
    verifier = _make_verifier({"glue": glue})
    probes: list[dict[str, Any]] = []
    verifier._check_glue(probes)
    assert _statuses(probes)["glue:partitions"] == "FAIL"
    glue.get_partitions.assert_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        # enum projection over a governed values list -> projection-managed.
        (
            {
                "projection.enabled": "true",
                "projection.tool.type": "enum",
                "projection.tool.values": "e2e-smoke,example-cli",
            },
            True,
        ),
        ({"projection.enabled": "TRUE", "projection.tool.type": "enum"}, True),
        # Any declared projection type is projection-managed (no physical partition).
        ({"projection.enabled": "true", "projection.tool.type": "injected"}, True),
        # A projection type is declared but projection is not enabled.
        ({"projection.enabled": "false", "projection.tool.type": "enum"}, False),
        # Projection enabled but no type declared for the tool column -> not managed.
        ({"projection.enabled": "true"}, False),
        ({}, False),
    ],
)
def test_is_projected_partition(parameters: dict[str, Any], expected: bool) -> None:
    assert e2e_verify.E2EVerifier._is_projected_partition(parameters, "tool") is expected


# ---------------------------------------------------------------------------
# Athena
# ---------------------------------------------------------------------------


def _athena_ok(found_counts: dict[str, int] | None = None) -> MagicMock:
    counts = found_counts or {"session_start": 6, "command_execution": 6}
    athena = MagicMock()
    athena.list_work_groups.return_value = {"WorkGroups": [{"Name": "sandbox-analytics"}]}
    athena.get_work_group.return_value = {
        "WorkGroup": {
            "Configuration": {
                "BytesScannedCutoffPerQuery": 107374182400,
                "ResultConfiguration": {"EncryptionConfiguration": {"EncryptionOption": "SSE_KMS"}},
            }
        }
    }
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    # The run-scoped reconciliation query selects (event_type, count) -- the tool is
    # the fixed e2e-smoke value and the run is scoped by the payload run_id predicate,
    # so tool is no longer projected. The first row is the column-name header row.
    rows: list[dict[str, Any]] = [{"Data": [{"VarCharValue": "event_type"}, {"VarCharValue": "c"}]}]
    for event_type, count in counts.items():
        rows.append({"Data": [{"VarCharValue": event_type}, {"VarCharValue": str(count)}]})
    athena.get_query_results.return_value = {"ResultSet": {"Rows": rows}}
    return athena


@pytest.mark.unit
def test_check_athena_success_reconciles() -> None:
    athena = _athena_ok()
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "telemetry_db", "table": "telemetry_events"})
    statuses = _statuses(probes)
    assert statuses["athena:workgroup"] == "OK"
    assert statuses["athena:cost-cap"] == "OK"
    assert statuses["athena:results-encrypted"] == "OK"
    # The no-tool-filter queryability guard ran and SUCCEEDED (enum keeps the table
    # queryable without a WHERE tool='...' predicate; the direct regression guard).
    assert statuses["athena:queryable"] == "OK"
    assert statuses["athena:query"] == "OK"
    assert statuses["athena:reconcile"] == "OK"
    # The reconciliation (last) query binds the fixed tool value AND the run_id as
    # native parameters, never interpolated; the run is scoped by a json_extract_scalar
    # predicate on the payload run_id, never a LIKE.
    _args, kwargs = athena.start_query_execution.call_args
    assert kwargs["ExecutionParameters"] == [TOOL, RUN_ID]
    query = kwargs["QueryString"]
    assert "tool = ?" in query
    assert "json_extract_scalar(payload, '$.run_id') = ?" in query
    assert "LIKE" not in query.upper()
    # The queryability-guard query (first call) carries NO WHERE predicate at all.
    guard_query = athena.start_query_execution.call_args_list[0].kwargs["QueryString"]
    assert "WHERE" not in guard_query.upper()
    assert "count(*)" in guard_query


@pytest.mark.unit
def test_check_athena_no_workgroup_fails() -> None:
    athena = MagicMock()
    athena.list_work_groups.return_value = {"WorkGroups": []}
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    assert _statuses(probes)["athena:workgroup"] == "FAIL"


@pytest.mark.unit
def test_check_athena_missing_cost_cap_and_encryption_fail() -> None:
    athena = _athena_ok()
    athena.get_work_group.return_value = {"WorkGroup": {"Configuration": {}}}
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    statuses = _statuses(probes)
    assert statuses["athena:cost-cap"] == "FAIL"
    assert statuses["athena:results-encrypted"] == "FAIL"


@pytest.mark.unit
def test_check_athena_reconcile_mismatch_fails() -> None:
    athena = _athena_ok({"session_start": 1})  # manifest expects 6+6
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    assert _statuses(probes)["athena:reconcile"] == "FAIL"


@pytest.mark.unit
def test_check_athena_skips_short_result_rows() -> None:
    athena = _athena_ok()
    # Inject a malformed short row (< 2 columns) that must be skipped, not crash.
    athena.get_query_results.return_value = {
        "ResultSet": {
            "Rows": [
                {"Data": [{"VarCharValue": "event_type"}, {"VarCharValue": "c"}]},
                {"Data": [{"VarCharValue": "session_start"}, {"VarCharValue": "6"}]},
                {"Data": [{"VarCharValue": "command_execution"}, {"VarCharValue": "6"}]},
                {"Data": [{"VarCharValue": "orphan-column"}]},
            ]
        }
    }
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    assert _statuses(probes)["athena:reconcile"] == "OK"


@pytest.mark.unit
def test_check_athena_reconciles_across_paginated_result_pages() -> None:
    # get_query_results returns at most one page plus a NextToken. The reconciliation
    # must read EVERY page or it under-counts and false-FAILs (issue #85). Athena puts
    # the column-header row on the FIRST page only; here the two data rows that satisfy
    # the manifest (session_start=6, command_execution=6) arrive on separate pages. The
    # queryability guard runs with fetch_rows=False, so it does NOT consume a page.
    athena = _athena_ok()
    athena.get_query_results.side_effect = [
        {
            "ResultSet": {
                "Rows": [
                    {"Data": [{"VarCharValue": "event_type"}, {"VarCharValue": "c"}]},
                    {"Data": [{"VarCharValue": "session_start"}, {"VarCharValue": "6"}]},
                ]
            },
            "NextToken": "page-2",
        },
        {
            "ResultSet": {
                "Rows": [
                    {"Data": [{"VarCharValue": "command_execution"}, {"VarCharValue": "6"}]},
                ]
            }
        },
    ]
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "telemetry_db", "table": "telemetry_events"})
    # OK only because BOTH pages were read (12 of 12 reconciled).
    assert _statuses(probes)["athena:reconcile"] == "OK"
    assert athena.get_query_results.call_count == 2
    assert athena.get_query_results.call_args_list[1].kwargs["NextToken"] == "page-2"


@pytest.mark.unit
def test_fetch_query_rows_follows_next_token() -> None:
    athena = MagicMock()
    athena.get_query_results.side_effect = [
        {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "a"}]}]}, "NextToken": "n1"},
        {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "b"}]}]}},
    ]
    rows = e2e_verify.E2EVerifier._fetch_query_rows(athena, "q1")
    assert len(rows) == 2
    assert athena.get_query_results.call_count == 2
    assert athena.get_query_results.call_args_list[1].kwargs["NextToken"] == "n1"


@pytest.mark.unit
def test_check_athena_query_failed_returns_early() -> None:
    athena = _athena_ok()
    athena.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "syntax"}}
    }
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    statuses = _statuses(probes)
    # Both the guard query and the reconcile query FAILED; reconcile returns early.
    assert statuses["athena:queryable"] == "FAIL"
    assert statuses["athena:query"] == "FAIL"
    assert "athena:reconcile" not in statuses


@pytest.mark.unit
def test_check_athena_rejects_unsafe_table() -> None:
    verifier = _make_verifier({"athena": _athena_ok()})
    probes: list[dict[str, Any]] = []
    with pytest.raises(e2e_verify.e2e_common.E2EError):
        verifier._check_athena(probes, {"database": "db", "table": "evil; DROP TABLE x"})


@pytest.mark.unit
def test_check_athena_queryability_guard_failure_is_surfaced() -> None:
    # The no-tool-filter SELECT count(*) is the direct regression guard for the
    # injected->enum fix: if the table rejects an unfiltered query (as injected
    # projection did with CONSTRAINT_VIOLATION), the guard MUST FAIL.
    athena = _athena_ok()
    # The FIRST query (the guard) FAILS; the reconcile query (second) still runs.
    athena.get_query_execution.side_effect = [
        {
            "QueryExecution": {
                "Status": {"State": "FAILED", "StateChangeReason": "CONSTRAINT_VIOLATION"}
            }
        },
        {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
    ]
    verifier = _make_verifier({"athena": athena})
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    statuses = _statuses(probes)
    assert statuses["athena:queryable"] == "FAIL"
    # The guard query carried no WHERE predicate at all.
    guard_query = athena.start_query_execution.call_args_list[0].kwargs["QueryString"]
    assert "WHERE" not in guard_query.upper()


@pytest.mark.unit
def test_check_athena_no_data_counts_skips_reconcile() -> None:
    # A non-data conformance run records no per-event counts: reconciliation is OK
    # (nothing landed in the lake) and the reconcile query is skipped -- but the
    # no-filter queryability guard STILL runs (it is independent of run data).
    manifest = {**MANIFEST, "counts_by_event": {}, "total_records": 0}
    athena = _athena_ok()
    verifier = _make_verifier({"athena": athena}, manifest=manifest)
    probes: list[dict[str, Any]] = []
    verifier._check_athena(probes, {"database": "db", "table": "telemetry_events"})
    statuses = _statuses(probes)
    assert statuses["athena:queryable"] == "OK"
    assert statuses["athena:reconcile"] == "OK"
    assert "athena:query" not in statuses  # the reconcile query was not issued
    # Exactly one query ran: the queryability guard.
    assert athena.start_query_execution.call_count == 1


@pytest.mark.unit
def test_tool_and_run_id_read_from_manifest() -> None:
    verifier = _make_verifier()
    assert verifier._tool == TOOL
    assert verifier._run_id == RUN_ID


@pytest.mark.unit
def test_build_count_query_scopes_by_tool_and_run_id() -> None:
    query = e2e_verify.E2EVerifier._build_count_query("telemetry_events")
    assert "WHERE tool = ?" in query
    assert "json_extract_scalar(payload, '$.run_id') = ?" in query
    assert query.count("?") == 2  # tool value + run id, both bound parameters
    assert "GROUP BY event_type" in query
    assert "LIKE" not in query.upper()


@pytest.mark.unit
def test_build_queryability_query_has_no_predicate() -> None:
    query = e2e_verify.E2EVerifier._build_queryability_query("telemetry_events")
    assert "count(*)" in query
    assert "telemetry_events" in query
    assert "WHERE" not in query.upper()  # no tool filter -> the regression guard
    assert "?" not in query  # no bound parameters


# ---------------------------------------------------------------------------
# QuickSight + embed Lambda
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_quicksight_enumerates() -> None:
    qs = MagicMock()
    qs.list_data_sources.return_value = {"DataSources": [{"Type": "ATHENA"}, {"Type": "S3"}]}
    qs.list_data_sets.return_value = {"DataSetSummaries": [{}, {}]}
    qs.list_dashboards.return_value = {"DashboardSummaryList": [{}]}
    verifier = _make_verifier({"quicksight": qs})
    probes: list[dict[str, Any]] = []
    verifier._check_quicksight(probes)
    statuses = _statuses(probes)
    assert statuses["quicksight:data-sources"] == "INFO"
    assert statuses["quicksight:dashboards"] == "INFO"


@pytest.mark.unit
def test_check_quicksight_not_enumerable_is_info() -> None:
    qs = MagicMock()
    qs.list_data_sources.side_effect = _client_error("AccessDeniedException")
    verifier = _make_verifier({"quicksight": qs})
    probes: list[dict[str, Any]] = []
    verifier._check_quicksight(probes)
    assert _statuses(probes)["quicksight:enumerate"] == "INFO"


def _embed_lambda(status_by_token: dict[str, int]) -> MagicMock:
    lambda_client = MagicMock()
    lambda_client.list_functions.return_value = {
        "Functions": [{"FunctionName": "sandbox-portal-embed-url"}]
    }
    lambda_client.get_function_url_config.return_value = {
        "FunctionUrl": "https://abc123.lambda-url.us-east-1.on.aws/"
    }

    def invoke(**kwargs: Any) -> dict[str, Any]:
        event = json.loads(kwargs["Payload"].decode("utf-8"))
        token = event["headers"]["Authorization"]
        origin = event["queryStringParameters"]["origin"]
        if token.count(".") != 2:
            code = 401
        elif "evil" in origin:
            code = 403
        else:
            code = status_by_token.get("valid", 200)
        stream = MagicMock()
        stream.read.return_value = json.dumps({"statusCode": code, "body": "{}"}).encode("utf-8")
        return {"Payload": stream}

    lambda_client.invoke.side_effect = invoke
    return lambda_client


def _stream(payload: bytes) -> MagicMock:
    stream = MagicMock()
    stream.read.return_value = payload
    return stream


@pytest.mark.unit
def test_check_embed_lambda_expected_codes() -> None:
    verifier = _make_verifier({"lambda": _embed_lambda({"valid": 200})})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    statuses = _statuses(probes)
    assert statuses["embed:function"] == "OK"
    assert statuses["embed:invalid-token"] == "OK"
    assert statuses["embed:bad-origin"] == "OK"
    assert statuses["embed:valid"] == "OK"


@pytest.mark.unit
def test_check_embed_lambda_valid_502_is_info_not_fail() -> None:
    verifier = _make_verifier({"lambda": _embed_lambda({"valid": 502})})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    assert _statuses(probes)["embed:valid"] == "INFO"


@pytest.mark.unit
def test_check_embed_lambda_no_function_is_info() -> None:
    lambda_client = MagicMock()
    lambda_client.list_functions.return_value = {"Functions": []}
    verifier = _make_verifier({"lambda": lambda_client})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    assert _statuses(probes)["embed:function"] == "INFO"


@pytest.mark.unit
def test_check_embed_lambda_reports_function_url() -> None:
    verifier = _make_verifier({"lambda": _embed_lambda({"valid": 200})})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    url_probe = next(p for p in probes if p["name"] == "embed:function-url")
    assert url_probe["status"] == "INFO"
    assert url_probe["detail"].endswith(".on.aws/")


@pytest.mark.unit
def test_check_embed_lambda_no_function_url_is_info() -> None:
    lambda_client = _embed_lambda({"valid": 200})
    lambda_client.get_function_url_config.side_effect = _client_error("ResourceNotFoundException")
    verifier = _make_verifier({"lambda": lambda_client})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    url_probe = next(p for p in probes if p["name"] == "embed:function-url")
    assert url_probe["status"] == "INFO"
    assert url_probe["detail"] == "no Function URL configured"


@pytest.mark.unit
def test_check_embed_lambda_function_error_degrades_to_info() -> None:
    # A FunctionError must NOT produce a spurious statusCode=0 FAIL; it degrades
    # to an INFO/NA observation carrying the diagnostic reason.
    lambda_client = _embed_lambda({"valid": 200})
    error_payload = json.dumps({"errorMessage": "boom", "errorType": "RuntimeError"}).encode(
        "utf-8"
    )
    lambda_client.invoke.side_effect = None
    lambda_client.invoke.return_value = {
        "FunctionError": "Unhandled",
        "Payload": _stream(error_payload),
    }
    verifier = _make_verifier({"lambda": lambda_client})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    statuses = _statuses(probes)
    assert statuses["embed:invalid-token"] == "INFO"
    assert statuses["embed:bad-origin"] == "INFO"
    # No probe was marked FAIL by the embed checks.
    assert "FAIL" not in statuses.values()


@pytest.mark.unit
def test_check_embed_lambda_missing_status_code_degrades_to_info() -> None:
    lambda_client = _embed_lambda({"valid": 200})
    lambda_client.invoke.side_effect = None
    lambda_client.invoke.return_value = {"Payload": _stream(b'{"body": "no status here"}')}
    verifier = _make_verifier({"lambda": lambda_client})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    assert _statuses(probes)["embed:invalid-token"] == "INFO"


@pytest.mark.unit
def test_check_embed_lambda_wrong_determinate_status_fails() -> None:
    # A determinate but unexpected status (e.g. 500 for an invalid token) is a
    # real failure, not NA.
    lambda_client = _embed_lambda({"valid": 200})
    lambda_client.invoke.side_effect = None
    lambda_client.invoke.return_value = {"Payload": _stream(b'{"statusCode": 500}')}
    verifier = _make_verifier({"lambda": lambda_client})
    probes: list[dict[str, Any]] = []
    verifier._check_embed_lambda(probes)
    assert _statuses(probes)["embed:invalid-token"] == "FAIL"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"Payload": "_status_200"}, (200, "")),
        ({"FunctionError": "Unhandled", "Payload": "_err"}, None),
        ({"Payload": "_no_status"}, None),
        ({"Payload": "_not_json"}, None),
        ({}, None),
    ],
)
def test_invoke_embed_parsing(response: dict[str, Any], expected: Any) -> None:
    payloads = {
        "_status_200": b'{"statusCode": 200}',
        "_err": b'{"errorMessage": "boom"}',
        "_no_status": b'{"body": "x"}',
        "_not_json": b"not-json-at-all",
    }
    if "Payload" in response:
        response = {**response, "Payload": _stream(payloads[response["Payload"]])}
    lambda_client = MagicMock()
    lambda_client.invoke.return_value = response
    status_code, detail = e2e_verify.E2EVerifier._invoke_embed(
        lambda_client, "fn", "tok", "https://origin"
    )
    if expected is None:
        assert status_code is None
        assert detail  # a non-empty diagnostic reason is always present
    else:
        assert (status_code, detail) == expected


@pytest.mark.unit
def test_structural_jwt_three_segments() -> None:
    import base64

    token = e2e_verify.E2EVerifier._structural_jwt()
    parts = token.split(".")
    assert len(parts) == 3
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    assert json.loads(base64.urlsafe_b64decode(payload))["sub"] == "synthetic-e2e"


# ---------------------------------------------------------------------------
# run() + CLI
# ---------------------------------------------------------------------------


def _full_clients() -> dict[str, Any]:
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {"Datapoints": [{"Sum": 12.0}]}
    logs = MagicMock()
    logs.describe_log_groups.return_value = {
        "logGroups": [{"logGroupName": "/telemetry/x-sandbox-y/ingest/otlp-logs"}]
    }
    logs.filter_log_events.return_value = {"events": [{}] * 12}
    glue = _glue_schema_match(partitions=[{"Values": [TOOL]}])
    qs = MagicMock()
    qs.list_data_sources.return_value = {"DataSources": []}
    qs.list_data_sets.return_value = {"DataSetSummaries": []}
    qs.list_dashboards.return_value = {"DashboardSummaryList": []}
    return {
        "logs": logs,
        "firehose": _firehose_client(),
        "cloudwatch": cw,
        "s3": _s3_ok(),
        "glue": glue,
        "athena": _athena_ok(),
        "quicksight": qs,
        "lambda": _embed_lambda({"valid": 200}),
    }


@pytest.mark.unit
def test_run_all_pass_writes_evidence(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "evidence.json"
    verifier = _make_verifier(_full_clients())
    verifier._output_path = str(out)
    assert verifier.run() == 0
    evidence = json.loads(out.read_text())
    assert evidence["result"] == "OK"
    assert evidence["tool_value"] == TOOL


@pytest.mark.unit
def test_run_with_failure_returns_one() -> None:
    clients = _full_clients()
    clients["logs"].filter_log_events.return_value = {"events": []}  # CWL never receives
    verifier = _make_verifier(clients)
    assert verifier.run() == 1


# ---------------------------------------------------------------------------
# Post-verify cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_delete_prefix_deletes_all_pages() -> None:
    s3 = MagicMock()
    s3.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": f"raw/tool={TOOL}/dt=2026-06-28/a"}],
            "IsTruncated": True,
            "NextContinuationToken": "t2",
        },
        {
            "Contents": [
                {"Key": f"raw/tool={TOOL}/dt=2026-06-28/b"},
                {"Key": f"raw/tool={TOOL}/dt=2026-06-28/c"},
            ],
            "IsTruncated": False,
        },
    ]
    deleted = e2e_verify.E2EVerifier._delete_prefix(s3, "lake", f"raw/tool={TOOL}/dt=2026-06-28/")
    assert deleted == 3
    # Both list pages were followed and each page was deleted in one batch.
    assert s3.list_objects_v2.call_count == 2
    assert s3.delete_objects.call_count == 2
    assert s3.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"] == "t2"
    # Every deleted key stays under the requested prefix (no other tool touched).
    for call in s3.delete_objects.call_args_list:
        for obj in call.kwargs["Delete"]["Objects"]:
            assert obj["Key"].startswith(f"raw/tool={TOOL}/dt=2026-06-28/")


@pytest.mark.unit
def test_delete_prefix_empty_is_noop() -> None:
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
    deleted = e2e_verify.E2EVerifier._delete_prefix(s3, "lake", f"raw/tool={TOOL}/dt=x/")
    assert deleted == 0
    s3.delete_objects.assert_not_called()


@pytest.mark.unit
def test_cleanup_run_objects_scopes_to_reserved_tool_date_partition() -> None:
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {
        "Contents": [{"Key": f"raw/tool={TOOL}/dt=2026-06-28/part-0"}],
        "IsTruncated": False,
    }
    verifier = _make_verifier({"s3": s3})
    verifier._cleanup = True
    probes: list[dict[str, Any]] = []
    firehose = {
        "bucket": "lake",
        "prefix": "raw/tool=!{x}/dt=!{y}/",
        "error_prefix": "errors/!{x}/",
    }
    verifier._cleanup_run_objects(probes, firehose)
    assert _statuses(probes)["cleanup:objects"] == "OK"
    # The list + delete are scoped to THIS run's date under the reserved e2e-smoke
    # partition only -- never another tool's data.
    list_kwargs = s3.list_objects_v2.call_args.kwargs
    assert list_kwargs["Bucket"] == "lake"
    assert list_kwargs["Prefix"] == f"raw/tool={TOOL}/dt=2026-06-28/"
    s3.delete_objects.assert_called_once()


@pytest.mark.unit
def test_cleanup_run_objects_no_bucket_fails() -> None:
    verifier = _make_verifier({"s3": MagicMock()})
    probes: list[dict[str, Any]] = []
    verifier._cleanup_run_objects(probes, None)
    assert _statuses(probes)["cleanup:objects"] == "FAIL"
    probes.clear()
    verifier._cleanup_run_objects(probes, {"bucket": "", "prefix": "", "error_prefix": ""})
    assert _statuses(probes)["cleanup:objects"] == "FAIL"


@pytest.mark.unit
def test_run_with_cleanup_deletes_run_objects() -> None:
    clients = _full_clients()
    s3 = MagicMock()
    s3.list_objects_v2.side_effect = [
        {"Contents": [{"Key": f"raw/tool={TOOL}/dt=2026-06-28/part-0.parquet"}]},  # verify objects
        {"Contents": []},  # verify errors
        {  # cleanup listing
            "Contents": [{"Key": f"raw/tool={TOOL}/dt=2026-06-28/part-0.parquet"}],
            "IsTruncated": False,
        },
    ]
    s3.head_object.return_value = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": "key/abc"}
    clients["s3"] = s3
    verifier = _make_verifier(clients)
    verifier._cleanup = True
    assert verifier.run() == 0
    # Cleanup ran after verification and deleted the run's reserved-tool date partition.
    s3.delete_objects.assert_called_once()
    del_kwargs = s3.delete_objects.call_args.kwargs
    assert del_kwargs["Bucket"] == "lake"
    for obj in del_kwargs["Delete"]["Objects"]:
        assert obj["Key"].startswith(f"raw/tool={TOOL}/dt=2026-06-28/")


@pytest.mark.unit
def test_run_without_cleanup_does_not_delete() -> None:
    # Default (cleanup disabled) leaves the lake untouched -- verify is read-only.
    clients = _full_clients()
    verifier = _make_verifier(clients)
    assert verifier.run() == 0
    clients["s3"].delete_objects.assert_not_called()


@pytest.mark.unit
def test_main_bad_manifest_path_exits_two(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit) as exc:
        e2e_verify.main(["--env", "sandbox", "--manifest", str(tmp_path / "nope.json")])
    assert exc.value.code == 2


@pytest.mark.unit
def test_main_runs_with_injected_verifier(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps(MANIFEST))
    captured: dict[str, Any] = {}

    class _StubVerifier:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        def run(self) -> int:
            return 0

    monkeypatch.setattr(e2e_verify, "E2EVerifier", _StubVerifier)
    with pytest.raises(SystemExit) as exc:
        e2e_verify.main(["--env", "sandbox", "--manifest", str(manifest_path)])
    assert exc.value.code == 0
    # cleanup defaults off so verify is read-only unless --cleanup is passed.
    assert captured["cleanup"] is False


@pytest.mark.unit
def test_main_forwards_cleanup_flag(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text(json.dumps(MANIFEST))
    captured: dict[str, Any] = {}

    class _StubVerifier:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        def run(self) -> int:
            return 0

    monkeypatch.setattr(e2e_verify, "E2EVerifier", _StubVerifier)
    with pytest.raises(SystemExit):
        e2e_verify.main(["--env", "sandbox", "--manifest", str(manifest_path), "--cleanup"])
    assert captured["cleanup"] is True
