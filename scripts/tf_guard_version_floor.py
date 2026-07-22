"""tf_guard_version_floor -- rejects module versions.tf and root generated blocks below the floor.

Run via: uv run python -m scripts.tf_guard_version_floor

Scans every versions.tf under the configured provider roots (excluding hidden .terraform
directories) and the root generated versions block in terragrunt/root.hcl.

For each module versions.tf it FAILS with a file:line finding when the file:

  (a) Omits required_version entirely.
  (b) Declares required_version below the configured Terraform floor (>= TF_FLOOR).
  (c) Omits the AWS provider version entirely.
  (d) Declares the AWS provider version below the configured provider floor.
  (e) Declares the AWS provider version with a pessimistic constraint (~>) that prevents
      minor/major upgrades above the floor.

For the root generated versions block it FAILS only on the Terraform core floor
(required_version absent or below TF_FLOOR). The AWS provider floor (required_providers
aws) is owned by each module's versions.tf and MUST NOT be declared in the generated
block -- a second required_providers block lands in the same cached module directory and
makes terraform init error with "Duplicate required providers configuration" (D-16). The
guard therefore never requires required_providers aws in the generated block; if one is
nonetheless present, its version is still validated against the floor as defence-in-depth.

All floor values and scan roots are read from environment variables or function parameters
-- no magic numbers are inlined in this script.

Environment variables consumed:
  TF_PROVIDERS_ROOTS   -- comma-separated list of provider root directories to scan
                          (default: providers/aws/primitives,providers/aws/references
                          relative to the repo root derived from __file__)
  TF_ROOT_HCL          -- path to the root HCL file containing the generate 'versions' block
                          (default: terragrunt/root.hcl relative to the repo root)
  TF_FLOOR             -- minimum Terraform version floor, e.g. 1.15.5
                          (default: 1.15.5)
  PROVIDER_FLOOR       -- minimum AWS provider version floor, e.g. 6.49.0
                          (default: 6.49.0)
  PROVIDER_SOURCE      -- AWS provider source address (default: hashicorp/aws)

Exit 0 when all files meet the floor; non-zero with ERROR:-shaped messages otherwise.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Default floor values (read from env or fallback -- never inlined as magic numbers
# in the guard logic; these defaults are only used when the env var is absent)
# ---------------------------------------------------------------------------

_DEFAULT_TF_FLOOR = "1.15.5"
_DEFAULT_PROVIDER_FLOOR = "6.49.0"
_DEFAULT_PROVIDER_SOURCE = "hashicorp/aws"

# ---------------------------------------------------------------------------
# Regex patterns for HCL parsing
# ---------------------------------------------------------------------------

# Matches: required_version = ">= 1.15.5" or required_version = ">= 1.12.1" etc.
REQUIRED_VERSION_RE = re.compile(
    r"""required_version\s*=\s*"([^"]+)\"""",
    re.MULTILINE,
)

# Matches: version = ">= 6.49.0" or version = "~> 6.0.0" etc. inside required_providers
# We use a broad pattern and rely on context (provider source check) to scope it.
PROVIDER_VERSION_RE = re.compile(
    r"""version\s*=\s*"([^"]+)\"""",
    re.MULTILINE,
)

# Matches the provider source in required_providers block
PROVIDER_SOURCE_RE = re.compile(
    r"""source\s*=\s*"([^"]+)\"""",
    re.MULTILINE,
)

# Matches a generate "versions" block in root.hcl (for finding the embedded versions block)
GENERATE_VERSIONS_RE = re.compile(
    r'generate\s+"versions"\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
    re.MULTILINE | re.DOTALL,
)

# Pessimistic constraint prefix (~>)
PESSIMISTIC_PREFIX = "~>"

# Valid constraint prefixes for the floor check
GTE_PREFIX = ">="


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class FloorConfig(NamedTuple):
    """Immutable floor configuration: Terraform floor, AWS provider floor, provider source."""

    tf_floor: str
    provider_floor: str
    provider_source: str


class VersionFloorError(RuntimeError):
    """Raised when a versions.tf or root block declares a version below the floor.

    The message always includes the file path and line number of the finding.
    """


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a semantic version string into a tuple of integers for comparison.

    Args:
        version_str: A version string like '1.15.5' or '6.49.0'.

    Returns:
        Tuple of integers, e.g. (1, 15, 5).

    Raises:
        ValueError: If the string cannot be parsed as a dotted numeric version.
    """
    parts = version_str.strip().split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse version '{version_str}' as a dotted numeric version: {exc}"
        ) from exc


