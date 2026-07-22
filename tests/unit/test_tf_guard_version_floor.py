"""Unit tests for scripts/tf_guard_version_floor.py.

Implements the test matrix for the version-floor lint guard described in E9-F7-S1-T1.
Tests assert that the scanner:
  - passes a tree whose versions.tf files and root generated block carry the floor (positive)
  - rejects a versions.tf below the Terraform floor (class a)
  - rejects a versions.tf missing/below the AWS provider floor (class b)
  - rejects the root generated block below the Terraform or provider floor (class c)

Every negative class asserts a file:line finding and non-zero exit from main().
"""

from __future__ import annotations

import pathlib
import runpy
import subprocess
import sys
import textwrap

import pytest

from scripts.tf_guard_version_floor import (
    FloorConfig,
    VersionFloorError,
    check_versions_file,
    run_version_floor_guard,
)

# ---------------------------------------------------------------------------
# Constants: floor values used in tests (must match what the guard reads from config)
# ---------------------------------------------------------------------------

TF_FLOOR = "1.15.5"
PROVIDER_FLOOR = "6.49.0"


# ---------------------------------------------------------------------------
# Helpers for building synthetic fixture trees
# ---------------------------------------------------------------------------


def _make_versions_tf(
    root: pathlib.Path,
    rel_path: str,
    tf_version: str = f">= {TF_FLOOR}",
    provider_version: str = f">= {PROVIDER_FLOOR}",
) -> pathlib.Path:
    """Create a synthetic versions.tf at root/rel_path with the given version constraints."""
    full_path = root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(
        textwrap.dedent(f"""\
            terraform {{
              required_version = "{tf_version}"

              required_providers {{
                aws = {{
                  source  = "hashicorp/aws"
                  version = "{provider_version}"
                }}
              }}
            }}
        """),
        encoding="utf-8",
    )
    return full_path


def _make_root_hcl(
    root: pathlib.Path,
    tf_version: str = f">= {TF_FLOOR}",
    provider_version: str = f">= {PROVIDER_FLOOR}",
    include_provider: bool = True,
) -> pathlib.Path:
    """Create a synthetic root.hcl with a generate 'versions' block at root/terragrunt/root.hcl.

    When include_provider is True the generated block also carries a required_providers aws
    block (used to exercise the defence-in-depth provider check). When False the block
    carries required_version only, mirroring the real terragrunt/root.hcl, where the AWS
    provider floor is owned by each module's versions.tf and must not be duplicated here.
    """
    hcl_path = root / "terragrunt" / "root.hcl"
    hcl_path.parent.mkdir(parents=True, exist_ok=True)
    if include_provider:
        terraform_block = textwrap.dedent(f"""\
            terraform {{
              required_version = "{tf_version}"
              required_providers {{
                aws = {{
                  source  = "hashicorp/aws"
                  version = "{provider_version}"
                }}
              }}
            }}""")
    else:
        terraform_block = textwrap.dedent(f"""\
            terraform {{
              required_version = "{tf_version}"
            }}""")
    # Indent the terraform block to sit inside the heredoc.
    indented = textwrap.indent(terraform_block, "    ")
    hcl_path.write_text(
        'generate "versions" {\n'
        '  path      = "versions_generated.tf"\n'
        '  if_exists = "overwrite_terragrunt"\n'
        "  contents  = <<-EOF\n"
        f"{indented}\n"
        "  EOF\n"
        "}\n",
        encoding="utf-8",
    )
    return hcl_path


def _build_passing_tree(root: pathlib.Path) -> None:
    """Build a minimal tree where all version constraints meet the floor.

    The root generated block carries required_version only (no required_providers aws),
    mirroring the real terragrunt/root.hcl design.
    """
    _make_versions_tf(root, "providers/aws/primitives/example-mod/versions.tf")
    _make_versions_tf(root, "providers/aws/references/example-ref/versions.tf")
    _make_root_hcl(root, include_provider=False)


def _make_floor_config() -> FloorConfig:
    return FloorConfig(
        tf_floor=TF_FLOOR,
        provider_floor=PROVIDER_FLOOR,
        provider_source="hashicorp/aws",
    )


