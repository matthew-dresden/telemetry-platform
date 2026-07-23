"""tf_validate_dependency_paths -- confirms every dependency.config_path resolves.

Run via: uv run python -m scripts.tf_validate_dependency_paths

Scans all leaf terragrunt.hcl files under the Terragrunt live tree and verifies
that every dependency.config_path resolves to an existing directory. Fails closed
(D16): any unresolvable path causes a non-zero exit.

Environment variable consumed (optional):
    TG_LIVE_ROOT    -- path to the Terragrunt live tree (default: terragrunt/ relative
                       to the repo root derived from __file__).
"""

from __future__ import annotations

import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to extract dependency config_path values from terragrunt.hcl content.
DEPENDENCY_CONFIG_PATH_RE = re.compile(
    r'dependency\s+"[^"]+"\s*\{[^}]*config_path\s*=\s*"([^"]+)"',
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class UnresolvableDependencyError(RuntimeError):
    """Raised when a dependency config_path cannot be resolved to an existing directory."""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


# Matches any Terragrunt/HCL interpolation token, e.g. ${local.svc_instance},
# ${local.env_active}, ${local.dns_prod_zone_active}. config_path values in the
# instance-relative tree interpolate the owning instance index (svc_instance) or an
# active-set basename (read from an active.hcl) so a copied folder rewires its sibling
# dependencies with zero edits (spec section 4.4). These tokens resolve to an instance
# DIRECTORY BASENAME (always a single path segment) at Terragrunt parse time. The guard
# checks the dependency TARGET DIRECTORY exists; it cannot evaluate HCL locals, so it
# substitutes each token with a single-segment glob wildcard and asserts at least one
# concrete sibling instance directory matches. This keeps the structural existence check
# intact for interpolated paths instead of failing on the literal "${...}" segment.
_INTERPOLATION_RE = re.compile(r"\$\{[^}]+\}")


def validate_dependency_path(hcl_file: pathlib.Path, raw_config_path: str) -> None:
    """Assert that a single dependency config_path resolves to an existing directory.

    Resolves the raw_config_path relative to the hcl_file's parent directory and
    checks that the resolved path exists on disk. When the config_path contains an
    HCL interpolation token (e.g. ${local.svc_instance}, ${local.env_active}) the
    token is replaced with a single-segment glob wildcard and the check passes if at
    least one concrete sibling instance directory matches -- the guard validates the
    dependency target DIRECTORY exists, and the interpolation only selects which
    instance index within it (spec section 4.4).

    Args:
        hcl_file: Path to the terragrunt.hcl file declaring the dependency.
        raw_config_path: The raw config_path string (may be relative, may interpolate).

    Raises:
        UnresolvableDependencyError: If the resolved path does not exist.
    """
    unit_dir = hcl_file.parent

    if _INTERPOLATION_RE.search(raw_config_path):
        # Replace every ${...} token with a single-segment glob wildcard. A token
        # resolves to one instance-dir basename at parse time, so "*" (no "/") is the
        # correct granularity. ".." segments are kept literal; the relative pattern is
        # globbed from unit_dir so the parent traversal is honored.
        glob_pattern = _INTERPOLATION_RE.sub("*", raw_config_path)
        matches = [m for m in unit_dir.glob(glob_pattern) if m.is_dir()]
        if not matches:
            resolved_pattern = unit_dir / glob_pattern
            raise UnresolvableDependencyError(
                f"ERROR: Unresolvable dependency path in {hcl_file}\n"
                f'  config_path = "{raw_config_path}"\n'
                f"  Glob pattern: {resolved_pattern}\n"
                f"  No sibling instance directory matches the interpolated path.\n"
                f"  Remedy: verify the dependency path is correct relative to {unit_dir}."
            )
        return

    resolved = (unit_dir / raw_config_path).resolve()
    if not resolved.exists():
        raise UnresolvableDependencyError(
            f"ERROR: Unresolvable dependency path in {hcl_file}\n"
            f'  config_path = "{raw_config_path}"\n'
            f"  Resolved to: {resolved}\n"
            f"  The resolved path does not exist.\n"
            f"  Remedy: verify the dependency path is correct relative to {unit_dir}."
        )


def validate_all_dependencies(terragrunt_root: pathlib.Path) -> None:
    """Validate all dependency config_paths in the terragrunt live tree.

    Scans all terragrunt.hcl files under terragrunt_root and validates each
    dependency config_path.

    Args:
        terragrunt_root: Root of the Terragrunt live tree.

    Raises:
        UnresolvableDependencyError: On the first unresolvable path found.
    """
    for hcl_file in sorted(terragrunt_root.rglob("terragrunt.hcl")):
        content = hcl_file.read_text(encoding="utf-8")
        dep_paths = DEPENDENCY_CONFIG_PATH_RE.findall(content)
        for raw_path in dep_paths:
            validate_dependency_path(hcl_file=hcl_file, raw_config_path=raw_path)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def _get_default_terragrunt_root() -> pathlib.Path | None:
    """Derive the default terragrunt root from the repo structure."""
    import os

    env_root = os.environ.get("TG_LIVE_ROOT", "")
    if env_root:
        return pathlib.Path(env_root)
    repo_root = pathlib.Path(__file__).parent.parent
    candidate = repo_root / "terragrunt"
    return candidate if candidate.exists() else None


def main() -> int:
    """Entry point for `uv run python -m scripts.tf_validate_dependency_paths`."""
    terragrunt_root = _get_default_terragrunt_root()
    if terragrunt_root is None or not terragrunt_root.exists():
        print(
            "ERROR: Terragrunt live tree not found.\n"
            "  Set TG_LIVE_ROOT to the path of the terragrunt/ directory.",
            file=sys.stderr,
        )
        return 1

    try:
        validate_all_dependencies(terragrunt_root=terragrunt_root)
    except UnresolvableDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("tf-validate-dependency-paths PASSED: all dependency paths resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
