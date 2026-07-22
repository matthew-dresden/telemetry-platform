"""Unit tests for scripts.check_required_var_wiring (issue #235 guard).

Covers, with real assertions and no live calls:
- parse_variable_blocks: default detection (incl. null/""), regex-brace strings,
  comment handling, and unbalanced-brace -> HclParseError.
- new_required_variables: newly-added-required detection vs base, base-absent file.
- module_leaf_for_variables_file: module-level vs examples/tests vs non-module.
- leaf_sources_module: exact `source` key vs `*_source` overrides, sibling-module
  boundary (analytics vs analytics-dashboard).
- envcommon_includes / variable_is_wired helpers.
- evaluate(): scenarios (a)-(f) from the story plus the #233 reproduction fixture.
- CLI/git integration via a real temporary git repository (FAIL + PASS).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_required_var_wiring as guard
from scripts.check_required_var_wiring import (
    Finding,
    HclParseError,
    _base_ref_for_diff_range,
    _changed_files,
    _file_text_at_ref,
    build_changed_variables_files,
    envcommon_includes,
    evaluate,
    leaf_sources_module,
    module_leaf_for_variables_file,
    new_required_variables,
    parse_variable_blocks,
    variable_is_wired,
)

MODULE_ROOTS = [
    "providers/aws/collections/",
    "providers/aws/data/",
    "providers/aws/primitives/",
    "providers/aws/references/",
]

# ---------------------------------------------------------------------------
# HCL samples
# ---------------------------------------------------------------------------

# A module variables.tf modelled on the real analytics reference: it declares a
# pre-existing required var, an optional var with a default, and (in the "head"
# form) two NEW required vars matching the actual #233 additions. Includes a
# regex-brace string ({1,64}) and comments to exercise the masking parser.
_VARIABLES_BASE = """
# Pre-existing required variable (present in base and head).
variable "workgroup_name" {
  type        = string
  description = "(Required) The name of the cost-capped Athena workgroup."

  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]{1,64}$", var.workgroup_name))
    error_message = "workgroup_name must match the pattern."
  }
}

variable "bytes_scanned_cutoff_per_query" {
  type        = number
  description = "(Optional) Maximum bytes scanned per query."
  default     = 107374182400
}
"""

# New required vars added by #233 (no default): data_lake_bucket_arn, data_kms_key_arn.
# Plus an optional var with a default (quicksight_service_role_name).
_VARIABLES_HEAD_233 = (
    _VARIABLES_BASE
    + """
variable "quicksight_service_role_name" {
  type        = string
  description = "(Optional) Name of the QuickSight-managed service role."
  default     = "aws-quicksight-service-role-v0"

  validation {
    condition     = can(regex("^[a-zA-Z0-9+=,.@_-]{1,64}$", var.quicksight_service_role_name))
    error_message = "must be a valid IAM role name."
  }
}

variable "data_lake_bucket_arn" {
  type        = string
  description = "(Required) ARN of the S3 data-lake bucket the QS role reads."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:s3:::", var.data_lake_bucket_arn))
    error_message = "must be a valid S3 bucket ARN."
  }
}

variable "data_kms_key_arn" {
  type        = string
  description = "(Required) ARN of the telemetry-data KMS CMK."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:kms:", var.data_kms_key_arn))
    error_message = "must be a valid KMS key ARN."
  }
}
"""
)

# Analytics leaf (unwired) modelled on the real terragrunt leaf: it sources the
# analytics reference but does NOT wire data_lake_bucket_arn / data_kms_key_arn.
_LEAF_UNWIRED = """
include "root" {
  path = find_in_parent_folders("root.hcl")
}

include "envcommon" {
  path = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/analytics.hcl"
}

terraform {
  source = "${get_repo_root()}//providers/aws/references/analytics"
}

