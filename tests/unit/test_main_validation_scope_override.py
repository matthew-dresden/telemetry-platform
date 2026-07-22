"""Static contract tests for scope=all override handling in main-validation.yml.

The PR path honours the detect-scope-override label from the pull_request event payload.
The push (post-merge) path has no such payload, so before this fix it hardcoded
SCOPE_OVERRIDE=false and a squash-merge of a scope-override PR was re-detected as
multi-module/mixed -- turning main red. These tests assert the push path now:

  - resolves the merged PR's override via `make check-merged-pr-override` BEFORE detecting
    scope, and feeds that resolved value (NOT a hardcoded false) into `make scope-detect`;
  - skips the auto-release jobs (version-check + release) when scope == 'all', because a
    multi-module batch has no single module to release (the constituent modules release
    individually via their own single-scope PRs).
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "main-validation.yml"

_SCOPE_JOB = "scope-detection"
_OVERRIDE_STEP_ID = "override"
_DETECT_STEP_ID = "detect"
_RELEASE_SKIP_JOBS = ("version-check", "release")


def _load_workflow() -> dict:
    """Parse main-validation.yml, asserting the file exists."""
    assert WORKFLOW_PATH.exists(), f"Workflow file not found: {WORKFLOW_PATH}."
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job(workflow: dict, job_key: str) -> dict:
    jobs = workflow.get("jobs", {})
    assert job_key in jobs, (
        f"Job '{job_key}' not found in {WORKFLOW_PATH.name}. Available: {list(jobs.keys())}"
    )
    return jobs[job_key]


def _step_by_id(job: dict, step_id: str) -> dict:
    steps = job.get("steps", [])
    matching = [s for s in steps if s.get("id") == step_id]
    assert len(matching) == 1, (
        f"Expected exactly one step with id '{step_id}', found {len(matching)}. "
        f"Step ids: {[s.get('id') for s in steps]}"
    )
    return matching[0]


# ---------------------------------------------------------------------------
# Push-path override resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scope_detection_has_merged_pr_override_step() -> None:
    """scope-detection must resolve the merged-PR override via check-merged-pr-override.

    Without this step the push path cannot know the merged PR carried the override label,
    so it would re-reject the multi-module squash as a scope violation.
    """
    job = _job(_load_workflow(), _SCOPE_JOB)
    step = _step_by_id(job, _OVERRIDE_STEP_ID)
    run = step.get("run", "")
    assert "check-merged-pr-override" in run, (
        "The override-resolution step must invoke 'make check-merged-pr-override' to read "
        f"the merged PR's label + author. run: was: {run!r}"
    )
    # COMMIT_SHA must be sourced from github.sha so the merged PR can be resolved.
    assert "github.sha" in run, (
        "check-merged-pr-override must receive COMMIT_SHA=${{ github.sha }} to look up the PR."
    )


@pytest.mark.unit
def test_detect_scope_consumes_resolved_override_not_hardcoded_false() -> None:
    """The Detect push scope step must feed the RESOLVED override into scope-detect.

    Regression guard for the original bug: SCOPE_OVERRIDE must reference the override step's
    output, never the literal string 'false'.
    """
    job = _job(_load_workflow(), _SCOPE_JOB)
    detect = _step_by_id(job, _DETECT_STEP_ID)
    run = detect.get("run", "")
    assert "scope-detect" in run, f"Detect step must run 'make scope-detect'. run: {run!r}"
    assert f"steps.{_OVERRIDE_STEP_ID}.outputs.scope_override" in run, (
        "SCOPE_OVERRIDE must reference the resolved override step output "
        f"(steps.{_OVERRIDE_STEP_ID}.outputs.scope_override), not a hardcoded value."
    )
    assert 'SCOPE_OVERRIDE="false"' not in run, (
        "SCOPE_OVERRIDE must no longer be hardcoded to false on the push path "
        "(that was the bug -- it rejected override merges on main)."
    )


# ---------------------------------------------------------------------------
# scope=all release-skip
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("job_key", _RELEASE_SKIP_JOBS)
def test_release_jobs_skip_for_scope_all(job_key: str) -> None:
    """version-check + release must be gated off for scope == 'all'.

    A scope=all admin batch has no single module to auto-release; the previous condition only
    skipped 'terragrunt', so a scope=all merge would fall into the per-module release path with
    no module_path and fail. The if: must also exclude 'all'.
    """
    job = _job(_load_workflow(), job_key)
    cond = str(job.get("if", ""))
    assert cond.strip(), f"Job '{job_key}' must retain its if: gate."
    assert "scope != 'all'" in cond, (
        f"Job '{job_key}' if: must skip scope=all (expected \"scope != 'all'\"). Got: {cond!r}"
    )
    # The pre-existing terragrunt skip must be preserved.
    assert "scope != 'terragrunt'" in cond, (
        f"Job '{job_key}' if: must still skip scope=terragrunt. Got: {cond!r}"
    )
