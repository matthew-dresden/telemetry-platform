"""check_version_immutability -- detect hand-edited VERSION pins (B13).

Compares the VERSION file at the current HEAD against the VERSION at BASE_REF.
If the current version differs from the base-ref version AND the current version
is already published as a git tag, the check FAILS with a clear error (B13).

This prevents an already-published version from being silently overwritten by
a hand-edited VERSION file.

Usage (invoked by `make check-version-immutability`):
    uv run python -m scripts.check_version_immutability --base-ref BASE_REF

Arguments:
    --base-ref:      The git ref to compare against (e.g. 'HEAD~1', 'origin/main').
    --scope:         The release scope the VERSION file belongs to: 'config' (the
                     repo-wide root VERSION, default) or 'module' (a per-module
                     VERSION). The tag prefix is derived from the scope, not
                     hardcoded.
    --module-path:   The module path (e.g. 'providers/aws/primitives/kms-key').
                     Required only when --scope=module; ignored for config scope.
    --config:        Path to monorepo-config.json (default: monorepo-config.json),
                     read to resolve config_tag_prefix for config scope.
    --version-file:  Path to the VERSION file (default: VERSION).

Exit codes:
    0 -- version is safe (unchanged or not yet published)
    1 -- immutability violation detected (B13) or hard error
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from scripts.calculate_version import build_tag_prefix
from scripts.constants import GH_ERROR_PREFIX, SCOPE_CONFIG, SCOPE_MODULE
from scripts.detect_scope import load_config

# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


def is_version_already_published(
    existing_tags: list[str],
    tag_prefix: str,
    version: str,
) -> bool:
    """Check whether a version is already published as a git tag.

    Args:
        existing_tags: All git tags available in the repository.
        tag_prefix: The tag prefix for this module (e.g. 'providers/aws/primitives/kms-key/v').
        version: The version string to check (e.g. '0.1.0').

    Returns:
        True if the version already has a published git tag.
    """
    full_tag = f"{tag_prefix}{version}"
    return full_tag in existing_tags


def check_version_immutability(
    current_version: str,
    base_version: str | None,
    existing_tags: list[str],
    tag_prefix: str,
) -> None:
    """Check that a VERSION change does not re-use an already-published version.

    If current_version == base_version, the VERSION file is unchanged -- pass.
    If base_version is None, the VERSION file did not exist at the base ref (it is
    being introduced) -- treated as a change, so the published-tag guard below
    applies. If current_version differs from base_version (or base is None) AND
    current_version is already published as a git tag -- reject (B13).

    Args:
        current_version: The version string in the current working tree.
        base_version: The version string at the base ref (before the change), or
            None when the VERSION file is absent at the base ref.
        existing_tags: All git tags available in the repository.
        tag_prefix: The tag prefix for this module.

    Raises:
        ValueError: If the current version is already published (B13).
    """
    if current_version == base_version:
        return

    if is_version_already_published(
        existing_tags=existing_tags,
        tag_prefix=tag_prefix,
        version=current_version,
    ):
        full_tag = f"{tag_prefix}{current_version}"
        raise ValueError(
            f"ERROR: B13 version immutability violation detected.\n"
            f"The VERSION file was changed from {base_version!r} to {current_version!r},\n"
            f"but version {current_version!r} is already published as git tag {full_tag!r}.\n"
            "An already-published version must never be overwritten. "
            "Use the release process to produce a new version.\n"
            "Remediation: revert the VERSION file to its previous value and let the "
            "release pipeline derive the next version automatically."
        )


def _run_git(cmd: list[str]) -> str:
    """Run a git command and return stripped stdout.

    Args:
        cmd: The git command and arguments.

    Returns:
        The stripped stdout output.

    Raises:
        RuntimeError: If the command exits non-zero.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: Command {cmd!r} exited with code {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _version_file_exists_at_ref(version_file: str, base_ref: str) -> bool:
    """Return True if version_file exists as a tracked blob at base_ref.

    Uses `git cat-file -e <ref>:<path>`, which exits 0 when the blob exists and
    non-zero when it is absent -- a precise existence probe that does not depend on
    parsing git's human-readable error text.

    Args:
        version_file: Path to the VERSION file (relative to repo root).
        base_ref: The git ref to probe.

    Returns:
        True if the file exists at base_ref; False otherwise.
    """
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{base_ref}:{version_file}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _get_version_at_ref(version_file: str, base_ref: str) -> str | None:
    """Get the VERSION file content at the specified git ref.

    When the file does not exist at base_ref (e.g. the very first PR that introduces
    the VERSION file -- base_ref is an initial commit that predates it), returns
    None to signal "no prior version". The B13 published-tag collision guard still
    runs against the current version, so this does not weaken the check; it only
    handles the legitimate file-introduction case instead of hard-erroring.

    Args:
        version_file: Path to the VERSION file (relative to repo root).
        base_ref: The git ref to read the VERSION at.

    Returns:
        The version string at that ref, or None if the file is absent at base_ref.

    Raises:
        RuntimeError: If git show fails for a reason other than the file being absent.
    """
    if not _version_file_exists_at_ref(version_file=version_file, base_ref=base_ref):
        return None
    return _run_git(["git", "show", f"{base_ref}:{version_file}"])


def _fetch_git_tags() -> list[str]:
    """Fetch all git tags from the local repository.

    Returns:
        A list of git tag strings.

    Raises:
        RuntimeError: If git tag fails.
    """
    output = _run_git(["git", "tag", "-l"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def _resolve_tag_prefix(scope: str, module_path: str, config_path: str) -> str:
    """Resolve the tag prefix for the given scope.

    For scope=config the prefix is derived from monorepo-config.json's
    config_tag_prefix (e.g. 'monorepo-config/v'); for scope=module it is derived
    from the module path (e.g. 'providers/aws/primitives/kms-key/v'). The
    derivation reuses calculate_version.build_tag_prefix so the immutability check
    and the version-derivation pipeline share one tag-prefix scheme (DRY).

    Args:
        scope: 'config' or 'module'.
        module_path: The module path (required only for module scope).
        config_path: Path to monorepo-config.json (read for config scope).

    Returns:
        The tag prefix string ending with '/v'.

    Raises:
        ValueError: If scope is unsupported, module_path is empty for module scope,
            or config_tag_prefix is missing for config scope.
        FileNotFoundError: If the config file is absent for config scope.
    """
    config_tag_prefix = ""
    if scope == SCOPE_CONFIG:
        config = load_config(config_path)
        prefix = config.get("config_tag_prefix")
        if not prefix or not isinstance(prefix, str):
            raise ValueError(
                "ERROR: 'config_tag_prefix' is missing or empty in "
                f"{config_path!r}.\nAdd a 'config_tag_prefix' key (e.g. "
                "'monorepo-config') so the repo-wide VERSION immutability check "
                "can derive its tag prefix."
            )
        config_tag_prefix = prefix
    return build_tag_prefix(
        scope=scope,
        module_path=module_path,
        config_tag_prefix=config_tag_prefix,
    )


def main_check(
    version_file: str,
    base_ref: str,
    module_path: str,
    scope: str = SCOPE_CONFIG,
    config_path: str = "monorepo-config.json",
) -> None:
    """Perform the version immutability check.

    Args:
        version_file: Path to the VERSION file.
        base_ref: The git ref to compare the current VERSION against.
        module_path: The module path for module-scope tag-prefix derivation.
        scope: 'config' (repo-wide root VERSION, default) or 'module'.
        config_path: Path to monorepo-config.json (for config-scope prefix).

    Raises:
        FileNotFoundError: If the VERSION file does not exist.
        RuntimeError: If any git command fails.
        ValueError: If the immutability check fails (B13) or the prefix cannot be
            derived for the given scope.
    """
    import pathlib

    vpath = pathlib.Path(version_file)
    if not vpath.exists():
        raise FileNotFoundError(
            f"ERROR: VERSION file not found at {version_file!r}.\n"
            "Ensure the repository has been checked out before running this check."
        )

    current_version = vpath.read_text().strip()

    # Get the version at the base ref.
    base_version = _get_version_at_ref(version_file=version_file, base_ref=base_ref)

    # Get all existing tags.
    existing_tags = _fetch_git_tags()

    # Derive the tag prefix from the release scope (config or module).
    tag_prefix = _resolve_tag_prefix(
        scope=scope,
        module_path=module_path,
        config_path=config_path,
    )

    check_version_immutability(
        current_version=current_version,
        base_version=base_version,
        existing_tags=existing_tags,
        tag_prefix=tag_prefix,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for check_version_immutability.

    Exit codes:
        0 -- VERSION is safe
        1 -- B13 violation or hard error
    """
    parser = argparse.ArgumentParser(
        description="Detect hand-edited VERSION pins to already-published versions (B13)."
    )
    parser.add_argument(
        "--base-ref",
        required=True,
        help="Git ref to compare against (e.g. 'HEAD~1', 'origin/main').",
    )
    parser.add_argument(
        "--scope",
        default=SCOPE_CONFIG,
        choices=[SCOPE_CONFIG, SCOPE_MODULE],
        help=(
            "Release scope the VERSION file belongs to: 'config' (the repo-wide "
            "root VERSION, default) or 'module' (a per-module VERSION)."
        ),
    )
    parser.add_argument(
        "--module-path",
        default="",
        help=(
            "Module path for module-scope tag-prefix derivation "
            "(e.g. providers/aws/primitives/kms-key). Required only when --scope=module."
        ),
    )
    parser.add_argument(
        "--config",
        default="monorepo-config.json",
        help="Path to monorepo-config.json (read to resolve config_tag_prefix).",
    )
    parser.add_argument(
        "--version-file",
        default="VERSION",
        help="Path to the VERSION file (default: VERSION).",
    )
    args = parser.parse_args()

    if args.scope == SCOPE_MODULE and not args.module_path:
        print(
            f"{GH_ERROR_PREFIX}ERROR: --module-path is required when --scope=module.\n"
            "Provide the module path for tag-prefix derivation.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        main_check(
            version_file=args.version_file,
            base_ref=args.base_ref,
            module_path=args.module_path,
            scope=args.scope,
            config_path=args.config,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
