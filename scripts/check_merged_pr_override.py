"""check_merged_pr_override -- scope=all override detection off the pull_request event payload.

The PR-validation path reads the ``detect-scope-override`` label straight from the
``pull_request`` event payload. Two other events have no PR payload and would otherwise
re-run scope-detection over the full multi-module diff and reject a deliberate
scope-override change as ``multi-module``/``mixed``:

1. The push (main-validation) path sees only the squashed merge commit on ``main``.
2. The merge_group (merge-queue) path validates the PR's changes on a temporary
   ``gh-readonly-queue/<base>/pr-<n>-<sha>`` ref; the ``merge_group`` event payload carries
   no labels or author, only the head ref (which embeds the queued PR number).

This script closes both gaps and emits ``scope_override=true|false``:

- ``--commit-sha`` (push path): resolves the merged PR via
  ``gh api repos/{repo}/commits/{sha}/pulls``.
- ``--merge-group-ref`` (merge-queue path): parses the queued PR number out of the
  merge_group head ref and resolves the PR via ``gh api repos/{repo}/pulls/{number}``.

Both paths extract that PR's labels and author and reuse the same fail-closed admin
authorization as the PR path (``check_scope_override``).

Fail-closed contract:
- No PR associated (commit path with no PR) -> emit scope_override=false (a direct push is
  never an override; downstream single-scope detection still applies).
- PR present without the override label -> emit scope_override=false.
- PR present WITH the override label + author is an org admin -> emit scope_override=true.
- PR present WITH the override label + author is NOT an org admin -> raise, exit 1.
- A malformed merge_group ref (no parseable PR number) -> raise, exit 1.
- gh API failure -> raise, exit 1 (never silently downgrade to false).

Usage:
    # push (post-merge) path
    uv run python -m scripts.check_merged_pr_override \\
        --commit-sha "$GITHUB_SHA" \\
        --repo "matthew-dresden/telemetry-platform" \\
        --org "matthew-dresden" \\
        --output "$GITHUB_OUTPUT"

    # merge_group (merge-queue) path
    uv run python -m scripts.check_merged_pr_override \\
        --merge-group-ref "$MERGE_GROUP_HEAD_REF" \\
        --repo "matthew-dresden/telemetry-platform" \\
        --org "matthew-dresden" \\
        --output "$GITHUB_OUTPUT"

Exit codes:
    0 -- override resolved (true or false) and written to the output file
    1 -- override label present but author not admin, a malformed merge_group ref,
         or a gh API call failed
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

from scripts.check_scope_override import AuthorizationError, is_admin_member
from scripts.constants import (
    GH_ERROR_PREFIX,
    OUTPUT_KEY_SCOPE_OVERRIDE,
    SCOPE_OVERRIDE_LABEL,
    write_output,
)


class MergedPrLookupError(Exception):
    """Raised when the merged PR for a commit cannot be resolved (fail-closed)."""


def fetch_merged_pr(repo: str, commit_sha: str) -> dict[str, object] | None:
    """Return the merged PR object associated with a commit SHA, or None if there is none.

    Uses ``gh api repos/{repo}/commits/{sha}/pulls`` -- the same association endpoint the
    version-check path relies on -- and returns the first associated PR's JSON object
    (labels + user are read from it by the caller). A direct push with no associated PR
    yields None rather than an error (a direct push is never an override).

    Args:
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        commit_sha: The merge commit SHA to look up.

    Returns:
        The first associated PR object as a dict, or None when no PR is associated.

    Raises:
        MergedPrLookupError: If the gh CLI is missing or the API call fails (fail-closed).
    """
    gh_binary = shutil.which("gh")
    if gh_binary is None:
        raise MergedPrLookupError(
            "ERROR: 'gh' CLI binary not found on PATH. "
            "Ensure the GitHub CLI is installed (run 'make tools-ensure')."
        )
    try:
        result = subprocess.run(
            [gh_binary, "api", f"repos/{repo}/commits/{commit_sha}/pulls"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise MergedPrLookupError(
            f"ERROR: gh api call failed resolving the PR for commit {commit_sha!r} "
            f"in repo {repo!r}. gh exited with code {exc.returncode}. "
            f"stderr: {exc.stderr.strip()}\n"
            "Ensure GH_TOKEN is set and has 'pull-requests: read' permission."
        ) from exc

    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        pulls = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MergedPrLookupError(
            f"ERROR: Failed to parse the gh api response for commit {commit_sha!r}: {exc}"
        ) from exc
    if not isinstance(pulls, list) or not pulls:
        return None
    first = pulls[0]
    if not isinstance(first, dict):
        raise MergedPrLookupError(
            f"ERROR: Unexpected gh api response shape for commit {commit_sha!r}: "
            "expected a list of PR objects."
        )
    return first


# The merge_group head ref GitHub creates for a queued PR. GitHub's documented format is
# ``gh-readonly-queue/<base_branch>/pr-<pr_number>-<base_sha>`` (optionally prefixed with
# ``refs/heads/``). Anchoring on the trailing ``-<sha>`` makes the PR-number capture robust
# even when the base branch name itself contains hyphens or slashes.
_MERGE_GROUP_REF_PR_PATTERN = re.compile(r"pr-(\d+)-[0-9a-fA-F]+$")


def parse_pr_number_from_merge_group_ref(merge_group_ref: str) -> int:
    """Extract the queued PR number from a merge_group head ref (fail-closed).

    The ``merge_group`` event payload carries no PR number directly; it is embedded in the
    temporary ``gh-readonly-queue/<base>/pr-<n>-<sha>`` head ref. Parsing it here keeps the
    regex in a testable script (ledger D11) rather than in a shell pipe.

    Args:
        merge_group_ref: The merge_group head ref (e.g.
            'gh-readonly-queue/main/pr-161-1edfc05...').

    Returns:
        The queued PR number.

    Raises:
        MergedPrLookupError: If the ref does not contain a parseable PR number (fail-closed --
            never silently treat a malformed ref as 'no override').
    """
    match = _MERGE_GROUP_REF_PR_PATTERN.search(merge_group_ref.strip())
    if match is None:
        raise MergedPrLookupError(
            f"ERROR: Could not parse a queued PR number from merge_group head ref "
            f"{merge_group_ref!r}. Expected the GitHub merge-queue format "
            "'gh-readonly-queue/<base>/pr-<number>-<sha>'. Cannot authorize the "
            "scope-override fail-closed."
        )
    return int(match.group(1))


def fetch_pr_by_number(repo: str, pr_number: int) -> dict[str, object] | None:
    """Return the PR object for a PR number, or None if the API yields no object.

    Uses ``gh api repos/{repo}/pulls/{number}`` -- the single-PR endpoint, whose response
    carries the same ``labels`` + ``user`` fields the commit-association endpoint does, so the
    existing ``extract_label_names`` / ``extract_author`` helpers apply unchanged.

    Args:
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        pr_number: The pull request number to look up.

    Returns:
        The PR object as a dict, or None when the response is empty.

    Raises:
        MergedPrLookupError: If the gh CLI is missing or the API call fails (fail-closed).
    """
    gh_binary = shutil.which("gh")
    if gh_binary is None:
        raise MergedPrLookupError(
            "ERROR: 'gh' CLI binary not found on PATH. "
            "Ensure the GitHub CLI is installed (run 'make tools-ensure')."
        )
    try:
        result = subprocess.run(
            [gh_binary, "api", f"repos/{repo}/pulls/{pr_number}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise MergedPrLookupError(
            f"ERROR: gh api call failed resolving PR #{pr_number} in repo {repo!r}. "
            f"gh exited with code {exc.returncode}. stderr: {exc.stderr.strip()}\n"
            "Ensure GH_TOKEN is set and has 'pull-requests: read' permission."
        ) from exc

    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        pr = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MergedPrLookupError(
            f"ERROR: Failed to parse the gh api response for PR #{pr_number}: {exc}"
        ) from exc
    if not isinstance(pr, dict):
        raise MergedPrLookupError(
            f"ERROR: Unexpected gh api response shape for PR #{pr_number}: "
            "expected a single PR object."
        )
    return pr


def extract_label_names(pr: dict[str, object]) -> list[str]:
    """Return the label-name strings from a PR object's ``labels`` array.

    Args:
        pr: A PR object as returned by the commits/{sha}/pulls API.

    Returns:
        The list of non-empty label name strings (empty list when there are no labels).
    """
    labels_raw = pr.get("labels")
    if not isinstance(labels_raw, list):
        return []
    names: list[str] = []
    for label in labels_raw:
        if isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def extract_author(pr: dict[str, object]) -> str | None:
    """Return the PR author's login from a PR object's ``user`` field.

    Args:
        pr: A PR object as returned by the commits/{sha}/pulls API.

    Returns:
        The author login string, or None when it cannot be determined.
    """
    user = pr.get("user")
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str) and login.strip():
            return login.strip()
    return None


def _authorize_pr_scope_override(
    pr: dict[str, object],
    org: str,
    output_path: str,
    pr_descriptor: str,
) -> bool:
    """Apply the fail-closed admin authorization to a resolved PR object and write the result.

    Shared by every no-payload entry path (commit-SHA push path, merge_group queue path):
    reads the PR's labels + author and reuses the identical org-admin contract as the
    pull_request path (``check_scope_override``). Keeping this single helper guarantees the
    three paths can never drift in their authorization logic (DRY).

    Args:
        pr: The resolved PR object (carrying ``labels`` + ``user``).
        org: The GitHub organization name.
        output_path: Output file path to write scope_override= to.
        pr_descriptor: Human-readable PR descriptor for error messages
            (e.g. "merged PR for commit 'abc123'", "queued PR #161").

    Returns:
        True if the override is authorized, False otherwise.

    Raises:
        AuthorizationError: If the override label is present but the author is not an admin.
        MergedPrLookupError: If the override label is present but the author is undeterminable.
    """
    labels = extract_label_names(pr)
    if SCOPE_OVERRIDE_LABEL not in labels:
        write_output(output_path, OUTPUT_KEY_SCOPE_OVERRIDE, "false")
        return False

    author = extract_author(pr)
    if author is None:
        raise MergedPrLookupError(
            f"ERROR: The {pr_descriptor} carries the '{SCOPE_OVERRIDE_LABEL}' label but its "
            "author could not be determined from the gh api response. "
            "Cannot authorize the override fail-closed."
        )

    # Re-verify org-admin membership fail-closed -- identical contract to the PR path.
    if not is_admin_member(org=org, author=author):
        raise AuthorizationError(
            f"ERROR: The {pr_descriptor} carries the '{SCOPE_OVERRIDE_LABEL}' label but its "
            f"author '{author}' is not an admin of org '{org}'. Only org admins may use the "
            "scope-override label."
        )

    write_output(output_path, OUTPUT_KEY_SCOPE_OVERRIDE, "true")
    return True


def resolve_push_scope_override(
    commit_sha: str,
    repo: str,
    org: str,
    output_path: str,
) -> bool:
    """Resolve the scope-override for the push path and write the result to the output file.

    Mirrors the PR-path authorization (fail-closed admin check) but sources the label set
    and author from the merged PR rather than the workflow event payload.

    Args:
        commit_sha: The merge commit SHA pushed to the default branch.
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        org: The GitHub organization name.
        output_path: Output file path to write scope_override= to.

    Returns:
        True if the override is authorized, False otherwise.

    Raises:
        AuthorizationError: If the override label is present but the author is not an admin.
        MergedPrLookupError: If the merged PR cannot be resolved (fail-closed).
    """
    pr = fetch_merged_pr(repo=repo, commit_sha=commit_sha)
    if pr is None:
        # Direct push with no associated PR -- never an override.
        write_output(output_path, OUTPUT_KEY_SCOPE_OVERRIDE, "false")
        return False

    return _authorize_pr_scope_override(
        pr=pr,
        org=org,
        output_path=output_path,
        pr_descriptor=f"merged PR for commit {commit_sha!r}",
    )


def resolve_merge_group_scope_override(
    merge_group_ref: str,
    repo: str,
    org: str,
    output_path: str,
) -> bool:
    """Resolve the scope-override for the merge_group (merge-queue) path.

    The merge_group event payload has no labels/author -- only the head ref, which embeds the
    queued PR number. This parses that number, resolves the PR via the single-PR API, and
    applies the same fail-closed admin authorization as every other path. Without this, a
    scope-override (multi-module) PR would re-detect as multi-module on its merge_group
    validation and be rejected by the queue, even though an admin already authorized it on
    the PR.

    Args:
        merge_group_ref: The merge_group head ref
            (e.g. 'gh-readonly-queue/main/pr-161-<sha>').
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        org: The GitHub organization name.
        output_path: Output file path to write scope_override= to.

    Returns:
        True if the override is authorized, False otherwise.

    Raises:
        AuthorizationError: If the override label is present but the author is not an admin.
        MergedPrLookupError: If the ref has no parseable PR number or the PR cannot be
            resolved (fail-closed).
    """
    pr_number = parse_pr_number_from_merge_group_ref(merge_group_ref)
    pr = fetch_pr_by_number(repo=repo, pr_number=pr_number)
    if pr is None:
        # The PR number came from the queue's own head ref, so the PR must exist. An empty
        # response is an anomaly -- fail closed rather than silently downgrade to no-override.
        raise MergedPrLookupError(
            f"ERROR: gh api returned no PR object for queued PR #{pr_number} in repo {repo!r}. "
            "Cannot authorize the scope-override fail-closed."
        )

    return _authorize_pr_scope_override(
        pr=pr,
        org=org,
        output_path=output_path,
        pr_descriptor=f"queued PR #{pr_number}",
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Resolve the detect-scope-override label for the no-PR-payload paths (push "
            "post-merge via --commit-sha, or merge-queue via --merge-group-ref) by reading "
            "the resolved PR's labels + author via the GitHub API."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--commit-sha",
        help="Push path: the merge commit SHA pushed to the default branch (e.g. $GITHUB_SHA).",
    )
    source.add_argument(
        "--merge-group-ref",
        help=(
            "Merge-queue path: the merge_group head ref "
            "(e.g. $GITHUB_REF or github.event.merge_group.head_ref) whose embedded PR number "
            "identifies the queued PR."
        ),
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').",
    )
    parser.add_argument(
        "--org",
        required=True,
        help="GitHub organization name.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output file path (e.g. $GITHUB_OUTPUT) to write scope_override= to.",
    )
    args = parser.parse_args()

    try:
        if args.merge_group_ref is not None:
            resolve_merge_group_scope_override(
                merge_group_ref=args.merge_group_ref,
                repo=args.repo,
                org=args.org,
                output_path=args.output,
            )
        else:
            resolve_push_scope_override(
                commit_sha=args.commit_sha,
                repo=args.repo,
                org=args.org,
                output_path=args.output,
            )
    except (AuthorizationError, MergedPrLookupError) as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
