"""check_staleness -- detect a stale base before the release proceeds.

Compares the merge SHA against HEAD to find files changed in the module path
or shared_paths_affecting_modules since the merge commit. If any such files
have changed, the base is stale and the release must not proceed.

Usage (invoked by `make check-staleness`):
    uv run python -m scripts.check_staleness

Arguments:
    --merge-sha:    The merge commit SHA (from $GITHUB_SHA or scope-detection output).
    --module-path:  The module directory path (e.g. 'providers/aws/primitives/kms-key').
    --shared-paths: Comma-separated list of shared paths that affect all modules.

Environment variables:
    MERGE_SHA:    Alternative to --merge-sha.
    MODULE_PATH:  Alternative to --module-path.

Exit codes:
    0 -- not stale (safe to proceed)
    1 -- stale base detected or hard error
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


class StalenessResult(Enum):
    """Result of a staleness check."""

    NOT_STALE = auto()  # No relevant changes since merge SHA.
    STALE = auto()  # Relevant changes detected -- base is stale.


# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


def is_path_in_scope(
    changed_path: str,
    module_path: str,
    shared_paths: list[str],
) -> bool:
    """Determine whether a changed path affects the current module release.

    A path is in scope if:
    1. It begins with the module_path (same-module change).
    2. It matches any prefix in shared_paths_affecting_modules.

    Args:
        changed_path: The file path that changed.
        module_path: The module directory path.
        shared_paths: List of shared path prefixes affecting all modules.

    Returns:
        True if the path is relevant to this module.
    """
    # Same-module touch.
    if changed_path.startswith(module_path + "/") or changed_path == module_path:
        return True

    # Shared-paths touch.
    for prefix in shared_paths:
        if changed_path.startswith(prefix) or changed_path == prefix.rstrip("/"):
            return True

    return False


def check_staleness(
    changed_files: list[str],
    module_path: str,
    shared_paths: list[str],
) -> StalenessResult:
    """Check whether any changed files indicate a stale base.

    Args:
        changed_files: List of file paths changed since the merge SHA.
        module_path: The module directory path.
        shared_paths: List of shared path prefixes affecting all modules.

    Returns:
        StalenessResult.STALE if any changed file is in scope.
        StalenessResult.NOT_STALE otherwise.
    """
    for path in changed_files:
        if is_path_in_scope(
            changed_path=path,
            module_path=module_path,
            shared_paths=shared_paths,
        ):
            return StalenessResult.STALE

    return StalenessResult.NOT_STALE


def _get_changed_files_since(merge_sha: str) -> list[str]:
    """Get the list of files changed since the given merge SHA.

    Uses 'git diff --name-only <merge_sha> HEAD' to find files that changed
    after the merge commit was created.

    Args:
        merge_sha: The merge commit SHA to compare against.

    Returns:
        List of file paths that changed since merge_sha.

    Raises:
        RuntimeError: If git diff fails.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", merge_sha, "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: 'git diff --name-only {merge_sha} HEAD' failed.\n"
            f"stderr: {result.stderr.strip()}\n"
            "Ensure the repository has been fetched with fetch-depth: 0 and "
            "that MERGE_SHA refers to a valid commit."
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for check_staleness.

    Reads the merge SHA and module path, detects changed files, and exits
    non-zero if the base is stale.
    """
    parser = argparse.ArgumentParser(description="Detect a stale base before the release proceeds.")
    parser.add_argument(
        "--merge-sha",
        default=os.environ.get("MERGE_SHA", ""),
        help="The merge commit SHA (HEAD at time of merge trigger).",
    )
    parser.add_argument(
        "--module-path",
        default=os.environ.get("MODULE_PATH", ""),
        help="The module directory path.",
    )
    parser.add_argument(
        "--shared-paths",
        default="",
        help="Comma-separated list of shared path prefixes.",
    )
    args = parser.parse_args()

    if not args.merge_sha:
        print(
            f"{GH_ERROR_PREFIX}ERROR: --merge-sha (or MERGE_SHA env var) is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    shared_paths = [p.strip() for p in args.shared_paths.split(",") if p.strip()]

    try:
        changed_files = _get_changed_files_since(merge_sha=args.merge_sha)
    except RuntimeError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # Config-scope release: scope-detection emits no module_path. The unit of
    # release is the whole-repo config (root VERSION + the config tag), so the
    # staleness check is repo-wide: ANY change to the repository since the merge
    # commit makes the base stale. (Module-scope releases pass a module_path and
    # use the per-module scope check below.)
    if not args.module_path:
        if changed_files:
            print(
                f"{GH_ERROR_PREFIX}ERROR: Stale base detected (config scope). Files changed "
                f"since merge commit {args.merge_sha!r}:\n"
                + "\n".join(f"  - {f}" for f in changed_files)
                + "\nThe release must not proceed on a stale base. "
                "Re-trigger the release pipeline from the latest commit.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"Base is fresh. No changes since merge commit {args.merge_sha!r} "
            "(config scope, repo-wide check)."
        )
        sys.exit(0)

    result = check_staleness(
        changed_files=changed_files,
        module_path=args.module_path,
        shared_paths=shared_paths,
    )

    if result == StalenessResult.STALE:
        stale_files = [
            f for f in changed_files if is_path_in_scope(f, args.module_path, shared_paths)
        ]
        print(
            f"{GH_ERROR_PREFIX}ERROR: Stale base detected. "
            f"Files relevant to module {args.module_path!r} changed since merge commit "
            f"{args.merge_sha!r}:\n"
            + "\n".join(f"  - {f}" for f in stale_files)
            + "\nThe release must not proceed on a stale base. "
            "Re-trigger the release pipeline from the latest commit.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Base is fresh. No relevant changes since merge commit {args.merge_sha!r}.")
    sys.exit(0)


if __name__ == "__main__":
    main()
