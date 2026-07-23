"""Makefile catalog printer -- backs the 'make help' target.

Reads the repo-root Makefile and prints all target names declared in .PHONY
lines, providing a quick reference of the available make targets.

Architecture:
- print_help(): library function that reads the Makefile and writes to stdout.
- main(): CLI entry point; calls sys.exit only here.
"""

from __future__ import annotations

import pathlib
import re
import sys


def print_help(makefile_path: str | None = None) -> None:
    """Read the Makefile and print all .PHONY targets to stdout.

    Args:
        makefile_path: Path to the Makefile to read. Defaults to the repo-root
            Makefile (one directory above this script's parent).

    Raises:
        FileNotFoundError: If the Makefile does not exist at the resolved path.
    """
    if makefile_path is None:
        repo_root = pathlib.Path(__file__).parent.parent
        resolved_path = repo_root / "Makefile"
    else:
        resolved_path = pathlib.Path(makefile_path)

    if not resolved_path.exists():
        raise FileNotFoundError(
            f"ERROR: Makefile not found at {resolved_path}. "
            f"Remediation: ensure the Makefile exists at the repository root."
        )

    content = resolved_path.read_text(encoding="utf-8")

    # Collect all targets from .PHONY lines
    phony_targets: list[str] = []
    phony_pattern = re.compile(r"^\.PHONY:\s*(.+)$", re.MULTILINE)
    for match in phony_pattern.finditer(content):
        targets_raw = match.group(1)
        targets = targets_raw.split()
        phony_targets.extend(targets)

    # Also collect bare target lines (target:) as a fallback
    target_pattern = re.compile(r"^([a-zA-Z0-9_-][a-zA-Z0-9_.-]*):", re.MULTILINE)
    bare_targets: list[str] = [m.group(1) for m in target_pattern.finditer(content)]

    # Merge: prefer PHONY targets, supplement with bare targets
    seen: set[str] = set()
    all_targets: list[str] = []
    for target in phony_targets + bare_targets:
        if target not in seen:
            seen.add(target)
            all_targets.append(target)

    sys.stdout.write("Available make targets:\n")
    for target in all_targets:
        sys.stdout.write(f"  {target}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(makefile_path: str | None = None) -> None:
    """Print available make targets from the Makefile.

    Usage:
        uv run python -m scripts.make_help

    Exits:
        0 on success.
        1 if the Makefile is not found or cannot be read.
    """
    try:
        print_help(makefile_path=makefile_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
