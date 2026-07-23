"""Render perf-test headline numbers as a Markdown block appended to a GitHub Step Summary.

Invoked as::

    uv run python -m scripts.perf_step_summary \\
        --results e2e-evidence/perf-results-sandbox.json --output "$GITHUB_STEP_SUMMARY"

Reads the merged ``scripts.perf_loadgen``/``scripts.perf_merge_results`` results JSON and appends a
compact Markdown summary (shards, sessions, request/record/error counts + error rate, latency
percentiles, and the run window) to the ``--output`` file. Kept out of the workflow YAML so the
formatting is a single tested unit rather than a brittle inline one-liner.

Fail-fast (CLAUDE.md): a missing/unparseable results file aborts non-zero.

Exit codes::

    0 -- summary appended
    2 -- usage error (results file missing / not JSON)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


def render_summary(results: dict[str, Any]) -> str:
    """Return the Markdown headline block for a (merged) perf results manifest."""
    totals = results.get("totals", {})
    latency = results.get("latency_ms", {})
    error_rate = float(totals.get("error_rate", 0.0))
    lines = [
        "## Perf test results",
        "",
        f"- Shards: {results.get('shard_count', 1)}  |  Sessions: {results.get('sessions')}",
        (
            f"- Requests: {totals.get('total_requests')}  |  "
            f"Records: {totals.get('total_records')}  |  "
            f"Errors: {totals.get('total_errors')} (rate {error_rate:.4f})"
        ),
        (
            "- Latency p50/p90/p99: "
            f"{float(latency.get('p50', 0)):.1f} / "
            f"{float(latency.get('p90', 0)):.1f} / "
            f"{float(latency.get('p99', 0)):.1f} ms"
        ),
        f"- Window: {results.get('started_at')} -> {results.get('finished_at')}",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="scripts.perf_step_summary",
        description="Append perf-test headline numbers to a GitHub Actions Step Summary.",
    )
    parser.add_argument("--results", required=True, help="Path to the merged perf results JSON.")
    parser.add_argument(
        "--output",
        required=True,
        help="Path to append the Markdown summary to (e.g. $GITHUB_STEP_SUMMARY).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    results_path = pathlib.Path(args.results)
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read perf results {results_path}: {exc}", file=sys.stderr)
        return 2

    summary = render_summary(results)
    with open(args.output, "a", encoding="utf-8") as fh:
        fh.write(summary)
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
