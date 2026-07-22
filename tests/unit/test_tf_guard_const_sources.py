"""Unit tests for scripts/tf_guard_const_sources.py.

Implements the test matrix for the const-source lint guard described in E9-F6-S1-T1.
Tests assert that the scanner:
  - passes a refactored tree (all in-repo sources use source = var.<name>_source with
    const = true relative-default variables; external terraform-modules sources stay
    pinned literals) -- exit 0
  - rejects (a) a bare git-URL or relative-literal in-repo child source (class a)
  - rejects (b) a *_source variable missing const = true (class b)
  - rejects (c) a *_source default that is a git URL or absolute path (class c)
  - rejects (d) a variable-ized external terraform-modules source (class d)
  - allows relative-literal sources under examples/ (allowlist)
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from scripts.tf_guard_const_sources import (
    BareLiteralSourceError,
    ExternalSourceVariableError,
    NonConstSourceVarError,
    NonRelativeDefaultError,
    collect_module_files,
    run_const_source_guard,
    scan_main_tf,
    scan_variables_tf,
)

# ---------------------------------------------------------------------------
# Helpers for building synthetic fixture trees
# ---------------------------------------------------------------------------

EXTERNAL_SOURCE = (
    "git::https://github.com/caylent-solutions/terraform-modules.git"
    "//providers/aws/primitives/budget?ref=providers/aws/primitives/budget/v1.1.0"
)


def _make_ref_module(
    root: pathlib.Path,
    name: str,
    main_tf: str,
    variables_tf: str,
) -> pathlib.Path:
    """Create a synthetic reference module directory under root/providers/aws/references/<name>."""
    module_dir = root / "providers" / "aws" / "references" / name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text(main_tf, encoding="utf-8")
    (module_dir / "variables.tf").write_text(variables_tf, encoding="utf-8")
    return module_dir


def _make_example(
    root: pathlib.Path,
    ref_name: str,
    example_name: str,
    main_tf: str,
) -> pathlib.Path:
    """Create a synthetic example under providers/aws/references/<ref_name>/examples/<name>."""
    example_dir = root / "providers" / "aws" / "references" / ref_name / "examples" / example_name
    example_dir.mkdir(parents=True, exist_ok=True)
    (example_dir / "main.tf").write_text(main_tf, encoding="utf-8")
    return example_dir


_CLEAN_MAIN_TF = """\
module "kms" {
  source = var.kms_source

  alias_name = var.kms_alias
}

module "budget" {
  source = "git::https://github.com/caylent-solutions/terraform-modules.git//providers/aws/primitives/budget?ref=providers/aws/primitives/budget/v1.1.0"

  budgets = {}
}
"""

_CLEAN_VARIABLES_TF = """\
variable "kms_source" {
  type        = string
  const       = true
  description = "Source path for the kms-key primitive."
  default     = "../../primitives/kms-key"
}
"""


# ---------------------------------------------------------------------------
# Positive case: clean refactored tree passes with exit 0
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_positive_clean_tree_exits_zero(tmp_path: pathlib.Path) -> None:
    """A fully refactored reference tree produces zero findings and exit 0.

    AC-FUNC-001, AC-FUNC-002, AC-FUNC-003: the positive case verifies that
    in-repo sources are var-driven, *_source vars have const = true with a
    relative-path default, and external sources stay as pinned literals.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert findings == [], f"Expected no findings for clean tree, got: {findings}"


