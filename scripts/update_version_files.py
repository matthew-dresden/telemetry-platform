"""update_version_files -- write the derived version into module version files.

For scope=module: writes <module_path>/VERSION.
For scope=config: writes the repo-root VERSION.

Fails loudly (FileNotFoundError) if the target VERSION file does not exist,
so a missing file is never silently skipped.

Usage (invoked by `make update-version`):
    uv run python -m scripts.update_version_files \\
        --scope module \\
        --module-path providers/aws/primitives/kms-key \\
        --version 0.1.0 \\
        --repo-root .

Exit codes:
    0 -- version file updated successfully
    1 -- error (missing file, invalid arguments, etc.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.constants import GH_ERROR_PREFIX, SCOPE_CONFIG, SCOPE_MODULE


def update_version_files(
    scope: str,
    module_path: str,
    version: str,
    repo_root: str = ".",
) -> None:
    """Write the new version into the appropriate VERSION file.

    For scope=module: writes to <repo_root>/<module_path>/VERSION.
    For scope=config: writes to <repo_root>/VERSION.

    Args:
        scope: 'module' or 'config'. Other values raise ValueError.
        module_path: The module directory path. Required when scope='module'.
        version: The new semver version string to write.
        repo_root: The repository root directory. Defaults to '.'.

    Raises:
        FileNotFoundError: If the target VERSION file does not exist.
        ValueError: If scope is unsupported or module_path is empty for scope=module.
    """
    root = Path(repo_root)

    if scope == SCOPE_MODULE:
        if not module_path:
            raise ValueError(
                "ERROR: module_path must be non-empty when scope='module'.\n"
                "Ensure the scope-detection step emits a non-empty module_path."
            )
        version_file = root / module_path / "VERSION"
    elif scope == SCOPE_CONFIG:
        version_file = root / "VERSION"
    else:
        raise ValueError(
            f"ERROR: Unsupported scope {scope!r} for update_version_files.\n"
            "Supported scopes: 'module', 'config'."
        )

    if not version_file.exists():
        raise FileNotFoundError(
            f"ERROR: VERSION file not found: {version_file}\n"
            f"Expected a VERSION file at {version_file} for scope={scope!r}, "
            f"module_path={module_path!r}.\n"
            "Ensure the module has a VERSION file before running the release pipeline."
        )

    version_file.write_text(f"{version}\n")


def main() -> None:
    """CLI entry point for update_version_files."""
    parser = argparse.ArgumentParser(
        description="Write the new version into the module or repo-root VERSION file."
    )
    parser.add_argument(
        "--scope",
        required=True,
        choices=["module", "config"],
        help="The release scope ('module' or 'config').",
    )
    parser.add_argument(
        "--module-path",
        required=False,
        default="",
        help="The module directory path (required when --scope=module).",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="The new semver version string to write.",
    )
    parser.add_argument(
        "--repo-root",
        required=False,
        default=".",
        help="The repository root directory (defaults to '.').",
    )
    args = parser.parse_args()

    try:
        update_version_files(
            scope=args.scope,
            module_path=args.module_path,
            version=args.version,
            repo_root=args.repo_root,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Version files updated to {args.version} for scope={args.scope!r}.")


if __name__ == "__main__":
    main()