def _meets_floor(constraint: str, floor: str) -> bool:
    """Return True if the HCL version constraint meets the floor.

    Accepted constraint shapes:
      '>= X.Y.Z'  -- meets the floor when X.Y.Z >= floor
      '~> X.Y.Z'  -- NEVER meets the floor (pessimistic constraint prevents upgrades)

    Any other prefix is treated as not meeting the floor.

    Args:
        constraint: Raw HCL version constraint string, e.g. '>= 1.15.5' or '~> 6.0.0'.
        floor: Minimum version string, e.g. '1.15.5'.

    Returns:
        True if the constraint satisfies '>= floor' semantically.
    """
    constraint = constraint.strip()

    if constraint.startswith(PESSIMISTIC_PREFIX):
        return False

    if constraint.startswith(GTE_PREFIX):
        version_part = constraint[len(GTE_PREFIX) :].strip()
        try:
            declared = _parse_version(version_part)
            floor_tuple = _parse_version(floor)
            return declared >= floor_tuple
        except ValueError:
            return False

    # Unknown prefix -- not '>=' and not '~>' -- cannot guarantee floor is met.
    return False


# ---------------------------------------------------------------------------
# Single-file checker
# ---------------------------------------------------------------------------


def _find_line_number(content: str, match_start: int) -> int:
    """Return the 1-based line number of a character offset in content."""
    return content[:match_start].count("\n") + 1


def check_versions_file(path: pathlib.Path, floor_config: FloorConfig) -> None:
    """Check a single versions.tf file against the floor configuration.

    Raises VersionFloorError with a file:line finding when:
      - required_version is absent
      - required_version is below floor_config.tf_floor
      - the AWS provider version is absent
      - the AWS provider version is below floor_config.provider_floor
      - the AWS provider version uses a pessimistic (~>) constraint

    Args:
        path: Path to the versions.tf file to check.
        floor_config: Immutable floor configuration.

    Raises:
        VersionFloorError: With a file:line finding when any constraint is below floor.
    """
    content = path.read_text(encoding="utf-8")

    # --- Check required_version ---
    tf_match = REQUIRED_VERSION_RE.search(content)
    if tf_match is None:
        line = 1
        raise VersionFloorError(
            f"{path}:{line}: ERROR: required_version is absent.\n"
            f'  Expected: required_version = ">= {floor_config.tf_floor}"\n'
            f'  Remedy: add required_version = ">= {floor_config.tf_floor}" to the terraform block.'
        )
    tf_line = _find_line_number(content, tf_match.start())
    tf_constraint = tf_match.group(1)
    if not _meets_floor(tf_constraint, floor_config.tf_floor):
        raise VersionFloorError(
            f'{path}:{tf_line}: ERROR: required_version "{tf_constraint}" is below the floor.\n'
            f"  Floor: >= {floor_config.tf_floor}\n"
            f'  Remedy: change required_version to ">= {floor_config.tf_floor}".'
        )

    # --- Check provider version ---
    # We look for the provider source block and then its version.
    # We find the provider source matching floor_config.provider_source,
    # then look for the nearest version constraint in the same block context.
    provider_match = _find_provider_version_in_content(content, path, floor_config)
    if provider_match is None:
        line = 1
        raise VersionFloorError(
            f"{path}:{line}: ERROR: AWS provider '{floor_config.provider_source}' block "
            f"is absent or has no version constraint.\n"
            f'  Expected: version = ">= {floor_config.provider_floor}"\n'
            f"  Remedy: add the required_providers block with the AWS provider "
            f'at ">= {floor_config.provider_floor}".'
        )


def _find_provider_version_in_content(
    content: str,
    path: pathlib.Path,
    floor_config: FloorConfig,
) -> bool | None:
    """Find the AWS provider version in content and validate it against the floor.

    Locates the provider source for floor_config.provider_source and then finds
    the nearest version constraint. Raises VersionFloorError if the constraint
    is missing or below the floor.

    Returns:
        True if the provider constraint meets the floor.
        None if no provider block was found (caller raises the error).
    """
    # Find all source = "..." occurrences and look for the provider source.
    for src_match in PROVIDER_SOURCE_RE.finditer(content):
        if src_match.group(1) != floor_config.provider_source:
            continue
        # Found the right provider source. Search forward for the version constraint
        # within the enclosing block (bounded by the next source = ... or closing brace).
        search_start = src_match.end()
        ver_match = PROVIDER_VERSION_RE.search(content, search_start)
        if ver_match is None:
            return None
        ver_line = _find_line_number(content, ver_match.start())
        ver_constraint = ver_match.group(1)
        if not _meets_floor(ver_constraint, floor_config.provider_floor):
            raise VersionFloorError(
                f'{path}:{ver_line}: ERROR: AWS provider version "{ver_constraint}" '
                f"is below the floor.\n"
                f"  Floor: >= {floor_config.provider_floor}\n"
                f"  Remedy: change the provider version to "
                f'">= {floor_config.provider_floor}".'
            )
        return True
    return None