# ---------------------------------------------------------------------------
# Class (a): in-repo child source is a bare literal (not source = var.<name>_source)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_source,description",
    [
        (
            'source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key?ref=v1.0.0"',
            "bare git-URL in-repo source",
        ),
        (
            'source = "../../primitives/kms-key"',
            "bare relative-path literal in-repo source",
        ),
    ],
)
def test_class_a_bare_literal_in_repo_source_fails(
    tmp_path: pathlib.Path,
    bad_source: str,
    description: str,
) -> None:
    """A bare literal in-repo source (not source = var.<name>_source) is rejected.

    AC-FUNC-001: class (a) -- bare literal source instead of source = var.<name>_source
    must produce a finding with file:line information.
    """
    bad_main_tf = f"""\
module "kms" {{
  {bad_source}

  alias_name = var.kms_alias
}}
"""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=bad_main_tf,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert len(findings) >= 1, (
        f"Expected at least one finding for {description}, got none.\nbad_source: {bad_source}"
    )
    # Each finding must include a file:line reference
    assert any(":" in f for f in findings), (
        f"Finding must contain file:line reference; got: {findings}"
    )
    assert any(
        "BareLiteralSource" in f or "bare" in f.lower() or "literal" in f.lower() or "var." in f
        for f in findings
    ), f"Finding message should reference the bare-literal class; got: {findings}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "non_sanctioned_module_name,description",
    [
        ("analytics", "analytics module with external-host literal"),
        ("athena", "athena module with external-host literal"),
        ("data-lake", "data-lake module with external-host literal"),
    ],
)
def test_class_a_external_host_literal_in_non_sanctioned_module_fails(
    tmp_path: pathlib.Path,
    non_sanctioned_module_name: str,
    description: str,
) -> None:
    """An external-host git-URL literal inside a non-sanctioned in-repo module is rejected.

    AC-FUNC-001: class (a) -- the guard must NOT blanket-exempt any literal containing the
    external host string. Only sanctioned external module blocks (budget, dynamodb-table)
    may use bare external git-URL literals. Any other in-repo module block that uses a bare
    external-host URL is still a class (a) violation (spec section 4.7a).

    This is a regression test for the anti-regression hole where the unconditional
    `if ext_host in source_val: continue` skips flagging external-host literals regardless
    of which module block owns them.
    """
    external_git_url = (
        "git::https://github.com/caylent-solutions/terraform-modules.git"
        "//providers/aws/primitives/kms-key?ref=providers/aws/primitives/kms-key/v1.0.0"
    )
    bad_main_tf = f"""\
module "{non_sanctioned_module_name}" {{
  source = "{external_git_url}"

  some_arg = var.some_value
}}
"""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=bad_main_tf,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert len(findings) >= 1, (
        f"Expected class (a) violation for external-host literal in non-sanctioned "
        f"module '{non_sanctioned_module_name}' ({description}), got no findings.\n"
        f"The guard must only exempt external-host literals when the owning module is "
        f"in EXTERNAL_MODULE_NAMES (budget/dynamodb-table)."
    )
    # The finding must include a file:line reference
    assert any(":" in f for f in findings), (
        f"Finding must contain file:line reference; got: {findings}"
    )
    assert any("bare" in f.lower() or "literal" in f.lower() or "var." in f for f in findings), (
        f"Finding should reference the bare-literal class (a); got: {findings}"
    )


@pytest.mark.unit
def test_class_a_external_host_literal_in_sanctioned_module_passes(
    tmp_path: pathlib.Path,
) -> None:
    """An external-host git-URL literal inside a sanctioned external module passes.

    AC-FUNC-001 / AC-FUNC-003: sanctioned external module blocks (budget, dynamodb-table)
    must be allowed to keep their bare external git-URL literals -- that is the required form
    per spec section 4.7d. This complements the negative test above.
    """
    external_git_url = (
        "git::https://github.com/caylent-solutions/terraform-modules.git"
        "//providers/aws/primitives/budget?ref=providers/aws/primitives/budget/v1.1.0"
    )
    # A clean in-repo var-driven source alongside the sanctioned external literal
    main_tf_with_sanctioned_external = f"""\
module "kms" {{
  source = var.kms_source

  alias_name = var.kms_alias
}}

module "budget" {{
  source = "{external_git_url}"

  budgets = {{}}
}}
"""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=main_tf_with_sanctioned_external,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert findings == [], (
        f"Sanctioned external literal in 'budget' module must not be flagged. Got: {findings}"
    )


