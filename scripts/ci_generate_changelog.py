"""ci_generate_changelog -- CI entrypoint for changelog generation.

Reads CLI arguments (or environment variables), generates the changelog for the
derived bump, writes it to a file under --changelog-dir, and emits changelog_path=
to the GITHUB_OUTPUT file.

Usage (invoked by `make generate-changelog`):
    uv run python -m scripts.ci_generate_changelog \\
        --version "0.1.0" \\
        --module-path "providers/aws/primitives/kms-key" \\
        --bump-type "minor" \\
        --pr-title "feat: add key rotation" \\
        --changelog-dir "/tmp/changelogs" \\
        --output "$GITHUB_OUTPUT"

Exit codes:
    0 -- success; changelog_path written to --output
    1 -- error; details on stderr
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.constants import (
    GH_ERROR_PREFIX,
    OUTPUT_KEY_CHANGELOG_PATH,
    write_output,
)
from scripts.generate_changelog import generate_changelog


def _sanitize_path_component(value: str) -> str:
    """Replace path separators with dashes for safe use in a filename.

    Args:
        value: A string that may contain '/' (e.g. a module path).

    Returns:
        The value with '/' replaced by '-'.
    """
    return value.replace("/", "-")


def main() -> None:
    """CLI entry point for ci_generate_changelog."""
    parser = argparse.ArgumentParser(description="Generate a changelog entry for a module release.")
    parser.add_argument(
        "--version",
        required=True,
        help="The new semver version string (e.g. '0.1.0').",
    )
    parser.add_argument(
        "--module-path",
        required=False,
        default="",
        help="The module path (e.g. 'providers/aws/primitives/kms-key'). Empty for config scope.",
    )
    parser.add_argument(
        "--bump-type",
        required=True,
        choices=["major", "minor", "patch"],
        help="The semver bump type.",
    )
    parser.add_argument(
        "--pr-title",
        required=True,
        help="The conventional-commit PR title (from the GitHub API).",
    )
    parser.add_argument(
        "--changelog-dir",
        required=False,
        default="",
        help="Directory to write the changelog file. Defaults to the module path or repo root.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the GITHUB_OUTPUT file to append changelog_path=.",
    )
    args = parser.parse_args()

    # The changelog entry uses the conventional-commit SUBJECT line. When the
    # release passes the squash-commit message as --pr-title it may include a
    # multi-line body; the entry must embed only the subject (and the section is
    # derived from it), matching ci_calculate_version's subject-line handling.
    pr_subject_lines = [line for line in args.pr_title.splitlines() if line.strip()]
    pr_subject = pr_subject_lines[0] if pr_subject_lines else args.pr_title

    try:
        changelog_content = generate_changelog(
            version=args.version,
            module_path=args.module_path,
            bump_type=args.bump_type,
            pr_title=pr_subject,
        )
    except ValueError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    # Determine where to write the changelog file.
    if args.changelog_dir:
        changelog_dir = Path(args.changelog_dir)
    elif args.module_path:
        changelog_dir = Path(args.module_path)
    else:
        changelog_dir = Path(".")

    try:
        changelog_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"{GH_ERROR_PREFIX}ERROR: Failed to create changelog directory "
            f"{str(changelog_dir)!r}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Construct a safe filename from module path and version.
    safe_module = _sanitize_path_component(args.module_path) if args.module_path else "config"
    changelog_filename = f"CHANGELOG-{safe_module}-{args.version}.md"
    changelog_path = changelog_dir / changelog_filename

    try:
        changelog_path.write_text(changelog_content)
    except OSError as exc:
        print(
            f"{GH_ERROR_PREFIX}ERROR: Failed to write changelog to {str(changelog_path)!r}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Write the changelog_path to GITHUB_OUTPUT.
    try:
        write_output(args.output, OUTPUT_KEY_CHANGELOG_PATH, str(changelog_path))
    except OSError as exc:
        print(
            f"{GH_ERROR_PREFIX}ERROR: Failed to write to output file {args.output!r}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"{OUTPUT_KEY_CHANGELOG_PATH}={changelog_path}")


if __name__ == "__main__":
    main()
