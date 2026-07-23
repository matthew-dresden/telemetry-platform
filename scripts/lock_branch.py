"""lock_branch -- engage/release the branch-protection lock.

Manages the branch protection lock that prevents concurrent releases from
racing each other. The lock is implemented as:
1. A GitHub Actions repository variable (BRANCH_LOCK_RUN_ID_<BRANCH>) that
   records the run-id and locked_at_epoch of the run that owns the lock.
2. A branch protection rule that enforces required status checks (prevents
   direct pushes while the lock is held).

--action lock:
    1. Write the lock marker variable (run_id + locked_at_epoch).
    2. Enable the branch protection lock rule via the GitHub API.

--action unlock:
    1. Check whether the lock marker variable exists.
       - If absent: no-op (D47 idempotent unlock).
    2. Delete the lock marker variable.
    3. Disable the branch protection lock rule.

Raises loudly on a missing protection rule (B30).

Usage (invoked by `make lock-branch`):
    uv run python -m scripts.lock_branch --action lock|unlock --repo REPO --branch BRANCH

Arguments:
    --action:   'lock' or 'unlock'
    --repo:     GitHub repository (owner/name)
    --branch:   Branch to lock/unlock
    --run-id:   GitHub Actions run ID (required for --action lock; from $GITHUB_RUN_ID)

Exit codes:
    0 -- success
    1 -- error; details on stderr
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any

from scripts.constants import GH_ERROR_PREFIX

# ---------------------------------------------------------------------------
# Shared subprocess helper
# ---------------------------------------------------------------------------


def _run(cmd: list[str], input_data: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and return the result.

    Does NOT raise on non-zero exit -- callers inspect returncode.

    Args:
        cmd: Command and arguments.
        input_data: Optional string piped to the process stdin. Required for
            `gh api --input -` calls that send a JSON request body.

    Returns:
        The CompletedProcess result.
    """
    return subprocess.run(cmd, capture_output=True, text=True, input=input_data)


# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


def write_lock_marker(repo: str, branch: str, run_id: str) -> None:
    """Write the lock marker variable to the GitHub repository.

    The marker is a JSON object with 'run_id' and 'locked_at_epoch', stored
    in the repo variable BRANCH_LOCK_RUN_ID_<BRANCH>.

    Args:
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        branch: The branch being locked.
        run_id: The GitHub Actions run ID that owns the lock.

    Raises:
        RuntimeError: If the gh API call fails.
    """
    var_name = f"BRANCH_LOCK_RUN_ID_{branch.replace('/', '_').upper()}"
    marker = json.dumps({"run_id": run_id, "locked_at_epoch": time.time()})

    # Try to update; create if it doesn't exist yet.
    result = _run(
        [
            "gh",
            "api",
            "--method",
            "PATCH",
            f"repos/{repo}/actions/variables/{var_name}",
            "--field",
            f"name={var_name}",
            "--field",
            f"value={marker}",
        ]
    )
    if result.returncode != 0:
        # Variable may not exist yet -- try POST (create).
        result2 = _run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{repo}/actions/variables",
                "--field",
                f"name={var_name}",
                "--field",
                f"value={marker}",
            ]
        )
        if result2.returncode != 0:
            raise RuntimeError(
                f"ERROR: Failed to write lock marker variable {var_name!r} "
                f"for repo {repo!r}.\n"
                f"PATCH stderr: {result.stderr.strip()}\n"
                f"POST stderr: {result2.stderr.strip()}\n"
                "Ensure GH_TOKEN is set with 'variables: write' permission."
            )


def _get_branch_protection(repo: str, branch: str) -> dict[str, Any]:
    """Fetch the current branch protection rule.

    The GitHub 'Update branch protection' PUT API requires the complete
    configuration on every call, so the lock toggle must read the existing rule
    and re-send it. A missing rule is the genuine B30 condition.

    Args:
        repo: The GitHub repository.
        branch: The branch whose protection rule to read.

    Returns:
        The branch protection rule as a dict.

    Raises:
        RuntimeError: B30 if no protection rule exists (404), or on other failures.
    """
    result = _run(["gh", "api", f"repos/{repo}/branches/{branch}/protection"])
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "404" in stderr or "Not Found" in stderr or "Branch not protected" in stderr:
            raise RuntimeError(
                f"ERROR: B30: Branch protection rule not found for branch {branch!r} "
                f"in repo {repo!r}.\n"
                f"stderr: {stderr}\n"
                "Ensure branch protection rules are configured before running the release."
            )
        raise RuntimeError(
            f"ERROR: Failed to read branch protection for {branch!r} in repo {repo!r}.\n"
            f"stderr: {stderr}\n"
            "Ensure GH_TOKEN is set with 'administration' permission."
        )
    try:
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"ERROR: Could not parse branch protection response for branch "
            f"{branch!r} in repo {repo!r}: {exc}"
        ) from exc


