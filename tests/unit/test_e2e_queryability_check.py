"""Unit tests for scripts/e2e_queryability_check.py (post-deploy queryability gate).

Every AWS client is a MagicMock returning canned Glue/Athena responses; no
network or AWS call is made. Poll budgets are tiny so timeout paths are fast.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts import constants, e2e_common, e2e_queryability_check

_ENUM_PARAMS = {
    "projection.enabled": "true",
    "projection.tool.type": "enum",
    "projection.tool.values": "e2e-smoke,example-cli",
}
_INJECTED_PARAMS = {
    "projection.enabled": "true",
    "projection.tool.type": "injected",
}


def _matching_table(name: str, parameters: dict[str, str]) -> dict[str, Any]:
    """Build a Glue table dict whose schema matches the telemetry table."""
    return {
        "Name": name,
        "StorageDescriptor": {
            "Columns": [{"Name": c} for c in constants.E2E_GLUE_REQUIRED_COLUMNS],
            "Location": "s3://data-lake/raw/",
        },
        "PartitionKeys": [{"Name": p} for p in constants.E2E_GLUE_PARTITION_KEYS],
        "Parameters": parameters,
    }


def _make_boto3(
    tables: list[dict[str, Any]],
    workgroups: list[str],
    query_states: list[str],
) -> tuple[Any, Any]:
    """Return ``(boto3_module, athena_client)`` wired with canned responses."""
    glue = MagicMock()
    glue.get_databases.return_value = {"DatabaseList": [{"Name": "telemetry_prod_db"}]}
    glue.get_tables.return_value = {"TableList": tables}

    athena = MagicMock()
    athena.list_work_groups.return_value = {"WorkGroups": [{"Name": n} for n in workgroups]}
    athena.start_query_execution.return_value = {"QueryExecutionId": "qid-123"}
    athena.get_query_execution.side_effect = [
        {"QueryExecution": {"Status": {"State": s, "StateChangeReason": "reason-x"}}}
        for s in query_states
    ]

    registry = {"glue": glue, "athena": athena}
    session = MagicMock()
    session.client.side_effect = lambda name: registry[name]
    boto3_module = MagicMock()
    boto3_module.Session.return_value = session
    return boto3_module, athena


@pytest.mark.unit
def test_pass_when_enum_and_no_filter_query_succeeds(tmp_path: pathlib.Path) -> None:
    boto3_module, _ = _make_boto3(
        [_matching_table("telemetry_prod_events", _ENUM_PARAMS)],
        ["telemetry-prod-analytics"],
        ["SUCCEEDED"],
    )
    out = tmp_path / "evidence.json"
    code = e2e_queryability_check.check_queryability(
        env="prod",
        boto3_module=boto3_module,
        output_path=str(out),
        region="us-east-1",
        sleep_fn=lambda _s: None,
    )
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["overall"] == "PASS"
    statuses = {p["probe"]: p["status"] for p in report["probes"]}
    assert statuses["glue:enum-projection"] == "PASS"
    assert statuses["athena:queryable"] == "PASS"


@pytest.mark.unit
def test_fail_when_projection_still_injected() -> None:
    boto3_module, _ = _make_boto3(
        [_matching_table("telemetry_prod_events", _INJECTED_PARAMS)],
        ["telemetry-prod-analytics"],
        ["SUCCEEDED"],
    )
    code = e2e_queryability_check.check_queryability(
        env="prod", boto3_module=boto3_module, region="us-east-1", sleep_fn=lambda _s: None
    )
    assert code == 1


@pytest.mark.unit
def test_fail_when_no_filter_query_does_not_succeed() -> None:
    boto3_module, _ = _make_boto3(
        [_matching_table("telemetry_prod_events", _ENUM_PARAMS)],
        ["telemetry-prod-analytics"],
        ["RUNNING", "FAILED"],
    )
    code = e2e_queryability_check.check_queryability(
        env="prod", boto3_module=boto3_module, region="us-east-1", sleep_fn=lambda _s: None
    )
    assert code == 1


@pytest.mark.unit
def test_fail_and_short_circuit_when_no_table_matches() -> None:
    boto3_module, athena = _make_boto3([], ["telemetry-prod-analytics"], ["SUCCEEDED"])
    code = e2e_queryability_check.check_queryability(
        env="prod", boto3_module=boto3_module, region="us-east-1", sleep_fn=lambda _s: None
    )
    assert code == 1
    athena.start_query_execution.assert_not_called()


@pytest.mark.unit
def test_fail_when_no_analytics_workgroup() -> None:
    boto3_module, _ = _make_boto3(
        [_matching_table("telemetry_prod_events", _ENUM_PARAMS)],
        ["some-other-workgroup"],
        ["SUCCEEDED"],
    )
    code = e2e_queryability_check.check_queryability(
        env="prod", boto3_module=boto3_module, region="us-east-1", sleep_fn=lambda _s: None
    )
    assert code == 1


@pytest.mark.unit
def test_queryability_query_has_no_tool_filter() -> None:
    query = e2e_queryability_check.build_queryability_query("tbl1")
    assert "where" not in query.lower(), "the guard query must carry NO tool filter"
    assert "tbl1" in query
    assert "count(*)" in query.lower()


@pytest.mark.unit
def test_queryability_query_rejects_unsafe_identifier() -> None:
    with pytest.raises(e2e_common.E2EError, match="unsafe identifier"):
        e2e_queryability_check.build_queryability_query('t"; DROP TABLE x; --')


@pytest.mark.unit
def test_run_query_to_state_returns_terminal_reason() -> None:
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "qid"}
    athena.get_query_execution.side_effect = [
        {"QueryExecution": {"Status": {"State": "RUNNING"}}},
        {"QueryExecution": {"Status": {"State": "SUCCEEDED", "StateChangeReason": "done"}}},
    ]
    state, reason = e2e_queryability_check.run_query_to_state(
        athena,
        "wg",
        "db",
        "SELECT 1",
        poll_timeout=100.0,
        poll_interval=1.0,
        sleep_fn=lambda _s: None,
    )
    assert state == "SUCCEEDED"
    assert reason == "done"


@pytest.mark.unit
def test_run_query_to_state_times_out() -> None:
    athena = MagicMock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "qid"}
    athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "RUNNING"}}}
    ticks = iter([0.0, 1.0, 200.0])
    with pytest.raises(e2e_common.E2EUsageError, match="did not reach a terminal state"):
        e2e_queryability_check.run_query_to_state(
            athena,
            "wg",
            "db",
            "SELECT 1",
            poll_timeout=100.0,
            poll_interval=1.0,
            sleep_fn=lambda _s: None,
            clock=lambda: next(ticks),
        )


@pytest.mark.unit
def test_resolve_region_prefers_explicit_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert e2e_queryability_check.resolve_region("eu-west-1") == "eu-west-1"
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    assert e2e_queryability_check.resolve_region(None) == "us-east-1"


@pytest.mark.unit
def test_resolve_region_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(e2e_common.E2EUsageError, match="AWS_DEFAULT_REGION is not set"):
        e2e_queryability_check.resolve_region(None)


@pytest.mark.unit
def test_main_usage_error_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    code = e2e_queryability_check.main(["--env", "prod"])
    assert code == 2
