"""Unit tests for scripts.ci_detect_scope -- CI wrapper for scope detection.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases cover:
- admin override scope=all with populated modules JSON array and empty module_path (D43)
- scope=module with single module_path and empty modules
- unauthorized violation emitting ::error:: and exiting 1
- scope=/module_path=/modules= written to the output file

AC-15:
- ci_detect_scope honours scope=all override (D43)
- ci_detect_scope emits a single MODULE_PATH for normal changes
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ci_detect_scope import D43ViolationError, run_ci_detect_scope

# Dynamic repo root -- two levels above the tests/unit/ directory
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

MODULE_ROOTS = [
    "providers/aws/collections/",
    "providers/aws/data/",
    "providers/aws/primitives/",
    "providers/aws/references/",
]
TERRAGRUNT_ROOT = "terragrunt/"
RESERVED_DIRS = ["primitives", "references", "collections", "data"]

_BASE_CONFIG = {
    "module_roots": MODULE_ROOTS,
    "terragrunt_root": TERRAGRUNT_ROOT,
    "reserved_directories": RESERVED_DIRS,
}


# ---------------------------------------------------------------------------
# scope=all admin override -- populated modules JSON array, empty module_path (D43)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_detect_scope_admin_override_emits_all_modules(tmp_path) -> None:
    """scope=all must emit a populated modules JSON array and leave module_path empty.

    AC-15 / D43: on admin override, ci_detect_scope writes scope=all, an empty
    module_path, and a non-empty modules JSON array to the output file.
    The modules array is NEVER empty (D43 contract).
    """
    output_file = tmp_path / "output.txt"

    # Simulate providers directories existing. A real module is identified by the
    # presence of a main.tf entrypoint, so each fixture module must contain one.
    providers = [
        tmp_path / "providers/aws/primitives/kms-key",
        tmp_path / "providers/aws/primitives/s3-bucket",
        tmp_path / "providers/aws/references/state-bootstrap",
    ]
    for p in providers:
        p.mkdir(parents=True)
        (p / "main.tf").write_text("# terraform module entrypoint\n")

    config = dict(_BASE_CONFIG)

    result = run_ci_detect_scope(
        changed_files=["providers/aws/primitives/kms-key/main.tf"],
        scope_override=True,
        config=config,
        output_path=str(output_file),
        repo_root=str(tmp_path),
    )

    assert result["scope"] == "all", (
        f"Expected scope='all' when scope_override=True, got {result['scope']!r}."
    )
    assert result["module_path"] == "", (
        f"module_path must be empty (not None) when scope=all (D43). Got: {result['module_path']!r}"
    )
    assert isinstance(result["modules"], list), "modules must be a list when scope=all."
    assert len(result["modules"]) > 0, (
        "D43 contract: modules list must be non-empty when scope=all. "
        "An empty modules array would leave downstream jobs with no MODULE_PATH."
    )

    output_content = output_file.read_text()
    assert "scope=all" in output_content, (
        f"Output file must contain 'scope=all'. Got: {output_content!r}"
    )
    assert "module_path=" in output_content, (
        f"Output file must contain 'module_path=' line. Got: {output_content!r}"
    )
    # Verify the module_path line is empty (scope=all)
    for line in output_content.splitlines():
        if line.startswith("module_path="):
            assert line == "module_path=", (
                f"module_path must be empty when scope=all (D43). Got: {line!r}"
            )
    assert "modules=" in output_content, (
        f"Output file must contain 'modules=' line. Got: {output_content!r}"
    )
    # Parse the modules JSON from the output
    modules_line = next(
        (line for line in output_content.splitlines() if line.startswith("modules=")), None
    )
    assert modules_line is not None, "Output must have a 'modules=' line."
    modules_json = modules_line[len("modules=") :]
    parsed_modules = json.loads(modules_json)
    assert len(parsed_modules) > 0, (
        "D43: parsed modules JSON array must be non-empty when scope=all."
    )


# ---------------------------------------------------------------------------
# scope=module -- single module_path, empty modules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_detect_scope_module_scope_emits_single_module_path(tmp_path) -> None:
    """scope=module must emit a single module_path and an empty modules array.

    AC-15: for a normal single-module change, ci_detect_scope writes
    scope=module, the module_path, and an empty modules JSON array.
    """
    output_file = tmp_path / "output.txt"

    result = run_ci_detect_scope(
        changed_files=["providers/aws/primitives/kms-key/main.tf"],
        scope_override=False,
        config=_BASE_CONFIG,
        output_path=str(output_file),
        repo_root=str(tmp_path),
    )

    assert result["scope"] == "module", (
        f"Expected scope='module' for a single primitive module change. Got {result['scope']!r}."
    )
    assert result["module_path"] == "providers/aws/primitives/kms-key", (
        f"Expected module_path='providers/aws/primitives/kms-key'. Got {result['module_path']!r}."
    )
    assert result["modules"] == [], (
        f"modules must be empty for scope=module. Got {result['modules']!r}."
    )

    output_content = output_file.read_text()
    assert "scope=module" in output_content, (
        f"Output file must contain 'scope=module'. Got: {output_content!r}"
    )
    assert "module_path=providers/aws/primitives/kms-key" in output_content, (
        f"Output file must contain the module_path. Got: {output_content!r}"
    )
    modules_line = next(
        (line for line in output_content.splitlines() if line.startswith("modules=")), None
    )
    assert modules_line is not None, "Output must have a 'modules=' line."
    modules_json = modules_line[len("modules=") :]
    parsed_modules = json.loads(modules_json)
    assert parsed_modules == [], (
        f"modules JSON must be [] for scope=module. Got: {parsed_modules!r}"
    )


@pytest.mark.unit
def test_ci_detect_scope_version_only_true_for_version_file(tmp_path) -> None:
    """A module change touching ONLY a VERSION file is a release trigger: version_only=true."""
    output_file = tmp_path / "output.txt"
    result = run_ci_detect_scope(
        changed_files=["providers/aws/primitives/kms-key/VERSION"],
        scope_override=False,
        config=_BASE_CONFIG,
        output_path=str(output_file),
        repo_root=str(tmp_path),
    )
    assert result["scope"] == "module"
    assert result["version_only"] is True, f"VERSION-only change must be version_only. Got {result}"
    assert "version_only=true" in output_file.read_text()


@pytest.mark.unit
def test_ci_detect_scope_version_only_false_for_code_change(tmp_path) -> None:
    """A module change touching code (not just VERSION) keeps version_only=false (tf-test runs)."""
    output_file = tmp_path / "output.txt"
    result = run_ci_detect_scope(
        changed_files=["providers/aws/primitives/kms-key/main.tf"],
        scope_override=False,
        config=_BASE_CONFIG,
        output_path=str(output_file),
        repo_root=str(tmp_path),
    )
    assert result["scope"] == "module"
    assert result["version_only"] is False
    assert "version_only=false" in output_file.read_text()


@pytest.mark.unit
def test_changed_files_from_diff_range_parses_git_output(monkeypatch) -> None:
    """_changed_files_from_diff_range runs git diff --name-only and returns its lines (D11)."""
    import subprocess

    from scripts.ci_detect_scope import _changed_files_from_diff_range

    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, check):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="a/VERSION\n\n b/main.tf \n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _changed_files_from_diff_range("BASE...HEAD")
    assert captured["cmd"] == ["git", "diff", "--name-only", "BASE...HEAD"]
    assert result == ["a/VERSION", "b/main.tf"]


# ---------------------------------------------------------------------------
# Unauthorized violation -- ::error:: + exit 1
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_detect_scope_cli_unauthorized_violation_exits_nonzero(tmp_path) -> None:
    """An unauthorized violation must cause ci_detect_scope CLI to emit ::error:: and exit 1.

    AC-15: if detect_scope returns an invalid scope, the CLI must print
    ::error:: to stdout and exit with a non-zero code.
    """
    import subprocess
    import sys

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_BASE_CONFIG))
    output_file = tmp_path / "output.txt"

    # Multi-module input triggers the violation
    input_files = (
        "providers/aws/primitives/kms-key/main.tf\nproviders/aws/primitives/s3-bucket/main.tf\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.ci_detect_scope",
            "--config",
            str(config_path),
            "--scope-override",
            "false",
            "--output",
            str(output_file),
        ],
        input=input_files,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode != 0, (
        "ci_detect_scope CLI must exit non-zero on a scope violation. "
        f"Got returncode={result.returncode}."
    )
    combined = result.stdout + result.stderr
    assert "::error::" in combined, (
        "ci_detect_scope CLI must emit '::error::' on a scope violation. "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Output file format -- scope= / module_path= / modules= written
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "changed_files,scope_override,expected_keys",
    [
        pytest.param(
            ["providers/aws/primitives/kms-key/main.tf"],
            False,
            ["scope=", "module_path=", "modules="],
            id="module-scope-all-three-keys-present",
        ),
        pytest.param(
            ["Makefile"],
            False,
            ["scope=", "module_path=", "modules="],
            id="config-scope-all-three-keys-present",
        ),
        pytest.param(
            ["terragrunt/live/prod/terragrunt.hcl"],
            False,
            ["scope=", "module_path=", "modules="],
            id="terragrunt-scope-all-three-keys-present",
        ),
    ],
)
def test_ci_detect_scope_writes_all_output_keys(
    changed_files: list[str],
    scope_override: bool,
    expected_keys: list[str],
    tmp_path,
) -> None:
    """ci_detect_scope must write scope= / module_path= / modules= to the output file.

    AC-15: The output file must always contain all three keys regardless of scope,
    so downstream workflow steps can reference them unconditionally.
    """
    output_file = tmp_path / "output.txt"

    run_ci_detect_scope(
        changed_files=changed_files,
        scope_override=scope_override,
        config=_BASE_CONFIG,
        output_path=str(output_file),
        repo_root=str(tmp_path),
    )

    output_content = output_file.read_text()
    for key in expected_keys:
        assert key in output_content, (
            f"Output file must contain '{key}' for changed_files={changed_files!r}. "
            f"Got: {output_content!r}"
        )


# ---------------------------------------------------------------------------
# D43: scope=all must fail if no modules discovered
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_detect_scope_all_with_empty_modules_raises_d43(tmp_path) -> None:
    """scope=all must raise D43ViolationError when no modules exist under module_roots.

    D43 contract: scope=all must NEVER emit an empty modules array. If no modules
    are discovered, run_ci_detect_scope must raise D43ViolationError rather than
    producing an empty matrix. The CLI main() catches this and calls sys.exit(1).
    """
    output_file = tmp_path / "output.txt"

    # repo_root with no module dirs -> discover_all_modules returns []
    with pytest.raises(D43ViolationError) as exc_info:
        run_ci_detect_scope(
            changed_files=[],
            scope_override=True,
            config=_BASE_CONFIG,
            output_path=str(output_file),
            repo_root=str(tmp_path),  # no module dirs here
        )

    assert "D43" in str(exc_info.value) or "empty" in str(exc_info.value).lower(), (
        "D43ViolationError must name the D43 contract or mention empty modules."
    )


# ---------------------------------------------------------------------------
# main() function coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_detect_scope_main_valid_scope_exits_zero(tmp_path) -> None:
    """main() must exit 0 for a valid single-module scope change.

    The main() entry point must write the output file and exit cleanly.
    """
    import io

    from scripts.ci_detect_scope import main

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_BASE_CONFIG))
    output_file = tmp_path / "output.txt"

    fake_stdin = io.StringIO("providers/aws/primitives/kms-key/main.tf\n")

    with (
        patch(
            "sys.argv",
            [
                "ci_detect_scope",
                "--config",
                str(config_path),
                "--scope-override",
                "false",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdin", fake_stdin),
        patch("sys.exit") as mock_exit,
    ):
        main()

    mock_exit.assert_not_called()
    assert output_file.exists(), "main() must create the output file."


@pytest.mark.unit
def test_ci_detect_scope_main_invalid_scope_exits_nonzero(tmp_path) -> None:
    """main() must call sys.exit(1) for a multi-module violation.

    Fail-fast: the CLI must exit non-zero when scope detection fails.
    """
    import io

    from scripts.ci_detect_scope import main

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(json.dumps(_BASE_CONFIG))
    output_file = tmp_path / "output.txt"

    fake_stdin = io.StringIO(
        "providers/aws/primitives/kms-key/main.tf\nproviders/aws/primitives/s3-bucket/main.tf\n"
    )

    with (
        patch(
            "sys.argv",
            [
                "ci_detect_scope",
                "--config",
                str(config_path),
                "--scope-override",
                "false",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdin", fake_stdin),
        patch("sys.exit") as mock_exit,
        patch("sys.stdout", io.StringIO()),
    ):
        main()

    mock_exit.assert_called_once_with(1)
