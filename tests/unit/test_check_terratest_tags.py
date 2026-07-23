"""Unit tests for scripts/check_terratest_tags.py.

Covers all three parts of the tagging contract (spec section 4.3, FR-3):

  1. default_tags block wired to var.project_tag and var.terratest_run_id
  2. Both variables declared as type string with NO defaults
  3. terraform.tfvars carries project_tag = "telemetry-platform" and
     terratest_run_id = "offline-validate"

Also covers:
  - Clean-tree pass (all three parts satisfied)
  - Nonexistent tree path: ERROR + exit 1, no silent empty scan
  - main() exit codes via subprocess and direct call

Violation classes tested:
  - missing default_tags entirely
  - default_tags block not wired to var.project_tag
  - default_tags block not wired to var.terratest_run_id
  - project_tag variable has a default value
  - terratest_run_id variable has a default value
  - project_tag variable is absent from variables file
  - terratest_run_id variable is absent from variables file
  - terraform.tfvars is missing entirely
  - terraform.tfvars missing project_tag key
  - terraform.tfvars missing terratest_run_id key
  - wrong value for project_tag in tfvars
  - wrong value for terratest_run_id in tfvars
  - provider "aws" block absent from example directory
  - variables.tf absent from example directory
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

from scripts.check_terratest_tags import (
    TagsContractError,
    _resolve_roots,
    check_example_dir,
    collect_example_dirs,
    main,
    run_tags_check,
)

# ---------------------------------------------------------------------------
# Helpers for building synthetic fixture trees
# ---------------------------------------------------------------------------

_VALID_VERSIONS_TF = textwrap.dedent("""\
    terraform {
      required_version = ">= 1.15.5"

      required_providers {
        aws = {
          source  = "hashicorp/aws"
          version = ">= 6.49.0"
        }
      }
    }

    provider "aws" {
      region = "us-east-1"

      default_tags {
        tags = {
          Project          = var.project_tag
          terratest-run = var.terratest_run_id
        }
      }
    }
""")

_VALID_VARIABLES_TF = textwrap.dedent("""\
    variable "project_tag" {
      type = string
    }

    variable "terratest_run_id" {
      type = string
    }
""")

_VALID_TFVARS = textwrap.dedent("""\
    project_tag      = "telemetry-platform"
    terratest_run_id = "offline-validate"
""")


def _make_example_dir(
    tmp_path: pathlib.Path,
    module: str = "kms-key",
    kind: str = "primitives",
    example: str = "basic",
    versions_content: str = _VALID_VERSIONS_TF,
    variables_content: str = _VALID_VARIABLES_TF,
    tfvars_content: str | None = _VALID_TFVARS,
) -> pathlib.Path:
    """Build a minimal example directory under a synthetic provider tree."""
    ex_dir = tmp_path / "providers" / "aws" / kind / module / "examples" / example
    ex_dir.mkdir(parents=True, exist_ok=True)
    (ex_dir / "versions.tf").write_text(versions_content, encoding="utf-8")
    (ex_dir / "variables.tf").write_text(variables_content, encoding="utf-8")
    if tfvars_content is not None:
        (ex_dir / "terraform.tfvars").write_text(tfvars_content, encoding="utf-8")
    return ex_dir


# ---------------------------------------------------------------------------
# collect_example_dirs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_example_dirs_finds_nested_examples(tmp_path: pathlib.Path) -> None:
    """collect_example_dirs must discover example dirs under both primitives and references."""
    _make_example_dir(tmp_path, module="kms-key", kind="primitives", example="basic")
    _make_example_dir(tmp_path, module="identity", kind="references", example="default")

    prims_root = tmp_path / "providers" / "aws" / "primitives"
    refs_root = tmp_path / "providers" / "aws" / "references"
    dirs = collect_example_dirs([prims_root, refs_root])

    rel = {d.relative_to(tmp_path) for d in dirs}
    assert pathlib.Path("providers/aws/primitives/kms-key/examples/basic") in rel
    assert pathlib.Path("providers/aws/references/identity/examples/default") in rel


@pytest.mark.unit
def test_collect_example_dirs_nonexistent_root_raises(tmp_path: pathlib.Path) -> None:
    """collect_example_dirs must raise TagsContractError when a root does not exist."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(TagsContractError, match="does not exist"):
        collect_example_dirs([missing])


