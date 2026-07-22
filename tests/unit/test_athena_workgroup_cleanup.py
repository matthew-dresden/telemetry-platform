"""Unit tests for scripts/athena_workgroup_cleanup.py.

No real AWS: an in-memory fake Athena client models list_work_groups (with
pagination) and delete_work_group (recording the RecursiveDeleteOption and
raising a botocore-style not-found error on demand). The tests assert:
  * discovery is namespace-derived -- selects every workgroup carrying both the
    analytics segment and the env segment, follows pagination, ignores primary;
  * delete_work_group is always called with RecursiveDeleteOption=True;
  * an already-absent workgroup is tolerated as a clean no-op (idempotent);
  * a non-absence AWS error surfaces fail-fast;
  * cleanup returns an accurate deleted/already_absent evidence split;
  * _resolve_region reads --aws-region then $AWS_DEFAULT_REGION (fail-fast);
  * main exits 0 on success, 1 on an AWS error, 2 on a usage error, and writes
    evidence to --output.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from scripts import athena_workgroup_cleanup as awc


class _ClientError(Exception):
    """Stand-in for botocore.exceptions.ClientError (only .response is inspected)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.response = {"Error": {"Code": code, "Message": message}}


class _FakeAthena:
    """In-memory Athena double: paginated list_work_groups + delete_work_group."""

    def __init__(self, names: list[str], page_size: int = 100) -> None:
        self._names = list(names)
        self._page_size = page_size
        self.deleted: list[tuple[str, bool]] = []
        self.absent: set[str] = set()  # names that raise not-found on delete
        self.raise_on_delete: dict[str, Exception] = {}

    def list_work_groups(self, **kwargs: Any) -> dict[str, Any]:
        start = 0
        token = kwargs.get("NextToken")
        if token is not None:
            start = int(token)
        page = self._names[start : start + self._page_size]
        next_start = start + self._page_size
        response: dict[str, Any] = {"WorkGroups": [{"Name": n} for n in page]}
        if next_start < len(self._names):
            response["NextToken"] = str(next_start)
        return response

    def delete_work_group(self, WorkGroup: str, RecursiveDeleteOption: bool) -> dict[str, Any]:  # noqa: N803
        if WorkGroup in self.raise_on_delete:
            raise self.raise_on_delete[WorkGroup]
        if WorkGroup in self.absent:
            raise _ClientError("InvalidRequestException", f"WorkGroup {WorkGroup} is not found")
        self.deleted.append((WorkGroup, RecursiveDeleteOption))
        return {}


# ---------------------------------------------------------------------------
# discover_analytics_workgroups
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discovery_selects_env_and_analytics_matches() -> None:
    athena = _FakeAthena(
        [
            "primary",
            "prod-analytics",
            "telemetry-useast1-sandbox-shared-analytics-000-analytics",
            "telemetry-useast1-sandbox-shared-data-lake-000",
        ]
    )
    found = awc.discover_analytics_workgroups(athena, "sandbox")
    assert found == ["telemetry-useast1-sandbox-shared-analytics-000-analytics"]


@pytest.mark.unit
def test_discovery_follows_pagination_and_dedupes() -> None:
    athena = _FakeAthena(
        [
            "sandbox-analytics-000",
            "primary",
            "sandbox-analytics-001",
            "prod-analytics",
        ],
        page_size=1,
    )
    found = awc.discover_analytics_workgroups(athena, "sandbox")
    assert found == ["sandbox-analytics-000", "sandbox-analytics-001"]


@pytest.mark.unit
def test_discovery_empty_when_no_match() -> None:
    athena = _FakeAthena(["primary", "prod-analytics"])
    assert awc.discover_analytics_workgroups(athena, "sandbox") == []


# ---------------------------------------------------------------------------
# delete_workgroup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_delete_uses_recursive_option() -> None:
    athena = _FakeAthena(["sandbox-analytics"])
    assert awc.delete_workgroup(athena, "sandbox-analytics") is True
    assert athena.deleted == [("sandbox-analytics", True)]


@pytest.mark.unit
def test_delete_tolerates_already_gone() -> None:
    athena = _FakeAthena(["sandbox-analytics"])
    athena.absent.add("sandbox-analytics")
    assert awc.delete_workgroup(athena, "sandbox-analytics") is False
    assert athena.deleted == []


@pytest.mark.unit
def test_delete_tolerates_resource_not_found_code() -> None:
    athena = _FakeAthena(["sandbox-analytics"])
    athena.raise_on_delete["sandbox-analytics"] = _ClientError("ResourceNotFoundException")
    assert awc.delete_workgroup(athena, "sandbox-analytics") is False


@pytest.mark.unit
def test_delete_reraises_non_absence_error() -> None:
    athena = _FakeAthena(["sandbox-analytics"])
    athena.raise_on_delete["sandbox-analytics"] = _ClientError("AccessDeniedException", "denied")
    with pytest.raises(_ClientError):
        awc.delete_workgroup(athena, "sandbox-analytics")


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cleanup_splits_deleted_and_absent() -> None:
    athena = _FakeAthena(
        ["sandbox-analytics-000", "sandbox-analytics-001", "prod-analytics", "primary"]
    )
    athena.absent.add("sandbox-analytics-001")
    evidence = awc.cleanup(athena, "sandbox")
    assert evidence["discovered"] == ["sandbox-analytics-000", "sandbox-analytics-001"]
    assert evidence["deleted"] == ["sandbox-analytics-000"]
    assert evidence["already_absent"] == ["sandbox-analytics-001"]
    assert evidence["env"] == "sandbox"


# ---------------------------------------------------------------------------
# _resolve_region
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_region_prefers_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    args = awc.build_parser().parse_args(["--env", "sandbox", "--aws-region", "us-west-2"])
    assert awc._resolve_region(args) == "us-west-2"


@pytest.mark.unit
def test_resolve_region_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    args = awc.build_parser().parse_args(["--env", "sandbox"])
    assert awc._resolve_region(args) == "us-east-1"


@pytest.mark.unit
def test_resolve_region_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    args = awc.build_parser().parse_args(["--env", "sandbox"])
    with pytest.raises(awc.AthenaCleanupUsageError):
        awc._resolve_region(args)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_success_writes_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    athena = _FakeAthena(["telemetry-useast1-sandbox-shared-analytics-000-analytics"])
    monkeypatch.setattr(awc, "_build_athena_client", lambda args, boto3_module, region: athena)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    out = tmp_path / "evidence.json"
    code = awc.main(["--env", "sandbox", "--ambient-credentials", "--output", str(out)])
    assert code == 0
    evidence = json.loads(out.read_text())
    assert evidence["deleted"] == ["telemetry-useast1-sandbox-shared-analytics-000-analytics"]
    assert athena.deleted[0][1] is True  # RecursiveDeleteOption


@pytest.mark.unit
def test_main_aws_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    athena = _FakeAthena(["sandbox-analytics"])
    athena.raise_on_delete["sandbox-analytics"] = _ClientError("AccessDeniedException", "denied")
    monkeypatch.setattr(awc, "_build_athena_client", lambda args, boto3_module, region: athena)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    code = awc.main(["--env", "sandbox", "--ambient-credentials"])
    assert code == 1
    assert "athena workgroup cleanup failed" in capsys.readouterr().err


@pytest.mark.unit
def test_main_usage_error_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    code = awc.main(["--env", "sandbox", "--ambient-credentials"])
    assert code == 2
    assert "AWS_DEFAULT_REGION" in capsys.readouterr().err