# ---------------------------------------------------------------------------
# Positive case: tree at the floor passes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_passing_tree_exits_zero(tmp_path: pathlib.Path) -> None:
    """A tree whose all versions.tf files and root block carry the floor passes."""
    _build_passing_tree(tmp_path)
    config = _make_floor_config()
    findings = run_version_floor_guard(
        providers_roots=[
            tmp_path / "providers" / "aws" / "primitives",
            tmp_path / "providers" / "aws" / "references",
        ],
        root_hcl=tmp_path / "terragrunt" / "root.hcl",
        floor_config=config,
    )
    assert findings == [], f"Expected no findings, got: {findings}"


@pytest.mark.unit
def test_main_passing_tree_returns_zero(tmp_path: pathlib.Path) -> None:
    """main() exits 0 when all version floors are met."""
    _build_passing_tree(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.tf_guard_version_floor",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            **{},
            "PATH": __import__("os").environ["PATH"],
            "PYTHONPATH": str(pathlib.Path(__file__).parent.parent.parent),
            "TF_PROVIDERS_ROOTS": (
                f"{tmp_path}/providers/aws/primitives,{tmp_path}/providers/aws/references"
            ),
            "TF_ROOT_HCL": str(tmp_path / "terragrunt" / "root.hcl"),
            "TF_FLOOR": TF_FLOOR,
            "PROVIDER_FLOOR": PROVIDER_FLOOR,
        },
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}. stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Class a: versions.tf below the Terraform floor
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_tf_version",
    [
        ">= 1.12.1",
        ">= 1.14.0",
        ">= 1.15.4",
    ],
)
def test_versions_tf_below_terraform_floor(
    tmp_path: pathlib.Path,
    bad_tf_version: str,
) -> None:
    """A versions.tf declaring required_version below the Terraform floor fails with a finding."""
    bad_file = _make_versions_tf(
        tmp_path,
        "providers/aws/primitives/bad-mod/versions.tf",
        tf_version=bad_tf_version,
    )
    config = _make_floor_config()
    findings = run_version_floor_guard(
        providers_roots=[tmp_path / "providers" / "aws" / "primitives"],
        root_hcl=None,
        floor_config=config,
    )
    assert len(findings) >= 1, "Expected at least one finding for below-floor Terraform version"
    assert any(str(bad_file) in f for f in findings), (
        f"Expected finding to reference {bad_file}, got: {findings}"
    )
    # Assert file:line format
    assert any(":" in f for f in findings), f"Expected file:line format in findings: {findings}"


@pytest.mark.unit
def test_check_versions_file_below_terraform_floor(tmp_path: pathlib.Path) -> None:
    """check_versions_file raises VersionFloorError for a file below the Terraform floor."""
    bad_file = _make_versions_tf(
        tmp_path,
        "providers/aws/primitives/bad-mod/versions.tf",
        tf_version=">= 1.12.1",
    )
    config = _make_floor_config()
    with pytest.raises(VersionFloorError) as exc_info:
        check_versions_file(bad_file, floor_config=config)
    assert (
        "required_version" in str(exc_info.value).lower()
        or "terraform" in str(exc_info.value).lower()
    )
    assert str(bad_file) in str(exc_info.value) or any(
        part in str(exc_info.value) for part in bad_file.parts
    )


# ---------------------------------------------------------------------------
# Class b: versions.tf missing/below the AWS provider floor
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_provider_version",
    [
        "~> 6.0.0",
        ">= 6.0.0",
        ">= 6.48.0",
        ">= 6.49.0-alpha",
    ],
)
def test_versions_tf_below_provider_floor(
    tmp_path: pathlib.Path,
    bad_provider_version: str,
) -> None:
    """A versions.tf declaring provider version below the floor fails with a finding."""
    bad_file = _make_versions_tf(
        tmp_path,
        "providers/aws/primitives/bad-mod/versions.tf",
        provider_version=bad_provider_version,
    )
    config = _make_floor_config()
    findings = run_version_floor_guard(
        providers_roots=[tmp_path / "providers" / "aws" / "primitives"],
        root_hcl=None,
        floor_config=config,
    )
    assert len(findings) >= 1, (
        f"Expected at least one finding for provider version {bad_provider_version!r}"
    )
    assert any(str(bad_file) in f for f in findings), (
        f"Expected finding to reference {bad_file}, got: {findings}"
    )