inputs = {
  workgroup_name = "telemetry-analytics"
}
"""

# Same leaf, but wiring the two new required vars in inputs.
_LEAF_WIRED = _LEAF_UNWIRED.replace(
    '  workgroup_name = "telemetry-analytics"\n',
    '  workgroup_name       = "telemetry-analytics"\n'
    "  data_lake_bucket_arn = dependency.data_lake.outputs.data_lake_bucket_arn\n"
    "  data_kms_key_arn     = dependency.data_lake.outputs.lake_kms_key_arn\n",
)

_ANALYTICS_LEAF_PATH = "terragrunt/live/telemetry/sandbox/analytics/000/terragrunt.hcl"
_ANALYTICS_LEAF_PATH_PROD = "terragrunt/live/telemetry/prod/analytics/000/terragrunt.hcl"
_ANALYTICS_VARS_PATH = "providers/aws/references/analytics/variables.tf"


# ---------------------------------------------------------------------------
# parse_variable_blocks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_variable_blocks_detects_required_and_optional() -> None:
    parsed = parse_variable_blocks(_VARIABLES_HEAD_233)
    assert parsed["workgroup_name"] is False  # required (no default)
    assert parsed["bytes_scanned_cutoff_per_query"] is True  # has default
    assert parsed["quicksight_service_role_name"] is True  # has default
    assert parsed["data_lake_bucket_arn"] is False  # required
    assert parsed["data_kms_key_arn"] is False  # required


@pytest.mark.unit
@pytest.mark.parametrize(
    "default_literal",
    ["default = null", 'default = ""', 'default     = "x"', "default = 0", "default=[]"],
)
def test_parse_variable_blocks_any_default_is_optional(default_literal: str) -> None:
    text = f'variable "v" {{\n  type = string\n  {default_literal}\n}}\n'
    assert parse_variable_blocks(text) == {"v": True}


@pytest.mark.unit
def test_parse_variable_blocks_ignores_default_word_inside_validation_and_strings() -> None:
    # The word "default" appears only inside a description string and a nested
    # validation block -- neither is a top-level default attribute.
    text = """
variable "v" {
  type        = string
  description = "the default is not set here; default = something"

  validation {
    condition     = can(regex("default", var.v))
    error_message = "no default = here either"
  }
}
"""
    assert parse_variable_blocks(text) == {"v": False}


@pytest.mark.unit
def test_parse_variable_blocks_masks_slash_and_block_comments_and_escapes() -> None:
    # Exercises the // line-comment, /* */ block-comment, and \" escape masking
    # paths so brace/keyword detection ignores their content.
    text = """
