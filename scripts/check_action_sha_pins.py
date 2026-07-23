"""SHA-pin gate for GitHub Actions workflow files.

Backs the Makefile target:
    make actions-sha-pin-check -> uv run python -m scripts.check_action_sha_pins

Rule (docs/release-pipeline.md):
    Every third-party uses: reference must be a 40-character commit SHA
    carrying a trailing version comment (e.g. @<sha40>  # vX.Y.Z).
    First-party local actions (uses: ./<path>) are exempt from this rule.

Architecture:
- check_file(): library function that validates a single workflow YAML file.
- main(): CLI entry point; calls sys.exit only here.
- SHAPinError: specific exception for a failed SHA-pin check.
"""

from __future__ import annotations

import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Matches a uses: line and captures the reference after @.
# Group 1: the full action reference (owner/action@ref or ./local)
# We use a line-oriented regex to capture uses: patterns from YAML text.
_USES_PATTERN: re.Pattern[str] = re.compile(
    r"""
    ^\s*-?\s*uses:\s*         # YAML key: - uses: or uses:
    (?P<ref>[^\s#]+)          # The action reference (no spaces, no comment)
    (?:\s*\#\s*(?P<comment>.+))?  # Optional: trailing comment after #
    \s*$
    """,
    re.VERBOSE,
)

# A 40-character hexadecimal SHA (exactly 40 chars, all hex digits).
_SHA40_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-fA-F]{40}$")

# Trailing version comment: at least one non-whitespace character after '#'.
_VERSION_COMMENT_PATTERN: re.Pattern[str] = re.compile(r"\S")


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class SHAPinError(Exception):
    """Raised when a uses: reference fails the SHA-pin rule.

    Attributes:
        file_path: Path to the workflow file containing the violation.
        line_number: 1-based line number of the offending uses: reference.
        ref: The unpinned or non-compliant action reference.
        reason: Human-readable explanation of why the pin check failed.
    """

    def __init__(
        self,
        file_path: pathlib.Path,
        line_number: int,
        ref: str,
        reason: str,
    ) -> None:
        self.file_path = file_path
        self.line_number = line_number
        self.ref = ref
        self.reason = reason
        super().__init__(
            f"ERROR: SHA-pin violation in {file_path}:{line_number}: "
            f"uses: {ref!r} -- {reason}. "
            f"Remediation: pin the action to a 40-char commit SHA and add a "
            f"trailing version comment, e.g. @<sha40>  # vX.Y.Z"
        )


# ---------------------------------------------------------------------------
# Core validation logic
# ---------------------------------------------------------------------------


def _is_first_party(ref: str) -> bool:
    """Return True if the reference is a first-party local action (./).

    First-party actions begin with './' and are exempt from the SHA-pin rule.

    Args:
        ref: The action reference string from a uses: key.

    Returns:
        True if the reference is a local path action.
    """
    return ref.startswith("./")


def _extract_sha_from_ref(ref: str) -> str | None:
    """Extract the SHA portion from an action ref (the part after @).

    Args:
        ref: Full action reference, e.g. 'actions/checkout@abc123...'.

    Returns:
        The string after '@', or None if '@' is not present.
    """
    if "@" not in ref:
        return None
    return ref.split("@", 1)[1]


def check_file(file_path: pathlib.Path) -> list[str]:
    """Validate all uses: references in a workflow YAML file.

    Returns a list of violation messages (each prefixed with 'ERROR:').
    Returns an empty list if all references are compliant.

    Args:
        file_path: Absolute path to the workflow YAML file to validate.

    Returns:
        List of violation message strings. Empty means compliant.

    Raises:
        FileNotFoundError: If file_path does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(
            f"Workflow file not found at {file_path}. "
            "The file must exist before SHA-pin validation can proceed."
        )

    lines = file_path.read_text(encoding="utf-8").splitlines()
    violations: list[str] = []

    for line_number, line in enumerate(lines, start=1):
        match = _USES_PATTERN.match(line)
        if match is None:
            continue

        ref = match.group("ref")
        comment = match.group("comment")

        # First-party actions are exempt.
        if _is_first_party(ref):
            continue

        sha_part = _extract_sha_from_ref(ref)

        # Missing @ separator.
        if sha_part is None:
            violation = SHAPinError(
                file_path=file_path,
                line_number=line_number,
                ref=ref,
                reason="no '@' separator; must be pinned to a 40-char SHA",
            )
            violations.append(str(violation))
            continue

        # Must be exactly 40 hex characters.
        if not _SHA40_PATTERN.match(sha_part):
            violation = SHAPinError(
                file_path=file_path,
                line_number=line_number,
                ref=ref,
                reason=(
                    f"ref after '@' is {sha_part!r} ({len(sha_part)} chars); "
                    "must be exactly a 40-character hex commit SHA"
                ),
            )
            violations.append(str(violation))
            continue

        # Must have a trailing version comment.
        if comment is None or not _VERSION_COMMENT_PATTERN.search(comment):
            violation = SHAPinError(
                file_path=file_path,
                line_number=line_number,
                ref=ref,
                reason=(
                    "40-char SHA is present but no trailing version comment found; "
                    "add a comment like '  # vX.Y.Z' after the SHA"
                ),
            )
            violations.append(str(violation))
            continue

    return violations


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(workflows_dir: str | None = None) -> None:
    """Scan all YAML files in the workflows directory for SHA-pin violations.

    Args:
        workflows_dir: Path to the .github/workflows directory. Defaults to
            '.github/workflows' relative to the repository root (resolved
            from this module's parent).

    Exits:
        0 if all workflow files are compliant (or directory is empty).
        1 if any violation is found; each violation is printed to stderr.
        2 if the workflows directory does not exist.
    """
    if workflows_dir is None:
        repo_root = pathlib.Path(__file__).parent.parent
        resolved_dir = repo_root / ".github" / "workflows"
    else:
        resolved_dir = pathlib.Path(workflows_dir)

    if not resolved_dir.exists():
        print(
            f"ERROR: workflows directory not found at {resolved_dir}. "
            "Ensure .github/workflows exists before running the SHA-pin gate.",
            file=sys.stderr,
        )
        sys.exit(2)

    all_violations: list[str] = []
    yaml_files = sorted(resolved_dir.glob("*.yml")) + sorted(resolved_dir.glob("*.yaml"))

    for workflow_file in yaml_files:
        file_violations = check_file(workflow_file)
        all_violations.extend(file_violations)

    if all_violations:
        for violation in all_violations:
            print(violation, file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