@pytest.mark.unit
def test_versions_tf_missing_provider_block(tmp_path: pathlib.Path) -> None:
    """A versions.tf with no required_providers block fails with a finding."""
    bad_file = tmp_path / "providers" / "aws" / "primitives" / "no-provider" / "versions.tf"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text(
        textwrap.dedent("""\
            terraform {
              required_version = ">= 1.15.5"
            }
        """),
        encoding="utf-8",
    )
    config = _make_floor_config()
    findings = run_version_floor_guard(
        providers_roots=[tmp_path / "providers" / "aws" / "primitives"],
        root_hcl=None,
        floor_config=config,
    )
    assert len(findings) >= 1, "Expected a finding when provider block is missing"
    assert any(str(bad_file) in f for f in findings), (
        f"Expected finding to reference {bad_file}, got: {findings}"
    )


# ---------------------------------------------------------------------------
# Class c: root generated block below the floor
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("bad_tf", "bad_provider"),
    [
        (">= 1.12.1", f">= {PROVIDER_FLOOR}"),
        (f">= {TF_FLOOR}", "~> 6.0.0"),
        (">= 1.12.1", "~> 6.0.0"),
    ],
)
def test_root_hcl_below_floor(
    tmp_path: pathlib.Path,
    bad_tf: str,
    bad_provider: str,
) -> None:
    """The root generated block below the floor fails with a finding referencing root.hcl."""
    root_hcl = _make_root_hcl(tmp_path, tf_version=bad_tf, provider_version=bad_provider)
    config = _make_floor_config()
    findings = run_version_floor_guard(
        providers_roots=[],
        root_hcl=root_hcl,
        floor_config=config,
    )
    assert len(findings) >= 1, (
        f"Expected finding for root block tf={bad_tf!r} provider={bad_provider!r}, got none"
    )
    assert any(str(root_hcl) in f for f in findings), (
        f"Expected finding to reference {root_hcl}, got: {findings}"
    )


@pytest.mark.unit
def test_main_below_floor_returns_nonzero(tmp_path: pathlib.Path) -> None:
    """main() exits non-zero when a versions.tf is below the floor."""
    _make_versions_tf(
        tmp_path,
        "providers/aws/primitives/bad-mod/versions.tf",
        tf_version=">= 1.12.1",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.tf_guard_version_floor",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            "PATH": __import__("os").environ["PATH"],
            "PYTHONPATH": str(pathlib.Path(__file__).parent.parent.parent),
            "TF_PROVIDERS_ROOTS": str(tmp_path / "providers" / "aws" / "primitives"),
            "TF_FLOOR": TF_FLOOR,
            "PROVIDER_FLOOR": PROVIDER_FLOOR,
        },
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit for below-floor tree, got {result.returncode}. "
        f"stdout: {result.stdout}"
    )
    # Verify a file:line finding appears in output
    combined = result.stdout + result.stderr
    assert ":" in combined, f"Expected file:line finding in output: {combined}"


# ---------------------------------------------------------------------------
# Missing required_version
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_versions_tf_missing_required_version(tmp_path: pathlib.Path) -> None:
    """A versions.tf that omits required_version entirely fails with a finding."""
    bad_file = tmp_path / "providers" / "aws" / "primitives" / "no-tf-ver" / "versions.tf"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text(
        textwrap.dedent(f"""\
            terraform {{
              required_providers {{
                aws = {{
                  source  = "hashicorp/aws"
                  version = ">= {PROVIDER_FLOOR}"
                }}
              }}
            }}
        """),
        encoding="utf-8",
    )
    config = _make_floor_config()
    with pytest.raises(VersionFloorError):
        check_versions_file(bad_file, floor_config=config)


