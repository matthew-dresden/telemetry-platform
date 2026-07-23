"""simulate_merge -- dry-run merge to validate mergeability (B14).

Performs a non-mutating 'git merge-tree' to check whether a branch can be
merged cleanly into the base branch. Reports CONFLICT or SUCCESS without
committing, tagging, or pushing anything (B14).

Usage (invoked by `make simulate-merge`):
    uv run python -m scripts.simulate_merge

Environment variables:
    HEAD_REF:   The source ref to merge from (default: 'HEAD').
    BASE_REF:   The target ref to merge into (default: 'origin/main').

Exit codes:
    0 -- merge simulation succeeded (no conflicts)
    1 -- merge conflict detected or hard error
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from enum import Enum, auto

from scripts.constants import GH_ERROR_PREFIX

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SimulateMergeResult(Enum):
    """Result of a dry-run merge simulation."""

    SUCCESS = auto()  # No conflicts detected.
    CONFLICT = auto()  # Merge conflicts detected.


# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


# git merge-tree --write-tree exit codes (git >= 2.38). 0 == clean merge,
# 1 == merge conflict; any other code is an unexpected hard failure.
_MERGE_TREE_CLEAN: int = 0
_MERGE_TREE_CONFLICT: int = 1


def simulate_merge(head_ref: str, base_ref: str) -> SimulateMergeResult:
    """Perform a dry-run merge to check mergeability.

    Uses real-merge ``git merge-tree --write-tree`` mode (git >= 2.38), which does
    NOT modify any branch, index, or working tree -- it computes the merged tree in
    the object store and reports mergeability purely through its exit code (B14):

        git merge-tree --write-tree --merge-base=<merge-base> <base-ref> <head-ref>

    Exit-code contract (per git-merge-tree(1)):
        0 -- clean merge (stdout is the merged tree OID)
        1 -- merge conflict (stdout lists the conflicted entries)
        other -- unexpected hard failure (raised as RuntimeError)

    This replaces the deprecated 3-arg ``git merge-tree <base> <branch1> <branch2>``
    mode, which always exits 0 and emits a raw content diff. Scanning that diff for
    the literal substring "CONFLICT" produced false positives whenever a merged file
    legitimately contained the word "CONFLICT" in its own content (e.g. this module's
    own source and tests), reporting conflicts on a perfectly clean merge. The
    --write-tree mode signals conflicts through the exit code only, so no content
    scanning is needed and the false positive is eliminated.

    Args:
        head_ref: The source ref to merge (e.g. 'HEAD').
        base_ref: The base ref to merge into (e.g. 'origin/main').

    Returns:
        SimulateMergeResult.SUCCESS if no conflicts.
        SimulateMergeResult.CONFLICT if conflicts were detected.

    Raises:
        RuntimeError: If a git command fails unexpectedly (not a conflict).
    """
    # Find the merge base so the three-way merge is anchored explicitly. This makes
    # the simulation deterministic regardless of which ref is checked out in CI
    # (PR head-vs-merge-ref differences) and matches what an actual merge would use.
    merge_base_result = subprocess.run(
        ["git", "merge-base", base_ref, head_ref],
        capture_output=True,
        text=True,
    )
    if merge_base_result.returncode != 0:
        raise RuntimeError(
            f"ERROR: 'git merge-base {base_ref} {head_ref}' failed.\n"
            f"stderr: {merge_base_result.stderr.strip()}\n"
            "Ensure both refs exist and the repository has been fetched "
            "with fetch-depth: 0."
        )
    merge_base = merge_base_result.stdout.strip()

    # Perform the dry-run merge using merge-tree --write-tree (non-mutating: writes
    # only loose objects into the store, never a ref/index/working tree).
    result = subprocess.run(
        [
            "git",
            "merge-tree",
            "--write-tree",
            f"--merge-base={merge_base}",
            base_ref,
            head_ref,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode == _MERGE_TREE_CLEAN:
        return SimulateMergeResult.SUCCESS
    if result.returncode == _MERGE_TREE_CONFLICT:
        return SimulateMergeResult.CONFLICT

    raise RuntimeError(
        f"ERROR: 'git merge-tree --write-tree' exited with unexpected code "
        f"{result.returncode} merging {head_ref!r} into {base_ref!r}.\n"
        f"stderr: {result.stderr.strip()}\n"
        "This is not a merge conflict (which exits 1) but a hard git failure. "
        "Ensure both refs exist, the repository was fetched with fetch-depth: 0, "
        "and the installed git is >= 2.38 (which supports 'merge-tree --write-tree')."
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for simulate_merge.

    Reads HEAD_REF and BASE_REF from the environment or argv, performs the
    dry-run merge, and exits 0 on success or 1 on conflict/error.
    """
    parser = argparse.ArgumentParser(
        description="Dry-run merge simulation to check mergeability without mutating the branch."
    )
    parser.add_argument(
        "--head-ref",
        default=os.environ.get("HEAD_REF", "HEAD"),
        help="The source ref to merge from (default: HEAD).",
    )
    parser.add_argument(
        "--base-ref",
        default=os.environ.get("BASE_REF", "origin/main"),
        help="The target ref to merge into (default: origin/main).",
    )
    args = parser.parse_args()

    try:
        result = simulate_merge(head_ref=args.head_ref, base_ref=args.base_ref)
    except RuntimeError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    if result == SimulateMergeResult.CONFLICT:
        print(
            f"{GH_ERROR_PREFIX}ERROR: B14 merge simulation detected conflicts merging "
            f"{args.head_ref!r} into {args.base_ref!r}.\n"
            "Resolve the conflicts before this pull request can be merged.\n"
            "No branch was mutated by this check.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Merge simulation succeeded: {args.head_ref!r} merges cleanly into {args.base_ref!r}.")
    sys.exit(0)


if __name__ == "__main__":
    main()
