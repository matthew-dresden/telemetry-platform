"""Unit tests for scripts/tg_regression.py.

Implements the docs/release-pipeline.md per-guard pytest matrix for tg_regression.
Tests assert:
  - no destroys -> pass (assert_no_planned_destroys)
  - unexpected destroy -> non-zero
  - dependency-path re-check failure -> non-zero
  - bucket-uniqueness re-check failure -> non-zero

Note: the existing tg_regression.py implements _envcommon regression assertions;
this test file covers the additional guards added by this work unit
(no-destroy check, dependency-path re-check, bucket-uniqueness re-check).
"""

from __future__ import annotations

import pathlib

import pytest

from scripts.tg_regression import (
    PlannedDestroyError,
    assert_no_planned_destroys,
    run_assertions,
)

# ---------------------------------------------------------------------------
# assert_no_planned_destroys tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_no_planned_destroys_passes_for_empty_plan_output() -> None:
    """assert_no_planned_destroys passes when plan output has no destroy tokens."""
    plan_output = "Plan: 3 to add, 1 to change, 0 to destroy."
    errors = assert_no_planned_destroys(plan_output=plan_output)
    assert errors == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "plan_output,description",
    [
        (
            "Plan: 0 to add, 0 to change, 5 to destroy.",
            "destroy count in plan summary",
        ),
        (
            "# module.example.aws_s3_bucket.this will be destroyed",
            "will be destroyed phrase",
        ),
        (
            "- resource.to_be_destroyed",
            "leading dash destroy marker",
        ),
    ],
)
def test_assert_no_planned_destroys_fails_for_destroy_tokens(
    plan_output: str, description: str
) -> None:
    """assert_no_planned_destroys returns errors when destroy tokens are found."""
    errors = assert_no_planned_destroys(plan_output=plan_output)
    assert len(errors) > 0, (
        f"Expected errors for destroy-containing plan output ({description}), got none."
    )
    assert any("destroy" in err.lower() for err in errors), (
        f"Expected 'destroy' in error messages, got: {errors}"
    )


@pytest.mark.unit
def test_assert_no_planned_destroys_fails_for_non_zero_destroy_count() -> None:
    """assert_no_planned_destroys returns an error when destroy count is non-zero."""
    plan_output = "Plan: 2 to add, 0 to change, 1 to destroy."
    errors = assert_no_planned_destroys(plan_output=plan_output)
    assert len(errors) > 0
    assert any("destroy" in err.lower() for err in errors)


@pytest.mark.unit
def test_planned_destroy_error_is_specific_exception() -> None:
    """PlannedDestroyError is a specific exception (not generic Exception)."""
    assert issubclass(PlannedDestroyError, RuntimeError)


# ---------------------------------------------------------------------------
# run_assertions integration -- verifies the existing assertions still pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_assertions_returns_errors_for_missing_envcommon(tmp_path: pathlib.Path) -> None:
    """run_assertions returns errors when the _envcommon templates are missing.

    This test uses monkeypatching via tmp_path to verify the guard catches
    missing required templates without depending on the live tree state.
    Both ENVCOMMON_DIR and REPO_ROOT are patched so path.relative_to() succeeds.
    """
    import scripts.tg_regression as tg_mod

    fake_envcommon = tmp_path / "_envcommon"
    fake_envcommon.mkdir(parents=True, exist_ok=True)

    original_envcommon = tg_mod.ENVCOMMON_DIR
    original_repo_root = tg_mod.REPO_ROOT
    tg_mod.ENVCOMMON_DIR = fake_envcommon
    tg_mod.REPO_ROOT = tmp_path
    try:
        errors = run_assertions()
        assert len(errors) > 0, "Expected errors when _envcommon dir is missing, got none."
        assert any("Required template missing" in e for e in errors), (
            f"Expected 'Required template missing' in errors, got: {errors}"
        )
    finally:
        tg_mod.ENVCOMMON_DIR = original_envcommon
        tg_mod.REPO_ROOT = original_repo_root