# ---------------------------------------------------------------------------
# Precondition guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_missing_providers_root_env(tmp_path: pathlib.Path) -> None:
    """main() exits non-zero with ERROR: message when TF_PROVIDERS_ROOTS points to
    a non-existent directory, causing a VersionFloorError precondition failure."""
    nonexistent = tmp_path / "does-not-exist"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.tf_guard_version_floor",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            "PATH": __import__("os").environ["PATH"],
            "PYTHONPATH": str(pathlib.Path(__file__).parent.parent.parent),
            "TF_PROVIDERS_ROOTS": str(nonexistent),
            "TF_FLOOR": TF_FLOOR,
            "PROVIDER_FLOOR": PROVIDER_FLOOR,
        },
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit when providers root missing, got {result.returncode}"
    )
    assert "ERROR:" in result.stderr or "ERROR:" in result.stdout, (
        f"Expected ERROR: message, got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Additional coverage: uncovered branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_meets_floor_unknown_prefix() -> None:
    """_meets_floor returns False for a constraint with an unknown prefix."""
    from scripts.tf_guard_version_floor import _meets_floor

    assert _meets_floor("!= 1.15.5", "1.15.5") is False
    assert _meets_floor("^ 1.15.5", "1.15.5") is False
    assert _meets_floor("1.15.5", "1.15.5") is False


@pytest.mark.unit
def test_collect_versions_files_nonexistent_root(tmp_path: pathlib.Path) -> None:
    """collect_versions_files raises VersionFloorError for a non-existent root."""
    from scripts.tf_guard_version_floor import VersionFloorError, collect_versions_files

    missing_root = tmp_path / "nonexistent"
    with pytest.raises(VersionFloorError) as exc_info:
        collect_versions_files(missing_root)
    assert "ERROR:" in str(exc_info.value)
    assert str(missing_root) in str(exc_info.value)


@pytest.mark.unit
def test_collect_versions_files_excludes_hidden(tmp_path: pathlib.Path) -> None:
    """collect_versions_files excludes versions.tf under hidden directories."""
    from scripts.tf_guard_version_floor import collect_versions_files

    # Create a visible versions.tf and one under .terraform/
    visible = tmp_path / "module" / "versions.tf"
    visible.parent.mkdir(parents=True)
    visible.write_text("", encoding="utf-8")

    hidden = tmp_path / ".terraform" / "modules" / "foo" / "versions.tf"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("", encoding="utf-8")

    result = collect_versions_files(tmp_path)
    assert visible in result
    assert hidden not in result


@pytest.mark.unit
def test_check_root_hcl_missing_generate_block(tmp_path: pathlib.Path) -> None:
    """check_root_hcl returns a finding when the generate 'versions' block is absent."""
    from scripts.tf_guard_version_floor import FloorConfig, check_root_hcl

    hcl_path = tmp_path / "root.hcl"
    hcl_path.write_text("# no generate block\n", encoding="utf-8")
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    findings = check_root_hcl(hcl_path, config)
    assert len(findings) >= 1
    assert any("generate" in f and "absent" in f for f in findings), (
        f"Unexpected findings: {findings}"
    )


@pytest.mark.unit
def test_check_root_hcl_missing_required_version(tmp_path: pathlib.Path) -> None:
    """check_root_hcl returns a finding when required_version is absent inside the block."""
    from scripts.tf_guard_version_floor import FloorConfig, check_root_hcl

    hcl_path = tmp_path / "root.hcl"
    hcl_path.write_text(
        textwrap.dedent(f"""\
            generate "versions" {{
              path      = "versions_generated.tf"
              if_exists = "overwrite_terragrunt"
              contents  = <<-EOF
                terraform {{
                  required_providers {{
                    aws = {{
                      source  = "hashicorp/aws"
                      version = ">= {PROVIDER_FLOOR}"
                    }}
                  }}
                }}
              EOF
            }}
        """),
        encoding="utf-8",
    )
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    findings = check_root_hcl(hcl_path, config)
    assert len(findings) >= 1
    assert any("required_version" in f and "absent" in f for f in findings), (
        f"Unexpected findings: {findings}"
    )


@pytest.mark.unit
def test_check_root_hcl_provider_block_absent_passes(tmp_path: pathlib.Path) -> None:
    """A root generated block with only required_version (no required_providers aws) passes.

    The AWS provider floor is owned by each module's versions.tf. Declaring a second
    required_providers block in the generated file would break terraform init with a
    "Duplicate required providers configuration" error (D-16), so its absence here is
    the correct design and must NOT be a finding. This mirrors the real terragrunt/root.hcl.
    """
    from scripts.tf_guard_version_floor import FloorConfig, check_root_hcl

    hcl_path = tmp_path / "root.hcl"
    hcl_path.write_text(
        textwrap.dedent(f"""\
            generate "versions" {{
              path      = "versions_generated.tf"
              if_exists = "overwrite_terragrunt"
              contents  = <<-EOF
                # Generated by Terragrunt root.hcl -- version constraint only.
                terraform {{
                  required_version = ">= {TF_FLOOR}"
                }}
              EOF
            }}
        """),
        encoding="utf-8",
    )
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    findings = check_root_hcl(hcl_path, config)
    assert findings == [], (
        f"A provider-less generated block must not produce a finding, got: {findings}"
    )


@pytest.mark.unit
def test_check_root_hcl_provider_version_absent_passes(tmp_path: pathlib.Path) -> None:
    """A root block whose aws provider declares only a source (no version) is not flagged.

    The guard never requires a provider version in the generated block, so a source-only
    provider declaration (which the design forbids anyway) is not a finding for an absent
    version; only a present-and-below-floor version is flagged.
    """
    from scripts.tf_guard_version_floor import FloorConfig, check_root_hcl

    hcl_path = tmp_path / "root.hcl"
    hcl_path.write_text(
        textwrap.dedent(f"""\
            generate "versions" {{
              path      = "versions_generated.tf"
              if_exists = "overwrite_terragrunt"
              contents  = <<-EOF
                terraform {{
                  required_version = ">= {TF_FLOOR}"
                  required_providers {{
                    aws = {{
                      source  = "hashicorp/aws"
                    }}
                  }}
                }}
              EOF
            }}
        """),
        encoding="utf-8",
    )
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    findings = check_root_hcl(hcl_path, config)
    assert findings == [], (
        f"An absent provider version in the generated block must not be a finding, got: {findings}"
    )


@pytest.mark.unit
def test_check_root_hcl_present_provider_below_floor_still_flagged(tmp_path: pathlib.Path) -> None:
    """If the generated block does declare an aws provider below floor, it is still flagged.

    Defence-in-depth: the provider should not be in the generated block at all, but if a
    stray below-floor pin is present it must not slip through.
    """
    from scripts.tf_guard_version_floor import FloorConfig, check_root_hcl

    hcl_path = _make_root_hcl(tmp_path, provider_version="~> 6.0.0")
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    findings = check_root_hcl(hcl_path, config)
    assert len(findings) >= 1, "A present below-floor provider pin must still be flagged"
    assert any("provider version" in f.lower() for f in findings), (
        f"Expected an AWS provider version finding, got: {findings}"
    )


@pytest.mark.unit
def test_resolve_floor_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_resolve_floor_config reads TF_FLOOR, PROVIDER_FLOOR, PROVIDER_SOURCE from env."""
    from scripts.tf_guard_version_floor import _resolve_floor_config

    monkeypatch.setenv("TF_FLOOR", "1.99.0")
    monkeypatch.setenv("PROVIDER_FLOOR", "7.0.0")
    monkeypatch.setenv("PROVIDER_SOURCE", "hashicorp/aws")
    config = _resolve_floor_config()
    assert config.tf_floor == "1.99.0"
    assert config.provider_floor == "7.0.0"
    assert config.provider_source == "hashicorp/aws"


@pytest.mark.unit
def test_resolve_providers_roots_from_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_providers_roots uses TF_PROVIDERS_ROOTS env var when set."""
    from scripts.tf_guard_version_floor import _resolve_providers_roots

    root1 = tmp_path / "root1"
    root1.mkdir()
    monkeypatch.setenv("TF_PROVIDERS_ROOTS", str(root1))
    result = _resolve_providers_roots(tmp_path)
    assert result == [root1]


@pytest.mark.unit
def test_resolve_providers_roots_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_providers_roots falls back to default paths when env var is absent."""
    from scripts.tf_guard_version_floor import _resolve_providers_roots

    monkeypatch.delenv("TF_PROVIDERS_ROOTS", raising=False)
    primitives = tmp_path / "providers" / "aws" / "primitives"
    primitives.mkdir(parents=True)
    result = _resolve_providers_roots(tmp_path)
    assert primitives in result


@pytest.mark.unit
def test_resolve_root_hcl_from_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_resolve_root_hcl uses TF_ROOT_HCL env var when set."""
    from scripts.tf_guard_version_floor import _resolve_root_hcl

    custom_hcl = tmp_path / "custom" / "root.hcl"
    custom_hcl.parent.mkdir()
    custom_hcl.write_text("", encoding="utf-8")
    monkeypatch.setenv("TF_ROOT_HCL", str(custom_hcl))
    result = _resolve_root_hcl(tmp_path)
    assert result == custom_hcl


@pytest.mark.unit
def test_resolve_root_hcl_default_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_root_hcl returns None when default path does not exist and env is absent."""
    from scripts.tf_guard_version_floor import _resolve_root_hcl

    monkeypatch.delenv("TF_ROOT_HCL", raising=False)
    result = _resolve_root_hcl(tmp_path)
    assert result is None


@pytest.mark.unit
def test_resolve_root_hcl_default_present(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_root_hcl returns the default path when it exists."""
    from scripts.tf_guard_version_floor import _resolve_root_hcl

    monkeypatch.delenv("TF_ROOT_HCL", raising=False)
    default_hcl = tmp_path / "terragrunt" / "root.hcl"
    default_hcl.parent.mkdir(parents=True)
    default_hcl.write_text("", encoding="utf-8")
    result = _resolve_root_hcl(tmp_path)
    assert result == default_hcl


@pytest.mark.unit
def test_main_with_root_hcl_below_floor(tmp_path: pathlib.Path) -> None:
    """main() exits non-zero and reports ERROR: when root HCL block is below floor."""
    _make_versions_tf(tmp_path, "providers/aws/primitives/mod/versions.tf")
    _make_root_hcl(tmp_path, tf_version=">= 1.12.1")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.tf_guard_version_floor"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            "PATH": __import__("os").environ["PATH"],
            "PYTHONPATH": str(pathlib.Path(__file__).parent.parent.parent),
            "TF_PROVIDERS_ROOTS": str(tmp_path / "providers" / "aws" / "primitives"),
            "TF_ROOT_HCL": str(tmp_path / "terragrunt" / "root.hcl"),
            "TF_FLOOR": TF_FLOOR,
            "PROVIDER_FLOOR": PROVIDER_FLOOR,
        },
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ERROR:" in combined, f"Expected ERROR: message, got: {combined}"


