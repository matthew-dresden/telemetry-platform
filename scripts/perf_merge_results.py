"""Merge N per-shard ``scripts.perf_loadgen`` result JSONs into one combined results file.

Invoked as::

    uv run python -m scripts.perf_merge_results \\
        --results perf-results-0.json --results perf-results-1.json \\
        --output perf-results-sandbox.json

The perf-test workflow fans the load out across a matrix of ``shards`` (distinct source runners),
each writing its own client-side results JSON. ``scripts.perf_report`` consumes exactly ONE results
file, so this merger folds every shard's client-side view into a single manifest whose fields
``perf_report`` already understands (config echo + totals + latency + bucket time series + the run
window ``started_at``/``finished_at`` it derives the CloudWatch window from).

Merge semantics:
  * EXACT (additive) -- request/record/error counts, per-status counts, per-kind counts, per-bucket
    counts, and the aggregate request/record throughput (per-shard rates over the SAME wall-clock
    bucket sum to the aggregate rate).
  * APPROXIMATE (documented) -- latency percentiles (p50/p90/p99). The per-shard results carry only
    reservoir-derived percentiles, not raw samples, so an exact global percentile cannot be
    reconstructed. The merge reports a request-weighted mean of the shard percentiles and preserves
    every shard's own percentiles under ``per_shard`` so the approximation is transparent. min/max
    are exact; mean is sample-count-weighted.
  * WINDOW -- ``started_at`` = earliest shard start, ``finished_at`` = latest shard finish, so the
    downstream CloudWatch pull spans the whole fan-out.

Fail-fast (CLAUDE.md): at least one input is required; an unreadable/!JSON input aborts non-zero.

Exit codes::

    0 -- merged results written
    2 -- usage error (no inputs / bad file)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

_PERCENTILE_KEYS = ("p50", "p90", "p99")


def _weighted_mean(pairs: list[tuple[float, float]]) -> float:
    """Return the weight-weighted mean of ``(value, weight)`` pairs (0.0 when total weight is 0)."""
    total_weight = sum(w for _v, w in pairs)
    if total_weight <= 0:
        return 0.0
    return sum(v * w for v, w in pairs) / total_weight


def _merge_counts(dicts: list[dict[str, Any]]) -> dict[str, int]:
    """Sum a list of ``{key: int}`` count maps into one (keys unioned)."""
    out: dict[str, int] = {}
    for d in dicts:
        for key, value in d.items():
            out[key] = out.get(key, 0) + int(value)
    return dict(sorted(out.items()))


def _merge_latency(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge the top-level ``latency_ms`` blocks (see module docstring for exact/approx split)."""
    latencies = [r.get("latency_ms", {}) for r in results]
    request_weights = [float(r.get("totals", {}).get("total_requests", 0)) for r in results]
    merged: dict[str, Any] = {}
    for key in _PERCENTILE_KEYS:
        merged[key] = _weighted_mean(
            [
                (float(lat.get(key, 0.0)), w)
                for lat, w in zip(latencies, request_weights, strict=True)
            ]
        )
    mins = [float(lat["min"]) for lat in latencies if lat.get("min")]
    maxes = [float(lat["max"]) for lat in latencies if lat.get("max")]
    merged["min"] = min(mins) if mins else 0.0
    merged["max"] = max(maxes) if maxes else 0.0
    merged["mean"] = _weighted_mean(
        [(float(lat.get("mean", 0.0)), float(lat.get("sample_count", 0))) for lat in latencies]
    )
    merged["sample_count"] = sum(int(lat.get("sample_count", 0)) for lat in latencies)
    merged["samples_seen"] = sum(int(lat.get("samples_seen", 0)) for lat in latencies)
    merged["per_shard"] = [
        {key: lat.get(key) for key in (*_PERCENTILE_KEYS, "min", "max", "mean")}
        for lat in latencies
    ]
    return merged


def _merge_buckets(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge per-shard bucket time series by ``bucket_start_seconds`` (aligned wall-clock)."""
    by_start: dict[float, list[dict[str, Any]]] = {}
    for r in results:
        for bucket in r.get("buckets", []):
            by_start.setdefault(float(bucket["bucket_start_seconds"]), []).append(bucket)

    merged: list[dict[str, Any]] = []
    for start in sorted(by_start):
        group = by_start[start]
        requests = sum(int(b["requests"]) for b in group)
        records = sum(int(b["records"]) for b in group)
        errors = sum(int(b["errors"]) for b in group)
        weights = [float(b["requests"]) for b in group]
        merged.append(
            {
                "bucket_start_seconds": start,
                "requests": requests,
                "records": records,
                "errors": errors,
                # Aggregate rate = sum of per-shard rates over the same wall-clock bucket.
                "requests_per_second": sum(float(b["requests_per_second"]) for b in group),
                "records_per_second": sum(float(b["records_per_second"]) for b in group),
                "error_rate": (errors / requests) if requests else 0.0,
                "avg_latency_ms": _weighted_mean(
                    [(float(b["avg_latency_ms"]), w) for b, w in zip(group, weights, strict=True)]
                ),
                "latency_ms": {
                    key: _weighted_mean(
                        [
                            (float(b["latency_ms"][key]), w)
                            for b, w in zip(group, weights, strict=True)
                        ]
                    )
                    for key in _PERCENTILE_KEYS
                },
                "by_kind": _merge_counts([b.get("by_kind", {}) for b in group]),
            }
        )
    return merged


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a non-empty list of perf_loadgen result manifests into one combined manifest."""
    if not results:
        raise ValueError("ERROR: merge_results requires at least one shard result.")

    base: dict[str, Any] = dict(results[0])
    totals_list = [r.get("totals", {}) for r in results]
    total_requests = sum(int(t.get("total_requests", 0)) for t in totals_list)
    total_errors = sum(int(t.get("total_errors", 0)) for t in totals_list)

    base["shard_count"] = len(results)
    base["shard_run_ids"] = [r.get("run_id") for r in results]
    base["sessions"] = sum(int(r.get("sessions", 0)) for r in results)
    starts = [str(r["started_at"]) for r in results if r.get("started_at")]
    finishes = [str(r["finished_at"]) for r in results if r.get("finished_at")]
    base["started_at"] = min(starts) if starts else base.get("started_at")
    base["finished_at"] = max(finishes) if finishes else base.get("finished_at")

    base["totals"] = {
        "total_requests": total_requests,
        "total_records": sum(int(t.get("total_records", 0)) for t in totals_list),
        "total_errors": total_errors,
        "error_rate": (total_errors / total_requests) if total_requests else 0.0,
        "status_counts": _merge_counts([t.get("status_counts", {}) for t in totals_list]),
        "by_kind": _merge_counts([t.get("by_kind", {}) for t in totals_list]),
    }
    base["latency_ms"] = _merge_latency(results)
    base["buckets"] = _merge_buckets(results)
    return base


def _load(path: pathlib.Path) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"ERROR: cannot read perf results {path}: {exc}") from exc
    return data


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="scripts.perf_merge_results",
        description="Merge N per-shard perf_loadgen result JSONs into one combined results file.",
    )
    parser.add_argument(
        "--results",
        dest="results",
        action="append",
        required=True,
        help="Path to a shard's perf_loadgen results JSON (repeatable, >=1).",
    )
    parser.add_argument("--output", required=True, help="Path to write the merged results JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    try:
        results = [_load(pathlib.Path(p)) for p in args.results]
        merged = merge_results(results)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2))
    print(
        f"perf_merge_results: merged {len(results)} shard result(s) -> {args.output} "
        f"(total_requests={merged['totals']['total_requests']}, sessions={merged['sessions']})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
