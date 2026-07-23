"""check_scope_override -- admin-label authorization for the scope=all override.

Performs a fail-closed members:read admin verification before allowing any
scope-override. An unauthorized actor cannot widen the build scope.

Algorithm (docs/release-pipeline.md):
1. If the detect-scope-override label is absent -> emit scope_override=false, exit 0.
2. If the label is present -> call 'gh api orgs/<org>/memberships/<author> --jq .role'.
3. If role == 'admin' -> emit scope_override=true, exit 0.
4. If role != 'admin' or the API call fails -> raise AuthorizationError, exit 1 with ERROR:.

Usage:
    uv run python -m scripts.check_scope_override \\
        --labels "detect-scope-override,other-label" \\
        --author "username" \\
        --org "example-org" \\
        --output "$GITHUB_OUTPUT"

Exit codes:
    0 -- label absent (no override) or label present + actor is admin
    1 -- label present + actor is not admin, or API call failed
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

from scripts.constants import (
    GH_ERROR_PREFIX,
    OUTPUT_KEY_SCOPE_OVERRIDE,
    SCOPE_OVERRIDE_LABEL,
    write_output,
)


class AuthorizationError(Exception):
    """Raised when an actor is not authorized to use the scope-override label."""


def parse_labels(raw_labels: str) -> list[str]:
    """Parse the --labels argument into a list of label-name strings.

    The PR-validation workflow passes the labels as the GitHub Actions
    ``toJson(github.event.pull_request.labels.*.name)`` value -- a JSON array
    (e.g. ``["detect-scope-override"]``), often spanning multiple lines. A naive
    comma-split mis-parses that into a single token still carrying the surrounding
    brackets, quotes, and newlines, so the override label never matches and an
    authorized admin is silently denied the override.

    This parser handles both forms:
    - A JSON array string -> decoded into its element strings (the workflow path).
    - A bare comma-separated string -> split on commas (legacy / direct CLI use).

    Args:
        raw_labels: The raw --labels value (JSON array or comma-separated string).

    Returns:
        The list of non-empty, stripped label names.
    """
    stripped = raw_labels.strip()
    if stripped.startswith("["):
        decoded = json.loads(stripped)
        return [str(label).strip() for label in decoded if str(label).strip()]
    return [label.strip() for label in raw_labels.split(",") if label.strip()]


def is_admin_member(org: str, author: str) -> bool:
    """Check whether the actor has 'admin' role in the org via the GitHub API.

    Uses 'gh api orgs/<org>/memberships/<author> --jq .role' to perform
    the members:read check.

    Args:
        org: GitHub organization name.
        author: GitHub username of the PR author.

    Returns:
        True if the actor has the 'admin' role; False otherwise.

    Raises:
        AuthorizationError: If the gh API call fails (fail-closed).
    """
    gh_binary = shutil.which("gh")
    if gh_binary is None:
        raise AuthorizationError(
            "ERROR: 'gh' CLI binary not found on PATH. "
            "Ensure the GitHub CLI is installed (run 'make tools-ensure')."
        )
    try:
        result = subprocess.run(
            [gh_binary, "api", f"orgs/{org}/memberships/{author}", "--jq", ".role"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise AuthorizationError(
            f"ERROR: Failed to verify org membership for actor '{author}' in org '{org}'. "
            f"gh API exited with code {exc.returncode}. "
            "Ensure the GitHub App has the 'members:read' permission and the actor exists."
        ) from exc

    role = result.stdout.strip()
    return role == "admin"


def check_scope_override(
    labels: list[str],
    author: str,
    org: str,
    output_path: str,
) -> None:
    """Authorize the scope-override label and write the result to the output file.

    Args:
        labels: List of PR label names present on the PR.
        author: GitHub username of the PR author.
        org: GitHub organization name.
        output_path: Output file path to write scope_override= to.

    Raises:
        AuthorizationError: If the label is present but the actor is not admin,
                            or if the API call fails (fail-closed).
    """
    if SCOPE_OVERRIDE_LABEL not in labels:
        write_output(output_path, OUTPUT_KEY_SCOPE_OVERRIDE, "false")
        return

    # Label is present -- verify the actor is an org admin (fail-closed)
    admin = is_admin_member(org=org, author=author)
    if not admin:
        raise AuthorizationError(
            f"ERROR: Actor '{author}' attempted the '{SCOPE_OVERRIDE_LABEL}' override "
            f"but is not an admin of org '{org}'. "
            "Only org admins may use the scope-override label. "
            "Remove the label or request an admin to apply it."
        )

    write_output(output_path, OUTPUT_KEY_SCOPE_OVERRIDE, "true")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Authorize the detect-scope-override label via members:read admin check."
    )
    parser.add_argument(
        "--labels",
        required=True,
        help=(
            "PR label names as the GitHub Actions toJson(labels.*.name) JSON array "
            "(e.g. '[\"detect-scope-override\"]') or a comma-separated string."
        ),
    )
    parser.add_argument(
        "--author",
        required=True,
        help="GitHub username of the PR author.",
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

    label_list = parse_labels(args.labels)

    try:
        check_scope_override(
            labels=label_list,
            author=args.author,
            org=args.org,
            output_path=args.output,
        )
    except AuthorizationError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
