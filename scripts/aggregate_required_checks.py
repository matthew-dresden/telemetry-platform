"""aggregate_required_checks -- required-checks gate aggregator.

Backs the Makefile target:
    make required-checks-aggregate -> uv run python -m scripts.aggregate_required_checks

Reads the toJson(needs) map produced by GitHub Actions (docs/release-pipeline.md, D47)
and computes the gate result:

- result in (success, skipped) -> PASS
- result in (failure, cancelled) -> FAIL, ::error:: names every failing job

A scope-skipped job (e.g. the module gauntlet on a config/terragrunt PR) has
result='skipped' and must NOT break the gate. A real failure or cancellation
always makes the gate fail-closed.

Architecture:
- aggregate_results(): pure library function; raises AggregationError on failure.
- main(): CLI entry point; calls sys.exit() only here.
- AggregationError: specific exception carrying the failed job names.

Usage (from the required-checks job step in pr-validation.yml):
    make required-checks-aggregate RESULTS='${{ toJson(needs) }}'
"""

from __future__ import annotations

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Results that count as a passing gate outcome.
_PASSING_RESULTS: frozenset[str] = frozenset({"success", "skipped"})

# Results that count as a failing gate outcome.
_FAILING_RESULTS: frozenset[str] = frozenset({"failure", "cancelled"})

# GitHub Actions annotation prefix for error reporting.
_GH_ERROR_PREFIX: str = "::error::"


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class AggregationError(Exception):
    """Raised when one or more required-checks jobs failed or were cancelled.

    Attributes:
        failed_jobs: List of job names whose result was failure or cancelled.
        message: The formatted ::error:: message naming the failing jobs.
    """

    def __init__(self, failed_jobs: list[str], unrecognized_jobs: list[str]) -> None:
        self.failed_jobs = failed_jobs
        self.unrecognized_jobs = unrecognized_jobs

        parts: list[str] = []
        if failed_jobs:
            job_list = ", ".join(sorted(failed_jobs))
            parts.append(f"Required checks failed for job(s): {job_list}")
        if unrecognized_jobs:
            job_list = ", ".join(sorted(unrecognized_jobs))
            parts.append(
                f"Unrecognized result value for job(s): {job_list}. "
                "Expected one of: success, skipped, failure, cancelled."
            )

        detail = " | ".join(parts)
        super().__init__(
            f"{_GH_ERROR_PREFIX}{detail}. "
            "Remediation: inspect the failing job logs and fix the underlying issue."
        )


# ---------------------------------------------------------------------------
# Core aggregation logic
# ---------------------------------------------------------------------------


def aggregate_results(needs_map: dict[str, dict[str, str]]) -> None:
    """Aggregate the toJson(needs) map and fail-close the gate on any failure.

    Treats result in (success, skipped) as PASS and result in (failure, cancelled)
    as FAIL. Any unrecognized result value is also treated as FAIL (fail-fast).

    Args:
        needs_map: The deserialized toJson(needs) object from GitHub Actions.
            Shape: {"job-name": {"result": "<status>", ...}, ...}

    Raises:
        AggregationError: If any job's result is failure, cancelled, or unrecognized.
    """
    failed_jobs: list[str] = []
    unrecognized_jobs: list[str] = []

    for job_name, job_data in needs_map.items():
        result = job_data.get("result", "")
        if result in _PASSING_RESULTS:
            continue
        if result in _FAILING_RESULTS:
            failed_jobs.append(job_name)
        else:
            # Unknown result value -- fail-fast (never treat unknown as pass).
            unrecognized_jobs.append(job_name)

    if failed_jobs or unrecognized_jobs:
        raise AggregationError(failed_jobs=failed_jobs, unrecognized_jobs=unrecognized_jobs)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse the --results JSON argument and run the aggregation gate.

    The --results value is the output of toJson(needs) in the GitHub Actions
    expression language, passed verbatim from the workflow step.

    Exits:
        0 if all jobs in the needs map passed or were skipped.
        1 if any job failed, was cancelled, or has an unrecognized result.
        2 if the --results value is not valid JSON.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate the required-checks gate from the toJson(needs) map. "
            "Exits non-zero if any job failed or was cancelled (docs/release-pipeline.md, D47)."
        )
    )
    parser.add_argument(
        "--results",
        required=True,
        help=(
            "JSON string produced by toJson(needs) in the GitHub Actions expression "
            'language. Shape: {"job-name": {"result": "<status>"}, ...}'
        ),
    )
    args = parser.parse_args()

    try:
        needs_map: dict[str, dict[str, str]] = json.loads(args.results)
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: --results value is not valid JSON: {exc}. "
            "Pass the raw toJson(needs) output from the GitHub Actions expression.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        aggregate_results(needs_map)
    except AggregationError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