# ---------------------------------------------------------------------------
# Root HCL checker
# ---------------------------------------------------------------------------


def check_root_hcl(path: pathlib.Path, floor_config: FloorConfig) -> list[str]:
    """Check the generate 'versions' block in a root.hcl file.

    The generated 'versions' block declares the repo-wide Terraform core floor only
    (required_version). The AWS provider floor (required_providers aws) is declared in
    each module's own versions.tf, not here: emitting a second required_providers block
    in the generated file would land in the same cached module directory as the module's
    versions.tf and make Terraform error with "Duplicate required providers configuration"
    (D-16). This checker therefore enforces required_version and does NOT require a
    required_providers aws block in the generated block. If the block does declare an AWS
    provider version (which it should not), that version is still validated against the
    floor as defence-in-depth, but its absence is correct and never a finding.

    Args:
        path: Path to the root.hcl file (e.g. terragrunt/root.hcl).
        floor_config: Immutable floor configuration.

    Returns:
        List of file:line finding strings (empty if the block meets the floor).
    """
    content = path.read_text(encoding="utf-8")
    findings: list[str] = []

    # Extract the generate "versions" block content.
    gen_match = GENERATE_VERSIONS_RE.search(content)
    if gen_match is None:
        findings.append(
            f'{path}:1: ERROR: generate "versions" block is absent from root HCL.\n'
            f'  Remedy: add a generate "versions" block with required_version '
            f'">= {floor_config.tf_floor}".'
        )
        return findings

    block_content = gen_match.group(1)
    block_start_offset = gen_match.start(1)

    # Check required_version inside the block (the Terraform core floor).
    tf_match = REQUIRED_VERSION_RE.search(block_content)
    if tf_match is None:
        line = _find_line_number(content, block_start_offset)
        findings.append(
            f'{path}:{line}: ERROR: required_version is absent in generate "versions" block.\n'
            f'  Remedy: add required_version = ">= {floor_config.tf_floor}".'
        )
    else:
        tf_constraint = tf_match.group(1)
        if not _meets_floor(tf_constraint, floor_config.tf_floor):
            actual_offset = block_start_offset + tf_match.start()
            line = _find_line_number(content, actual_offset)
            findings.append(
                f'{path}:{line}: ERROR: required_version "{tf_constraint}" in generate '
                f'"versions" block is below the floor.\n'
                f"  Floor: >= {floor_config.tf_floor}\n"
                f'  Remedy: change required_version to ">= {floor_config.tf_floor}".'
            )

    # The generated block must NOT declare the AWS provider (required_providers aws is
    # owned by each module's versions.tf; a duplicate here breaks terraform init per D-16).
    # If a provider source is nonetheless present, validate its version against the floor
    # so a stray below-floor pin is still caught; its absence is the expected design.
    src_match = PROVIDER_SOURCE_RE.search(block_content)
    if src_match is None or src_match.group(1) != floor_config.provider_source:
        return findings

    search_start = src_match.end()
    ver_match = PROVIDER_VERSION_RE.search(block_content, search_start)
    if ver_match is not None:
        ver_constraint = ver_match.group(1)
        if not _meets_floor(ver_constraint, floor_config.provider_floor):
            actual_offset = block_start_offset + ver_match.start()
            line = _find_line_number(content, actual_offset)
            findings.append(
                f'{path}:{line}: ERROR: AWS provider version "{ver_constraint}" in generate '
                f'"versions" block is below the floor.\n'
                f"  Floor: >= {floor_config.provider_floor}\n"
                f'  Remedy: change the provider version to ">= {floor_config.provider_floor}".'
            )

    return findings


# ---------------------------------------------------------------------------
# Directory walker
# ---------------------------------------------------------------------------