@pytest.mark.unit
def test_find_provider_version_in_content_wrong_source() -> None:
    """_find_provider_version_in_content returns None when provider source doesn't match."""
    from scripts.tf_guard_version_floor import FloorConfig, _find_provider_version_in_content

    content = textwrap.dedent("""\
        required_providers {
          azure = {
            source  = "hashicorp/azurerm"
            version = ">= 3.0.0"
          }
        }
    """)
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    result = _find_provider_version_in_content(content, pathlib.Path("dummy.tf"), config)
    assert result is None


@pytest.mark.unit
def test_run_version_floor_guard_precondition_error(tmp_path: pathlib.Path) -> None:
    """run_version_floor_guard propagates VersionFloorError for non-existent roots."""
    from scripts.tf_guard_version_floor import (
        FloorConfig,
        VersionFloorError,
        run_version_floor_guard,
    )

    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    with pytest.raises(VersionFloorError):
        run_version_floor_guard(
            providers_roots=[tmp_path / "nonexistent"],
            root_hcl=None,
            floor_config=config,
        )


@pytest.mark.unit
def test_main_precondition_error_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """main() exits 1 with ERROR: when a providers root doesn't exist (VersionFloorError)."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.tf_guard_version_floor"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            "PATH": __import__("os").environ["PATH"],
            "PYTHONPATH": str(pathlib.Path(__file__).parent.parent.parent),
            "TF_PROVIDERS_ROOTS": str(tmp_path / "nonexistent"),
            "TF_FLOOR": TF_FLOOR,
            "PROVIDER_FLOOR": PROVIDER_FLOOR,
        },
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ERROR:" in combined, f"Expected ERROR: message, got: {combined}"


# ---------------------------------------------------------------------------
# Direct main() invocation tests for coverage of in-process paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_direct_passing(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 0 when called directly with a passing tree."""
    from scripts.tf_guard_version_floor import main

    _build_passing_tree(tmp_path)
    monkeypatch.setenv(
        "TF_PROVIDERS_ROOTS",
        (f"{tmp_path}/providers/aws/primitives,{tmp_path}/providers/aws/references"),
    )
    monkeypatch.setenv("TF_ROOT_HCL", str(tmp_path / "terragrunt" / "root.hcl"))
    monkeypatch.setenv("TF_FLOOR", TF_FLOOR)
    monkeypatch.setenv("PROVIDER_FLOOR", PROVIDER_FLOOR)
    ret = main()
    assert ret == 0


