"""Unit tests for scripts/perf_step_summary.py."""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.perf_step_summary import main, render_summary

_RESULTS = {
    "shard_count": 3,
    "sessions": 2000,
    "started_at": "2026-07-20T00:00:00Z",
    "finished_at": "2026-07-20T00:35:00Z",
    "totals": {
        "total_requests": 120000,
        "total_records": 240000,
        "total_errors": 12,
        "error_rate": 0.0001,
    },
    "latency_ms": {"p50": 12.3, "p90": 25.6, "p99": 88.9},
}


@pytest.mark.unit
def test_render_summary_contains_headline_numbers() -> None:
    md = render_summary(_RESULTS)
    assert "## Perf test results" in md
    assert "Shards: 3" in md
    assert "Sessions: 2000" in md
    assert "Requests: 120000" in md
    assert "Errors: 12 (rate 0.0001)" in md
    assert "Latency p50/p90/p99: 12.3 / 25.6 / 88.9 ms" in md
    assert "2026-07-20T00:00:00Z -> 2026-07-20T00:35:00Z" in md


@pytest.mark.unit
def test_render_summary_tolerates_missing_fields() -> None:
    md = render_summary({})
    # defaults render without raising (error_rate 0, latencies 0).
    assert "Shards: 1" in md
    assert "rate 0.0000" in md
    assert "0.0 / 0.0 / 0.0 ms" in md


@pytest.mark.unit
def test_main_appends_to_output(tmp_path: pathlib.Path) -> None:
    results = tmp_path / "r.json"
    results.write_text(json.dumps(_RESULTS), encoding="utf-8")
    out = tmp_path / "summary.md"
    out.write_text("existing\n", encoding="utf-8")
    rc = main(["--results", str(results), "--output", str(out)])
    assert rc == 0
    content = out.read_text(encoding="utf-8")
    assert content.startswith("existing\n")  # appended, not overwritten
    assert "Requests: 120000" in content


@pytest.mark.unit
def test_main_missing_results_fails_fast(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "summary.md"
    rc = main(["--results", str(tmp_path / "nope.json"), "--output", str(out)])
    assert rc == 2