def collect_versions_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Collect all versions.tf files under root, excluding hidden directories (.terraform).

    Args:
        root: Root directory to scan.

    Returns:
        Sorted list of Path objects for each discovered versions.tf file.

    Raises:
        VersionFloorError: If root does not exist.
    """
    if not root.exists():
        raise VersionFloorError(
            f"ERROR: Provider root directory does not exist: {root}\n"
            f"  Remedy: verify TF_PROVIDERS_ROOTS or the default path is correct."
        )
    results: list[pathlib.Path] = []
    for candidate in root.rglob("versions.tf"):
        # Exclude hidden directories (e.g. .terraform/)
        if any(part.startswith(".") for part in candidate.parts):
            continue
        results.append(candidate)
    return sorted(results)


# ---------------------------------------------------------------------------
# Main guard runner
# ---------------------------------------------------------------------------


def run_version_floor_guard(
    providers_roots: list[pathlib.Path],
    root_hcl: pathlib.Path | None,
    floor_config: FloorConfig,
) -> list[str]:
    """Run the version floor guard across all provider roots and the root HCL.

    Args:
        providers_roots: List of root directories to scan for versions.tf files.
        root_hcl: Path to the root HCL file with the generate 'versions' block,
                  or None to skip the root HCL check.
        floor_config: Immutable floor configuration.

    Returns:
        List of file:line finding strings (empty if all files meet the floor).
    """
    findings: list[str] = []

    for root in providers_roots:
        for versions_file in collect_versions_files(root):
            try:
                check_versions_file(versions_file, floor_config)
            except VersionFloorError as exc:
                findings.append(str(exc))

    if root_hcl is not None:
        findings.extend(check_root_hcl(root_hcl, floor_config))

    return findings


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def _resolve_floor_config() -> FloorConfig:
    """Resolve the FloorConfig from environment variables.

    Reads TF_FLOOR, PROVIDER_FLOOR, and PROVIDER_SOURCE from the environment.
    Falls back to the module-level defaults only when the env var is absent.

    Returns:
        FloorConfig with tf_floor, provider_floor, and provider_source populated.
    """
    tf_floor = os.environ.get("TF_FLOOR", _DEFAULT_TF_FLOOR)
    provider_floor = os.environ.get("PROVIDER_FLOOR", _DEFAULT_PROVIDER_FLOOR)
    provider_source = os.environ.get("PROVIDER_SOURCE", _DEFAULT_PROVIDER_SOURCE)
    return FloorConfig(
        tf_floor=tf_floor,
        provider_floor=provider_floor,
        provider_source=provider_source,
    )


def _resolve_providers_roots(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Resolve the providers roots from TF_PROVIDERS_ROOTS env var or default paths.

    Args:
        repo_root: The repository root directory.

    Returns:
        List of resolved pathlib.Path objects for each provider root.

    Raises:
        SystemExit(1): If TF_PROVIDERS_ROOTS is not set and no default paths exist.
    """
    env_roots = os.environ.get("TF_PROVIDERS_ROOTS", "")
    if env_roots:
        return [pathlib.Path(r.strip()) for r in env_roots.split(",") if r.strip()]

    # Default: primitives and references under providers/aws
    defaults = [
        repo_root / "providers" / "aws" / "primitives",
        repo_root / "providers" / "aws" / "references",
    ]
    existing = [p for p in defaults if p.exists()]
    if not existing:
        print(
            "ERROR: No provider roots found.\n"
            "  Set TF_PROVIDERS_ROOTS to a comma-separated list of directories to scan.\n"
            f"  Default paths checked: {[str(p) for p in defaults]}",
            file=sys.stderr,
        )
        sys.exit(1)
    return existing


def _resolve_root_hcl(repo_root: pathlib.Path) -> pathlib.Path | None:
    """Resolve the root HCL path from TF_ROOT_HCL env var or default path.

    Args:
        repo_root: The repository root directory.

    Returns:
        Path to the root HCL file, or None if it does not exist and env var is absent.
    """
    env_hcl = os.environ.get("TF_ROOT_HCL", "")
    if env_hcl:
        return pathlib.Path(env_hcl)
    candidate = repo_root / "terragrunt" / "root.hcl"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point for `uv run python -m scripts.tf_guard_version_floor`.

    Reads configuration from environment variables, scans all versions.tf files
    under the provider roots and the root HCL, and exits non-zero with ERROR:-shaped
    messages when any file is below the version floor.
    """
    repo_root = pathlib.Path(__file__).parent.parent

    floor_config = _resolve_floor_config()
    providers_roots = _resolve_providers_roots(repo_root)
    root_hcl = _resolve_root_hcl(repo_root)

    try:
        findings = run_version_floor_guard(
            providers_roots=providers_roots,
            root_hcl=root_hcl,
            floor_config=floor_config,
        )
    except VersionFloorError as exc:
        print(str(exc), file=sys.stderr)
        print("\ntf-guard-version-floor FAILED: precondition error.", file=sys.stderr)
        return 1

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        print(
            f"\ntf-guard-version-floor FAILED: {len(findings)} version floor violation(s) found.",
            file=sys.stderr,
        )
        return 1

    print(
        "tf-guard-version-floor PASSED: all version floors are met.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
