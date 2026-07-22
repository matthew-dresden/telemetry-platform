"""validate_pr_title -- parse a conventional-commit PR title and map to semver bump.

D17 contract: the bump type MUST be derived from the PR title read via the GitHub API,
never from the editable squash-merge subject line. This module is a pure parsing library;
the caller (ci_calculate_version.py) is responsible for fetching the API title.

Usage:
    uv run python -m scripts.validate_pr_title --title "feat(kms-key): add key rotation"

Exit codes:
    0 -- valid conventional-commit title
    1 -- invalid title (not parseable)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum

from scripts.constants import (
    GH_ERROR_PREFIX,
    MINOR_TYPES,
    VALID_COMMIT_TYPES,
)

# Conventional-commit pattern: <type>[(<scope>)][!]: <description>
# - type: one of the recognized commit types
# - scope: optional parenthesized scope identifier
# - !: optional breaking-change marker -> major bump
# - description: non-empty remainder after ": "
_CC_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<type>[a-z]+)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?"
    r":\s+(?P<description>.+)$"
)


class BumpType(Enum):
    """Semver bump type derived from the conventional-commit PR title."""

    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


@dataclass(frozen=True)
class ParsedTitle:
    """Result of parsing a conventional-commit PR title.

    Attributes:
        commit_type: The parsed conventional-commit type (e.g. 'feat', 'fix').
        scope: Optional scope parsed from parentheses (e.g. 'kms-key'), or None.
        breaking: True when the title contains '!' (breaking-change marker).
        description: The commit description text after the type/scope/colon.
        bump: The derived semver bump type.
    """

    commit_type: str
    scope: str | None
    breaking: bool
    description: str
    bump: BumpType


def parse_pr_title(title: str) -> ParsedTitle:
    """Parse a conventional-commit PR title and derive the semver bump type.

    D17: this function is the single source of bump derivation. The caller must
    supply the title as fetched from the GitHub API (not the squash subject) to
    satisfy the D17 anti-forgery requirement.

    Args:
        title: The PR title string (e.g. 'feat(kms-key): add key rotation').

    Returns:
        ParsedTitle with the parsed fields and derived BumpType.

    Raises:
        ValueError: If the title does not match the conventional-commit pattern
            or if the commit type is not in VALID_COMMIT_TYPES.
    """
    if not title or not title.strip():
        raise ValueError(
            "ERROR: PR title is empty.\n"
            "A conventional-commit title is required (e.g. 'feat: add rotation').\n"
            "Ensure the PR title follows the conventional-commit format."
        )

    match = _CC_PATTERN.match(title.strip())
    if match is None:
        raise ValueError(
            f"ERROR: PR title {title!r} does not match the conventional-commit pattern.\n"
            "Expected format: <type>[(<scope>)][!]: <description>\n"
            f"Valid types: {sorted(VALID_COMMIT_TYPES)}\n"
            "Example: 'feat(kms-key): add key rotation support'"
        )

    commit_type = match.group("type")
    scope = match.group("scope")
    breaking = match.group("breaking") == "!"
    description = match.group("description")

    if commit_type not in VALID_COMMIT_TYPES:
        raise ValueError(
            f"ERROR: Unrecognized commit type {commit_type!r} in title {title!r}.\n"
            f"Valid types: {sorted(VALID_COMMIT_TYPES)}\n"
            "Use a recognized conventional-commit type."
        )

    bump = _derive_bump(commit_type=commit_type, breaking=breaking)

    return ParsedTitle(
        commit_type=commit_type,
        scope=scope,
        breaking=breaking,
        description=description,
        bump=bump,
    )


def _derive_bump(commit_type: str, breaking: bool) -> BumpType:
    """Derive the semver bump type from commit type and breaking flag.

    Args:
        commit_type: The parsed conventional-commit type.
        breaking: True when '!' is present in the title.

    Returns:
        BumpType.MAJOR for breaking changes, MINOR for feature types,
        PATCH for fix/maintenance types.
    """
    if breaking:
        return BumpType.MAJOR
    if commit_type in MINOR_TYPES:
        return BumpType.MINOR
    return BumpType.PATCH


def main() -> None:
    """CLI entry point for validate_pr_title.

    Parses --title and exits 0 on valid, 1 on invalid.
    Prints the bump type and parsed fields to stdout on success.
    Prints ::error:: to stderr on failure.
    """
    parser = argparse.ArgumentParser(
        description="Validate a conventional-commit PR title and derive the semver bump."
    )
    parser.add_argument(
        "--title",
        required=True,
        help="The PR title to validate (fetched from the GitHub API, not the squash subject).",
    )
    args = parser.parse_args()

    try:
        result = parse_pr_title(args.title)
    except ValueError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    print(f"bump={result.bump.value}")
    print(f"type={result.commit_type}")
    if result.scope:
        print(f"scope={result.scope}")
    print(f"breaking={str(result.breaking).lower()}")
    print(f"description={result.description}")


if __name__ == "__main__":
    main()
