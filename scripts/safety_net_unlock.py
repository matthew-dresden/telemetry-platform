"""safety_net_unlock -- fail-closed watchdog that releases the branch lock.

Guards the unlock path so the branch is never left unlocked while a release
could still be mutating it (B15, docs/release-pipeline.md).

Logic:
1. Read lock marker from the GitHub Actions variable BRANCH_LOCK_RUN_ID.
   If the branch is not locked (variable absent/empty) -> no-op exit 0.
2. Query the GitHub API for in-progress runs of the release workflow.
   If ANY run is still in progress -> retain the lock (fail-closed, B15).
3. Read the lock age from the lock marker timestamp.
   If the age is below LOCK_MAX_AGE_MINUTES -> retain the lock (not yet stale).
4. Otherwise -> remove the branch protection lock.

Both LOCK_MAX_AGE_MINUTES and WORKFLOW_FILE_NAME must be set explicitly;
neither has a default (fail fast if absent).

Usage (invoked by `make safety-net-unlock`):
    uv run python -m scripts.safety_net_unlock --repo REPO --branch BRANCH

Environment variables:
    REPO:               e.g. 'matthew-dresden/telemetry-platform' (required)
    BRANCH:             e.g. 'main' (required)
    GH_TOKEN:           GitHub App token with workflow + branch-protection read/write
    LOCK_MAX_AGE_MINUTES: integer -- minimum lock age before an unlock is allowed (required)
    WORKFLOW_FILE_NAME: the release workflow filename to query (required, no default)

Exit codes:
    0 -- success (either no-op or unlock performed)
    1 -- error; details on stderr
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum, auto

from scripts.constants import GH_ERROR_PREFIX

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigurationError(RuntimeError):
    """Raised when a required environment variable is absent or empty.

    This exception is raised by library helpers so that the CLI entry point
    (main()) can catch it and call sys.exit(1) in a single place, keeping
    sys.exit out of library code per the error handling contract.
    """


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockInfo:
    """Information about the current branch lock.

    Attributes:
        run_id: The GitHub Actions run ID that acquired the lock.
        age_minutes: How many minutes ago the lock was acquired.
    """

    run_id: str
    age_minutes: float


class UnlockDecision(Enum):
    """The decision made by decide_unlock."""

    NOT_LOCKED = auto()  # Branch is not locked; no-op.
    RETAIN_ACTIVE_RELEASE = auto()  # Active release run present; retain lock (B15).
    RETAIN_AGE_TOO_LOW = auto()  # Lock age below threshold; retain lock.
    DO_UNLOCK = auto()  # Conditions met; proceed with unlock.


# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


def decide_unlock(
    lock_info: LockInfo | None,
    in_progress_count: int,
    lock_max_age_minutes: int,
) -> UnlockDecision:
    """Decide whether to unlock the branch.

    Args:
        lock_info: Lock marker data, or None if the branch is not locked.
        in_progress_count: Number of in-progress release workflow runs.
        lock_max_age_minutes: Minimum lock age (minutes) before an unlock is allowed.

    Returns:
        The appropriate UnlockDecision.
    """
    if lock_info is None:
        return UnlockDecision.NOT_LOCKED

    if in_progress_count > 0:
        return UnlockDecision.RETAIN_ACTIVE_RELEASE

    if lock_info.age_minutes < lock_max_age_minutes:
        return UnlockDecision.RETAIN_AGE_TOO_LOW

    return UnlockDecision.DO_UNLOCK


def check_in_progress_release_runs(repo: str, branch: str) -> int:
    """Query the GitHub API for in-progress release workflow runs.

    Uses 'gh api repos/{repo}/actions/workflows/{workflow_file}/runs
    --jq .total_count' filtered to in_progress status on the given branch.

    Args:
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        branch: The branch name.

    Returns:
        The count of in-progress release runs (>= 0).

    Raises:
        RuntimeError: If the gh API call fails.
    """
    workflow_file = _require_env("WORKFLOW_FILE_NAME")
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/actions/workflows/{workflow_file}/runs",
        "--field",
        "status=in_progress",
        "--field",
        f"branch={branch}",
        "--jq",
        ".total_count",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: gh API call failed querying in-progress release runs "
            f"for repo {repo!r} branch {branch!r}.\n"
            f"stderr: {result.stderr.strip()}\n"
            "Ensure GH_TOKEN is set with 'actions: read' permission."
        )
    count_str = result.stdout.strip()
    try:
        return int(count_str)
    except ValueError as exc:
        raise RuntimeError(
            f"ERROR: gh API returned unexpected count value {count_str!r} "
            f"for repo {repo!r} branch {branch!r}.\n"
            "Expected an integer from --jq '.total_count'."
        ) from exc


def read_lock_info(repo: str, branch: str) -> LockInfo | None:
    """Read the lock marker from the GitHub Actions variable BRANCH_LOCK_RUN_ID.

    The lock marker stores the run-id and timestamp as a JSON string persisted
    in the repository variable BRANCH_LOCK_RUN_ID by lock_branch.py.

    Args:
        repo: The GitHub repository.
        branch: The branch name (used in the variable name).

    Returns:
        LockInfo if locked, None if not locked.

    Raises:
        RuntimeError: If the gh API call fails unexpectedly.
    """
    import json
    import time

    var_name = f"BRANCH_LOCK_RUN_ID_{branch.replace('/', '_').upper()}"
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/actions/variables/{var_name}",
        "--jq",
        ".value",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # 404 means the variable doesn't exist -> not locked
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "404" in stderr or "Not Found" in stderr:
            return None
        raise RuntimeError(
            f"ERROR: Failed to read lock variable {var_name!r} from repo {repo!r}.\n"
            f"stderr: {stderr}\n"
            "Ensure GH_TOKEN is set with 'variables: read' permission."
        )

    value = result.stdout.strip()
    if not value or value == "null":
        return None

    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"ERROR: Lock variable {var_name!r} contains invalid JSON: {value!r}.\n"
            f"Parse error: {exc}\n"
            "The variable must be a JSON object with 'run_id' and 'locked_at_epoch' keys."
        ) from exc

    run_id = data.get("run_id")
    locked_at_epoch = data.get("locked_at_epoch")

    if not run_id or locked_at_epoch is None:
        raise RuntimeError(
            f"ERROR: Lock variable {var_name!r} is missing required keys.\n"
            f"Got: {data!r}\n"
            "Expected keys: 'run_id' (str), 'locked_at_epoch' (float)."
        )

    age_minutes = (time.time() - float(locked_at_epoch)) / 60.0
    return LockInfo(run_id=str(run_id), age_minutes=age_minutes)


def perform_unlock(repo: str, branch: str) -> None:
    """Remove the branch protection lock by deleting the lock variable.

    Args:
        repo: The GitHub repository.
        branch: The branch name.

    Raises:
        RuntimeError: If the gh API call fails.
    """
    var_name = f"BRANCH_LOCK_RUN_ID_{branch.replace('/', '_').upper()}"
    cmd = [
        "gh",
        "api",
        "--method",
        "DELETE",
        f"repos/{repo}/actions/variables/{var_name}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: Failed to unlock branch {branch!r} in repo {repo!r}. "
            f"Could not delete lock variable {var_name!r}.\n"
            f"stderr: {result.stderr.strip()}\n"
            "Ensure GH_TOKEN is set with 'variables: write' permission."
        )


def _require_env(name: str) -> str:
    """Read a required environment variable, failing fast if absent or empty.

    Args:
        name: The environment variable name.

    Returns:
        The variable's value (guaranteed non-empty).

    Raises:
        ConfigurationError: If the variable is absent or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"{GH_ERROR_PREFIX}ERROR: Required environment variable {name!r} is not set.\n"
            "Ensure the workflow passes all required environment variables to this step."
        )
    return value


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for safety_net_unlock.

    Reads REPO, BRANCH, LOCK_MAX_AGE_MINUTES, and WORKFLOW_FILE_NAME from the
    environment, queries the GitHub API, and decides whether to unlock the branch.
    Fails fast if any required variable is unset (no defaults -- B15).

    Exit codes:
        0 -- success (no-op or unlock performed)
        1 -- error
    """
    try:
        repo = _require_env("REPO")
        branch = _require_env("BRANCH")

        # LOCK_MAX_AGE_MINUTES must be explicitly configured -- no default (B15).
        lock_max_age_raw = _require_env("LOCK_MAX_AGE_MINUTES")
        try:
            lock_max_age_minutes = int(lock_max_age_raw)
        except ValueError as exc:
            raise ConfigurationError(
                f"{GH_ERROR_PREFIX}ERROR: LOCK_MAX_AGE_MINUTES={lock_max_age_raw!r} is not "
                "a valid integer.\n"
                "Set LOCK_MAX_AGE_MINUTES to a positive integer (minutes)."
            ) from exc

        lock_info = read_lock_info(repo=repo, branch=branch)
        in_progress_count = (
            check_in_progress_release_runs(repo=repo, branch=branch) if lock_info is not None else 0
        )
        decision = decide_unlock(
            lock_info=lock_info,
            in_progress_count=in_progress_count,
            lock_max_age_minutes=lock_max_age_minutes,
        )

        if decision == UnlockDecision.NOT_LOCKED:
            print(f"Branch {branch!r} is not locked. No-op.")
        elif decision == UnlockDecision.RETAIN_ACTIVE_RELEASE:
            print(
                f"Retaining lock on {branch!r}: {in_progress_count} release run(s) "
                "still in progress (B15 fail-closed)."
            )
        elif decision == UnlockDecision.RETAIN_AGE_TOO_LOW:
            assert lock_info is not None, (
                "RETAIN_AGE_TOO_LOW requires a non-None lock_info; "
                "decide_unlock guarantees this invariant."
            )
            print(
                f"Retaining lock on {branch!r}: lock age "
                f"{lock_info.age_minutes:.1f} min < {lock_max_age_minutes} min threshold."
            )
        elif decision == UnlockDecision.DO_UNLOCK:
            perform_unlock(repo=repo, branch=branch)
            print(f"Branch {branch!r} unlocked successfully.")

    except (ConfigurationError, RuntimeError) as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