@pytest.mark.unit
def test_collect_example_dirs_empty_tree_returns_empty(tmp_path: pathlib.Path) -> None:
    """collect_example_dirs must return empty list when the root exists but has no examples."""
    root = tmp_path / "providers" / "aws" / "primitives"
    root.mkdir(parents=True)
    dirs = collect_example_dirs([root])
    assert dirs == []


# ---------------------------------------------------------------------------
# check_example_dir -- clean tree pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_example_dir_clean_tree_returns_no_violations(tmp_path: pathlib.Path) -> None:
    """check_example_dir must return an empty violation list when all three contract parts pass."""
    ex_dir = _make_example_dir(tmp_path)
    violations = check_example_dir(ex_dir)
    assert violations == []


# ---------------------------------------------------------------------------
# check_example_dir -- contract part 1: default_tags wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_example_dir_missing_default_tags_block(tmp_path: pathlib.Path) -> None:
    """Missing default_tags block must produce a violation naming the file and remediation."""
    no_tags = textwrap.dedent("""\
        provider "aws" {
          region = "us-east-1"
        }
    """)
    ex_dir = _make_example_dir(tmp_path, versions_content=no_tags)
    violations = check_example_dir(ex_dir)
    assert len(violations) >= 1
    combined = " ".join(str(v) for v in violations)
    assert "default_tags" in combined
    assert "project_tag" in combined or "terratest_run_id" in combined


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_var",
    ["project_tag", "terratest_run_id"],
)
def test_check_example_dir_default_tags_not_wired_to_var(
    tmp_path: pathlib.Path, missing_var: str
) -> None:
    """default_tags missing one variable reference must produce a named violation."""
    other_var = "terratest_run_id" if missing_var == "project_tag" else "project_tag"
    partial_tags = textwrap.dedent(f"""\
        provider "aws" {{
          region = "us-east-1"

          default_tags {{
            tags = {{
              SomeTag = var.{other_var}
            }}
          }}
        }}
    """)
    ex_dir = _make_example_dir(tmp_path, versions_content=partial_tags)
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert missing_var in msgs


# ---------------------------------------------------------------------------
# check_example_dir -- contract part 2: variable declarations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "var_with_default",
    ["project_tag", "terratest_run_id"],
)
def test_check_example_dir_variable_has_default(
    tmp_path: pathlib.Path, var_with_default: str
) -> None:
    """A tagging variable declared with a default must produce a violation."""
    other_var = "terratest_run_id" if var_with_default == "project_tag" else "project_tag"
    vars_with_default = textwrap.dedent(f"""\
        variable "{var_with_default}" {{
          type    = string
          default = "some-value"
        }}

        variable "{other_var}" {{
          type = string
        }}
    """)
    ex_dir = _make_example_dir(tmp_path, variables_content=vars_with_default)
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert var_with_default in msgs
    assert "default" in msgs


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_var",
    ["project_tag", "terratest_run_id"],
)
def test_check_example_dir_variable_absent(tmp_path: pathlib.Path, missing_var: str) -> None:
    """A tagging variable missing from variables.tf must produce a violation."""
    other_var = "terratest_run_id" if missing_var == "project_tag" else "project_tag"
    vars_missing_one = textwrap.dedent(f"""\
        variable "{other_var}" {{
          type = string
        }}
    """)
    ex_dir = _make_example_dir(tmp_path, variables_content=vars_missing_one)
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert missing_var in msgs


