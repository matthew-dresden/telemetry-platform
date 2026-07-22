"""ci_detect_scope -- CI wrapper for the single-scope detection pipeline.

Wraps detect_scope.py for the workflow layer. Honours the admin scope=all
override by emitting a JSON array of every module path (never an empty
MODULE_PATH, per decision D43). For a normal single-scope PR, emits the
detected module path.

Writes scope=, module_path=, and modules= to the --output file so downstream
workflow steps can reference all three unconditionally.

D43 contract:
- scope=all: module_path is empty, modules is a non-empty JSON array.
- scope=module: module_path is set, modules is [].
- scope=config/terragrunt: module_path is empty, modules is [].

Usage (reads changed files from stdin, one per line):
    git diff --name-only origin/main...HEAD | \\
    uv run python -m scripts.ci_detect_scope \\
        --config monorepo-config.json \\
        --scope-override false \\
        --output "$GITHUB_OUTPUT"

Exit codes:
    0 -- valid scope detected and output written
    1 -- scope violation (multi-module or mixed) or D43 contract violation
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from scripts.constants import (
    GH_ERROR_PREFIX,
    OUTPUT_KEY_MODULE_PATH,
    OUTPUT_KEY_MODULES,
    OUTPUT_KEY_SCOPE,
    SCOPE_ALL,
    write_output,
)
from scripts.detect_scope import detect_scope, discover_all_modules, load_config, parse_scope_config


class D43ViolationError(Exception):
    """Raised when scope=all is requested but no modules are discovered (D43 contract)."""


def _changed_files_from_diff_range(diff_range: str) -> list[str]:
    """Return repo-relative changed files for a git diff range (git diff --name-only).

    The diff logic lives here (a Python script), not in a shell pipe in the Makefile recipe,
    per ledger D11. Fails closed: a non-zero git exit raises CalledProcessError, surfacing the
    error rather than silently yielding an empty changeset (which would mis-detect as config).
    """
    completed = subprocess.run(
        ["git", "diff", "--name-only", diff_range],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def write_ci_output(
    output_path: str,
    scope: str,
    module_path: str,
    modules: list[str],
    version_only: bool = False,
) -> None:
    """Write scope=, module_path=, modules=, and version_only= to the output file.

    Args:
        output_path: File path to write to (e.g. $GITHUB_OUTPUT).
        scope: The detected scope string.
        module_path: The module path (empty string when not applicable).
        modules: List of module paths (populated only for scope=all).
        version_only: True when a module-scope change touches only VERSION file(s) -- a
            release-trigger with no code/test change, so the terratest job is skipped for it.
    """
    write_output(output_path, OUTPUT_KEY_SCOPE, scope)
    write_output(output_path, OUTPUT_KEY_MODULE_PATH, module_path)
    write_output(output_path, OUTPUT_KEY_MODULES, json.dumps(modules))
    write_output(output_path, "version_only", "true" if version_only else "false")


def run_ci_detect_scope(
    changed_files: list[str],
    scope_override: bool,
    config: dict[str, object],
    output_path: str,
    repo_root: str = ".",
) -> dict[str, object]:
    """Run the CI scope detection and write results to the output file.

    Args:
        changed_files: List of changed file paths (repo-relative).
        scope_override: True if the admin override label is active.
        config: Parsed monorepo-config.json as a dict.
        output_path: Output file path to write scope=/module_path=/modules= to.
        repo_root: Repository root directory for module discovery (scope=all).

    Returns:
        {
          'scope': str,
          'module_path': str,   -- empty string when not applicable
          'modules': list[str], -- populated only for scope=all (D43)
          'valid': bool,
          'violations': list[str],
        }

    Raises:
        D43ViolationError: If scope=all is requested but no modules are discovered.

    D43: scope=all must never produce an empty modules array.
    """
    module_roots, terragrunt_root = parse_scope_config(config)

    if scope_override:
        # Admin override: emit scope=all with the full module list (D43).
        all_modules = discover_all_modules(module_roots=module_roots, repo_root=repo_root)
        if not all_modules:
            # D43 violation: scope=all must never emit an empty modules array.
            msg = (
                f"{GH_ERROR_PREFIX}D43 violation: scope=all was requested but no modules "
                "were discovered under the configured module_roots. "
                "Ensure at least one module directory exists under a module_roots prefix."
            )
            print(msg)
            raise D43ViolationError(msg)
        write_ci_output(
            output_path=output_path,
            scope=SCOPE_ALL,
            module_path="",
            modules=all_modules,
        )
        return {
            "scope": SCOPE_ALL,
            "module_path": "",
            "modules": all_modules,
            "valid": True,
            "violations": [],
        }

    result = detect_scope(
        changed_files=changed_files,
        module_roots=module_roots,
        terragrunt_root=terragrunt_root,
    )

    if not result["valid"]:
        violations = result["violations"]
        if isinstance(violations, list):
            for violation in violations:
                print(f"{GH_ERROR_PREFIX}{violation}")
        scope_str = str(result["scope"])
        write_ci_output(
            output_path=output_path,
            scope=scope_str,
            module_path="",
            modules=[],
        )
        return {
            "scope": scope_str,
            "module_path": "",
            "modules": [],
            "valid": False,
            "violations": violations,
        }

    scope: str = str(result["scope"])
    raw_module_path = result["module_path"]
    module_path: str = str(raw_module_path) if raw_module_path else ""
    modules: list[str] = []

    # A module-scope change that touches ONLY VERSION file(s) is a release trigger with no
    # code/test change, so the terratest (tf-test) job is redundant and is skipped for it
    # (the module was already terratest-verified when its code last changed).
    version_only: bool = (
        scope == "module"
        and bool(changed_files)
        and all(f == "VERSION" or f.endswith("/VERSION") for f in changed_files)
    )

    write_ci_output(
        output_path=output_path,
        scope=scope,
        module_path=module_path,
        modules=modules,
        version_only=version_only,
    )

    return {
        "scope": scope,
        "module_path": module_path,
        "modules": modules,
        "version_only": version_only,
        "valid": True,
        "violations": [],
    }


def main() -> None:
    """CLI entry point. Reads changed files from stdin, writes scope output."""
    parser = argparse.ArgumentParser(
        description="CI wrapper for scope detection. Reads changed files from stdin."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to monorepo-config.json",
    )
    parser.add_argument(
        "--scope-override",
        required=True,
        help="'true' to activate the admin scope=all override; 'false' for normal detection.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output file path (e.g. $GITHUB_OUTPUT) to write scope=/module_path=/modules= to.",
    )
    parser.add_argument(
        "--diff-range",
        default="",
        help=(
            "git diff range (e.g. '<before>...HEAD') whose --name-only changed files drive "
            "detection. When empty, changed files are read from stdin (legacy/piped usage)."
        ),
    )
    args = parser.parse_args()

    scope_override_flag = args.scope_override.lower() == "true"

    config = load_config(args.config)

    # D11: compute the changed files in-script from --diff-range (no shell pipe in the recipe).
    # Fall back to stdin when no range is given (legacy piped usage / unit tests).
    if args.diff_range:
        changed_files = _changed_files_from_diff_range(args.diff_range)
    else:
        changed_files = [line.strip() for line in sys.stdin if line.strip()]

    try:
        result = run_ci_detect_scope(
            changed_files=changed_files,
            scope_override=scope_override_flag,
            config=config,
            output_path=args.output,
            repo_root=".",
        )
    except D43ViolationError:
        sys.exit(1)

    if not result["valid"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
