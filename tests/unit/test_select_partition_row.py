"""Unit tests for scripts/select_partition_row.py."""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.select_partition_row import (
    SelectRowError,
    extract_matrix,
    main,
    run,
    select_row,
)

_ROWS = [
    {
        "account_id": "222222222222",
        "role_arn": "arn:aws:iam::222222222222:role/telemetry-platform-gha-tg-apply",
        "needs_dns_writer": "false",
        "aws_region": "us-east-1",
        "include_dir_flags": "--queue-include-dir a --queue-include-dir b",
    },
    {
        "account_id": "444444444444",
        "role_arn": "arn:aws:iam::444444444444:role/telemetry-platform-dns-writer",
        "needs_dns_writer": "true",
        "aws_region": "us-east-1",
        "include_dir_flags": "--queue-include-dir c",
    },
]


def _matrix_blob(rows: list[dict]) -> str:
    return (
        "partition-units-by-account (apply): 2 jobs\n" + "matrix=" + json.dumps({"include": rows})
    )


@pytest.mark.unit
def test_extract_matrix_reads_the_matrix_line() -> None:
    rows = extract_matrix(_matrix_blob(_ROWS))
    assert [r["account_id"] for r in rows] == ["222222222222", "444444444444"]


@pytest.mark.unit
def test_extract_matrix_missing_line_fails_fast() -> None:
    with pytest.raises(SelectRowError, match="no 'matrix=' line"):
        extract_matrix("some other output\nno matrix here")


@pytest.mark.unit
def test_extract_matrix_bad_json_fails_fast() -> None:
    with pytest.raises(SelectRowError, match="cannot parse"):
        extract_matrix("matrix={not json")


@pytest.mark.unit
def test_select_row_picks_the_service_account() -> None:
    row = select_row(_ROWS, "222222222222")
    assert row["role_arn"].endswith("telemetry-platform-gha-tg-apply")
    # the dns-owner row is NOT selected (excluded from the sandbox service scope).
    assert row["account_id"] == "222222222222"


@pytest.mark.unit
def test_select_row_absent_account_fails_fast() -> None:
    with pytest.raises(SelectRowError, match="no partition row for account"):
        select_row(_ROWS, "999999999999")


@pytest.mark.unit
def test_run_writes_scalar_outputs(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "part.txt"
    matrix_file.write_text(_matrix_blob(_ROWS), encoding="utf-8")
    out = tmp_path / "gh_output"
    run(matrix_file, "222222222222", str(out))

    written = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").strip().splitlines()
    )
    assert written["role_arn"] == "arn:aws:iam::222222222222:role/telemetry-platform-gha-tg-apply"
    assert written["aws_region"] == "us-east-1"
    assert written["include_dir_flags"] == "--queue-include-dir a --queue-include-dir b"
    assert written["unit_count"] == "2"


@pytest.mark.unit
def test_main_absent_account_returns_1(tmp_path: pathlib.Path) -> None:
    matrix_file = tmp_path / "part.txt"
    matrix_file.write_text(_matrix_blob(_ROWS), encoding="utf-8")
    out = tmp_path / "gh_output"
    rc = main(
        ["--matrix-file", str(matrix_file), "--account-id", "111111111111", "--output", str(out)]
    )
    assert rc == 1