# ---------------------------------------------------------------------------
# check_example_dir -- contract part 3: terraform.tfvars offline values
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_example_dir_missing_tfvars_file(tmp_path: pathlib.Path) -> None:
    """Missing terraform.tfvars must produce a violation naming the file."""
    ex_dir = _make_example_dir(tmp_path, tfvars_content=None)
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert "terraform.tfvars" in msgs


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_key,present_content",
    [
        (
            "project_tag",
            'terratest_run_id = "offline-validate"\n',
        ),
        (
            "terratest_run_id",
            'project_tag = "telemetry-platform"\n',
        ),
    ],
)
def test_check_example_dir_tfvars_missing_key(
    tmp_path: pathlib.Path, missing_key: str, present_content: str
) -> None:
    """terraform.tfvars missing a required key must produce a violation."""
    ex_dir = _make_example_dir(tmp_path, tfvars_content=present_content)
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert missing_key in msgs


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,wrong_value,correct_value",
    [
        ("project_tag", '"wrong-project"', '"telemetry-platform"'),
        ("terratest_run_id", '"wrong-run-id"', '"offline-validate"'),
    ],
)
def test_check_example_dir_tfvars_wrong_value(
    tmp_path: pathlib.Path, key: str, wrong_value: str, correct_value: str
) -> None:
    """terraform.tfvars with the wrong offline value must produce a violation."""
    if key == "project_tag":
        tfvars = f'project_tag      = {wrong_value}\nterratest_run_id = "offline-validate"\n'
    else:
        tfvars = f'project_tag = "telemetry-platform"\nterratest_run_id = {wrong_value}\n'
    ex_dir = _make_example_dir(tmp_path, tfvars_content=tfvars)
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert key in msgs


# ---------------------------------------------------------------------------
# TagsContractViolation format: file:line + element + remediation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_violation_str_includes_file_line_element_remediation(tmp_path: pathlib.Path) -> None:
    """Each TagsContractViolation must format as file:line, missing element, remediation."""
    ex_dir = _make_example_dir(tmp_path, tfvars_content=None)
    violations = check_example_dir(ex_dir)
    assert violations, "Expected at least one violation for missing tfvars"
    v = violations[0]
    text = str(v)
    # Must contain a file path
    assert str(ex_dir) in text or "terraform.tfvars" in text
    # Must contain a remediation hint
    assert "Remedy" in text or "remedy" in text or "Add" in text or "add" in text


# ---------------------------------------------------------------------------
# run_tags_check -- integration across multiple example dirs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_tags_check_all_clean_returns_empty(tmp_path: pathlib.Path) -> None:
    """run_tags_check must return empty list when all examples satisfy the contract."""
    _make_example_dir(tmp_path, module="mod-a", kind="primitives", example="basic")
    _make_example_dir(tmp_path, module="mod-b", kind="references", example="default")
    prims = tmp_path / "providers" / "aws" / "primitives"
    refs = tmp_path / "providers" / "aws" / "references"
    violations = run_tags_check([prims, refs])
    assert violations == []


@pytest.mark.unit
def test_run_tags_check_nonexistent_root_raises(tmp_path: pathlib.Path) -> None:
    """run_tags_check must raise TagsContractError when a root path does not exist."""
    missing = tmp_path / "no-such-dir"
    with pytest.raises(TagsContractError, match="does not exist"):
        run_tags_check([missing])


@pytest.mark.unit
def test_run_tags_check_returns_all_violations(tmp_path: pathlib.Path) -> None:
    """run_tags_check must aggregate violations from multiple violating example dirs."""
    # Two violating examples: one missing tfvars, one with a variable default
    _make_example_dir(
        tmp_path, module="mod-a", kind="primitives", example="basic", tfvars_content=None
    )
    bad_vars = textwrap.dedent("""\
        variable "project_tag" {
          type    = string
          default = "telemetry-platform"
        }
        variable "terratest_run_id" {
          type = string
        }
    """)
    _make_example_dir(
        tmp_path, module="mod-b", kind="primitives", example="basic", variables_content=bad_vars
    )
    prims = tmp_path / "providers" / "aws" / "primitives"
    violations = run_tags_check([prims])
    assert len(violations) >= 2


# ---------------------------------------------------------------------------
# main() -- subprocess integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_exits_0_on_clean_tree(tmp_path: pathlib.Path) -> None:
    """main() must exit 0 when all example dirs satisfy the tagging contract."""
    _make_example_dir(tmp_path, module="kms-key", kind="primitives", example="basic")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_tags"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Expected exit 0; stderr: {result.stderr}"