// variable "commented_out" { default = 1 }
/* variable "also_out" {
   default = 2
} */
variable "kept" {
  type        = string
  description = "a quote \\" and a brace { are inside this string; default = nope"
}
"""
    assert parse_variable_blocks(text) == {"kept": False}


@pytest.mark.unit
def test_parse_variable_blocks_default_substring_is_not_a_default_attribute() -> None:
    # "defaults" (trailing alnum) and a nested-block default must not be counted.
    text = """
variable "v" {
  type     = string
  defaults = "not a real attribute name"
}
"""
    assert parse_variable_blocks(text) == {"v": False}


@pytest.mark.unit
def test_parse_variable_blocks_unbalanced_braces_raises() -> None:
    text = 'variable "v" {\n  type = string\n'  # missing closing brace
    with pytest.raises(HclParseError):
        parse_variable_blocks(text)


@pytest.mark.unit
def test_parse_variable_blocks_ignores_commented_out_declaration() -> None:
    text = """
# variable "ghost" {
#   type = string
# }
variable "real" {
  type = string
}
"""
    assert parse_variable_blocks(text) == {"real": False}


# ---------------------------------------------------------------------------
# new_required_variables
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_new_required_variables_detects_only_new_required() -> None:
    result = new_required_variables(_VARIABLES_BASE, _VARIABLES_HEAD_233)
    # workgroup_name pre-existed; quicksight_service_role_name has a default.
    assert result == ["data_kms_key_arn", "data_lake_bucket_arn"]


@pytest.mark.unit
def test_new_required_variables_base_absent_treats_all_as_new() -> None:
    result = new_required_variables(None, _VARIABLES_HEAD_233)
    assert result == ["data_kms_key_arn", "data_lake_bucket_arn", "workgroup_name"]


@pytest.mark.unit
def test_new_required_variables_ambiguous_head_returns_empty() -> None:
    assert new_required_variables(None, 'variable "v" {\n') == []


@pytest.mark.unit
def test_new_required_variables_ambiguous_base_returns_empty() -> None:
    assert new_required_variables('variable "v" {\n', _VARIABLES_HEAD_233) == []


# ---------------------------------------------------------------------------
# module_leaf_for_variables_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,expected",
    [
        ("providers/aws/references/analytics/variables.tf", "providers/aws/references/analytics"),
        ("providers/aws/primitives/kms-key/variables.tf", "providers/aws/primitives/kms-key"),
        # examples / tests nested variables.tf -> ignored
        ("providers/aws/references/analytics/examples/basic/variables.tf", None),
        ("providers/aws/references/analytics/tests/variables.tf", None),
        # non-variables.tf module file -> ignored
        ("providers/aws/references/analytics/main.tf", None),
        # file directly under a module root (no leaf dir) -> ignored
        ("providers/aws/references/variables.tf", None),
        # outside module roots -> ignored
        ("terragrunt/live/x/terragrunt.hcl", None),
        ("scripts/foo.py", None),
    ],
)
def test_module_leaf_for_variables_file(path: str, expected: str | None) -> None:
    assert module_leaf_for_variables_file(path, MODULE_ROOTS) == expected


# ---------------------------------------------------------------------------
# leaf_sources_module
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_leaf_sources_module_matches_top_level_source() -> None:
    assert leaf_sources_module(_LEAF_UNWIRED, "providers/aws/references/analytics") is True


@pytest.mark.unit
def test_leaf_sources_module_boundary_excludes_sibling_module() -> None:
    dashboard_leaf = _LEAF_UNWIRED.replace("references/analytics", "references/analytics-dashboard")
    # A leaf that sources analytics-dashboard must NOT be seen as an analytics consumer.
    assert leaf_sources_module(dashboard_leaf, "providers/aws/references/analytics") is False
    assert (
        leaf_sources_module(dashboard_leaf, "providers/aws/references/analytics-dashboard") is True
    )


@pytest.mark.unit
def test_leaf_sources_module_ignores_child_source_override_key() -> None:
    # A `*_source` override key that references a primitive must not count as the
    # leaf's top-level module source.
    leaf = 'inputs = {\n  spice_kms_source = "git::x//providers/aws/primitives/kms-key?ref=v1"\n}\n'
    assert leaf_sources_module(leaf, "providers/aws/primitives/kms-key") is False


# ---------------------------------------------------------------------------
# envcommon_includes / variable_is_wired
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_envcommon_includes_extracts_template_basenames() -> None:
    assert envcommon_includes(_LEAF_UNWIRED) == ["analytics.hcl"]


@pytest.mark.unit
def test_variable_is_wired_word_boundary() -> None:
    assert variable_is_wired("data_kms_key_arn", [_LEAF_WIRED]) is True
    assert variable_is_wired("data_kms_key_arn", [_LEAF_UNWIRED]) is False
    # Must not match a superstring token.
    assert variable_is_wired("kms", ["  data_kms_key_arn = x\n"]) is False


# ---------------------------------------------------------------------------
# evaluate() -- story scenarios (a)-(f) + #233 reproduction
# ---------------------------------------------------------------------------


def _terragrunt_files(leaf_text: str, *, include_envcommon: bool = False) -> dict[str, str]:
    files = {_ANALYTICS_LEAF_PATH: leaf_text}
    if include_envcommon:
        files["terragrunt/_envcommon/analytics.hcl"] = 'inputs = {\n  workgroup_name = "x"\n}\n'
    return files


@pytest.mark.unit
def test_scenario_a_new_required_var_unwired_module_has_leaves_fails() -> None:
    """(a) module adds required var + no leaf wiring + module HAS leaves -> FAIL."""
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, _VARIABLES_HEAD_233)},
        module_roots=MODULE_ROOTS,
        terragrunt_files=_terragrunt_files(_LEAF_UNWIRED),
    )
    variables = {f.variable for f in findings}
    assert variables == {"data_lake_bucket_arn", "data_kms_key_arn"}
    for f in findings:
        assert f.module == "providers/aws/references/analytics"
        assert f.unwired_leaves == (_ANALYTICS_LEAF_PATH,)
        assert "do not wire it" in f.message()


@pytest.mark.unit
def test_scenario_b_new_required_var_wired_in_pr_passes() -> None:
    """(b) same as (a) but leaves ARE wired in the PR -> PASS."""
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, _VARIABLES_HEAD_233)},
        module_roots=MODULE_ROOTS,
        terragrunt_files=_terragrunt_files(_LEAF_WIRED),
    )
    assert findings == []


@pytest.mark.unit
def test_scenario_c_new_var_with_default_passes() -> None:
    """(c) adds a var WITH a default -> PASS (optional, ignored)."""
    head = _VARIABLES_BASE + '\nvariable "opt" {\n  type = string\n  default = "x"\n}\n'
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, head)},
        module_roots=MODULE_ROOTS,
        terragrunt_files=_terragrunt_files(_LEAF_UNWIRED),
    )
    assert findings == []


@pytest.mark.unit
def test_scenario_d_no_consuming_leaves_passes() -> None:
    """(d) module has NO consuming terragrunt leaves -> PASS (nothing to wire)."""
    unrelated_leaf = _LEAF_UNWIRED.replace("analytics", "some-other-module")
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, _VARIABLES_HEAD_233)},
        module_roots=MODULE_ROOTS,
        terragrunt_files={_ANALYTICS_LEAF_PATH: unrelated_leaf},
    )
    assert findings == []


@pytest.mark.unit
def test_scenario_e_only_examples_tests_variables_changed_passes() -> None:
    """(e) only examples/ or tests/ variables.tf changed -> PASS (ignored)."""
    findings = evaluate(
        changed_variables_files={
            "providers/aws/references/analytics/examples/basic/variables.tf": (
                _VARIABLES_BASE,
                _VARIABLES_HEAD_233,
            ),
            "providers/aws/references/analytics/tests/variables.tf": (
                _VARIABLES_BASE,
                _VARIABLES_HEAD_233,
            ),
        },
        module_roots=MODULE_ROOTS,
        terragrunt_files=_terragrunt_files(_LEAF_UNWIRED),
    )
    assert findings == []


@pytest.mark.unit
def test_scenario_f_preexisting_required_var_unchanged_passes() -> None:
    """(f) a pre-existing (unchanged) required var -> PASS (not newly added)."""
    # HEAD == BASE for the required workgroup_name; even though the leaf does not
    # wire workgroup_name in this minimal fixture, it is not NEWLY added, so no
    # finding is produced.
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, _VARIABLES_BASE)},
        module_roots=MODULE_ROOTS,
        terragrunt_files=_terragrunt_files(_LEAF_UNWIRED),
    )
    assert findings == []


@pytest.mark.unit
def test_scenario_b_wired_via_envcommon_passes() -> None:
    """A new required var wired in the included _envcommon template -> PASS."""
    envcommon = "inputs = {\n  data_lake_bucket_arn = x\n  data_kms_key_arn = y\n}\n"
    files = {
        _ANALYTICS_LEAF_PATH: _LEAF_UNWIRED,
        "terragrunt/_envcommon/analytics.hcl": envcommon,
    }
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, _VARIABLES_HEAD_233)},
        module_roots=MODULE_ROOTS,
        terragrunt_files=files,
    )
    assert findings == []


@pytest.mark.unit
def test_issue_233_reproduction_would_have_failed() -> None:
    """#233 reproduction: analytics adds data_lake_bucket_arn + data_kms_key_arn
    (both required, no default), consumed by BOTH sandbox and prod leaves, neither
    wiring them. The guard must FAIL for both variables across both leaves."""
    files = {
        _ANALYTICS_LEAF_PATH: _LEAF_UNWIRED,
        _ANALYTICS_LEAF_PATH_PROD: _LEAF_UNWIRED,
    }
    findings = evaluate(
        changed_variables_files={_ANALYTICS_VARS_PATH: (_VARIABLES_BASE, _VARIABLES_HEAD_233)},
        module_roots=MODULE_ROOTS,
        terragrunt_files=files,
    )
    by_var = {f.variable: f for f in findings}
    assert set(by_var) == {"data_lake_bucket_arn", "data_kms_key_arn"}
    for finding in findings:
        assert finding.unwired_leaves == tuple(
            sorted((_ANALYTICS_LEAF_PATH, _ANALYTICS_LEAF_PATH_PROD))
        )


@pytest.mark.unit
def test_finding_message_is_actionable() -> None:
    f = Finding(
        module="providers/aws/references/analytics",
        variable="data_kms_key_arn",
        unwired_leaves=(_ANALYTICS_LEAF_PATH,),
    )
    msg = f.message()
    assert "providers/aws/references/analytics" in msg
    assert "data_kms_key_arn" in msg
    assert _ANALYTICS_LEAF_PATH in msg
    assert "#235" in msg


# ---------------------------------------------------------------------------
# CLI / git integration (real temporary git repository)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path, *, leaf_text: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    (repo / "monorepo-config.json").write_text(
        "{\n"
        '  "module_roots": ["providers/aws/references/"],\n'
        '  "terragrunt_root": "terragrunt/"\n'
        "}\n"
    )
    vars_path = repo / _ANALYTICS_VARS_PATH
    vars_path.parent.mkdir(parents=True, exist_ok=True)
    vars_path.write_text(_VARIABLES_BASE)

    leaf_path = repo / _ANALYTICS_LEAF_PATH
    leaf_path.parent.mkdir(parents=True, exist_ok=True)
    leaf_path.write_text(leaf_text)

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    # HEAD commit: add the two new required vars to the module.
    vars_path.write_text(_VARIABLES_HEAD_233)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head: add required vars")
    return repo


def _invoke_main(monkeypatch, repo: Path, diff_range: str = "HEAD~1...HEAD") -> int:
    """Run the guard CLI main() IN-PROCESS against a temp git repo.

    In-process invocation (vs a python subprocess) keeps the real git calls the
    guard makes -- exercising the git/filesystem integration -- while avoiding a
    spawned Python interpreter, so no subprocess coverage-data files are produced.
    """
    argv = [
        "check_required_var_wiring",
        "--config",
        str(repo / "monorepo-config.json"),
        "--repo-root",
        str(repo),
        "--diff-range",
        diff_range,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc_info:
        guard.main()
    code = exc_info.value.code
    return int(code) if code is not None else 0


@pytest.mark.unit
def test_cli_fails_on_unwired_new_required_var(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _init_repo(tmp_path, leaf_text=_LEAF_UNWIRED)
    code = _invoke_main(monkeypatch, repo)
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "data_lake_bucket_arn" in captured.err
    assert "data_kms_key_arn" in captured.err


@pytest.mark.unit
def test_cli_passes_when_leaf_wires_new_required_var(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _init_repo(tmp_path, leaf_text=_LEAF_WIRED)
    code = _invoke_main(monkeypatch, repo)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert "OK" in captured.out


@pytest.mark.unit
@pytest.mark.parametrize(
    "diff_range,expected",
    [
        ("feature..main", "feature"),  # two-dot -> left side
        ("main..", "main"),  # two-dot, empty right -> left side
        ("v1.2.3", "v1.2.3"),  # bare ref -> itself
    ],
)
def test_base_ref_for_diff_range_non_three_dot(diff_range: str, expected: str) -> None:
    # These branches never call git, so repo_root is irrelevant.
    assert _base_ref_for_diff_range(diff_range, repo_root=".") == expected


@pytest.mark.unit
def test_changed_files_raises_on_git_failure(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="bad revision")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="git diff"):
        _changed_files("origin/main...HEAD", repo_root=".")


@pytest.mark.unit
def test_file_text_at_ref_returns_none_when_absent(monkeypatch) -> None:
    def fake_run(cmd, capture_output, text):
        # cat-file -e probe fails -> file absent at ref.
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _file_text_at_ref("providers/aws/references/x/variables.tf", "BASE", ".") is None


@pytest.mark.unit
def test_file_text_at_ref_raises_when_show_fails(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run(cmd, capture_output, text):
        calls["n"] += 1
        if "cat-file" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")  # exists
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="show boom")  # show fails

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="git show"):
        _file_text_at_ref("providers/aws/references/x/variables.tf", "BASE", ".")


@pytest.mark.unit
def test_build_changed_variables_files_skips_deleted_file(tmp_path: Path, monkeypatch) -> None:
    # A changed module variables.tf that no longer exists in the working tree
    # (deleted in HEAD) is skipped -- a deletion cannot add a required variable.
    monkeypatch.setattr(
        guard, "_file_text_at_ref", lambda path, base_ref, repo_root: _VARIABLES_BASE
    )
    result = build_changed_variables_files(
        changed_files=[_ANALYTICS_VARS_PATH],
        module_roots=MODULE_ROOTS,
        base_ref="BASE",
        repo_root=str(tmp_path),  # file does not exist here
    )
    assert result == {}


@pytest.mark.unit
def test_cli_new_module_variables_file_absent_in_base(tmp_path: Path, monkeypatch, capsys) -> None:
    # A brand-new module whose variables.tf did not exist at base: every required
    # var is "new". With a consuming leaf that does not wire it -> FAIL.
    repo = tmp_path / "repo3"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "monorepo-config.json").write_text(
        '{\n  "module_roots": ["providers/aws/references/"],\n'
        '  "terragrunt_root": "terragrunt/"\n}\n'
    )
    leaf_path = repo / _ANALYTICS_LEAF_PATH
    leaf_path.parent.mkdir(parents=True, exist_ok=True)
    leaf_path.write_text(_LEAF_UNWIRED)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base without module variables.tf")
    vars_path = repo / _ANALYTICS_VARS_PATH
    vars_path.parent.mkdir(parents=True, exist_ok=True)
    vars_path.write_text(_VARIABLES_HEAD_233)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head: introduce module variables.tf")
    code = _invoke_main(monkeypatch, repo)
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "data_lake_bucket_arn" in captured.err


@pytest.mark.unit
def test_cli_fails_fast_on_invalid_diff_range(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _init_repo(tmp_path, leaf_text=_LEAF_UNWIRED)
    code = _invoke_main(monkeypatch, repo, diff_range="does-not-exist...HEAD")
    captured = capsys.readouterr()
    assert code == 1
    assert "::error::" in captured.err


@pytest.mark.unit
def test_cli_passes_when_no_module_variables_changed(tmp_path: Path, monkeypatch, capsys) -> None:
    # A repo whose HEAD commit changes only a non-module file: guard finds no
    # changed module variables.tf and PASSES.
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "monorepo-config.json").write_text(
        '{\n  "module_roots": ["providers/aws/references/"],\n'
        '  "terragrunt_root": "terragrunt/"\n}\n'
    )
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "README.md").write_text("changed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    code = _invoke_main(monkeypatch, repo)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
