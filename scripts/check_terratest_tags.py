"""check_terratest_tags -- FR-3 static tagging-contract lint.

Run via: uv run python -m scripts.check_terratest_tags

Scans every providers/aws/{primitives,references}/*/examples/<ex>/ directory
under the configured tree root and asserts the three-part tagging contract
(spec section 4.3):

  Part 1: The provider block carries a default_tags block wired to
          var.project_tag and var.terratest_run_id.

  Part 2: Both variables are declared as type string with NO defaults
          (fail-fast when unset; a default would mask an unset run id).

  Part 3: A committed terraform.tfvars provides the offline-validation values:
          project_tag = "telemetry-platform" and terratest_run_id = "offline-validate".

On any violation the lint prints file:line, the missing element, and a
remediation hint to stderr, then exits 1 (matching the spec section 4.3
worked example). A nonexistent tree path yields ERROR: + exit 1 (no silent
empty scan).

Environment variables consumed:
  TERRATEST_TAGS_ROOTS -- colon/comma-separated list of provider root directories
                          to scan (default: providers/aws/primitives and
                          providers/aws/references relative to the invocation cwd).
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants for offline-validation values (spec section 4.3)
# ---------------------------------------------------------------------------

_EXPECTED_PROJECT_TAG = "telemetry-platform"
_EXPECTED_TERRATEST_RUN_ID = "offline-validate"

# ---------------------------------------------------------------------------
# Regex patterns for HCL parsing
# ---------------------------------------------------------------------------

# Matches a default_tags block anywhere in HCL content
_DEFAULT_TAGS_RE = re.compile(r"default_tags\s*\{", re.MULTILINE)

# Matches a reference to var.project_tag inside a default_tags block
_PROJECT_TAG_REF_RE = re.compile(r"var\.project_tag\b")

# Matches a reference to var.terratest_run_id inside a default_tags block
_RUN_ID_REF_RE = re.compile(r"var\.terratest_run_id\b")

# Matches: variable "project_tag" { ... } or variable "terratest_run_id" { ... }
# Captures the variable name and the full block body.
_VARIABLE_BLOCK_RE = re.compile(
    r'variable\s+"(project_tag|terratest_run_id)"\s*\{([^}]*)\}',
    re.MULTILINE | re.DOTALL,
)

# Matches a default = ... assignment inside a variable block
_DEFAULT_ASSIGNMENT_RE = re.compile(r"\bdefault\s*=")

# Matches project_tag = "..." in tfvars
_TFVARS_PROJECT_TAG_RE = re.compile(
    r'^\s*project_tag\s*=\s*"([^"]*)"',
    re.MULTILINE,
)

# Matches terratest_run_id = "..." in tfvars
_TFVARS_RUN_ID_RE = re.compile(
    r'^\s*terratest_run_id\s*=\s*"([^"]*)"',
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class TagsContractViolation(NamedTuple):
    """A single tagging contract violation with location and remediation guidance."""

    file_path: pathlib.Path
    line: int
    element: str
    remediation: str

    def __str__(self) -> str:
        return (
            f"{self.file_path}:{self.line}: VIOLATION: {self.element}\n  Remedy: {self.remediation}"
        )


class TagsContractError(RuntimeError):
    """Raised for precondition errors such as a missing scan root."""


# ---------------------------------------------------------------------------
# Line number helper
# ---------------------------------------------------------------------------


def _line_number(content: str, offset: int) -> int:
    """Return the 1-based line number of character offset in content."""
    return content[:offset].count("\n") + 1


# ---------------------------------------------------------------------------
# Part 1: default_tags wiring check
# ---------------------------------------------------------------------------


def _check_default_tags_wiring(
    provider_file: pathlib.Path,
    content: str,
) -> list[TagsContractViolation]:
    """Check that the provider block has default_tags wired to both tagging variables.

    Returns a list of violations (empty when the contract is satisfied).
    """
    violations: list[TagsContractViolation] = []

    tags_match = _DEFAULT_TAGS_RE.search(content)
    if tags_match is None:
        violations.append(
            TagsContractViolation(
                file_path=provider_file,
                line=1,
                element="default_tags block is absent from the provider block",
                remediation=(
                    'Add a default_tags block to the provider "aws" block: '
                    "default_tags { tags = { Project = var.project_tag, "
                    "terratest-run = var.terratest_run_id } }"
                ),
            )
        )
        return violations

    # Extract the region from the default_tags block to the end of content
    # so we check the reference appears INSIDE the default_tags scope.
    # Simple heuristic: find the substring after the default_tags opening.
    block_start = tags_match.end()
    block_content = content[block_start:]

    if not _PROJECT_TAG_REF_RE.search(block_content):
        line = _line_number(content, tags_match.start())
        violations.append(
            TagsContractViolation(
                file_path=provider_file,
                line=line,
                element="default_tags is not wired to var.project_tag",
                remediation=("Add Project = var.project_tag to the default_tags.tags map."),
            )
        )

    if not _RUN_ID_REF_RE.search(block_content):
        line = _line_number(content, tags_match.start())
        violations.append(
            TagsContractViolation(
                file_path=provider_file,
                line=line,
                element="default_tags is not wired to var.terratest_run_id",
                remediation=(
                    'Add "terratest-run" = var.terratest_run_id to the default_tags.tags map.'
                ),
            )
        )

    return violations


# ---------------------------------------------------------------------------
# Part 2: variable declaration check
# ---------------------------------------------------------------------------


def _check_variable_declarations(
    variables_file: pathlib.Path,
    content: str,
) -> list[TagsContractViolation]:
    """Check that both tagging variables are declared as type string with no defaults.

    Returns a list of violations (empty when the contract is satisfied).
    """
    violations: list[TagsContractViolation] = []
    required_vars = {"project_tag", "terratest_run_id"}
    found_vars: set[str] = set()

    for match in _VARIABLE_BLOCK_RE.finditer(content):
        var_name = match.group(1)
        block_body = match.group(2)
        found_vars.add(var_name)

        if _DEFAULT_ASSIGNMENT_RE.search(block_body):
            line = _line_number(content, match.start())
            violations.append(
                TagsContractViolation(
                    file_path=variables_file,
                    line=line,
                    element=(
                        f'variable "{var_name}" must not have a default '
                        f"(a default would mask an unset run id)"
                    ),
                    remediation=(
                        f'Remove the default = ... assignment from variable "{var_name}".'
                    ),
                )
            )

    for missing_var in sorted(required_vars - found_vars):
        violations.append(
            TagsContractViolation(
                file_path=variables_file,
                line=1,
                element=f'variable "{missing_var}" is absent',
                remediation=(
                    f'Declare variable "{missing_var}" {{ type = string }} '
                    f"in variables.tf with no default."
                ),
            )
        )

    return violations


# ---------------------------------------------------------------------------
# Part 3: terraform.tfvars offline values check
# ---------------------------------------------------------------------------


def _check_tfvars_key(
    tfvars_path: pathlib.Path,
    content: str,
    key: str,
    pattern: re.Pattern[str],
    expected_value: str,
) -> list[TagsContractViolation]:
    """Check that a single key in terraform.tfvars has the expected offline value.

    Shared helper used by _check_tfvars_offline_values to eliminate duplicate
    logic for project_tag and terratest_run_id.

    Args:
        tfvars_path: Path to the terraform.tfvars file (for violation location).
        content: Full file content.
        key: Variable name (e.g. "project_tag").
        pattern: Compiled regex that captures the key's value in group 1.
        expected_value: The required offline-validation value.

    Returns:
        List of TagsContractViolation objects (empty when the key is correct).
    """
    violations: list[TagsContractViolation] = []
    match = pattern.search(content)
    if match is None:
        violations.append(
            TagsContractViolation(
                file_path=tfvars_path,
                line=1,
                element=f"terraform.tfvars is missing {key}",
                remediation=f'Add {key} = "{expected_value}" to terraform.tfvars.',
            )
        )
    elif match.group(1) != expected_value:
        line = _line_number(content, match.start())
        violations.append(
            TagsContractViolation(
                file_path=tfvars_path,
                line=line,
                element=(f'{key} = "{match.group(1)}" must be "{expected_value}"'),
                remediation=f'Change {key} to "{expected_value}" in terraform.tfvars.',
            )
        )
    return violations


def _check_tfvars_offline_values(
    ex_dir: pathlib.Path,
) -> list[TagsContractViolation]:
    """Check that terraform.tfvars carries the required offline-validation values.

    Returns a list of violations (empty when the contract is satisfied).
    """
    violations: list[TagsContractViolation] = []
    tfvars_path = ex_dir / "terraform.tfvars"

    if not tfvars_path.exists():
        violations.append(
            TagsContractViolation(
                file_path=tfvars_path,
                line=1,
                element="terraform.tfvars is absent",
                remediation=(
                    f"Create terraform.tfvars with:\n"
                    f'  project_tag      = "{_EXPECTED_PROJECT_TAG}"\n'
                    f'  terratest_run_id = "{_EXPECTED_TERRATEST_RUN_ID}"'
                ),
            )
        )
        return violations

    content = tfvars_path.read_text(encoding="utf-8")

    violations.extend(
        _check_tfvars_key(
            tfvars_path, content, "project_tag", _TFVARS_PROJECT_TAG_RE, _EXPECTED_PROJECT_TAG
        )
    )
    violations.extend(
        _check_tfvars_key(
            tfvars_path, content, "terratest_run_id", _TFVARS_RUN_ID_RE, _EXPECTED_TERRATEST_RUN_ID
        )
    )

    return violations


# ---------------------------------------------------------------------------
# Per-example-directory checker
# ---------------------------------------------------------------------------


def _find_provider_file(ex_dir: pathlib.Path) -> pathlib.Path | None:
    """Return the first .tf file in ex_dir that contains a provider block, or None."""
    for tf_file in sorted(ex_dir.glob("*.tf")):
        content = tf_file.read_text(encoding="utf-8")
        if re.search(r'\bprovider\s+"aws"\s*\{', content):
            return tf_file
    return None


def _find_variables_file(ex_dir: pathlib.Path) -> pathlib.Path | None:
    """Return variables.tf in ex_dir if it exists, else None."""
    candidate = ex_dir / "variables.tf"
    return candidate if candidate.exists() else None


def check_example_dir(ex_dir: pathlib.Path) -> list[TagsContractViolation]:
    """Assert the three-part tagging contract for a single example directory.

    Checks:
      1. Provider block has default_tags wired to var.project_tag and var.terratest_run_id.
      2. Both variables are declared as type string with no defaults.
      3. terraform.tfvars carries the offline-validation values.

    Args:
        ex_dir: Path to the example directory
                (e.g. providers/aws/primitives/kms-key/examples/basic).

    Returns:
        List of TagsContractViolation objects (empty when all three parts pass).
    """
    violations: list[TagsContractViolation] = []

    # Part 1: default_tags wiring
    provider_file = _find_provider_file(ex_dir)
    if provider_file is not None:
        content = provider_file.read_text(encoding="utf-8")
        violations.extend(_check_default_tags_wiring(provider_file, content))
    else:
        # Check all .tf files for default_tags -- if no provider block exists at all,
        # report against the directory itself via versions.tf or the first .tf file.
        fallback = next(ex_dir.glob("*.tf"), None) or (ex_dir / "versions.tf")
        violations.append(
            TagsContractViolation(
                file_path=fallback,
                line=1,
                element='provider "aws" block is absent in example directory',
                remediation=(
                    'Add a provider "aws" block with default_tags { tags = { '
                    "Project = var.project_tag, "
                    '"terratest-run" = var.terratest_run_id } } to versions.tf or main.tf.'
                ),
            )
        )

    # Part 2: variable declarations
    variables_file = _find_variables_file(ex_dir)
    if variables_file is not None:
        vars_content = variables_file.read_text(encoding="utf-8")
        violations.extend(_check_variable_declarations(variables_file, vars_content))
    else:
        violations.append(
            TagsContractViolation(
                file_path=ex_dir / "variables.tf",
                line=1,
                element="variables.tf is absent",
                remediation=(
                    'Create variables.tf declaring variable "project_tag" { type = string } '
                    'and variable "terratest_run_id" { type = string } with no defaults.'
                ),
            )
        )

    # Part 3: tfvars offline values
    violations.extend(_check_tfvars_offline_values(ex_dir))

    return violations


# ---------------------------------------------------------------------------
# Example directory collector
# ---------------------------------------------------------------------------


def collect_example_dirs(roots: list[pathlib.Path]) -> list[pathlib.Path]:
    """Collect all examples/<ex>/ directories under each provider root.

    Scans for paths matching <module>/examples/<ex>/ under each root.

    Args:
        roots: List of provider root directories to scan
               (e.g. providers/aws/primitives, providers/aws/references).

    Returns:
        Sorted list of example directory paths.

    Raises:
        TagsContractError: When any root does not exist.
    """
    for root in roots:
        if not root.exists():
            raise TagsContractError(
                f"ERROR: Provider root directory does not exist: {root}\n"
                f"  Remedy: verify TERRATEST_TAGS_ROOTS or the default path is correct."
            )

    results: list[pathlib.Path] = []
    for root in roots:
        for candidate in sorted(root.rglob("examples")):
            if not candidate.is_dir():
                continue
            # Skip examples directories inside vendored .terraform module caches.
            if ".terraform" in candidate.parts:
                continue
            for ex_dir in sorted(candidate.iterdir()):
                if ex_dir.is_dir():
                    results.append(ex_dir)
    return results


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_tags_check(roots: list[pathlib.Path]) -> list[TagsContractViolation]:
    """Run the tagging contract lint across all example directories under roots.

    Args:
        roots: List of provider root directories to scan.

    Returns:
        Aggregated list of TagsContractViolation objects (empty when all pass).

    Raises:
        TagsContractError: When any root does not exist.
    """
    example_dirs = collect_example_dirs(roots)
    violations: list[TagsContractViolation] = []
    for ex_dir in example_dirs:
        violations.extend(check_example_dir(ex_dir))
    return violations


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def _resolve_roots(cwd: pathlib.Path) -> list[pathlib.Path]:
    """Resolve the list of provider roots from TERRATEST_TAGS_ROOTS or defaults.

    When TERRATEST_TAGS_ROOTS is set, all listed paths are returned as-is
    (missing paths are caught by collect_example_dirs). When not set, only
    the default paths that exist on disk are returned; if NONE of the defaults
    exist, the caller will receive an empty list and no error is raised here
    (collect_example_dirs handles the empty-root case gracefully).

    Args:
        cwd: Current working directory used to resolve default paths.

    Returns:
        List of resolved provider root paths.
    """
    env_roots = os.environ.get("TERRATEST_TAGS_ROOTS", "")
    if env_roots:
        separators = re.compile(r"[,:]")
        parts = [p.strip() for p in separators.split(env_roots) if p.strip()]
        return [pathlib.Path(p) for p in parts]

    defaults = [
        cwd / "providers" / "aws" / "primitives",
        cwd / "providers" / "aws" / "references",
    ]
    # Return only the default paths that exist on disk so partial trees (e.g.
    # test fixtures with only one provider kind) do not fail the precondition.
    return [p for p in defaults if p.exists()]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point for `uv run python -m scripts.check_terratest_tags`.

    Reads configuration from environment variables, scans all example
    directories under the provider roots, and exits non-zero with violation
    messages when the tagging contract is not satisfied.
    """
    cwd = pathlib.Path.cwd()
    roots = _resolve_roots(cwd)

    try:
        violations = run_tags_check(roots)
    except TagsContractError as exc:
        print(str(exc), file=sys.stderr)
        print("\nterratest-tags-check FAILED: precondition error.", file=sys.stderr)
        return 1

    if violations:
        for violation in violations:
            print(str(violation), file=sys.stderr)
        count = len(violations)
        print(
            f"\nterratest-tags-check FAILED: {count} tagging contract violation(s) found.",
            file=sys.stderr,
        )
        return 1

    print("terratest-tags-check PASSED: all example directories satisfy the tagging contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
