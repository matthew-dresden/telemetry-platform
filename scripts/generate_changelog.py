"""generate_changelog -- render the changelog for a derived bump.

Pure library function: generates a changelog entry given the version, module path,
bump type, and PR title. The changelog follows conventional-commit section mapping
from constants.COMMIT_TYPE_TO_SECTION.

The CI entrypoint that wires environment inputs and writes the changelog file
is ci_generate_changelog.py.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from scripts.constants import COMMIT_TYPE_TO_SECTION

# Conventional-commit pattern for parsing the PR title type.
_CC_TYPE_PATTERN: re.Pattern[str] = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?!?:")


def generate_changelog(
    version: str,
    module_path: str,
    bump_type: str,
    pr_title: str,
) -> str:
    """Render a changelog entry for the given version and PR title.

    Args:
        version: The new semver version string (e.g. '0.1.0').
        module_path: The module path (e.g. 'providers/aws/primitives/kms-key')
            or empty string for config scope.
        bump_type: The bump type string ('major', 'minor', or 'patch').
        pr_title: The conventional-commit PR title (used for changelog content).

    Returns:
        A markdown-formatted changelog section string.

    Raises:
        ValueError: If version or pr_title is empty.
    """
    if not version or not version.strip():
        raise ValueError(
            "ERROR: version must be non-empty to generate a changelog entry.\n"
            "Ensure the version derivation step succeeded before generating the changelog."
        )
    if not pr_title or not pr_title.strip():
        raise ValueError(
            "ERROR: pr_title must be non-empty to generate a changelog entry.\n"
            "Ensure the PR title was fetched from the GitHub API (D17) before "
            "generating the changelog."
        )

    # Determine the section header from the commit type.
    section = _derive_section(pr_title=pr_title, bump_type=bump_type)

    # Format the changelog entry.
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    scope_note = f" ({module_path})" if module_path else ""

    lines = [
        f"## [{version}]{scope_note} - {today}",
        "",
        f"### {section}",
        "",
        f"- {pr_title}",
        "",
    ]
    return "\n".join(lines)


def _derive_section(pr_title: str, bump_type: str) -> str:
    """Derive the changelog section header from the PR title's commit type and bump.

    Breaking changes (bump_type='major' or '!' in title) map to 'Breaking Changes'.
    Otherwise, the section is derived from the commit type via COMMIT_TYPE_TO_SECTION.
    Falls back to bump_type-based sections if the type cannot be parsed.

    Args:
        pr_title: The PR title to parse for commit type and breaking flag.
        bump_type: The bump type ('major', 'minor', 'patch').

    Returns:
        The section header string.
    """
    # Breaking changes always go to a dedicated section regardless of commit type.
    if bump_type == "major":
        return "Breaking Changes"

    match = _CC_TYPE_PATTERN.match(pr_title.strip())
    if match:
        commit_type = match.group("type")
        if commit_type in COMMIT_TYPE_TO_SECTION:
            return COMMIT_TYPE_TO_SECTION[commit_type]

    # Fallback based on bump_type
    bump_section_map: dict[str, str] = {
        "minor": "Features",
        "patch": "Bug Fixes",
    }
    return bump_section_map.get(bump_type, "Changes")
