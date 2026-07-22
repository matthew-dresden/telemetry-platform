"""tf_guard_const_sources -- rejects forbidden const-source patterns in reference modules.

Run via: uv run python -m scripts.tf_guard_const_sources --root providers/aws/references

Scans all Terraform files under the given references root and rejects four forbidden
classes defined by spec section 4.7:

  (a) Any in-repo child module source that is a bare git-URL or relative-path literal
      instead of source = var.<name>_source (spec section 4.7a, AC-1).

  (b) Any *_source variable used in a source that lacks const = true
      (spec section 4.7b, AC-2).

  (c) Any *_source default that is not an in-repo relative path -- a git URL or an
      absolute /... path is rejected (spec section 4.7c, AC-2, AC-21).

  (d) Any external (caylent-solutions/terraform-modules) source that was variable-ized
      -- the external child blocks (budget, dynamodb-table) must stay hardcoded
      pinned literals (spec section 4.7d, AC-3, AC-21).

      Note: the vpc primitive was vendored in-repo (providers/aws/primitives/vpc)
      and is now consumed via a const-source variable (vpc_source), so it is NOT
      an external-exempt module -- it is handled by classes (a), (b), and (c) like
      every other in-repo const-source.

Files under examples/ directories are allowlisted (spec section 4.2) and never flagged.
Files under hidden directories (e.g. .terraform/) are excluded from scanning.

On any finding the script exits non-zero and prints ERROR:-shaped messages to stderr,
naming the file, line, offending construct, and remediation.

Configuration read from parameters, never inline literals:
  CONST_SOURCE_GUARD_EXTERNAL_HOST -- git host for the external terraform-modules org
                                      (default: github.com/caylent-solutions/terraform-modules)
  CONST_SOURCE_GUARD_SOURCE_VAR_SUFFIX -- suffix that marks a child-source variable
                                          (default: _source)
  CONST_SOURCE_GUARD_EXAMPLES_ALLOWLIST_DIR -- allowlist directory name
                                               (default: examples)
  CONST_SOURCE_GUARD_EXTERNAL_MODULE_NAMES -- comma-separated list of module block names
                                              that must stay as external pinned literals
                                              (default: budget,dynamodb-table)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Configuration -- all knobs read from environment variables.
# ---------------------------------------------------------------------------

_DEFAULT_EXTERNAL_HOST = "github.com/caylent-solutions/terraform-modules"
_DEFAULT_SOURCE_VAR_SUFFIX = "_source"
_DEFAULT_EXAMPLES_DIR = "examples"
_DEFAULT_EXTERNAL_MODULE_NAMES = "budget,dynamodb-table"

# ---------------------------------------------------------------------------
# Module-level configuration constants (re-read at each function call so
# tests can override env vars between test cases via monkeypatch).
# ---------------------------------------------------------------------------

EXTERNAL_HOST: str = os.environ.get("CONST_SOURCE_GUARD_EXTERNAL_HOST", _DEFAULT_EXTERNAL_HOST)
SOURCE_VAR_SUFFIX: str = os.environ.get(
    "CONST_SOURCE_GUARD_SOURCE_VAR_SUFFIX", _DEFAULT_SOURCE_VAR_SUFFIX
)
EXAMPLES_DIR: str = os.environ.get(
    "CONST_SOURCE_GUARD_EXAMPLES_ALLOWLIST_DIR", _DEFAULT_EXAMPLES_DIR
)


def _parse_external_module_names() -> frozenset[str]:
    """Parse CONST_SOURCE_GUARD_EXTERNAL_MODULE_NAMES into a frozenset."""
    raw = os.environ.get("CONST_SOURCE_GUARD_EXTERNAL_MODULE_NAMES", _DEFAULT_EXTERNAL_MODULE_NAMES)
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


EXTERNAL_MODULE_NAMES: frozenset[str] = _parse_external_module_names()

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches: source = "some-literal" (bare literal source in a module block)
_BARE_LITERAL_SOURCE_RE = re.compile(
    r'^\s*source\s*=\s*"([^"]+)"',
    re.MULTILINE,
)

# Matches: source = var.<name> (var-driven source)
_VAR_SOURCE_RE = re.compile(
    r"^\s*source\s*=\s*var\.(\w+)",
    re.MULTILINE,
)

# Matches a module block opening: module "<name>" {
_MODULE_BLOCK_START_RE = re.compile(
    r'^\s*module\s+"(\w+)"\s*\{',
    re.MULTILINE,
)

# Matches a variable block: variable "<name>" { ... } (non-greedy across newlines)
_VARIABLE_BLOCK_RE = re.compile(
    r'variable\s+"(\w+)"\s*\{([^}]*)\}',
    re.DOTALL,
)

# Matches const = true within a variable block body
_CONST_TRUE_RE = re.compile(r"\bconst\s*=\s*true\b", re.IGNORECASE)

# Matches default = "..." within a variable block body
_DEFAULT_VALUE_RE = re.compile(r'default\s*=\s*"([^"]*)"')

# Matches a git URL: starts with git:: or contains github.com
_GIT_URL_RE = re.compile(r"(git::|github\.com)", re.IGNORECASE)

# Matches an absolute path: starts with /
_ABSOLUTE_PATH_RE = re.compile(r"^/")

# ---------------------------------------------------------------------------
# Custom exceptions (one per forbidden class)
# ---------------------------------------------------------------------------


class BareLiteralSourceError(RuntimeError):
    """Raised when a child module source is a bare literal instead of source = var.<name>_source.

    Corresponds to class (a): spec section 4.7a.
    """


class NonConstSourceVarError(RuntimeError):
    """Raised when a *_source variable used in a source lacks const = true.

    Corresponds to class (b): spec section 4.7b.
    """


class NonRelativeDefaultError(RuntimeError):
    """Raised when a *_source default is not an in-repo relative path.

    Corresponds to class (c): spec section 4.7c.
    """


class ExternalSourceVariableError(RuntimeError):
    """Raised when an external terraform-modules source was variable-ized.

    Corresponds to class (d): spec section 4.7d.
    """


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def collect_module_files(refs_root: pathlib.Path) -> list[pathlib.Path]:
    """Collect all .tf files under refs_root, excluding examples/ and hidden directories.

    Args:
        refs_root: Root of the references tree (providers/aws/references).

    Returns:
        List of pathlib.Path objects for non-excluded .tf files.

    Raises:
        FileNotFoundError: If refs_root does not exist.
    """
    if not refs_root.exists():
        raise FileNotFoundError(
            f"ERROR: refs_root does not exist: {refs_root}\n"
            f"  Pass a valid --root argument pointing to the references directory."
        )

    examples_dir = os.environ.get(
        "CONST_SOURCE_GUARD_EXAMPLES_ALLOWLIST_DIR", _DEFAULT_EXAMPLES_DIR
    )
    tf_files: list[pathlib.Path] = []
    for tf_file in refs_root.rglob("*.tf"):
        # Skip files in examples/ directories
        if examples_dir in tf_file.parts:
            continue
        # Skip files in hidden directories (e.g. .terraform/)
        if any(part.startswith(".") for part in tf_file.parts):
            continue
        tf_files.append(tf_file)
    return tf_files


# ---------------------------------------------------------------------------
# Helper: line-number from character offset
# ---------------------------------------------------------------------------


def _line_number(text: str, char_offset: int) -> int:
    """Return the 1-based line number for a character offset in text."""
    return text.count("\n", 0, char_offset) + 1


# ---------------------------------------------------------------------------
# Helper: find the owning module name for a source declaration
# ---------------------------------------------------------------------------


def _owning_module_name(content: str, source_match_start: int) -> str | None:
    """Return the module block name that contains the source declaration at source_match_start.

    Searches backwards from source_match_start to find the nearest module block
    opening, then verifies no other module block opening intervenes.

    The search scans all module block openings from the start of the file up to
    source_match_start and returns the last one found (nearest preceding block).

    Args:
        content: Full text of the Terraform file.
        source_match_start: Character offset of the source = ... declaration.

    Returns:
        The module block name string, or None if the enclosing block cannot be found.
    """
    # Find all module block openings up to source_match_start.
    # The last match found is the nearest enclosing block for the source declaration.
    last_match = None
    for m in _MODULE_BLOCK_START_RE.finditer(content, 0, source_match_start):
        last_match = m

    if last_match is None:
        return None

    return last_match.group(1)


# ---------------------------------------------------------------------------
# Core scanning functions
# ---------------------------------------------------------------------------


def scan_main_tf(
    main_tf_path: pathlib.Path,
    known_external_modules: set[str] | None = None,
) -> list[str]:
    """Scan a main.tf file for class (a) and class (d) violations.

    Class (a): any in-repo child module source that is a bare literal instead of
               source = var.<name>_source.
    Class (d): any module block whose name is in known_external_modules (or in the
               configured EXTERNAL_MODULE_NAMES) and whose source is var.-driven
               instead of a pinned literal.

    The function collects ALL findings rather than stopping at the first, so
    callers receive the full list of violations.

    Args:
        main_tf_path: Path to the main.tf file.
        known_external_modules: Optional override for the set of module names that
            must stay as external pinned literals. When None, EXTERNAL_MODULE_NAMES
            is used.

    Returns:
        List of ERROR: message strings (empty when clean).

    Raises:
        BareLiteralSourceError: When exactly one class (a) violation is found and no
            other findings exist -- preserves exception-based API for unit tests.
        ExternalSourceVariableError: When exactly one class (d) violation is found
            and no other findings exist -- preserves exception-based API for unit tests.
    """
    external_names = (
        known_external_modules
        if known_external_modules is not None
        else _parse_external_module_names()
    )
    content = main_tf_path.read_text(encoding="utf-8")
    ext_host = os.environ.get("CONST_SOURCE_GUARD_EXTERNAL_HOST", _DEFAULT_EXTERNAL_HOST)
    src_suffix = os.environ.get("CONST_SOURCE_GUARD_SOURCE_VAR_SUFFIX", _DEFAULT_SOURCE_VAR_SUFFIX)

    class_a_findings: list[str] = []
    class_d_findings: list[str] = []

    # Class (d): detect variable-ized external sources.
    # A module block whose name is in external_names must use a literal source, not var.
    for var_match in _VAR_SOURCE_RE.finditer(content):
        var_name = var_match.group(1)
        module_name = _owning_module_name(content, var_match.start())
        if module_name and module_name in external_names:
            line = _line_number(content, var_match.start())
            msg = (
                f"ERROR: External terraform-modules source was variable-ized at "
                f"{main_tf_path}:{line}\n"
                f"  Module '{module_name}' uses source = var.{var_name} but external\n"
                f"  terraform-modules sources must stay as hardcoded pinned literals\n"
                f"  (spec section 4.7d).\n"
                f"  Source var: var.{var_name}\n"
                f"  Remedy: replace var.{var_name} with the pinned literal git URL,\n"
                f'  e.g. source = "git::https://{ext_host}.git//...?ref=.../v<semver>"'
            )
            class_d_findings.append(msg)

    # Class (a): detect bare literal in-repo sources (non-external literals).
    for lit_match in _BARE_LITERAL_SOURCE_RE.finditer(content):
        source_val = lit_match.group(1)
        # External terraform-modules literals are ALLOWED only when the owning module
        # block is one of the sanctioned external names (budget, dynamodb-table).
        # A bare external-host literal in any other in-repo module is still a class (a)
        # violation -- the unconditional skip would allow an external-host URL anywhere.
        if ext_host in source_val:
            owning = _owning_module_name(content, lit_match.start())
            if owning is not None and owning in external_names:
                continue
        line = _line_number(content, lit_match.start())
        msg = (
            f"ERROR: Bare literal in-repo source at {main_tf_path}:{line}\n"
            f'  source = "{source_val}"\n'
            f"  In-repo child module sources must use source = var.<name>{src_suffix}\n"
            f"  (spec section 4.7a).\n"
            f"  Remedy: declare a variable '<name>{src_suffix}' with const = true\n"
            f"  and a relative-path default, then replace the literal with\n"
            f"  source = var.<name>{src_suffix}"
        )
        class_a_findings.append(msg)

    all_findings = class_d_findings + class_a_findings

    # Preserve exception-based API: single-violation tests expect an exception.
    if len(all_findings) == 1:
        if class_d_findings:
            raise ExternalSourceVariableError(all_findings[0])
        raise BareLiteralSourceError(all_findings[0])

    return all_findings


def scan_variables_tf(
    variables_tf_path: pathlib.Path,
    source_var_names: set[str],
) -> list[str]:
    """Scan a variables.tf file for class (b) and class (c) violations.

    Class (b): any *_source variable missing const = true.
    Class (c): any *_source default that is a git URL or absolute path.

    The function collects ALL findings rather than stopping at the first.

    Args:
        variables_tf_path: Path to the variables.tf file.
        source_var_names: Set of *_source variable names used in module source blocks.
                          Only these variables are checked.

    Returns:
        List of ERROR: message strings (empty when clean).

    Raises:
        NonConstSourceVarError: When exactly one class (b) violation is found.
        NonRelativeDefaultError: When exactly one class (c) violation is found.
    """
    content = variables_tf_path.read_text(encoding="utf-8")
    class_b_findings: list[str] = []
    class_c_findings: list[str] = []

    for var_match in _VARIABLE_BLOCK_RE.finditer(content):
        var_name = var_match.group(1)
        if var_name not in source_var_names:
            continue

        block_body = var_match.group(2)
        block_start_offset = var_match.start()
        line = _line_number(content, block_start_offset)

        # Class (b): check for const = true
        if not _CONST_TRUE_RE.search(block_body):
            msg = (
                f"ERROR: *_source variable '{var_name}' lacks const = true at "
                f"{variables_tf_path}:{line}\n"
                f"  The variable '{var_name}' is used as a child module source but does\n"
                f"  not declare const = true (spec section 4.7b).\n"
                f"  Remedy: add 'const = true' to the variable block for '{var_name}'."
            )
            class_b_findings.append(msg)
            # Skip the default check when const is missing (class b supersedes class c
            # for the same variable -- fixing b first then re-checking c is the right flow).
            continue

        # Class (c): check that the default is an in-repo relative path
        default_match = _DEFAULT_VALUE_RE.search(block_body)
        if default_match:
            default_val = default_match.group(1)
            if _GIT_URL_RE.search(default_val):
                msg = (
                    f"ERROR: *_source variable '{var_name}' has a git URL default at "
                    f"{variables_tf_path}:{line}\n"
                    f'  default = "{default_val}"\n'
                    f"  The default for '{var_name}' must be an in-repo relative path\n"
                    f"  (e.g. '../../primitives/<name>' or '../<ref-name>'),\n"
                    f"  not a git URL (spec section 4.7c).\n"
                    f"  Remedy: set default to the relative path, e.g.:\n"
                    f'    default = "../../primitives/<name>"'
                )
                class_c_findings.append(msg)
            elif _ABSOLUTE_PATH_RE.search(default_val):
                msg = (
                    f"ERROR: *_source variable '{var_name}' has an absolute path default at "
                    f"{variables_tf_path}:{line}\n"
                    f'  default = "{default_val}"\n'
                    f"  The default for '{var_name}' must be an in-repo relative path,\n"
                    f"  not an absolute path (spec section 4.7c).\n"
                    f"  Remedy: set default to the relative path, e.g.:\n"
                    f'    default = "../../primitives/<name>"'
                )
                class_c_findings.append(msg)

    all_findings = class_b_findings + class_c_findings

    # Preserve exception-based API: single-violation tests expect an exception.
    if len(all_findings) == 1:
        if class_b_findings:
            raise NonConstSourceVarError(all_findings[0])
        raise NonRelativeDefaultError(all_findings[0])

    return all_findings


# ---------------------------------------------------------------------------
# Orchestration: per-module-dir and whole-tree scanning
# ---------------------------------------------------------------------------


def _extract_source_var_names_from_main(content: str) -> set[str]:
    """Extract the set of variable names used as sources (source = var.<name>) from main.tf.

    Only variable names that match the configured source-var suffix are returned.

    Args:
        content: Text content of main.tf.

    Returns:
        Set of variable names (e.g. {'kms_source', 'bucket_source'}).
    """
    suffix = os.environ.get("CONST_SOURCE_GUARD_SOURCE_VAR_SUFFIX", _DEFAULT_SOURCE_VAR_SUFFIX)
    var_names: set[str] = set()
    for match in _VAR_SOURCE_RE.finditer(content):
        name = match.group(1)
        if name.endswith(suffix):
            var_names.add(name)
    return var_names


def _collect_findings_for_module_dir(
    module_dir: pathlib.Path,
    external_module_names: frozenset[str],
) -> list[str]:
    """Collect all findings for a single reference module directory.

    Scans main.tf (if present) for classes (a) and (d), then variables.tf
    (if present) for classes (b) and (c). When scan_main_tf or scan_variables_tf
    raise their single-finding exceptions, the message is captured into the list.

    Args:
        module_dir: Directory of the reference module.
        external_module_names: Module names whose source must stay as a pinned literal.

    Returns:
        List of error message strings (empty when clean).
    """
    findings: list[str] = []

    main_tf = module_dir / "main.tf"
    variables_tf = module_dir / "variables.tf"

    # Read source var names from main.tf for cross-file class (b)/(c) validation
    source_var_names: set[str] = set()
    if main_tf.exists():
        content = main_tf.read_text(encoding="utf-8")
        source_var_names = _extract_source_var_names_from_main(content)

    # Scan main.tf for classes (a) and (d)
    if main_tf.exists():
        try:
            result = scan_main_tf(main_tf, known_external_modules=set(external_module_names))
            findings.extend(result)
        except (BareLiteralSourceError, ExternalSourceVariableError) as exc:
            findings.append(str(exc))

    # Scan variables.tf for classes (b) and (c)
    if variables_tf.exists() and source_var_names:
        try:
            result = scan_variables_tf(variables_tf, source_var_names=source_var_names)
            findings.extend(result)
        except (NonConstSourceVarError, NonRelativeDefaultError) as exc:
            findings.append(str(exc))

    return findings


def _discover_module_dirs(refs_root: pathlib.Path) -> list[pathlib.Path]:
    """Discover the top-level reference module directories under refs_root.

    Returns only the direct subdirectories of refs_root that are not hidden
    or allowlisted.

    Args:
        refs_root: Root of the references tree.

    Returns:
        List of module directory paths, sorted for determinism.
    """
    examples_dir = os.environ.get(
        "CONST_SOURCE_GUARD_EXAMPLES_ALLOWLIST_DIR", _DEFAULT_EXAMPLES_DIR
    )
    module_dirs: list[pathlib.Path] = []
    if not refs_root.is_dir():
        return module_dirs
    for child in sorted(refs_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        if child.name == examples_dir:
            continue
        module_dirs.append(child)
    return module_dirs


def run_const_source_guard(refs_root: pathlib.Path) -> list[str]:
    """Run the const-source guard across all reference module directories.

    Walks refs_root, discovers each top-level reference module, and checks it
    against all four forbidden classes. Returns all findings collected across
    the entire tree.

    Args:
        refs_root: Root of the references tree (providers/aws/references).

    Returns:
        List of error message strings. Empty when the tree is clean.

    Raises:
        FileNotFoundError: If refs_root does not exist.
    """
    if not refs_root.exists():
        raise FileNotFoundError(
            f"ERROR: refs_root does not exist: {refs_root}\n"
            f"  Pass a valid --root argument pointing to the references directory."
        )

    external_names = _parse_external_module_names()
    all_findings: list[str] = []
    for module_dir in _discover_module_dirs(refs_root):
        module_findings = _collect_findings_for_module_dir(module_dir, external_names)
        all_findings.extend(module_findings)

    return all_findings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(refs_root: str | None = None) -> int:
    """Entry point for `uv run python -m scripts.tf_guard_const_sources`.

    Args:
        refs_root: Optional path to the references root (for programmatic use).
                   When None, the value is taken from the --root CLI argument.

    Returns:
        0 when the tree is clean, 1 when any violations are found or on error.
    """
    if refs_root is None:
        parser = argparse.ArgumentParser(
            description=(
                "Guard that rejects forbidden const-source patterns in reference modules "
                "(spec section 4.7)."
            )
        )
        parser.add_argument(
            "--root",
            required=True,
            help="Path to the references root directory (providers/aws/references).",
        )
        args = parser.parse_args()
        refs_root = args.root

    refs_path = pathlib.Path(refs_root)
    if not refs_path.exists():
        print(
            f"ERROR: refs_root does not exist: {refs_path}\n"
            f"  Pass a valid --root argument pointing to the references directory.",
            file=sys.stderr,
        )
        return 1

    try:
        findings = run_const_source_guard(refs_root=refs_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        print("\ntf-guard-const-sources FAILED: invalid refs_root.", file=sys.stderr)
        return 1

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        print(
            f"\ntf-guard-const-sources FAILED: {len(findings)} violation(s) found.",
            file=sys.stderr,
        )
        return 1

    print("tf-guard-const-sources PASSED: all reference module sources are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