def _protection_put_body(protection: dict[str, Any], lock: bool) -> str:
    """Build a complete branch-protection PUT body from a GET response.

    The GitHub 'Update branch protection' API requires the full configuration on
    every PUT (required_status_checks, enforce_admins,
    required_pull_request_reviews, restrictions); sending only lock_branch yields
    HTTP 422. This preserves the existing rule and only toggles lock_branch.

    Args:
        protection: The branch protection rule from the GitHub GET API.
        lock: The desired lock_branch state.

    Returns:
        A JSON string for the PUT request body.
    """
    rsc = protection.get("required_status_checks")
    rsc_body = (
        {"strict": rsc.get("strict", False), "contexts": rsc.get("contexts", [])}
        if rsc is not None
        else None
    )

    rpr = protection.get("required_pull_request_reviews")
    rpr_body = (
        {
            "dismiss_stale_reviews": rpr.get("dismiss_stale_reviews", False),
            "require_code_owner_reviews": rpr.get("require_code_owner_reviews", False),
            "required_approving_review_count": rpr.get("required_approving_review_count", 0),
        }
        if rpr is not None
        else None
    )

    restr = protection.get("restrictions")
    restr_body = (
        {
            "users": [u["login"] for u in restr.get("users", [])],
            "teams": [t["slug"] for t in restr.get("teams", [])],
            "apps": [a["slug"] for a in restr.get("apps", [])],
        }
        if restr is not None
        else None
    )

    body = {
        "required_status_checks": rsc_body,
        "enforce_admins": protection.get("enforce_admins", {}).get("enabled", False),
        "required_pull_request_reviews": rpr_body,
        "restrictions": restr_body,
        "required_linear_history": protection.get("required_linear_history", {}).get(
            "enabled", False
        ),
        "allow_force_pushes": protection.get("allow_force_pushes", {}).get("enabled", False),
        "allow_deletions": protection.get("allow_deletions", {}).get("enabled", False),
        "required_conversation_resolution": protection.get(
            "required_conversation_resolution", {}
        ).get("enabled", False),
        "lock_branch": lock,
    }
    return json.dumps(body)


def _enable_branch_protection(repo: str, branch: str) -> None:
    """Enable the branch protection lock via the GitHub API.

    Reads the existing protection rule (B30 if absent) and re-PUTs the complete
    configuration with lock_branch=true, preserving every other setting. The
    protection PUT requires the full body; sending only lock_branch yields 422.

    Args:
        repo: The GitHub repository.
        branch: The branch to lock.

    Raises:
        RuntimeError: If the protection rule is missing (B30) or the call fails.
    """
    protection = _get_branch_protection(repo=repo, branch=branch)
    body = _protection_put_body(protection, lock=True)
    result = _run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{repo}/branches/{branch}/protection",
            "--input",
            "-",
        ],
        input_data=body,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"ERROR: Failed to enable branch protection for {branch!r} in repo {repo!r}.\n"
            f"stderr: {stderr}\n"
            "Ensure GH_TOKEN is set with 'administration: write' permission."
        )


def _disable_branch_lock(repo: str, branch: str) -> None:
    """Disable the branch protection lock.

    Reads the existing protection rule (B30 if absent) and re-PUTs the complete
    configuration with lock_branch=false, preserving every other setting.

    Args:
        repo: The GitHub repository.
        branch: The branch to unlock.

    Raises:
        RuntimeError: If the protection rule is missing (B30) or the call fails.
    """
    protection = _get_branch_protection(repo=repo, branch=branch)
    body = _protection_put_body(protection, lock=False)
    result = _run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{repo}/branches/{branch}/protection",
            "--input",
            "-",
        ],
        input_data=body,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"ERROR: Failed to disable branch protection lock for {branch!r} "
            f"in repo {repo!r}.\n"
            f"stderr: {stderr}\n"
            "Ensure GH_TOKEN is set with 'administration: write' permission."
        )


