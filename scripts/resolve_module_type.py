"""resolve_module_type -- map a module path to its concrete module type.

Maps a module path to its module type via the provider.*.module_types.*.path_patterns
entries in monorepo-config.json. Downstream matrix jobs use the resolved type to
select the correct validation gauntlet.

Usage:
    uv run python -m scripts.resolve_module_type \\
        --config monorepo-config.json \\
        --module-path providers/aws/primitives/kms-key \\
        --output "$GITHUB_OUTPUT"

Output:
    Prints the bare module type (e.g. 'primitive') to stdout.
    Writes 'module_type=<type>' to the --output file.

Exit codes:
    0 -- module type resolved successfully
    1 -- module_path matches no configured path_pattern (::error:: emitted)
"""

from __future__ import annotations

import argparse
import fnmatch
import sys

from scripts.constants import GH_ERROR_PREFIX, OUTPUT_KEY_MODULE_TYPE, write_output
from scripts.detect_scope import load_config


class ModuleTypeError(Exception):
    """Raised when a module_path matches no configured path_pattern."""


def resolve_module_type(module_path: str, config: dict[str, object]) -> str:
    """Resolve the module type for a given module path.

    Iterates over all provider.*.module_types entries and checks
    path_patterns using fnmatch glob matching.

    Args:
        module_path: Repo-relative module directory path.
        config: Parsed monorepo-config.json as a dict.

    Returns:
        The module type string (e.g. 'primitive', 'reference', 'collection', 'data').

    Raises:
        ModuleTypeError: If no path_pattern matches the module_path.
    """
    provider_section = config.get("provider", {})
    if not isinstance(provider_section, dict):
        raise ModuleTypeError(
            "ERROR: 'provider' in config must be a dict. Check monorepo-config.json structure."
        )

    for _provider_name, provider_config in provider_section.items():
        if not isinstance(provider_config, dict):
            continue
        module_types = provider_config.get("module_types", {})
        if not isinstance(module_types, dict):
            continue
        for type_name, type_config in module_types.items():
            if not isinstance(type_config, dict):
                continue
            path_patterns = type_config.get("path_patterns", [])
            if not isinstance(path_patterns, list):
                continue
            for pattern in path_patterns:
                if fnmatch.fnmatch(module_path, str(pattern)):
                    return str(type_name)

    raise ModuleTypeError(
        f"ERROR: module_path {module_path!r} matches no configured path_pattern.\n"
        "Ensure the module directory is under a provider.*.module_types.*.path_patterns\n"
        "entry in monorepo-config.json."
    )


def write_module_type_output(output_path: str, module_type: str) -> None:
    """Write module_type= to the output file (e.g. $GITHUB_OUTPUT).

    Args:
        output_path: File path to write the key=value pair to.
        module_type: The resolved module type string.

    Raises:
        OSError: If the output file cannot be written.
    """
    write_output(output_path, OUTPUT_KEY_MODULE_TYPE, module_type)


def main() -> None:
    """CLI entry point. Resolves module type and writes output."""
    parser = argparse.ArgumentParser(description="Resolve a module path to its module type.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to monorepo-config.json",
    )
    parser.add_argument(
        "--module-path",
        required=True,
        help="Repo-relative module directory path to resolve.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output file path (e.g. $GITHUB_OUTPUT) to write module_type= to.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    try:
        module_type = resolve_module_type(module_path=args.module_path, config=config)
    except ModuleTypeError as exc:
        print(f"{GH_ERROR_PREFIX}{exc}", file=sys.stdout)
        sys.exit(1)
        return

    print(module_type)
    write_module_type_output(args.output, module_type)


if __name__ == "__main__":
    main()