@pytest.mark.unit
def test_main_direct_below_floor(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 1 when called directly with a below-floor tree."""
    from scripts.tf_guard_version_floor import main

    _make_versions_tf(
        tmp_path,
        "providers/aws/primitives/bad-mod/versions.tf",
        tf_version=">= 1.12.1",
    )
    monkeypatch.setenv(
        "TF_PROVIDERS_ROOTS",
        str(tmp_path / "providers" / "aws" / "primitives"),
    )
    monkeypatch.delenv("TF_ROOT_HCL", raising=False)
    monkeypatch.setenv("TF_FLOOR", TF_FLOOR)
    monkeypatch.setenv("PROVIDER_FLOOR", PROVIDER_FLOOR)
    ret = main()
    assert ret == 1


@pytest.mark.unit
def test_main_direct_precondition_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when a providers root raises VersionFloorError (nonexistent dir)."""
    from scripts.tf_guard_version_floor import main

    monkeypatch.setenv("TF_PROVIDERS_ROOTS", str(tmp_path / "nonexistent"))
    monkeypatch.delenv("TF_ROOT_HCL", raising=False)
    monkeypatch.setenv("TF_FLOOR", TF_FLOOR)
    monkeypatch.setenv("PROVIDER_FLOOR", PROVIDER_FLOOR)
    ret = main()
    assert ret == 1


@pytest.mark.unit
def test_resolve_providers_roots_no_defaults(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_providers_roots calls sys.exit(1) when env is absent and defaults are missing."""
    from scripts.tf_guard_version_floor import _resolve_providers_roots

    monkeypatch.delenv("TF_PROVIDERS_ROOTS", raising=False)
    # tmp_path has no providers/aws/... subdirs
    with pytest.raises(SystemExit) as exc_info:
        _resolve_providers_roots(tmp_path)
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_find_provider_version_in_content_missing_version() -> None:
    """_find_provider_version_in_content returns None when version absent after matching source."""
    from scripts.tf_guard_version_floor import FloorConfig, _find_provider_version_in_content

    # Has the correct source but no version constraint following it
    content = textwrap.dedent("""\
        required_providers {
          aws = {
            source = "hashicorp/aws"
          }
        }
    """)
    config = FloorConfig(
        tf_floor=TF_FLOOR, provider_floor=PROVIDER_FLOOR, provider_source="hashicorp/aws"
    )
    result = _find_provider_version_in_content(content, pathlib.Path("dummy.tf"), config)
    assert result is None


@pytest.mark.unit
def test_module_main_guard_invokes_exit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The __main__ guard (if __name__ == '__main__': sys.exit(main())) is invoked correctly.

    We use runpy.run_module with run_name='__main__' to execute the module as __main__
    within the same process, so coverage.py captures the if-block on line 547.
    """
    _build_passing_tree(tmp_path)
    monkeypatch.setenv(
        "TF_PROVIDERS_ROOTS",
        (f"{tmp_path}/providers/aws/primitives,{tmp_path}/providers/aws/references"),
    )
    monkeypatch.setenv("TF_ROOT_HCL", str(tmp_path / "terragrunt" / "root.hcl"))
    monkeypatch.setenv("TF_FLOOR", TF_FLOOR)
    monkeypatch.setenv("PROVIDER_FLOOR", PROVIDER_FLOOR)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("scripts.tf_guard_version_floor", run_name="__main__", alter_sys=True)

    assert exc_info.value.code == 0, (
        f"Expected SystemExit(0) from runpy __main__ invocation, got {exc_info.value.code}"
    )
