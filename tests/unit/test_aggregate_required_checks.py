"""Unit tests for scripts.aggregate_required_checks -- required-checks gate aggregator.

Parametrized cases per docs/release-pipeline.md test matrix (D47):

- all jobs succeed -> exit 0
- one job skipped with rest success -> exit 0 (skipped is a PASS for scope-skipped jobs)
- one job failed with rest success -> non-zero exit with ::error:: naming the failed job
- one job cancelled with rest success -> non-zero exit with ::error:: naming the cancelled job
- mixed success+skipped+failure -> non-zero exit with ::error:: naming only the failed jobs

AC-15: The required-checks gate must be fail-closed:
- result in (success, skipped) -> PASS
- result in (failure, cancelled) -> FAIL, ::error:: names the job
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.aggregate_required_checks import AggregationError, aggregate_results

# ---------------------------------------------------------------------------
# Parametrized cases (docs/release-pipeline.md test matrix, D47)
# ---------------------------------------------------------------------------

_PASS_CASES = [
    pytest.param(
        {"pr-title-check": {"result": "success"}, "scope-detection": {"result": "success"}},
        id="all_success",
    ),
    pytest.param(
        {
            "pr-title-check": {"result": "success"},
            "scope-detection": {"result": "skipped"},
            "python-quality": {"result": "success"},
        },
        id="one_skipped_rest_success",
    ),
    pytest.param(
        {
            "pr-title-check": {"result": "skipped"},
            "scope-detection": {"result": "skipped"},
            "python-quality": {"result": "skipped"},
        },
        id="all_skipped",
    ),
    pytest.param(
        {
            "pr-title-check": {"result": "success"},
            "scope-detection": {"result": "skipped"},
            "python-quality": {"result": "success"},
            "go-rego-quality": {"result": "success"},
            "portal-app-check": {"result": "success"},
            "validations": {"result": "skipped"},
        },
        id="mix_success_and_skipped",
    ),
]

_FAIL_CASES = [
    pytest.param(
        {"pr-title-check": {"result": "failure"}, "scope-detection": {"result": "success"}},
        ["pr-title-check"],
        id="one_failure",
    ),
    pytest.param(
        {"pr-title-check": {"result": "cancelled"}, "scope-detection": {"result": "success"}},
        ["pr-title-check"],
        id="one_cancelled",
    ),
    pytest.param(
        {
            "pr-title-check": {"result": "success"},
            "scope-detection": {"result": "skipped"},
            "python-quality": {"result": "failure"},
            "go-rego-quality": {"result": "success"},
        },
        ["python-quality"],
        id="mixed_success_skipped_failure",
    ),
    pytest.param(
        {
            "pr-title-check": {"result": "failure"},
            "scope-detection": {"result": "cancelled"},
            "python-quality": {"result": "success"},
        },
        ["pr-title-check", "scope-detection"],
        id="multiple_failures_and_cancellations",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("needs_map", _PASS_CASES)
def test_aggregate_results_pass(needs_map: dict[str, dict[str, str]]) -> None:
    """aggregate_results must return without raising when all jobs pass or are skipped.

    AC-15: result in (success, skipped) is treated as PASS.
    """
    # Raises AggregationError on failure; does not raise means PASS.
    aggregate_results(needs_map)


@pytest.mark.unit
@pytest.mark.parametrize("needs_map, expected_failed_jobs", _FAIL_CASES)
def test_aggregate_results_fail(
    needs_map: dict[str, dict[str, str]],
    expected_failed_jobs: list[str],
) -> None:
    """aggregate_results must raise AggregationError naming every failed/cancelled job.

    AC-15: result in (failure, cancelled) must produce a non-zero exit with
    ::error:: naming the offending jobs so a real failure keeps the gate fail-closed.
    """
    with pytest.raises(AggregationError) as exc_info:
        aggregate_results(needs_map)

    error_message = str(exc_info.value)
    for job_name in expected_failed_jobs:
        assert job_name in error_message, (
            f"Expected failed job '{job_name}' to be named in the error message, "
            f"but got: {error_message!r}"
        )
    assert "::error::" in error_message, (
        f"Expected '::error::' prefix in error message, but got: {error_message!r}"
    )


@pytest.mark.unit
def test_aggregate_results_error_message_does_not_include_passing_jobs() -> None:
    """The error message must name only failed/cancelled jobs, not passing ones.

    AC-15: Passing jobs must not appear in the ::error:: message; only failing ones.
    """
    needs_map = {
        "pr-title-check": {"result": "success"},
        "scope-detection": {"result": "skipped"},
        "python-quality": {"result": "failure"},
    }

    with pytest.raises(AggregationError) as exc_info:
        aggregate_results(needs_map)

    error_message = str(exc_info.value)
    # Only the failed job should be named
    assert "python-quality" in error_message
    # Passing/skipped jobs should not be in the fail list
    assert "pr-title-check" not in error_message
    assert "scope-detection" not in error_message


@pytest.mark.unit
def test_aggregate_results_raises_on_unknown_result() -> None:
    """aggregate_results must raise AggregationError on an unrecognized result value.

    Fail-fast: an unknown result is not treated as success or skipped.
    """
    needs_map = {
        "pr-title-check": {"result": "unknown-value"},
    }

    with pytest.raises(AggregationError) as exc_info:
        aggregate_results(needs_map)

    error_message = str(exc_info.value)
    assert "pr-title-check" in error_message


@pytest.mark.unit
def test_aggregate_results_empty_map_passes() -> None:
    """An empty needs map (no jobs) must pass without raising.

    This covers the edge case where no jobs contributed to the gate.
    """
    aggregate_results({})


@pytest.mark.unit
def test_main_exits_zero_on_all_success(capsys) -> None:
    """main() must exit 0 when the JSON results map has all success entries.

    Validates the CLI entry point wiring (make required-checks-aggregate).
    """
    from scripts.aggregate_required_checks import main

    needs_map = {
        "pr-title-check": {"result": "success"},
        "scope-detection": {"result": "success"},
    }
    results_json = json.dumps(needs_map)

    with (
        patch("sys.argv", ["aggregate_required_checks", "--results", results_json]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 0


@pytest.mark.unit
def test_main_exits_nonzero_on_failure(capsys) -> None:
    """main() must exit non-zero when the JSON results map contains a failure.

    Validates the CLI entry point wiring and fail-closed gate behaviour.
    """
    from scripts.aggregate_required_checks import main

    needs_map = {
        "pr-title-check": {"result": "failure"},
        "scope-detection": {"result": "success"},
    }
    results_json = json.dumps(needs_map)

    with (
        patch("sys.argv", ["aggregate_required_checks", "--results", results_json]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "pr-title-check" in captured.err
    assert "::error::" in captured.err


@pytest.mark.unit
def test_main_exits_nonzero_on_invalid_json(capsys) -> None:
    """main() must exit non-zero and print an error when --results is invalid JSON.

    Fail-fast: malformed input must never silently pass the gate.
    """
    from scripts.aggregate_required_checks import main

    with (
        patch("sys.argv", ["aggregate_required_checks", "--results", "not-valid-json"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
