"""detect_scope -- single-scope detection for the telemetry-platform monorepo.

Classifies a changed-file set into exactly one of: module, terragrunt, config,
multi-module (invalid), or mixed (invalid). A PR must touch exactly one scope;
spanning more than one scope fails fast (AC-15 / docs/release-pipeline.md).

Usage (reads changed files from stdin, one per line):
    git diff --name-only origin/main...HEAD | uv run python -m scripts.detect_scope \\
        --config monorepo-config.json

Exit codes:
    0 -- valid single-scope changeset
    1 -- invalid (multi-module or mixed scope)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.constants import (
    SCOPE_CONFIG,
    SCOPE_MIXED,
    SCOPE_MODULE,
    SCOPE_MULTI_MODULE,
    SCOPE_TERRAGRUNT,
)


class ScopeViolationError(Exception):
    """Raised when a changeset spans more than one scope."""


def _find_module_path(file_path: str, module_root: str) -> str | None:
    """Return the module directory for a file under the given module_root.

    The module directory is the first path component after the module_root
    that is not a reserved organizational directory. Reserved components
    like 'primitives' or 'references' are organizational, never module leaves.

    Returns None if the file has no nested module directory (e.g., a file
    placed directly under the module root falls through to config).
    """
    remainder = file_path[len(module_root) :]
    # Split on the first separator to get the first component
    parts = remainder.split("/", 1)
    leaf = parts[0]
    if not leaf:
        return None
    # The first component is the module leaf directory name; prepend the root
    return module_root.rstrip("/") + "/" + leaf


def parse_scope_config(config: dict[str, object]) -> tuple[list[str], str]:
    """Extract and validate module_roots and terragrunt_root from the parsed config.

    Args:
        config: Parsed monorepo-config.json as a dict.

    Returns:
        A tuple of (module_roots, terragrunt_root) where module_roots is a list
        of module root prefix strings and terragrunt_root is the terragrunt prefix.

    Raises:
        ValueError: If 'module_roots' is not a list.
    """
    module_roots_raw = config["module_roots"]
    if not isinstance(module_roots_raw, list):
        raise ValueError(
            f"ERROR: 'module_roots' in config must be a list, "
            f"got {type(module_roots_raw).__name__}."
        )
    module_roots: list[str] = [str(r) for r in module_roots_raw]
    terragrunt_root: str = str(config["terragrunt_root"])
    return module_roots, terragrunt_root


def detect_scope(
    changed_files: list[str],
    module_roots: list[str],
    terragrunt_root: str,
) -> dict[str, object]:
    """Classify a changed-file set into exactly one scope.

    Args:
        changed_files: List of changed file paths (repo-relative).
        module_roots: List of module root prefixes from monorepo-config.json.
        terragrunt_root: The terragrunt live-tree root prefix (e.g. 'terragrunt/').

    Returns:
        {
          'scope': 'module' | 'terragrunt' | 'config' | 'multi-module' | 'mixed',
          'module_path': str | None,   -- set only when scope == 'module'
          'modules': list[str],        -- always [] from detect_scope; populated by ci_detect_scope
          'valid': bool,
          'violations': list[str],
        }

    Algorithm (docs/release-pipeline.md):
    1. Empty changeset -> scope=config, valid=True.
    2. Classify each file into terragrunt, module (by root prefix), or config.
    3. Apply decision table: single scope -> valid; multi or mixed -> invalid.
    """
    if not changed_files:
        return {
            "scope": SCOPE_CONFIG,
            "module_path": None,
            "modules": [],
            "valid": True,
            "violations": [],
        }

    terragrunt_files: list[str] = []
    module_files: dict[str, list[str]] = {}
    config_files: list[str] = []

    for file_path in changed_files:
        if file_path.startswith(terragrunt_root):
            # Documentation files under the terragrunt tree (e.g. a relocated
            # runbook or a unit README) are not deployable units, so they must
            # not affect scope: a docs-only change must never read as terragrunt
            # (or mixed) scope or trigger an apply lane with an empty unit set
            # (rejected, D3). They are ignored for classification -- a real unit
            # change in the same PR still classifies the PR as terragrunt.
            if file_path.endswith(".md"):
                continue
            terragrunt_files.append(file_path)
            continue

        matched_root: str | None = None
        for root in module_roots:
            if file_path.startswith(root):
                matched_root = root
                break

        if matched_root is not None:
            module_path = _find_module_path(file_path, matched_root)
            if module_path is None:
                # File directly under a module root with no nested module dir
                config_files.append(file_path)
            else:
                module_files.setdefault(module_path, []).append(file_path)
        else:
            config_files.append(file_path)

    num_modules = len(module_files)
    has_terragrunt = len(terragrunt_files) > 0
    has_config = len(config_files) > 0

    # Decision table (docs/release-pipeline.md)
    if num_modules >= 2:
        violations = [
            f"ERROR: PR spans {num_modules} module scopes: "
            + ", ".join(sorted(module_files.keys()))
            + ". A PR must touch exactly one scope (AC-15)."
        ]
        return {
            "scope": SCOPE_MULTI_MODULE,
            "module_path": None,
            "modules": [],
            "valid": False,
            "violations": violations,
        }

    if num_modules >= 1 and (has_terragrunt or has_config):
        touched = []
        if num_modules >= 1:
            touched.append(f"module ({', '.join(sorted(module_files.keys()))})")
        if has_terragrunt:
            touched.append("terragrunt")
        if has_config:
            touched.append("config")
        violations = [
            f"ERROR: PR spans mixed scopes: {'; '.join(touched)}. "
            "A PR must touch exactly one scope (AC-15)."
        ]
        return {
            "scope": SCOPE_MIXED,
            "module_path": None,
            "modules": [],
            "valid": False,
            "violations": violations,
        }

    if has_terragrunt and has_config:
        violations = [
            "ERROR: PR spans mixed scopes: terragrunt and config. "
            "A PR must touch exactly one scope (AC-15)."
        ]
        return {
            "scope": SCOPE_MIXED,
            "module_path": None,
            "modules": [],
            "valid": False,
            "violations": violations,
        }

    if num_modules == 1 and not has_terragrunt and not has_config:
        module_path = next(iter(module_files))
        return {
            "scope": SCOPE_MODULE,
            "module_path": module_path,
            "modules": [],
            "valid": True,
            "violations": [],
        }

    if has_terragrunt and not has_config and num_modules == 0:
        return {
            "scope": SCOPE_TERRAGRUNT,
            "module_path": None,
            "modules": [],
            "valid": True,
            "violations": [],
        }

    # 0 modules, 0 terragrunt, >=0 config -> config (valid)
    return {
        "scope": SCOPE_CONFIG,
        "module_path": None,
        "modules": [],
        "valid": True,
        "violations": [],
    }


def discover_all_modules(module_roots: list[str], repo_root: str = ".") -> list[str]:
    """Discover every real module directory under the configured module roots.

    Used by ci_detect_scope for the scope=all override (ledger D43).

    A real module is an immediate subdirectory of a module root that contains a
    ``main.tf`` file. Subdirectories without a ``main.tf`` (e.g. the shared
    ``providers/aws/primitives/tests`` fixtures directory) are organizational,
    not module leaves, and must be skipped so that scope=all module-validate does
    not attempt to validate a non-module directory.

    Args:
        module_roots: List of module root prefixes from monorepo-config.json.
        repo_root: Repository root directory to resolve paths against.

    Returns:
        Sorted list of module paths (repo-relative, e.g. 'providers/aws/primitives/kms-key').
    """
    repo_path = Path(repo_root)
    modules: list[str] = []
    for root in module_roots:
        root_path = repo_path / root.rstrip("/")
        if not root_path.exists():
            continue
        for entry in sorted(root_path.iterdir()):
            if not entry.is_dir():
                continue
            # A real module is identified by the presence of a main.tf entrypoint.
            # Directories without one (fixtures, shared test dirs) are skipped.
            if not (entry / "main.tf").is_file():
                continue
            modules.append(root.rstrip("/") + "/" + entry.name)
    return sorted(modules)


def load_config(config_path: str) -> dict[str, object]:
    """Load and return the monorepo-config.json content.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config file is not valid JSON.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"ERROR: Config file not found: {config_path}\n"
            "Ensure monorepo-config.json exists at the repo root."
        )
    with path.open() as fh:
        try:
            result: dict[str, object] = json.load(fh)
            return result
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"ERROR: Failed to parse config file {config_path}: {exc}\n"
                "Ensure monorepo-config.json is valid JSON."
            ) from exc


def main() -> None:
    """CLI entry point. Reads changed files from stdin, writes scope result to stdout."""
    parser = argparse.ArgumentParser(
        description="Detect the scope of a PR from its changed-file set."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to monorepo-config.json",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    module_roots, terragrunt_root = parse_scope_config(config)

    changed_files = [line.strip() for line in sys.stdin if line.strip()]

    result = detect_scope(
        changed_files=changed_files,
        module_roots=module_roots,
        terragrunt_root=terragrunt_root,
    )

    print(json.dumps(result))

    if not result["valid"]:
        violations = result["violations"]
        if isinstance(violations, list):
            for violation in violations:
                print(violation, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
