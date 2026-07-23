"""ci_calculate_version -- CI entrypoint for version calculation.

Wires CI environment inputs (COMMIT_SHA, COMMIT_MSG, SCOPE, MODULE_PATH, REPO)
into the pure calculate_version derivation. Enforces D17 anti-forgery by:
1. Fetching the PR title via the GitHub API (gh api repos/{REPO}/commits/{SHA}/pulls).
2. Deriving the bump type exclusively from that API-fetched title.
3. Comparing against the squash-subject bump -- fails if they disagree.

Usage (invoked by `make calculate-version`):
    uv run python -m scripts.ci_calculate_version

Environment variables:
    GH_TOKEN:    GitHub API token (required for the PR title API call).
    COMMIT_SHA:  The merge commit SHA.
    COMMIT_MSG:  The squash-commit subject line (validated but NOT the bump source).
    SCOPE:       'module', 'config', or 'terragrunt'.
    MODULE_PATH: The module directory path (required when SCOPE=module).
    REPO:        The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
    OUTPUT:      Path to the GITHUB_OUTPUT file.

Exit codes:
    0 -- success; outputs written to OUTPUT
    1 -- error; details on stderr
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from scripts.calculate_version import (
    build_tag_prefix,
    calculate_next_version,
    derive_bump_from_api_result,
    validate_bump_agreement,
)
from scripts.constants import (
    GH_ERROR_PREFIX,
    OUTPUT_KEY_BUMP_TYPE,
    OUTPUT_KEY_FULL_TAG,
    OUTPUT_KEY_IS_INITIAL,
    OUTPUT_KEY_NEXT_VERSION,
    OUTPUT_KEY_TAG_PREFIX,
    SCOPE_TERRAGRUNT,
    write_output,
)
from scripts.validate_pr_title import parse_pr_title


def _fetch_pr_title_from_api(repo: str, commit_sha: str) -> str | None:
    """Fetch the PR title associated with a commit SHA via the GitHub API.

    Uses 'gh api repos/{repo}/commits/{sha}/pulls --jq .[0].title'.
    Returns None if the API returns an empty list (no associated PR).

    Args:
        repo: The GitHub repository (e.g. 'matthew-dresden/telemetry-platform').
        commit_sha: The commit SHA to look up.

    Returns:
        The PR title string or None if no PR is associated.

    Raises:
        RuntimeError: If the gh API call fails.
    """
    cmd = [
        "gh",
        "api",
        f"repos/{repo}/commits/{commit_sha}/pulls",
        "--jq",
        ".[0].title",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: gh api call failed for commit {commit_sha!r} in repo {repo!r}.\n"
            f"stderr: {result.stderr.strip()}\n"
            "Ensure GH_TOKEN is set and has 'pull-requests: read' permission."
        )
    title = result.stdout.strip()
    # gh --jq returns 'null' when the field is null (no PR found)
    if not title or title == "null":
        return None
    return title


def _fetch_git_tags() -> list[str]:
    """Fetch all git tags from the local repository.

    Returns:
        A list of git tag strings.

    Raises:
        RuntimeError: If git tag fails.
    """
    result = subprocess.run(["git", "tag", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: git tag -l failed.\nstderr: {result.stderr.strip()}\n"
            "Ensure the repository has been cloned with full tag history (fetch-depth: 0)."
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _require_env(name: str) -> str:
    """Read a required environment variable, failing fast if absent or empty.

    Args:
        name: The environment variable name.

    Returns:
        The variable's value (guaranteed non-empty).

    Raises:
        SystemExit: If the variable is absent or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        print(
            f"{GH_ERROR_PREFIX}ERROR: Required environment variable {name!r} is not set.\n"
            "Ensure the workflow passes all required environment variables to this step.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def main() -> None:
    """CI entrypoint for version calculation.

    Reads env vars, enforces D17, derives the version, and writes outputs.
    For scope=terragrunt, exits 0 without writing any version outputs.
    """
    scope = _require_env("SCOPE")
    output_path = _require_env("OUTPUT")

    # Terragrunt scope produces no module tag -- short-circuit.
    if scope == SCOPE_TERRAGRUNT:
        sys.exit(0)

    commit_sha = _require_env("COMMIT_SHA")
    commit_msg = _require_env("COMMIT_MSG")
    repo = _require_env("REPO")
    module_path = os.environ.get("MODULE_PATH", "").strip()

    # Load config for the config_tag_prefix
    config_tag_prefix = _load_config_tag_prefix()

    # D17: Fetch authoritative PR title from the GitHub API.
    try:
        api_pr_title = _fetch_pr_title_from_api(repo=repo, commit_sha=commit_sha)
    except RuntimeError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # D17: Derive the bump from the API-fetched title (the enforcement point).
    try:
        api_bump = derive_bump_from_api_result(api_pr_title=api_pr_title)
    except ValueError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # D17: Validate squash subject bump agrees with API bump (forgery detection).
    # COMMIT_MSG may arrive as the full squash-commit message (subject + body) when
    # the workflow passes github.event.head_commit.message; the conventional-commit
    # contract applies only to the subject line, so validate the first line.
    squash_lines = [line for line in commit_msg.splitlines() if line.strip()]
    squash_subject = squash_lines[0] if squash_lines else commit_msg
    try:
        squash_parsed = parse_pr_title(squash_subject)
        validate_bump_agreement(api_bump=api_bump, squash_bump=squash_parsed.bump)
    except ValueError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # Build the tag prefix per scope.
    try:
        tag_prefix = build_tag_prefix(
            scope=scope,
            module_path=module_path,
            config_tag_prefix=config_tag_prefix,
        )
    except ValueError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # Fetch existing tags and calculate the next version.
    try:
        existing_tags = _fetch_git_tags()
        result = calculate_next_version(
            existing_tags=existing_tags,
            tag_prefix=tag_prefix,
            bump=api_bump,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # Write outputs to GITHUB_OUTPUT.
    write_output(output_path, OUTPUT_KEY_NEXT_VERSION, result.next_version)
    write_output(output_path, OUTPUT_KEY_BUMP_TYPE, result.bump.value)
    write_output(output_path, OUTPUT_KEY_TAG_PREFIX, result.tag_prefix)
    write_output(output_path, OUTPUT_KEY_FULL_TAG, result.full_tag)
    write_output(output_path, OUTPUT_KEY_IS_INITIAL, str(result.is_initial).lower())

    print(f"next_version={result.next_version}")
    print(f"bump_type={result.bump.value}")
    print(f"tag_prefix={result.tag_prefix}")
    print(f"full_tag={result.full_tag}")
    print(f"is_initial={str(result.is_initial).lower()}")


def _load_config_tag_prefix() -> str:
    """Load the config_tag_prefix from monorepo-config.json.

    Returns:
        The config_tag_prefix string.

    Raises:
        SystemExit: If the config file is missing or malformed.
    """
    from pathlib import Path

    config_path = Path("monorepo-config.json")
    if not config_path.exists():
        print(
            f"{GH_ERROR_PREFIX}ERROR: monorepo-config.json not found in the working directory.\n"
            "Ensure the repository has been checked out before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        with config_path.open() as fh:
            config: dict[str, object] = json.load(fh)
    except json.JSONDecodeError as exc:
        print(
            f"{GH_ERROR_PREFIX}ERROR: Failed to parse monorepo-config.json: {exc}\n"
            "Ensure monorepo-config.json is valid JSON.",
            file=sys.stderr,
        )
        sys.exit(1)

    prefix = config.get("config_tag_prefix")
    if not prefix or not isinstance(prefix, str):
        print(
            f"{GH_ERROR_PREFIX}ERROR: 'config_tag_prefix' missing or empty "
            "in monorepo-config.json.\n"
            "Add a 'config_tag_prefix' key (e.g. 'monorepo-config').",
            file=sys.stderr,
        )
        sys.exit(1)

    return prefix


if __name__ == "__main__":
    main()
