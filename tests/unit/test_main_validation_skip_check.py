"""Static contract tests for the skip-check job in main-validation.yml.

Asserts the structural constraints for:
  - AC-FIX-1: the Check release commit skip step routes
    github.event.head_commit.message through an env: block (COMMIT_MESSAGE)
    and the run: script references the shell variable ${COMMIT_MESSAGE} instead
    of interpolating the expression inline -- mirroring the safe pattern used by
    the calc step and changelog step in the same file.
  - AC-FIX-3: the run: block does NOT contain the literal substring
    'github.event.head_commit.message' (which would be an untrusted-input
    injection, flagged by actionlint [expression]).
  - AC-FIX-4: the job-level if: condition (expression context, not a shell
    run: script) is left unchanged -- actionlint only flags untrusted input
    in run: scripts, not expression contexts.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "main-validation.yml"

# The exact job key and step name we are asserting against.
_SKIP_CHECK_JOB = "skip-check"
_STEP_NAME = "Check release commit skip"

# The untrusted expression that must NOT appear in run: scripts.
_UNSAFE_EXPRESSION = "github.event.head_commit.message"

# The env key we expect to carry the expression safely.
_ENV_KEY = "COMMIT_MESSAGE"

# The shell variable reference that must appear in the run: block.
_SHELL_VAR = "${COMMIT_MESSAGE}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_workflow() -> dict:
    """Parse main-validation.yml, asserting the file exists."""
    assert WORKFLOW_PATH.exists(), (
        f"Workflow file not found: {WORKFLOW_PATH}. "
        "It must exist per the Changes Manifest (E10-F2-S4-T2)."
    )
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _find_step(workflow: dict, job_key: str, step_name: str) -> dict:
    """Locate a specific named step within a job, failing fast if absent."""
    jobs = workflow.get("jobs", {})
    assert job_key in jobs, (
        f"Job '{job_key}' not found in {WORKFLOW_PATH.name}. Available jobs: {list(jobs.keys())}"
    )
    steps = jobs[job_key].get("steps", [])
    matching = [s for s in steps if s.get("name") == step_name]
    assert len(matching) == 1, (
        f"Expected exactly one step named '{step_name}' in job '{job_key}', "
        f"found {len(matching)}. Available step names: "
        f"{[s.get('name') for s in steps]}"
    )
    return matching[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_skip_check_step_has_env_block_with_commit_message() -> None:
    """The Check release commit skip step must declare an env: block that
    maps COMMIT_MESSAGE to the github.event.head_commit.message expression.

    This is the safe pattern (mirroring the calc step ~line 130 and the
    changelog step ~line 231) that prevents actionlint from flagging the
    expression as untrusted shell interpolation.
    """
    workflow = _load_workflow()
    step = _find_step(workflow, _SKIP_CHECK_JOB, _STEP_NAME)

    env = step.get("env")
    assert env is not None, (
        f"Step '{_STEP_NAME}' in job '{_SKIP_CHECK_JOB}' has no 'env:' block. "
        "Add 'COMMIT_MESSAGE: ${{{{ github.event.head_commit.message }}}}' "
        "to route the untrusted input through the environment (AC-FIX-1)."
    )
    assert isinstance(env, dict), (
        f"Step '{_STEP_NAME}' env: block must be a mapping, got {type(env).__name__}."
    )
    assert _ENV_KEY in env, (
        f"Step '{_STEP_NAME}' env: block must contain key '{_ENV_KEY}'. "
        f"Found keys: {list(env.keys())}. "
        "Add 'COMMIT_MESSAGE: ${{{{ github.event.head_commit.message }}}}' (AC-FIX-1)."
    )
    env_value = str(env[_ENV_KEY])
    assert _UNSAFE_EXPRESSION in env_value, (
        f"'{_ENV_KEY}' in step env: must reference the expression "
        f"'{_UNSAFE_EXPRESSION}', but got: {env_value!r} (AC-FIX-1)."
    )


@pytest.mark.unit
def test_skip_check_run_does_not_inline_commit_message_expression() -> None:
    """The Check release commit skip step's run: script must NOT contain the
    literal string 'github.event.head_commit.message'.

    Direct interpolation of this value into a shell script is a command-
    injection vector (CWE-78) flagged by actionlint as [expression].
    The safe pattern passes the value through an env: block and references
    the shell variable ${COMMIT_MESSAGE} instead (AC-FIX-3).
    """
    workflow = _load_workflow()
    step = _find_step(workflow, _SKIP_CHECK_JOB, _STEP_NAME)

    run_script = step.get("run", "")
    assert isinstance(run_script, str), (
        f"Step '{_STEP_NAME}' run: must be a string, got {type(run_script).__name__}."
    )
    assert _UNSAFE_EXPRESSION not in run_script, (
        f"Step '{_STEP_NAME}' run: script contains the untrusted expression "
        f"'{_UNSAFE_EXPRESSION}' inline. Remove it and reference the shell "
        f"variable '{_SHELL_VAR}' instead (AC-FIX-1, AC-FIX-3)."
    )


@pytest.mark.unit
def test_skip_check_run_references_commit_message_shell_var() -> None:
    """The Check release commit skip step's run: script must reference
    ${COMMIT_MESSAGE} -- the shell variable bound by the env: block.

    This confirms the fix is complete end-to-end: the value is declared in
    env: and consumed via the shell variable in run: (AC-FIX-3).
    """
    workflow = _load_workflow()
    step = _find_step(workflow, _SKIP_CHECK_JOB, _STEP_NAME)

    run_script = step.get("run", "")
    assert _SHELL_VAR in run_script, (
        f"Step '{_STEP_NAME}' run: script must reference '{_SHELL_VAR}' "
        f"(the env-block shell variable), but it does not. "
        f"Ensure COMMIT_MESSAGE is declared in env: and referenced as "
        f"'{_SHELL_VAR}' in the run: script (AC-FIX-3)."
    )


@pytest.mark.unit
def test_skip_check_job_if_condition_unchanged() -> None:
    """The job-level if: condition on skip-check is in expression context and
    legitimately references github.event.head_commit.message -- actionlint
    does NOT flag expression-context usage, only run: script interpolation.

    This test asserts the if: condition is still present and unchanged
    (AC-FIX-4): we must NOT remove or alter it as part of this fix.
    """
    workflow = _load_workflow()
    jobs = workflow.get("jobs", {})
    assert _SKIP_CHECK_JOB in jobs, f"Job '{_SKIP_CHECK_JOB}' not found in {WORKFLOW_PATH.name}."
    job = jobs[_SKIP_CHECK_JOB]
    job_if = job.get("if", "")
    assert job_if is not None and str(job_if).strip() != "", (
        f"Job '{_SKIP_CHECK_JOB}' has no if: condition. "
        "The job-level if: referencing head_commit.message must be preserved (AC-FIX-4)."
    )
    assert _UNSAFE_EXPRESSION in str(job_if), (
        f"Job '{_SKIP_CHECK_JOB}' if: condition does not reference "
        f"'{_UNSAFE_EXPRESSION}'. The original expression-context usage must be "
        "preserved unchanged (AC-FIX-4)."
    )