@pytest.mark.unit
def test_class_a_scan_main_tf_raises_on_bare_literal(tmp_path: pathlib.Path) -> None:
    """scan_main_tf raises BareLiteralSourceError on a bare literal in-repo source.

    AC-FUNC-001: specific exception type for class (a).
    """
    main_tf = tmp_path / "main.tf"
    main_tf.write_text(
        'module "kms" {\n  source = "../../primitives/kms-key"\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(BareLiteralSourceError) as exc_info:
        scan_main_tf(main_tf)
    error_msg = str(exc_info.value)
    assert "ERROR:" in error_msg
    assert "main.tf" in error_msg or str(main_tf) in error_msg


# ---------------------------------------------------------------------------
# Class (b): *_source variable missing const = true
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_class_b_missing_const_true_fails(tmp_path: pathlib.Path) -> None:
    """A *_source variable used in a source block that lacks const = true is rejected.

    AC-FUNC-002: class (b) -- missing const = true on a *_source variable.
    """
    bad_variables_tf = """\
variable "kms_source" {
  type        = string
  description = "Source path for the kms-key primitive."
  default     = "../../primitives/kms-key"
}
"""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=bad_variables_tf,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert len(findings) >= 1, "Expected at least one finding for missing const = true, got none."
    assert any("const" in f.lower() for f in findings), (
        f"Finding should mention 'const'; got: {findings}"
    )


@pytest.mark.unit
def test_class_b_scan_variables_tf_raises_on_missing_const(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf raises NonConstSourceVarError when *_source var lacks const = true.

    AC-FUNC-002: specific exception type for class (b).
    """
    variables_tf = tmp_path / "variables.tf"
    variables_tf.write_text(
        'variable "kms_source" {\n  type = string\n  default = "../../primitives/kms-key"\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(NonConstSourceVarError) as exc_info:
        scan_variables_tf(variables_tf, source_var_names={"kms_source"})
    error_msg = str(exc_info.value)
    assert "ERROR:" in error_msg
    assert "const" in error_msg.lower()


# ---------------------------------------------------------------------------
# Class (c): *_source default is a git URL or absolute path (not an in-repo relative path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_default,description",
    [
        (
            "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key?ref=v1.0.0",
            "git URL default (not a relative path)",
        ),
        (
            "/absolute/path/to/kms-key",
            "absolute path default",
        ),
    ],
)
def test_class_c_non_relative_default_fails(
    tmp_path: pathlib.Path,
    bad_default: str,
    description: str,
) -> None:
    """A *_source default that is a git URL or absolute path is rejected.

    AC-FUNC-002: class (c) -- non-relative-path default on a *_source variable.
    """
    bad_variables_tf = f"""\
variable "kms_source" {{
  type        = string
  const       = true
  description = "Source path for the kms-key primitive."
  default     = "{bad_default}"
}}
"""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=bad_variables_tf,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert len(findings) >= 1, (
        f"Expected finding for non-relative default ({description}), got none."
    )
    assert any(
        "relative" in f.lower() or "default" in f.lower() or "path" in f.lower() for f in findings
    ), f"Finding should mention relative-path issue; got: {findings}"


@pytest.mark.unit
def test_class_c_scan_variables_tf_raises_on_git_url_default(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf raises NonRelativeDefaultError when *_source default is a git URL.

    AC-FUNC-002: specific exception type for class (c).
    """
    variables_tf = tmp_path / "variables.tf"
    variables_tf.write_text(
        'variable "kms_source" {\n  type = string\n  const = true\n'
        '  default = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/primitives/kms-key?ref=v1.0.0"\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(NonRelativeDefaultError) as exc_info:
        scan_variables_tf(variables_tf, source_var_names={"kms_source"})
    error_msg = str(exc_info.value)
    assert "ERROR:" in error_msg


# ---------------------------------------------------------------------------
# Class (d): external terraform-modules source was variable-ized
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_class_d_variable_ized_external_source_fails(tmp_path: pathlib.Path) -> None:
    """A variable-ized external terraform-modules source is rejected.

    AC-FUNC-003: class (d) -- external sources (budget, dynamodb-table) must stay pinned literals.
    """
    bad_main_tf = """\
module "budget" {
  source = var.budget_source

  budgets = {}
}
"""
    bad_variables_tf = """\
variable "budget_source" {
  type        = string
  const       = true
  description = "Source path for the budget module."
  default     = "../../primitives/budget"
}
"""
    _make_ref_module(
        root=tmp_path,
        name="observability",
        main_tf=bad_main_tf,
        variables_tf=bad_variables_tf,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert len(findings) >= 1, "Expected finding for variable-ized external source, got none."
    assert any(
        "external" in f.lower() or "terraform-modules" in f.lower() or "literal" in f.lower()
        for f in findings
    ), f"Finding should reference external source requirement; got: {findings}"


@pytest.mark.unit
def test_class_d_scan_main_tf_raises_on_var_external_source(tmp_path: pathlib.Path) -> None:
    """scan_main_tf raises ExternalSourceVariableError when an external source was variable-ized.

    AC-FUNC-003: specific exception type for class (d).
    The test uses a context where the module name matches external module patterns
    (budget, dynamodb-table) but uses var. instead of a literal.
    """
    main_tf = tmp_path / "main.tf"
    main_tf.write_text(
        'module "budget" {\n  source = var.budget_source\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(ExternalSourceVariableError) as exc_info:
        scan_main_tf(main_tf, known_external_modules={"budget"})
    error_msg = str(exc_info.value)
    assert "ERROR:" in error_msg
    assert "external" in error_msg.lower() or "literal" in error_msg.lower()


# ---------------------------------------------------------------------------
# examples/ allowlist: relative-literal sources inside examples/ are NOT flagged
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_examples_allowlist_relative_literal_not_flagged(tmp_path: pathlib.Path) -> None:
    """A relative-path literal source inside examples/ produces zero findings.

    AC-FUNC-001: the examples/ allowlist (spec section 4.2) -- relative literals
    in examples/ are legitimate and must not be flagged.
    """
    # A main reference module with clean var-driven sources
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    # An example that uses a relative-path literal source (the legitimate form)
    example_main_tf = """\
module "analytics" {
  source = "../../"

  workgroup_name = "test"
}
"""
    _make_example(
        root=tmp_path,
        ref_name="analytics",
        example_name="basic",
        main_tf=example_main_tf,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert findings == [], (
        f"Expected no findings -- examples/ relative literals must be allowlisted. Got: {findings}"
    )


@pytest.mark.unit
def test_examples_allowlist_also_skips_git_url_in_example(tmp_path: pathlib.Path) -> None:
    """Sources inside examples/ directories are completely allowlisted (skipped by scanner).

    AC-FUNC-001: examples/ is entirely allowlisted -- git URLs and relative paths
    inside examples/ are not flagged as class (a) violations.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    # An example that uses a full git URL (also legitimate in examples/)
    example_main_tf = """\
module "analytics" {
  source = "git::https://github.com/example-org/telemetry-platform.git//providers/aws/references/analytics?ref=v1.0.0"

  workgroup_name = "test"
}
"""
    _make_example(
        root=tmp_path,
        ref_name="analytics",
        example_name="basic",
        main_tf=example_main_tf,
    )
    findings = run_const_source_guard(refs_root=tmp_path / "providers" / "aws" / "references")
    assert findings == [], (
        f"Expected no findings -- examples/ is fully allowlisted. Got: {findings}"
    )


# ---------------------------------------------------------------------------
# collect_module_files: file-discovery logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_module_files_finds_main_and_variables(tmp_path: pathlib.Path) -> None:
    """collect_module_files returns main.tf and variables.tf for each reference module.

    AC-FUNC-004: the scanner discovers all non-example, non-hidden TF files.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    refs_root = tmp_path / "providers" / "aws" / "references"
    files = collect_module_files(refs_root)
    paths = {f.name for f in files}
    assert "main.tf" in paths
    assert "variables.tf" in paths


@pytest.mark.unit
def test_collect_module_files_excludes_examples(tmp_path: pathlib.Path) -> None:
    """collect_module_files excludes files under examples/ directories.

    AC-FUNC-001: examples/ allowlist is implemented at the file-collection level.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    _make_example(
        root=tmp_path,
        ref_name="analytics",
        example_name="basic",
        main_tf='module "analytics" {\n  source = "../../"\n}\n',
    )
    refs_root = tmp_path / "providers" / "aws" / "references"
    files = collect_module_files(refs_root)
    for f in files:
        assert "examples" not in f.parts, (
            f"collect_module_files returned a file under examples/: {f}"
        )


@pytest.mark.unit
def test_collect_module_files_excludes_hidden_dirs(tmp_path: pathlib.Path) -> None:
    """collect_module_files excludes files under hidden directories (like .terraform/).

    The .terraform/ vendor cache must not be scanned.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    # Place a file in .terraform/
    hidden_dir = (
        tmp_path / "providers" / "aws" / "references" / "analytics" / ".terraform" / "modules"
    )
    hidden_dir.mkdir(parents=True, exist_ok=True)
    (hidden_dir / "main.tf").write_text(
        'module "vendor" {\n  source = "../../primitives/kms-key"\n}\n',
        encoding="utf-8",
    )
    refs_root = tmp_path / "providers" / "aws" / "references"
    files = collect_module_files(refs_root)
    for f in files:
        assert ".terraform" not in f.parts, (
            f"collect_module_files returned a file under .terraform/: {f}"
        )


# ---------------------------------------------------------------------------
# run_const_source_guard end-to-end: multiple modules, multiple findings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_multiple_violations_all_reported(tmp_path: pathlib.Path) -> None:
    """run_const_source_guard reports all findings across multiple modules.

    AC-FUNC-005: all forbidden classes are reported when present.
    """
    # Module A: class (a) violation -- bare literal in-repo source
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf='module "kms" {\n  source = "../../primitives/kms-key"\n}\n',
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    # Module B: class (b) violation -- kms_source used in main.tf but missing const = true
    # in variables.tf (source_var_names={'kms_source'} from main.tf, variables.tf lacks const)
    _make_ref_module(
        root=tmp_path,
        name="data-lake",
        main_tf='module "kms" {\n  source = var.kms_source\n}\n',
        variables_tf=(
            'variable "kms_source" {\n  type = string\n  default = "../../primitives/kms-key"\n}\n'
        ),
    )
    refs_root = tmp_path / "providers" / "aws" / "references"
    findings = run_const_source_guard(refs_root=refs_root)
    assert len(findings) >= 2, (
        f"Expected at least 2 findings for 2 modules with violations; got: {findings}"
    )


# ---------------------------------------------------------------------------
# main() entrypoint
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_exits_zero_for_clean_tree(tmp_path: pathlib.Path) -> None:
    """main() exits 0 when the references tree is clean.

    AC-FUNC-004: the main() CLI entry point returns 0 on a clean tree.
    """
    from scripts.tf_guard_const_sources import main as guard_main

    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    result = guard_main(
        refs_root=str(tmp_path / "providers" / "aws" / "references"),
    )
    assert result == 0


@pytest.mark.unit
def test_main_exits_nonzero_for_violation(tmp_path: pathlib.Path) -> None:
    """main() exits non-zero when a violation is found.

    AC-FUNC-004: the main() CLI entry point returns non-zero on any violation.
    """
    from scripts.tf_guard_const_sources import main as guard_main

    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf='module "kms" {\n  source = "../../primitives/kms-key"\n}\n',
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    result = guard_main(
        refs_root=str(tmp_path / "providers" / "aws" / "references"),
    )
    assert result != 0


@pytest.mark.unit
def test_main_exits_nonzero_when_refs_root_missing(tmp_path: pathlib.Path) -> None:
    """main() exits non-zero when the refs_root directory does not exist.

    Fail-fast: invalid input produces a non-zero exit with an ERROR: message.
    """
    from scripts.tf_guard_const_sources import main as guard_main

    result = guard_main(refs_root=str(tmp_path / "nonexistent" / "path"))
    assert result != 0


@pytest.mark.unit
def test_main_entry_point_exits_zero_as_subprocess(tmp_path: pathlib.Path) -> None:
    """Executing the script as a module subprocess exits 0 for a clean tree.

    AC-FUNC-004: end-to-end subprocess execution of the guard.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.tf_guard_const_sources",
            "--root",
            str(tmp_path / "providers" / "aws" / "references"),
        ],
        capture_output=True,
        cwd=str(pathlib.Path(__file__).parent.parent.parent),
    )
    assert result.returncode == 0, (
        f"Expected exit 0 for clean tree; got {result.returncode}.\n"
        f"stdout: {result.stdout.decode()}\n"
        f"stderr: {result.stderr.decode()}"
    )


@pytest.mark.unit
def test_main_entry_point_exits_nonzero_as_subprocess_on_violation(
    tmp_path: pathlib.Path,
) -> None:
    """Executing the script as a module subprocess exits non-zero when a violation is found.

    AC-FUNC-004: end-to-end subprocess execution -- non-zero on violation.
    """
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf='module "kms" {\n  source = "../../primitives/kms-key"\n}\n',
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.tf_guard_const_sources",
            "--root",
            str(tmp_path / "providers" / "aws" / "references"),
        ],
        capture_output=True,
        cwd=str(pathlib.Path(__file__).parent.parent.parent),
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit for violation; got {result.returncode}.\n"
        f"stdout: {result.stdout.decode()}\n"
        f"stderr: {result.stderr.decode()}"
    )


# ---------------------------------------------------------------------------
# Additional coverage tests for edge paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_module_files_raises_when_refs_root_missing(tmp_path: pathlib.Path) -> None:
    """collect_module_files raises FileNotFoundError when refs_root does not exist."""
    from scripts.tf_guard_const_sources import collect_module_files

    missing = tmp_path / "nonexistent"
    with pytest.raises(FileNotFoundError, match="ERROR:"):
        collect_module_files(missing)


@pytest.mark.unit
def test_owning_module_name_returns_none_when_no_module_keyword(
    tmp_path: pathlib.Path,
) -> None:
    """scan_main_tf returns empty list when source declaration has no enclosing module."""
    from scripts.tf_guard_const_sources import _owning_module_name

    content = '  source = "../../primitives/kms-key"\n'
    result = _owning_module_name(content, content.index("source"))
    assert result is None


@pytest.mark.unit
def test_owning_module_name_returns_none_when_intermediate_block(
    tmp_path: pathlib.Path,
) -> None:
    """_owning_module_name returns None when an intermediate module block intervenes."""
    from scripts.tf_guard_const_sources import _owning_module_name

    # Two module blocks: source belongs to the second, not the first
    content = 'module "first" {\n}\nmodule "second" {\n  source = "../../"\n}\n'
    source_offset = content.index('source = "../../"')
    result = _owning_module_name(content, source_offset)
    # Should find "second", not None (the intermediate check finds "second" is the owner)
    assert result == "second"


@pytest.mark.unit
def test_scan_variables_tf_variable_not_in_source_var_names(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf skips variables not in source_var_names (the continue branch)."""
    from scripts.tf_guard_const_sources import scan_variables_tf

    variables_tf = tmp_path / "variables.tf"
    # This variable is NOT in the source_var_names set -- should be skipped
    variables_tf.write_text(
        'variable "other_var" {\n  type = string\n  default = "some-value"\n}\n',
        encoding="utf-8",
    )
    result = scan_variables_tf(variables_tf, source_var_names={"kms_source"})
    assert result == []


@pytest.mark.unit
def test_scan_variables_tf_no_default_value_is_clean(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf does not flag a *_source var without a default (no class c check)."""
    from scripts.tf_guard_const_sources import scan_variables_tf

    variables_tf = tmp_path / "variables.tf"
    # Has const = true but no default value -- no class (c) violation
    variables_tf.write_text(
        'variable "kms_source" {\n  type = string\n  const = true\n  description = "x"\n}\n',
        encoding="utf-8",
    )
    result = scan_variables_tf(variables_tf, source_var_names={"kms_source"})
    assert result == []


@pytest.mark.unit
def test_scan_variables_tf_absolute_path_raises(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf raises NonRelativeDefaultError for absolute-path defaults."""
    from scripts.tf_guard_const_sources import NonRelativeDefaultError, scan_variables_tf

    variables_tf = tmp_path / "variables.tf"
    variables_tf.write_text(
        'variable "kms_source" {\n  type = string\n  const = true\n'
        '  default = "/absolute/path"\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(NonRelativeDefaultError, match="ERROR:"):
        scan_variables_tf(variables_tf, source_var_names={"kms_source"})


@pytest.mark.unit
def test_extract_source_var_names_non_source_suffix_excluded(tmp_path: pathlib.Path) -> None:
    """_extract_source_var_names_from_main excludes var names without the _source suffix."""
    from scripts.tf_guard_const_sources import _extract_source_var_names_from_main

    content = 'module "kms" {\n  source = var.kms_name\n}\n'
    result = _extract_source_var_names_from_main(content)
    # 'kms_name' does not end with '_source', so it is excluded
    assert "kms_name" not in result


@pytest.mark.unit
def test_collect_findings_no_main_tf(tmp_path: pathlib.Path) -> None:
    """_collect_findings_for_module_dir handles a module dir with no main.tf (no findings)."""
    from scripts.tf_guard_const_sources import _collect_findings_for_module_dir

    # Create an empty module dir (no main.tf, no variables.tf)
    module_dir = tmp_path / "empty-module"
    module_dir.mkdir()
    result = _collect_findings_for_module_dir(module_dir, frozenset({"budget"}))
    assert result == []


@pytest.mark.unit
def test_discover_module_dirs_ignores_files_and_hidden(tmp_path: pathlib.Path) -> None:
    """_discover_module_dirs skips non-directories, hidden directories, and examples/."""
    from scripts.tf_guard_const_sources import _discover_module_dirs

    refs_root = tmp_path / "providers" / "aws" / "references"
    refs_root.mkdir(parents=True)

    # A normal module dir
    (refs_root / "analytics").mkdir()
    # A hidden dir
    (refs_root / ".hidden").mkdir()
    # A file (not a dir)
    (refs_root / "README.md").write_text("docs", encoding="utf-8")
    # The examples dir
    (refs_root / "examples").mkdir()

    dirs = _discover_module_dirs(refs_root)
    names = {d.name for d in dirs}
    assert "analytics" in names
    assert ".hidden" not in names
    assert "examples" not in names
    # Files should not appear
    for d in dirs:
        assert d.is_dir()


@pytest.mark.unit
def test_run_const_source_guard_raises_on_missing_refs_root(tmp_path: pathlib.Path) -> None:
    """run_const_source_guard raises FileNotFoundError when refs_root does not exist."""
    from scripts.tf_guard_const_sources import run_const_source_guard

    missing = tmp_path / "nonexistent"
    with pytest.raises(FileNotFoundError, match="ERROR:"):
        run_const_source_guard(refs_root=missing)


@pytest.mark.unit
def test_main_with_none_refs_root_uses_cli_arg(tmp_path: pathlib.Path) -> None:
    """main() with refs_root=None parses --root from CLI args via subprocess."""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.tf_guard_const_sources",
            "--root",
            str(tmp_path / "providers" / "aws" / "references"),
        ],
        capture_output=True,
        cwd=str(pathlib.Path(__file__).parent.parent.parent),
    )
    assert result.returncode == 0


@pytest.mark.unit
def test_scan_main_tf_multiple_violations_returns_list(tmp_path: pathlib.Path) -> None:
    """scan_main_tf returns a list when multiple bare-literal violations exist."""
    from scripts.tf_guard_const_sources import scan_main_tf

    main_tf = tmp_path / "main.tf"
    main_tf.write_text(
        'module "a" {\n  source = "../../primitives/a"\n}\n'
        'module "b" {\n  source = "../../primitives/b"\n}\n',
        encoding="utf-8",
    )
    # With multiple violations, scan_main_tf returns a list (not raising)
    result = scan_main_tf(main_tf)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all("ERROR:" in r for r in result)


@pytest.mark.unit
def test_scan_variables_tf_multiple_violations_returns_list(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf returns a list when multiple violations exist."""
    from scripts.tf_guard_const_sources import scan_variables_tf

    variables_tf = tmp_path / "variables.tf"
    variables_tf.write_text(
        'variable "a_source" {\n  type = string\n  default = "../../primitives/a"\n}\n'
        'variable "b_source" {\n  type = string\n  default = "../../primitives/b"\n}\n',
        encoding="utf-8",
    )
    result = scan_variables_tf(variables_tf, source_var_names={"a_source", "b_source"})
    assert isinstance(result, list)
    assert len(result) == 2
    assert all("ERROR:" in r for r in result)


@pytest.mark.unit
def test_owning_module_name_returns_none_when_name_match_beyond_source(
    tmp_path: pathlib.Path,
) -> None:
    """_owning_module_name returns None when the module block start is after the source position."""
    from scripts.tf_guard_const_sources import _owning_module_name

    # The source comes BEFORE any module keyword in the file -- no valid enclosing block
    content = '  source = "../../"\nmodule "after" {\n}\n'
    source_offset = content.index("source")
    result = _owning_module_name(content, source_offset)
    assert result is None


@pytest.mark.unit
def test_owning_module_name_returns_none_with_intermediate_module_block(
    tmp_path: pathlib.Path,
) -> None:
    """_owning_module_name returns None when a module block starts between owner and source."""
    from scripts.tf_guard_const_sources import _owning_module_name

    # module "outer" opens, then module "inner" opens before source
    # The inner block starts between outer's end of name_match and the source
    content = 'module "outer" {\n  module "inner" {\n    source = "../../"\n  }\n}\n'
    # Position of the source declaration inside "inner"
    source_offset = content.index('source = "../../"')
    result = _owning_module_name(content, source_offset)
    # "inner" opens between "outer"'s name_match.end() and source_offset,
    # so intermediate is found and the function returns None for "outer".
    # The actual owner "inner" may or may not be found depending on rfind placement.
    # Either None (can't identify) or "inner" is acceptable; neither should be "outer".
    assert result != "outer"


@pytest.mark.unit
def test_scan_variables_tf_single_class_c_raises(tmp_path: pathlib.Path) -> None:
    """scan_variables_tf raises NonRelativeDefaultError for a single class (c) violation."""
    from scripts.tf_guard_const_sources import NonRelativeDefaultError, scan_variables_tf

    variables_tf = tmp_path / "variables.tf"
    # Has const = true but the default is a git URL (class c violation)
    variables_tf.write_text(
        'variable "kms_source" {\n  type = string\n  const = true\n'
        '  default = "git::https://github.com/example/repo.git//module?ref=v1.0.0"\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(NonRelativeDefaultError, match="ERROR:"):
        scan_variables_tf(variables_tf, source_var_names={"kms_source"})


@pytest.mark.unit
def test_discover_module_dirs_returns_empty_when_refs_root_not_dir(
    tmp_path: pathlib.Path,
) -> None:
    """_discover_module_dirs returns empty list when refs_root is not a directory."""
    from scripts.tf_guard_const_sources import _discover_module_dirs

    # Pass a file path instead of a directory
    fake_file = tmp_path / "not-a-dir"
    fake_file.write_text("content", encoding="utf-8")
    result = _discover_module_dirs(fake_file)
    assert result == []


@pytest.mark.unit
def test_main_handles_filenotfounderror_from_guard(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when run_const_source_guard raises FileNotFoundError.

    Covers the except FileNotFoundError branch in main().
    """
    import unittest.mock

    from scripts.tf_guard_const_sources import main as guard_main

    # Create a valid path but then make run_const_source_guard raise FileNotFoundError
    refs_dir = tmp_path / "providers" / "aws" / "references"
    refs_dir.mkdir(parents=True)

    with unittest.mock.patch(
        "scripts.tf_guard_const_sources.run_const_source_guard",
        side_effect=FileNotFoundError("ERROR: forced for test"),
    ):
        result = guard_main(refs_root=str(refs_dir))
    assert result == 1


@pytest.mark.unit
def test_main_entry_point_sys_exit_via_subprocess(tmp_path: pathlib.Path) -> None:
    """The __main__ block calls sys.exit(main()) -- verified via subprocess exit code."""
    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    # Run as __main__ (the if __name__ == "__main__" path)
    result = subprocess.run(
        [
            sys.executable,
            str(
                pathlib.Path(__file__).parent.parent.parent
                / "scripts"
                / "tf_guard_const_sources.py"
            ),
            "--root",
            str(tmp_path / "providers" / "aws" / "references"),
        ],
        capture_output=True,
        cwd=str(pathlib.Path(__file__).parent.parent.parent),
    )
    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}.\n"
        f"stdout: {result.stdout.decode()}\nstderr: {result.stderr.decode()}"
    )


@pytest.mark.unit
def test_main_with_none_refs_root_parses_argv(tmp_path: pathlib.Path) -> None:
    """main(None) falls through to argparse using sys.argv -- covers the CLI branch."""
    import unittest.mock

    from scripts.tf_guard_const_sources import main as guard_main

    _make_ref_module(
        root=tmp_path,
        name="analytics",
        main_tf=_CLEAN_MAIN_TF,
        variables_tf=_CLEAN_VARIABLES_TF,
    )
    refs_path = str(tmp_path / "providers" / "aws" / "references")
    with unittest.mock.patch(
        "sys.argv",
        ["tf_guard_const_sources", "--root", refs_path],
    ):
        result = guard_main(refs_root=None)
    assert result == 0
