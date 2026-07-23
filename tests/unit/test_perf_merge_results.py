"""Unit tests for scripts/perf_merge_results.py (per-shard perf results merge)."""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts import perf_merge_results as merge


def _shard(
    *,
    run_id: str,
    sessions: int,
    requests: int,
    errors: int,
    p50: float,
    p99: float,
    started: str,
    finished: str,
) -> dict:
    """Build a minimal perf_loadgen-shaped results manifest for one shard."""
    return {
        "run_id": run_id,
        "env": "sandbox",
        "sessions": sessions,
        "started_at": started,
        "finished_at": finished,
        "totals": {
            "total_requests": requests,
            "total_records": requests * 2,
            "total_errors": errors,
            "error_rate": (errors / requests) if requests else 0.0,
            "status_counts": {"200": requests - errors, "0": errors},
            "by_kind": {"metrics": requests // 2, "log_burst": requests - requests // 2},
        },
        "latency_ms": {
            "p50": p50,
            "p90": p50 * 1.5,
            "p99": p99,
            "min": 1.0,
            "max": p99 * 2,
            "mean": p50,
            "sample_count": requests,
            "samples_seen": requests,
        },
        "buckets": [
            {
                "bucket_start_seconds": 0.0,
                "requests": requests,
                "records": requests * 2,
                "errors": errors,
                "requests_per_second": requests / 30.0,
                "records_per_second": (requests * 2) / 30.0,
                "error_rate": (errors / requests) if requests else 0.0,
                "avg_latency_ms": p50,
                "latency_ms": {"p50": p50, "p90": p50 * 1.5, "p99": p99},
                "by_kind": {"metrics": requests},
            }
        ],
    }


@pytest.mark.unit
def test_merge_sums_exact_counters() -> None:
    a = _shard(
        run_id="a",
        sessions=1000,
        requests=600,
        errors=6,
        p50=10,
        p99=40,
        started="2026-07-20T00:00:00Z",
        finished="2026-07-20T00:30:00Z",
    )
    b = _shard(
        run_id="b",
        sessions=1000,
        requests=400,
        errors=4,
        p50=20,
        p99=80,
        started="2026-07-20T00:00:05Z",
        finished="2026-07-20T00:31:00Z",
    )
    merged = merge.merge_results([a, b])

    assert merged["shard_count"] == 2
    assert merged["shard_run_ids"] == ["a", "b"]
    assert merged["sessions"] == 2000
    # exact additive counters
    assert merged["totals"]["total_requests"] == 1000
    assert merged["totals"]["total_errors"] == 10
    assert merged["totals"]["total_records"] == 2000
    assert merged["totals"]["error_rate"] == pytest.approx(10 / 1000)
    assert merged["totals"]["status_counts"] == {"0": 10, "200": 990}
    # widest window across shards
    assert merged["started_at"] == "2026-07-20T00:00:00Z"
    assert merged["finished_at"] == "2026-07-20T00:31:00Z"


@pytest.mark.unit
def test_merge_latency_is_request_weighted_with_per_shard_preserved() -> None:
    a = _shard(
        run_id="a", sessions=1, requests=600, errors=0, p50=10, p99=40, started="t0", finished="t1"
    )
    b = _shard(
        run_id="b", sessions=1, requests=400, errors=0, p50=20, p99=80, started="t0", finished="t1"
    )
    merged = merge.merge_results([a, b])
    # request-weighted p50: (10*600 + 20*400) / 1000 = 14
    assert merged["latency_ms"]["p50"] == pytest.approx(14.0)
    assert merged["latency_ms"]["min"] == 1.0
    assert merged["latency_ms"]["max"] == pytest.approx(160.0)  # max(40*2, 80*2)
    assert merged["latency_ms"]["sample_count"] == 1000
    # transparency: every shard's own percentiles are preserved.
    assert len(merged["latency_ms"]["per_shard"]) == 2
    assert merged["latency_ms"]["per_shard"][0]["p50"] == 10


@pytest.mark.unit
def test_merge_buckets_aggregate_rate_and_counts() -> None:
    a = _shard(
        run_id="a", sessions=1, requests=600, errors=6, p50=10, p99=40, started="t0", finished="t1"
    )
    b = _shard(
        run_id="b", sessions=1, requests=400, errors=0, p50=20, p99=80, started="t0", finished="t1"
    )
    merged = merge.merge_results([a, b])
    assert len(merged["buckets"]) == 1
    bucket = merged["buckets"][0]
    assert bucket["requests"] == 1000
    assert bucket["errors"] == 6
    # aggregate throughput = sum of per-shard rates
    assert bucket["requests_per_second"] == pytest.approx((600 + 400) / 30.0)
    assert bucket["by_kind"] == {"metrics": 1000}


@pytest.mark.unit
def test_merge_single_shard_is_passthrough_counts() -> None:
    a = _shard(
        run_id="solo",
        sessions=2000,
        requests=1000,
        errors=0,
        p50=15,
        p99=60,
        started="t0",
        finished="t1",
    )
    merged = merge.merge_results([a])
    assert merged["shard_count"] == 1
    assert merged["sessions"] == 2000
    assert merged["totals"]["total_requests"] == 1000
    assert merged["latency_ms"]["p50"] == pytest.approx(15.0)


@pytest.mark.unit
def test_merge_results_empty_fails_fast() -> None:
    with pytest.raises(ValueError, match="at least one shard result"):
        merge.merge_results([])


@pytest.mark.unit
def test_main_writes_merged_file(tmp_path: pathlib.Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(
        json.dumps(
            _shard(
                run_id="a",
                sessions=1000,
                requests=600,
                errors=0,
                p50=10,
                p99=40,
                started="t0",
                finished="t1",
            )
        )
    )
    b.write_text(
        json.dumps(
            _shard(
                run_id="b",
                sessions=1000,
                requests=400,
                errors=0,
                p50=20,
                p99=80,
                started="t0",
                finished="t2",
            )
        )
    )
    out = tmp_path / "merged.json"
    rc = merge.main(["--results", str(a), "--results", str(b), "--output", str(out)])
    assert rc == 0
    written = json.loads(out.read_text())
    assert written["sessions"] == 2000
    assert written["totals"]["total_requests"] == 1000


@pytest.mark.unit
def test_main_bad_input_fails_fast(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nope.json"
    out = tmp_path / "merged.json"
    rc = merge.main(["--results", str(missing), "--output", str(out)])
    assert rc == 2
    assert not out.exists()
