"""Unit tests for scripts/e2e_common.py (OTLP e2e harness shared helpers).

No network or AWS calls: every boto3 client is a MagicMock and every file read
targets either the real committed config or a tmp_path fixture.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts import constants, e2e_common

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ACCOUNTS = json.loads((REPO_ROOT / "terragrunt" / "common" / "accounts.json").read_text())
DOMAINS = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())


# ---------------------------------------------------------------------------
# load_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_json_reads_object(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "obj.json"
    path.write_text(json.dumps({"a": 1}))
    assert e2e_common.load_json(path) == {"a": 1}


@pytest.mark.unit
def test_load_json_missing_file_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(e2e_common.E2EUsageError, match="not found"):
        e2e_common.load_json(tmp_path / "absent.json")


@pytest.mark.unit
def test_load_json_non_object_raises(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "arr.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(e2e_common.E2EUsageError, match="expected a JSON object"):
        e2e_common.load_json(path)


# ---------------------------------------------------------------------------
# resolve_account
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("env", "expected_account"),
    [
        ("sandbox", "222222222222"),
        ("prod", "111111111111"),
        ("qa", "333333333333"),
    ],
)
def test_resolve_account_maps_env_to_account(env: str, expected_account: str) -> None:
    account_id, profile = e2e_common.resolve_account(env, ACCOUNTS)
    assert account_id == expected_account
    assert profile == env


@pytest.mark.unit
def test_resolve_account_unknown_env_raises() -> None:
    with pytest.raises(e2e_common.E2EUsageError, match="unknown ENV"):
        e2e_common.resolve_account("staging", ACCOUNTS)


@pytest.mark.unit
def test_resolve_account_env_without_account_raises() -> None:
    # sandbox is a valid env-class but absent from this fabricated accounts map.
    accounts = {"111": {"aws_profile": "prod"}}
    with pytest.raises(e2e_common.E2EUsageError, match="no account"):
        e2e_common.resolve_account("sandbox", accounts)


# ---------------------------------------------------------------------------
# resolve_endpoints
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_endpoints_sandbox_composes_fqdns() -> None:
    endpoints = e2e_common.resolve_endpoints("sandbox", DOMAINS)
    apex = DOMAINS["sandbox"]["dns_pretty_apex"]
    service_apex = DOMAINS["sandbox"]["dns_service_apex"]
    instance = constants.E2E_ENVIRONMENT_INSTANCE
    assert endpoints["collector_pretty_fqdn"] == f"collector.{apex}"
    assert endpoints["collector_service_fqdn"] == f"collector-{instance}.{service_apex}"
    assert endpoints["portal_pretty_fqdn"] == f"telemetry.{apex}"
    assert endpoints["portal_service_fqdn"] == f"telemetry-{instance}.{service_apex}"


@pytest.mark.unit
def test_resolve_endpoints_unknown_env_raises() -> None:
    with pytest.raises(e2e_common.E2EUsageError, match="not found in domains.json"):
        e2e_common.resolve_endpoints("staging", DOMAINS)


# ---------------------------------------------------------------------------
# build_session
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_session_binds_profile() -> None:
    boto3_module = MagicMock()
    e2e_common.build_session(boto3_module, "sandbox")
    boto3_module.Session.assert_called_once_with(profile_name="sandbox")


@pytest.mark.unit
def test_build_ambient_session_binds_no_profile() -> None:
    boto3_module = MagicMock()
    e2e_common.build_ambient_session(boto3_module)
    boto3_module.Session.assert_called_once_with()
    _, kwargs = boto3_module.Session.call_args
    assert "profile_name" not in kwargs, "ambient session must not bind a named profile"


# ---------------------------------------------------------------------------
# poll_until
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_poll_until_returns_true_immediately() -> None:
    calls = [0]

    def predicate() -> bool:
        calls[0] += 1
        return True

    assert e2e_common.poll_until(predicate, timeout=5, interval=0.01, timeout_msg="x") is True
    assert calls[0] == 1


@pytest.mark.unit
def test_poll_until_becomes_true_after_retries() -> None:
    state = {"n": 0}

    def predicate() -> bool:
        state["n"] += 1
        return state["n"] >= 3

    assert e2e_common.poll_until(predicate, timeout=5, interval=0.001, timeout_msg="x") is True
    assert state["n"] == 3


@pytest.mark.unit
def test_poll_until_times_out(capsys: pytest.CaptureFixture[str]) -> None:
    assert e2e_common.poll_until(lambda: False, timeout=0, interval=0.01, timeout_msg="TO") is False
    assert "TO" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_utc_now_iso_shape() -> None:
    value = e2e_common.utc_now_iso()
    assert value.endswith("Z")
    assert len(value) == len("2026-06-28T12:00:00Z")


@pytest.mark.unit
def test_dt_partition() -> None:
    assert e2e_common.dt_partition("2026-06-28T12:34:56Z") == "2026-06-28"


@pytest.mark.unit
def test_tool_marker_returns_fixed_reserved_value() -> None:
    # Every synthetic record now carries the single fixed reserved tool value; the
    # run identity moves into the payload ($.run_id), so run_id no longer changes
    # the tool value. tool_marker returns constants.E2E_TOOL_VALUE regardless of run.
    assert e2e_common.tool_marker("abc123") == "e2e-smoke"
    assert e2e_common.tool_marker("abc123") == constants.E2E_TOOL_VALUE
    # Distinct run ids yield the SAME fixed tool value (not a per-run value).
    assert e2e_common.tool_marker("run-a") == e2e_common.tool_marker("run-b")


# ---------------------------------------------------------------------------
# discover_glue_table (schema-based, namespace-agnostic)
# ---------------------------------------------------------------------------

# Per the data-lake BUG-3 fix, tool is a partition key only -- the data columns
# are timestamp/event_type/payload and the partition keys are tool, dt.
REQUIRED_COLUMNS = ("timestamp", "event_type", "payload")
PARTITION_KEYS = ("tool", "dt")

# A namespace-derived table name -- the deployed name is NEVER hardcoded.
_DEPLOYED_TABLE = "telemetry_useast1_sandbox_shared_data_lake_000_events"


def _telemetry_table(name: str = _DEPLOYED_TABLE) -> dict[str, Any]:
    return {
        "Name": name,
        "StorageDescriptor": {
            "Location": "s3://bucket/raw/",
            # tool is NOT a data column: it is a partition key only (BUG-3 fix).
            "Columns": [
                {"Name": "timestamp", "Type": "string"},
                {"Name": "event_type", "Type": "string"},
                {"Name": "payload", "Type": "string"},
            ],
        },
        "PartitionKeys": [{"Name": "tool", "Type": "string"}, {"Name": "dt", "Type": "string"}],
    }


@pytest.mark.unit
def test_discover_glue_table_matches_by_schema() -> None:
    glue = MagicMock()
    glue.get_databases.return_value = {
        "DatabaseList": [{"Name": "other_db"}, {"Name": "telemetry_db"}]
    }

    def get_tables(**kwargs: str) -> dict[str, Any]:
        if kwargs["DatabaseName"] == "other_db":
            # An unrelated table that does not match the telemetry schema.
            return {
                "TableList": [
                    {
                        "Name": "audit_log",
                        "StorageDescriptor": {"Columns": [{"Name": "id"}]},
                        "PartitionKeys": [],
                    }
                ]
            }
        return {"TableList": [_telemetry_table()]}

    glue.get_tables.side_effect = get_tables
    found = e2e_common.discover_glue_table(glue, REQUIRED_COLUMNS, PARTITION_KEYS)
    assert found == {
        "database": "telemetry_db",
        "table": _DEPLOYED_TABLE,
        "location": "s3://bucket/raw/",
        # The table-level Parameters map (TBLPROPERTIES) is always returned so the
        # caller can detect Athena injected partition projection; the fixture table
        # declares none, so it is the empty map.
        "parameters": {},
    }


@pytest.mark.unit
def test_discover_glue_table_returns_projection_parameters() -> None:
    # The Glue table-level Parameters (TBLPROPERTIES) carry the Athena partition-
    # projection config and MUST be surfaced so the verifier can tell a projection-
    # managed partition (enum-projected tool; no physical catalog partition) apart
    # from a physical one. The tool partition uses enum projection over a governed
    # values list.
    glue = MagicMock()
    glue.get_databases.return_value = {"DatabaseList": [{"Name": "telemetry_db"}]}
    table = _telemetry_table()
    table["Parameters"] = {
        "projection.enabled": "true",
        "projection.tool.type": "enum",
        "projection.tool.values": "e2e-smoke,example-cli",
        "projection.dt.type": "date",
    }
    glue.get_tables.return_value = {"TableList": [table]}
    found = e2e_common.discover_glue_table(glue, REQUIRED_COLUMNS, PARTITION_KEYS)
    assert found is not None
    assert found["parameters"]["projection.tool.type"] == "enum"
    assert found["parameters"]["projection.tool.values"] == "e2e-smoke,example-cli"
    assert found["parameters"]["projection.enabled"] == "true"


@pytest.mark.unit
def test_discover_glue_table_requires_partition_keys() -> None:
    # Columns match but partition keys are missing -> not a match.
    glue = MagicMock()
    glue.get_databases.return_value = {"DatabaseList": [{"Name": "db"}]}
    table = _telemetry_table()
    table["PartitionKeys"] = [{"Name": "tool"}]  # missing 'dt'
    glue.get_tables.return_value = {"TableList": [table]}
    assert e2e_common.discover_glue_table(glue, REQUIRED_COLUMNS, PARTITION_KEYS) is None


@pytest.mark.unit
def test_discover_glue_table_returns_none_when_no_schema_match() -> None:
    glue = MagicMock()
    glue.get_databases.return_value = {"DatabaseList": [{"Name": "db1"}]}
    glue.get_tables.return_value = {"TableList": []}
    assert e2e_common.discover_glue_table(glue, REQUIRED_COLUMNS, PARTITION_KEYS) is None


@pytest.mark.unit
def test_discover_glue_table_follows_pagination() -> None:
    glue = MagicMock()
    glue.get_databases.side_effect = [
        {"DatabaseList": [{"Name": "db1"}], "NextToken": "more"},
        {"DatabaseList": [{"Name": "telemetry_db"}]},
    ]
    glue.get_tables.side_effect = [
        {"TableList": [], "NextToken": "t2"},  # db1, page 1
        {"TableList": []},  # db1, page 2
        {"TableList": [_telemetry_table()]},  # telemetry_db, page 1
    ]
    found = e2e_common.discover_glue_table(glue, REQUIRED_COLUMNS, PARTITION_KEYS)
    assert found is not None and found["database"] == "telemetry_db"


# ---------------------------------------------------------------------------
# discover_workgroup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_workgroup_prefers_env_match() -> None:
    athena = MagicMock()
    athena.list_work_groups.return_value = {
        "WorkGroups": [
            {"Name": "primary"},
            {"Name": "prod-analytics"},
            {"Name": "sandbox-analytics"},
        ]
    }
    assert e2e_common.discover_workgroup(athena, "sandbox") == "sandbox-analytics"


@pytest.mark.unit
def test_discover_workgroup_falls_back_to_any_analytics() -> None:
    athena = MagicMock()
    athena.list_work_groups.return_value = {"WorkGroups": [{"Name": "team-analytics"}]}
    assert e2e_common.discover_workgroup(athena, "sandbox") == "team-analytics"


@pytest.mark.unit
def test_discover_workgroup_returns_none() -> None:
    athena = MagicMock()
    athena.list_work_groups.return_value = {"WorkGroups": [{"Name": "primary"}]}
    assert e2e_common.discover_workgroup(athena, "sandbox") is None


# ---------------------------------------------------------------------------
# reconcile_counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconcile_counts_matched() -> None:
    expected = {"m|a": 2, "m|b": 3}
    found = {"m|a": 2, "m|b": 4}
    report = e2e_common.reconcile_counts(expected, found)
    assert report["matched"] is True
    assert report["total_expected"] == 5
    assert report["total_found"] == 6


@pytest.mark.unit
def test_reconcile_counts_short_found_fails() -> None:
    report = e2e_common.reconcile_counts({"m|a": 5}, {"m|a": 2})
    assert report["matched"] is False
    bad = [r for r in report["per_key"] if r["key"] == "m|a"][0]
    assert bad["ok"] is False
    assert bad["expected"] == 5
    assert bad["found"] == 2


@pytest.mark.unit
def test_reconcile_counts_extra_found_key_ok() -> None:
    report = e2e_common.reconcile_counts({"m|a": 1}, {"m|a": 1, "m|z": 9})
    assert report["matched"] is True
    keys = {r["key"] for r in report["per_key"]}
    assert keys == {"m|a", "m|z"}
