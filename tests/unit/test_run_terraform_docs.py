"""Unit tests for scripts.run_terraform_docs -- pinned terraform-docs wrapper.

Tests assert that:
- AC-1: The module is importable and backs the tf-docs-check Makefile target
- AC-1: All subprocess calls go through scripts.binary_runner.invoke_pinned_binary (DRY)
- AC-1: TerraformDocsCommandError raised on non-zero exit; missing binary emits ERROR: message
- AC-1: 'check <module>' maps to 'markdown table --output-file README.md --output-check <module>'
  (terraform-docs 0.24.0 has no 'check' subcommand; freshness is a read-only render+diff)
- AC-1: CLI exits 2 on missing subcommand, unknown subcommand, or wrong module-path arity;
  exits 1 on missing binary; propagates wrapped exit code on binary failure
- AC-5: 100 percent line coverage on scripts.run_terraform_docs
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from unittest.mock import patch

import pytest


def _import_run_terraform_docs():
    """Import (or re-import) scripts.run_terraform_docs."""
    import scripts.run_terraform_docs as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-1: module is importable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_module_is_importable() -> None:
    """scripts.run_terraform_docs must be importable without ModuleNotFoundError."""
    mod = _import_run_terraform_docs()
    assert hasattr(mod, "run_terraform_docs_command"), (
        "scripts.run_terraform_docs must expose run_terraform_docs_command function."
    )
    assert hasattr(mod, "TerraformDocsCommandError"), (
        "scripts.run_terraform_docs must expose TerraformDocsCommandError exception class."
    )
    assert hasattr(mod, "main"), "scripts.run_terraform_docs must expose a main() entry point."


# ---------------------------------------------------------------------------
# AC-1: correct argv for 'check' subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "subcommand,module_path,expected_argv_tail",
    [
        (
            "check",
            "providers/aws/references/my-module",
            [
                "markdown",
                "table",
                "--output-file",
                "README.md",
                "--output-check",
                "providers/aws/references/my-module",
            ],
        ),
        (
            "check",
            "providers/aws/primitives/s3-bucket",
            [
                "markdown",
                "table",
                "--output-file",
                "README.md",
                "--output-check",
                "providers/aws/primitives/s3-bucket",
            ],
        ),
    ],
)
def test_run_terraform_docs_builds_correct_argv(
    subcommand: str, module_path: str, expected_argv_tail: list[str]
) -> None:
    """check <module> must map to 'markdown table --output-file README.md --output-check <module>'.

    terraform-docs 0.24.0 has no 'check' subcommand; passing it as the first positional
    arg is what produced the 'accepts at most 1 arg(s)' failure. The wrapper must instead
    render markdown and diff against the README via --output-check.
    """
    mod = _import_run_terraform_docs()

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command(
            subcommand=subcommand,
            extra_args=[module_path],
            terraform_docs_binary="terraform-docs",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one invoke_pinned_binary call, got {len(captured_calls)}."
    )
    actual_argv = captured_calls[0]
    assert actual_argv == ["terraform-docs", *expected_argv_tail], (
        f"Expected argv {['terraform-docs', *expected_argv_tail]!r}, got {actual_argv!r}."
    )
    # Regression guard: the literal 'check' token must never be forwarded to the binary
    # (doing so is exactly what broke the gate -- terraform-docs treats it as a path).
    assert "check" not in actual_argv, (
        f"'check' must not be forwarded to the terraform-docs binary, got argv {actual_argv!r}."
    )
    # The README freshness check must be read-only: --output-check, never an in-place write.
    assert "--output-check" in actual_argv, (
        f"argv must include '--output-check' for a read-only README diff, got {actual_argv!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: all subprocess calls go through invoke_pinned_binary (DRY)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_uses_invoke_pinned_binary() -> None:
    """run_terraform_docs_command must delegate subprocess calls to invoke_pinned_binary."""
    mod = _import_run_terraform_docs()

    captured_calls: list[dict] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append({"binary": binary, "args": args})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=["some/module"],
            terraform_docs_binary="terraform-docs",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one invoke_pinned_binary call, got {len(captured_calls)}."
    )
    assert captured_calls[0]["binary"] == "terraform-docs", (
        f"invoke_pinned_binary must be called with binary='terraform-docs', "
        f"got {captured_calls[0]['binary']!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: TerraformDocsCommandError raised on non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_run_terraform_docs_raises_on_nonzero_exit(exit_code: int) -> None:
    """A non-zero terraform-docs exit must raise TerraformDocsCommandError."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], exit_code, stdout="", stderr=f"terraform-docs error rc={exit_code}"
        )

    with (
        patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke),
        pytest.raises(mod.TerraformDocsCommandError) as exc_info,
    ):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=["some/module"],
            terraform_docs_binary="terraform-docs",
        )

    err = exc_info.value
    assert err.exit_code == exit_code, (
        f"TerraformDocsCommandError.exit_code must be {exit_code}, got {err.exit_code!r}."
    )
    err_msg = str(err)
    assert err_msg.startswith("ERROR:"), (
        f"TerraformDocsCommandError message must start with 'ERROR:', got: {err_msg!r}"
    )
    assert str(exit_code) in err_msg, (
        f"TerraformDocsCommandError message must include exit code, got: {err_msg!r}"
    )


