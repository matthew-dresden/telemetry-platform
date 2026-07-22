"""check_terratest_coverage -- FR-19 item-1 per-module per-example coverage gate.

Run via: uv run python -m scripts.check_terratest_coverage

Scans every providers/aws/{primitives,references}/*/  directory that contains
both a go.mod and an examples/ subdirectory (spec section 4.19 FR-19 item 1,
D-25). For each examples/<ex>/ directory under a qualifying module, the gate
asserts that at least one first-party Go test file outside **/.terraform/**
invokes RunSingleExample against that example name.

On any gap the gate prints one ERROR line per missing example + remediation to
stderr then exits 1. Exit 0 means every module/example pair is covered.

A nonexistent provider root yields ERROR: + exit 1 (no silent empty scan).

Environment variables consumed:
  TERRATEST_COVERAGE_ROOTS -- colon/comma-separated list of provider root
                              directories to scan (default: providers/aws/primitives
                              and providers/aws/references relative to cwd).
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Sentinel pattern: first-party RunSingleExample call
# ---------------------------------------------------------------------------

_RUN_SINGLE_EXAMPLE_RE = re.compile(r"\bRunSingleExample\b")

# Pattern that extracts the example name argument from RunSingleExample calls.
# Matches both:
#   RunSingleExample(t, "../../examples", "basic", ...)
#   RunSingleExample(t, examplesRoot, "basic", ...)
_EXAMPLE_NAME_RE = re.compile(
    r'\bRunSingleExample\s*\([^,]+,\s*[^,]+,\s*"([^"]+)"',
)

# ---------------------------------------------------------------------------
# Error / data types
# ---------------------------------------------------------------------------


class CoverageError(RuntimeError):
    """Raised for precondition errors such as a missing scan root."""


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------


def collect_modules(roots: list[pathlib.Path]) -> list[pathlib.Path]:
    """Collect qualifying module roots from the given provider roots.

    A qualifying module is a directory under a root that has BOTH:
      - a go.mod file, AND
      - an examples/ subdirectory

    Args:
        roots: Provider root directories to scan.

    Returns:
        Sorted list of module root paths.

    Raises:
        CoverageError: When any root does not exist.
    """
    results: list[pathlib.Path] = []
    for root in roots:
        if not root.exists():
            raise CoverageError(
                f"ERROR: provider root does not exist: {root}\n"
                f"  Check that TERRATEST_COVERAGE_ROOTS is set correctly."
            )
        for candidate in sorted(root.iterdir()):
            if not candidate.is_dir():
                continue
            if (candidate / "go.mod").exists() and (candidate / "examples").is_dir():
                results.append(candidate)
    return results


# ---------------------------------------------------------------------------
# First-party test file discovery
# ---------------------------------------------------------------------------


def _collect_first_party_test_files(module_root: pathlib.Path) -> list[pathlib.Path]:
    """Collect first-party Go test files under module_root/tests/.

    Excludes any path containing the segment '.terraform'.

    Args:
        module_root: Module root directory.

    Returns:
        List of first-party module_test.go (and any *_test.go) file paths.
    """
    tests_dir = module_root / "tests"
    if not tests_dir.is_dir():
        return []

    results: list[pathlib.Path] = []
    for test_file in tests_dir.rglob("*_test.go"):
        # Exclude vendored copies inside .terraform/
        if ".terraform" in test_file.parts:
            continue
        results.append(test_file)
    return results


# ---------------------------------------------------------------------------
# Coverage check per module
# ---------------------------------------------------------------------------


def check_module_coverage(module_root: pathlib.Path) -> list[str]:
    """Assert that every example directory has at least one first-party test.

    Counts only first-party tests (outside **/.terraform/**) that invoke
    RunSingleExample against that example's name.

    Args:
        module_root: Module root directory (must have examples/ and go.mod).

    Returns:
        List of gap strings (one ERROR: line per uncovered example).
        Empty list means all examples are covered.
    """
    examples_dir = module_root / "examples"
    example_names: list[str] = sorted(d.name for d in examples_dir.iterdir() if d.is_dir())

    if not example_names:
        return []

    # Build a set of covered example names from first-party tests
    covered: set[str] = set()
    first_party_files = _collect_first_party_test_files(module_root)
    for test_file in first_party_files:
        content = test_file.read_text(encoding="utf-8")
        for match in _EXAMPLE_NAME_RE.finditer(content):
            covered.add(match.group(1))

    # Determine module display name (relative path from providers/aws/...)
    module_name = module_root.name

    gaps: list[str] = []
    for ex in example_names:
        if ex not in covered:
            gaps.append(
                f"ERROR: {module_name} example '{ex}' has no first-party apply-level test"
                f" (vendored copies excluded)\n"
                f"  Remedy: add a tests/<variant>/module_test.go that calls"
                f' RunSingleExample(t, "../../examples", "{ex}", ...) in {module_root}'
            )

    return gaps


# ---------------------------------------------------------------------------
# Full-tree runner
# ---------------------------------------------------------------------------


def run_coverage_check(roots: list[pathlib.Path]) -> list[str]:
    """Run the coverage gate across all qualifying modules under roots.

    Args:
        roots: Provider root directories to scan.

    Returns:
        Aggregated list of gap strings (empty when all examples are covered).

    Raises:
        CoverageError: When any root does not exist.
    """
    modules = collect_modules(roots)
    all_gaps: list[str] = []
    for module_root in modules:
        all_gaps.extend(check_module_coverage(module_root))
    return all_gaps


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def _resolve_roots(cwd: pathlib.Path) -> list[pathlib.Path]:
    """Resolve provider roots from TERRATEST_COVERAGE_ROOTS env or defaults.

    Args:
        cwd: Current working directory used to resolve default paths.

    Returns:
        List of resolved provider root paths.
    """
    env_roots = os.environ.get("TERRATEST_COVERAGE_ROOTS", "")
    if env_roots:
        separators = re.compile(r"[,:]")
        parts = [p.strip() for p in separators.split(env_roots) if p.strip()]
        return [pathlib.Path(p) for p in parts]

    defaults = [
        cwd / "providers" / "aws" / "primitives",
        cwd / "providers" / "aws" / "references",
    ]
    return [p for p in defaults if p.exists()]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point for `uv run python -m scripts.check_terratest_coverage`.

    Reads configuration from environment variables, scans all qualifying
    modules, and exits non-zero with gap messages when coverage is incomplete.
    """
    cwd = pathlib.Path.cwd()
    roots = _resolve_roots(cwd)

    if not roots:
        print(
            "ERROR: No provider root directories found. "
            "Expected providers/aws/primitives and/or providers/aws/references to exist.",
            file=sys.stderr,
        )
        return 1

    try:
        gaps = run_coverage_check(roots)
    except CoverageError as exc:
        print(str(exc), file=sys.stderr)
        print("\nterratest-coverage-check FAILED: precondition error.", file=sys.stderr)
        return 1

    if gaps:
        for gap in gaps:
            print(gap, file=sys.stderr)
        count = len(gaps)
        print(
            f"\nterratest-coverage-check FAILED: {count} module/example pair(s) lack"
            f" first-party apply-level test coverage.",
            file=sys.stderr,
        )
        return 1

    print("terratest-coverage-check PASSED: all module/example pairs have first-party coverage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