@pytest.mark.unit
def test_main_exits_1_on_violation(tmp_path: pathlib.Path) -> None:
    """main() must exit 1 when any example violates the tagging contract."""
    _make_example_dir(
        tmp_path, module="kms-key", kind="primitives", example="basic", tfvars_content=None
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_tags"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"Expected exit 1; stderr: {result.stderr}"
    # Must name the violation
    output = result.stdout + result.stderr
    assert "terraform.tfvars" in output


@pytest.mark.unit
def test_main_exits_1_on_nonexistent_tree(tmp_path: pathlib.Path) -> None:
    """main() must exit 1 with ERROR: message when tree root does not exist."""
    import os

    env = {**os.environ, "TERRATEST_TAGS_ROOTS": str(tmp_path / "no-such-providers")}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_tags"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, f"Expected exit 1; stderr: {result.stderr}"
    output = result.stdout + result.stderr
    assert "ERROR" in output


@pytest.mark.unit
def test_main_prints_findings_to_stderr(tmp_path: pathlib.Path) -> None:
    """main() must write violation messages to stderr, not only stdout."""
    _make_example_dir(
        tmp_path, module="kms-key", kind="primitives", example="basic", tfvars_content=None
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_tags"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stderr, "Expected violation output on stderr"


# ---------------------------------------------------------------------------
# check_example_dir -- no provider block and no variables.tf
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_example_dir_no_provider_block_in_any_tf(tmp_path: pathlib.Path) -> None:
    """When no .tf file contains a provider block, a violation is reported."""
    ex_dir = tmp_path / "providers" / "aws" / "primitives" / "mod" / "examples" / "basic"
    ex_dir.mkdir(parents=True, exist_ok=True)
    # Write a versions.tf with no provider block
    (ex_dir / "versions.tf").write_text(
        textwrap.dedent("""\
            terraform {
              required_version = ">= 1.15.5"
            }
        """),
        encoding="utf-8",
    )
    (ex_dir / "variables.tf").write_text(_VALID_VARIABLES_TF, encoding="utf-8")
    (ex_dir / "terraform.tfvars").write_text(_VALID_TFVARS, encoding="utf-8")
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert 'provider "aws"' in msgs or "provider" in msgs


@pytest.mark.unit
def test_check_example_dir_no_tf_files_at_all(tmp_path: pathlib.Path) -> None:
    """When example directory has no .tf files, a provider-absent violation is reported."""
    ex_dir = tmp_path / "providers" / "aws" / "primitives" / "mod" / "examples" / "basic"
    ex_dir.mkdir(parents=True, exist_ok=True)
    # Create terraform.tfvars so tfvars check is separate from provider check
    (ex_dir / "terraform.tfvars").write_text(_VALID_TFVARS, encoding="utf-8")
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert "provider" in msgs or "variables.tf" in msgs


@pytest.mark.unit
def test_check_example_dir_no_variables_tf(tmp_path: pathlib.Path) -> None:
    """When variables.tf is absent, a violation is reported naming the file."""
    ex_dir = tmp_path / "providers" / "aws" / "primitives" / "mod" / "examples" / "basic"
    ex_dir.mkdir(parents=True, exist_ok=True)
    (ex_dir / "versions.tf").write_text(_VALID_VERSIONS_TF, encoding="utf-8")
    (ex_dir / "terraform.tfvars").write_text(_VALID_TFVARS, encoding="utf-8")
    # No variables.tf
    violations = check_example_dir(ex_dir)
    msgs = " ".join(str(v) for v in violations)
    assert "variables.tf" in msgs


# ---------------------------------------------------------------------------
# collect_example_dirs -- non-dir candidate in examples
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_example_dirs_skips_nondirectory_in_examples(tmp_path: pathlib.Path) -> None:
    """collect_example_dirs must skip non-directory items inside an examples/ directory."""
    root = tmp_path / "providers" / "aws" / "primitives"
    ex_parent = root / "some-module" / "examples"
    ex_parent.mkdir(parents=True)
    # Create a real sub-dir
    real_ex = ex_parent / "basic"
    real_ex.mkdir()
    (real_ex / "versions.tf").write_text("", encoding="utf-8")
    # Create a file (not a dir) inside examples/
    (ex_parent / "README.md").write_text("# docs", encoding="utf-8")

    dirs = collect_example_dirs([root])
    assert real_ex in dirs
    # The README.md file must NOT appear in results
    assert ex_parent / "README.md" not in dirs


# ---------------------------------------------------------------------------
# _resolve_roots -- direct in-process tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_roots_uses_env_var(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_roots must use TERRATEST_TAGS_ROOTS when set."""
    custom_root = tmp_path / "custom" / "root"
    custom_root.mkdir(parents=True)
    monkeypatch.setenv("TERRATEST_TAGS_ROOTS", str(custom_root))
    result = _resolve_roots(tmp_path)
    assert result == [custom_root]


@pytest.mark.unit
def test_resolve_roots_comma_separated(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_roots must parse comma-separated TERRATEST_TAGS_ROOTS."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setenv("TERRATEST_TAGS_ROOTS", f"{root_a},{root_b}")
    result = _resolve_roots(tmp_path)
    assert result == [root_a, root_b]


@pytest.mark.unit
def test_resolve_roots_defaults_existing_only(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_roots must return only existing default roots when env var is absent."""
    monkeypatch.delenv("TERRATEST_TAGS_ROOTS", raising=False)
    # Create only primitives
    prims = tmp_path / "providers" / "aws" / "primitives"
    prims.mkdir(parents=True)
    result = _resolve_roots(tmp_path)
    assert result == [prims]


@pytest.mark.unit
def test_resolve_roots_defaults_both_absent_returns_empty(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_roots must return empty list when both defaults are absent and no env var."""
    monkeypatch.delenv("TERRATEST_TAGS_ROOTS", raising=False)
    result = _resolve_roots(tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# main() -- direct in-process tests for coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_0_on_clean_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 0 when all example dirs satisfy the tagging contract (in-process)."""
    _make_example_dir(tmp_path, module="kms-key", kind="primitives", example="basic")
    monkeypatch.delenv("TERRATEST_TAGS_ROOTS", raising=False)
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 0


@pytest.mark.unit
def test_main_returns_1_on_violation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when an example violates the tagging contract (in-process)."""
    _make_example_dir(
        tmp_path,
        module="kms-key",
        kind="primitives",
        example="basic",
        tfvars_content=None,
    )
    monkeypatch.delenv("TERRATEST_TAGS_ROOTS", raising=False)
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 1


@pytest.mark.unit
def test_main_returns_1_on_nonexistent_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when TERRATEST_TAGS_ROOTS points to a nonexistent path (in-process)."""
    missing = tmp_path / "no-such-dir"
    monkeypatch.setenv("TERRATEST_TAGS_ROOTS", str(missing))
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 1


@pytest.mark.unit
def test_main_returns_0_on_empty_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 0 when no example dirs exist (nothing to lint)."""
    monkeypatch.delenv("TERRATEST_TAGS_ROOTS", raising=False)
    monkeypatch.chdir(tmp_path)
    # No providers directory at all -- defaults don't exist, returns empty list, no violations
    result = main()
    assert result == 0


# ---------------------------------------------------------------------------
# collect_example_dirs -- rglob returns a non-directory named "examples"
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_example_dirs_skips_file_named_examples(tmp_path: pathlib.Path) -> None:
    """collect_example_dirs must skip a file named 'examples' (not a dir) found by rglob."""
    root = tmp_path / "providers" / "aws" / "primitives"
    module_dir = root / "some-module"
    module_dir.mkdir(parents=True)
    # Create a file named "examples" (not a directory)
    (module_dir / "examples").write_text("not a directory", encoding="utf-8")

    dirs = collect_example_dirs([root])
    # Nothing should be collected (the file "examples" is skipped)
    assert dirs == []


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_module_main_entry_point_exits_0_on_clean_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running check_terratest_tags as __main__ exits 0 on a clean tree."""
    import runpy

    _make_example_dir(tmp_path, module="kms-key", kind="primitives", example="basic")
    monkeypatch.delenv("TERRATEST_TAGS_ROOTS", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("scripts.check_terratest_tags", run_name="__main__", alter_sys=True)
    assert exc_info.value.code == 0