@pytest.mark.unit
def test_run_terraform_docs_does_not_raise_on_zero_exit() -> None:
    """A zero terraform-docs exit must not raise any exception."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=["some/module"],
            terraform_docs_binary="terraform-docs",
        )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 2 on missing subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_cli_exits_2_when_no_subcommand() -> None:
    """CLI must exit 2 when invoked without a subcommand."""
    mod = _import_run_terraform_docs()

    with (
        patch.object(sys, "argv", ["run_terraform_docs"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 when no subcommand given, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 2 on unknown subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_cli_exits_2_on_unknown_subcommand(capsys) -> None:
    """CLI must exit 2 with ERROR: on unknown subcommand."""
    mod = _import_run_terraform_docs()

    with (
        patch.object(sys, "argv", ["run_terraform_docs", "unknown-subcommand", "some/module"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 on unknown subcommand, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Unknown subcommand must print ERROR: to stderr, got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 1 on missing binary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_cli_exits_1_on_missing_binary(capsys) -> None:
    """CLI must exit 1 with ERROR: required binary not found; run make tools-ensure."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{binary}'")

    with (
        patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform_docs", "check", "some/module"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 1, (
        f"CLI must exit 1 on missing binary, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Missing binary error must print ERROR: to stderr, got: {captured.err!r}"
    )
    assert "terraform-docs" in captured.err, (
        f"Missing binary error must name the binary, got: {captured.err!r}"
    )
    assert "tools-ensure" in captured.err, (
        f"Missing binary error must mention 'make tools-ensure', got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 0 on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_cli_exits_zero_on_success() -> None:
    """CLI main must exit 0 when terraform-docs succeeds."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with (
        patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform_docs", "check", "some/module"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 0, f"CLI must exit 0 on success, got {exc_info.value.code!r}."


# ---------------------------------------------------------------------------
# AC-1: CLI exits non-zero on TerraformDocsCommandError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_cli_exits_nonzero_on_failure() -> None:
    """CLI main must exit non-zero when terraform-docs fails."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 1, stdout="", stderr="terraform-docs failed"
        )

    with (
        patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform_docs", "check", "some/module"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI main must exit non-zero on failure, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: binary path is configurable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_accepts_custom_binary_path() -> None:
    """The terraform-docs binary path must be configurable, not hard-coded inline."""
    mod = _import_run_terraform_docs()
    custom_binary = "/usr/local/bin/terraform-docs-0.18.0"

    captured_calls: list[str] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append(binary)
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=["some/module"],
            terraform_docs_binary=custom_binary,
        )

    assert captured_calls[0] == custom_binary, (
        f"Expected binary {custom_binary!r}, got {captured_calls[0]!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: stdout/stderr passthrough on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_passes_stdout_on_success(capsys) -> None:
    """On success, run_terraform_docs_command must pass binary stdout to sys.stdout."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="docs ok\n", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=["some/module"],
            terraform_docs_binary="terraform-docs",
        )

    captured = capsys.readouterr()
    assert "docs ok" in captured.out, f"Expected stdout passthrough, got: {captured.out!r}"


@pytest.mark.unit
def test_run_terraform_docs_passes_stderr_on_success(capsys) -> None:
    """On success, run_terraform_docs_command must pass binary stderr to sys.stderr."""
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout="", stderr="warning: docs note\n"
        )

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=["some/module"],
            terraform_docs_binary="terraform-docs",
        )

    captured = capsys.readouterr()
    assert "warning" in captured.err, f"Expected stderr passthrough, got: {captured.err!r}"


# ---------------------------------------------------------------------------
# Coverage: ValueError raised on unsupported subcommand in library function
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_command_raises_value_error_on_unsupported_subcommand() -> None:
    """run_terraform_docs_command must raise ValueError for unsupported subcommand."""
    mod = _import_run_terraform_docs()

    with pytest.raises(ValueError, match="Unsupported subcommand"):
        mod.run_terraform_docs_command(
            subcommand="totally-unknown",
            extra_args=["some/module"],
            terraform_docs_binary="terraform-docs",
        )


# ---------------------------------------------------------------------------
# Coverage: ValueError raised when 'check' is not given exactly one module path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra_args",
    [
        [],
        ["module/a", "module/b"],
    ],
)
def test_run_terraform_docs_command_raises_value_error_on_wrong_arity(
    extra_args: list[str],
) -> None:
    """check must require exactly one module path; the binary accepts a single positional.

    Forwarding zero or multiple paths is what produced the 'accepts at most 1 arg(s),
    received 2' failure, so the wrapper fails fast before invoking the binary.
    """
    mod = _import_run_terraform_docs()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        raise AssertionError("invoke_pinned_binary must not be called on an arity error.")

    with (
        patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke),
        pytest.raises(ValueError, match="exactly one module path"),
    ):
        mod.run_terraform_docs_command(
            subcommand="check",
            extra_args=extra_args,
            terraform_docs_binary="terraform-docs",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "cli_args",
    [
        ["run_terraform_docs", "check"],
        ["run_terraform_docs", "check", "module/a", "module/b"],
    ],
)
def test_run_terraform_docs_cli_exits_2_on_wrong_arity(cli_args: list[str], capsys) -> None:
    """CLI must exit 2 with ERROR: when check is not given exactly one module path."""
    mod = _import_run_terraform_docs()

    with (
        patch.object(sys, "argv", cli_args),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 on wrong module-path arity, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Wrong arity must print ERROR: to stderr, got: {captured.err!r}"
    )
    assert "exactly one module path" in captured.err, (
        f"Error must explain the one-module-path requirement, got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Coverage: __main__ block -- executed via importlib with __name__ == '__main__'
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_docs_dunder_main_block() -> None:
    """The if __name__ == '__main__' block must execute main() when run as module."""
    import importlib.util
    import subprocess
    from pathlib import Path

    import scripts.binary_runner as br

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    orig_invoke = br.invoke_pinned_binary
    br.invoke_pinned_binary = fake_invoke
    try:
        repo_root = Path(__file__).parent.parent.parent
        source_path = str(repo_root / "scripts" / "run_terraform_docs.py")
        spec = importlib.util.spec_from_file_location("__main__", source_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert module is not None

        with (
            patch.object(sys, "argv", ["run_terraform_docs", "check", "some/module"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            assert spec.loader is not None
            spec.loader.exec_module(module)
    finally:
        br.invoke_pinned_binary = orig_invoke

    assert exc_info.value.code == 0, (
        f"__main__ block must exit 0 on success, got {exc_info.value.code!r}."
    )


def test_check_skips_variable_driven_source_module(tmp_path) -> None:
    """A module whose main.tf uses `source = var.<x>` is skipped: terraform-docs cannot parse a
    variable module source (upstream #793/#860), so invoke_pinned_binary is never called."""
    mod = _import_run_terraform_docs()
    (tmp_path / "main.tf").write_text(
        'module "kms" {\n  source = var.spice_kms_source\n}\n', encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_invoke(binary, args, capture_output=False, cwd=None):
        calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command("check", [str(tmp_path)])

    assert calls == [], (
        f"terraform-docs must be skipped for variable-driven-source modules; called: {calls!r}"
    )


def test_check_runs_for_literal_source_module(tmp_path) -> None:
    """A module with only literal module sources is NOT skipped: terraform-docs runs normally."""
    mod = _import_run_terraform_docs()
    (tmp_path / "main.tf").write_text(
        'module "p" {\n  source = "../../primitives/s3-bucket"\n}\n', encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_invoke(binary, args, capture_output=False, cwd=None):
        calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform_docs.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_docs_command("check", [str(tmp_path)])

    assert len(calls) == 1, f"terraform-docs must run for literal-source modules; calls={calls!r}"
    assert "--output-check" in calls[0]