def _delete_lock_marker(repo: str, branch: str) -> None:
    """Delete the lock marker variable.

    Args:
        repo: The GitHub repository.
        branch: The branch whose lock marker should be deleted.

    Raises:
        RuntimeError: If the delete call fails unexpectedly.
    """
    var_name = f"BRANCH_LOCK_RUN_ID_{branch.replace('/', '_').upper()}"
    result = _run(
        [
            "gh",
            "api",
            "--method",
            "DELETE",
            f"repos/{repo}/actions/variables/{var_name}",
        ]
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # 404 means already deleted -- not an error for the delete step.
        if "404" in stderr or "Not Found" in stderr:
            return
        raise RuntimeError(
            f"ERROR: Failed to delete lock marker variable {var_name!r} "
            f"for repo {repo!r}.\n"
            f"stderr: {stderr}\n"
            "Ensure GH_TOKEN is set with 'variables: write' permission."
        )


def _is_branch_locked(repo: str, branch: str) -> bool:
    """Check whether the branch lock marker variable exists.

    Args:
        repo: The GitHub repository.
        branch: The branch to check.

    Returns:
        True if the lock marker variable exists.
    """
    var_name = f"BRANCH_LOCK_RUN_ID_{branch.replace('/', '_').upper()}"
    result = _run(
        [
            "gh",
            "api",
            f"repos/{repo}/actions/variables/{var_name}",
            "--jq",
            ".value",
        ]
    )
    if result.returncode != 0:
        # 404 -> not locked.
        return False
    value = result.stdout.strip()
    return bool(value) and value != "null"


def lock_branch(repo: str, branch: str, run_id: str) -> None:
    """Engage the branch protection lock.

    Writes the lock marker variable and enables branch protection.

    Args:
        repo: The GitHub repository.
        branch: The branch to lock.
        run_id: The GitHub Actions run ID acquiring the lock.

    Raises:
        RuntimeError: If any API call fails (including B30 missing protection rule).
    """
    write_lock_marker(repo=repo, branch=branch, run_id=run_id)
    _enable_branch_protection(repo=repo, branch=branch)


def unlock_branch(repo: str, branch: str) -> None:
    """Release the branch protection lock.

    Idempotent: if the branch is not locked, this is a no-op (D47).

    Args:
        repo: The GitHub repository.
        branch: The branch to unlock.

    Raises:
        RuntimeError: If any API call fails unexpectedly.
    """
    if not _is_branch_locked(repo=repo, branch=branch):
        print(f"Branch {branch!r} is not locked -- no-op (D47).")
        return

    _delete_lock_marker(repo=repo, branch=branch)
    _disable_branch_lock(repo=repo, branch=branch)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for lock_branch.

    Exit codes:
        0 -- success
        1 -- error
    """
    parser = argparse.ArgumentParser(
        description="Engage or release the branch protection lock for the release pipeline."
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["lock", "unlock"],
        help="Action to perform: 'lock' or 'unlock'.",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("REPO", ""),
        help="GitHub repository (owner/name).",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("BRANCH", ""),
        help="Branch to lock/unlock.",
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("GITHUB_RUN_ID", ""),
        help="GitHub Actions run ID (required for --action lock).",
    )
    args = parser.parse_args()

    if not args.repo:
        print(
            f"{GH_ERROR_PREFIX}ERROR: --repo (or REPO env var) is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.branch:
        print(
            f"{GH_ERROR_PREFIX}ERROR: --branch (or BRANCH env var) is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if args.action == "lock":
            if not args.run_id:
                print(
                    f"{GH_ERROR_PREFIX}ERROR: --run-id (or GITHUB_RUN_ID env var) is required "
                    "for --action lock.",
                    file=sys.stderr,
                )
                sys.exit(1)
            lock_branch(repo=args.repo, branch=args.branch, run_id=args.run_id)
            print(f"Branch {args.branch!r} locked by run {args.run_id!r}.")
        else:
            unlock_branch(repo=args.repo, branch=args.branch)
            print(f"Branch {args.branch!r} unlock operation complete.")
    except RuntimeError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
