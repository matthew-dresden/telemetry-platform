"""Unit tests for scripts.run_terraform -- pinned Terraform toolchain wrapper.

Tests assert that:
- AC-1: The module is importable and backs the tf-format/lint/validate/plan Makefile targets
- AC-1: All subprocess calls go through scripts.binary_runner.invoke_pinned_binary (DRY)
- AC-1: TerraformCommandError raised on non-zero exit; missing binary emits correct ERROR: message
- AC-1: Parametrized over fmt/lint/validate/plan subcommands
- AC-1: validate/plan run per examples/<ex> with terraform init -backend=false (offline)
- AC-1: lint uses tflint --chdir <path>
- AC-5: 100 percent line coverage on scripts.run_terraform
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _import_run_terraform():
    """Import (or re-import) scripts.run_terraform."""
    import scripts.run_terraform as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-1: module is importable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_module_is_importable() -> None:
    """scripts.run_terraform must be importable without ModuleNotFoundError."""
    mod = _import_run_terraform()
    assert hasattr(mod, "run_terraform_command"), (
        "scripts.run_terraform must expose run_terraform_command function."
    )
    assert hasattr(mod, "TerraformCommandError"), (
        "scripts.run_terraform must expose TerraformCommandError exception class."
    )
    assert hasattr(mod, "main"), "scripts.run_terraform must expose a main() entry point."


# ---------------------------------------------------------------------------
# AC-1: fmt subcommand -- passes all extra args to terraform fmt
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra_args,expected_fragments",
    [
        (["--check", "--recursive", "."], ["fmt", "--check", "--recursive", "."]),
        (["--check", "--recursive", "providers/"], ["fmt", "--check", "--recursive", "providers/"]),
    ],
)
def test_run_terraform_fmt_builds_correct_argv(
    extra_args: list[str], expected_fragments: list[str], tmp_path: Path
) -> None:
    """run_terraform_command fmt must pass all extra args to terraform fmt."""
    mod = _import_run_terraform()

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="fmt",
            extra_args=extra_args,
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    assert len(captured_calls) == 1, (
        f"fmt must make exactly one invoke_pinned_binary call, got {len(captured_calls)}."
    )
    actual_argv = captured_calls[0]
    for fragment in expected_fragments:
        assert fragment in actual_argv, f"Expected {fragment!r} in fmt argv {actual_argv!r}."


# ---------------------------------------------------------------------------
# AC-1: lint subcommand -- uses tflint --chdir <path>
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_lint_uses_tflint_chdir(tmp_path: Path) -> None:
    """run_terraform_command lint must invoke tflint --chdir <module_path>."""
    mod = _import_run_terraform()

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    module_path = str(tmp_path / "providers" / "aws" / "references" / "my-module")
    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="lint",
            extra_args=["--chdir", module_path],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    assert len(captured_calls) == 1, (
        f"lint must make exactly one invoke_pinned_binary call, got {len(captured_calls)}."
    )
    actual_argv = captured_calls[0]
    assert actual_argv[0] == "tflint", f"lint must invoke tflint binary, got {actual_argv[0]!r}."
    assert "--chdir" in actual_argv, f"lint argv must contain '--chdir', got {actual_argv!r}."
    assert module_path in actual_argv, (
        f"lint argv must contain module path {module_path!r}, got {actual_argv!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: validate subcommand -- runs per examples/<ex> with init -backend=false
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_validate_runs_init_and_validate_per_example(tmp_path: Path) -> None:
    """validate must run 'terraform init -backend=false' then 'terraform validate' per example."""
    mod = _import_run_terraform()

    # Create examples structure
    module_dir = tmp_path / "my-module"
    examples_dir = module_dir / "examples"
    ex1 = examples_dir / "basic"
    ex2 = examples_dir / "advanced"
    ex1.mkdir(parents=True)
    ex2.mkdir(parents=True)
    (ex1 / "main.tf").write_text("# basic example\n")
    (ex2 / "main.tf").write_text("# advanced example\n")

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="validate",
            extra_args=[str(module_dir)],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    # Should have init + validate for each example (2 examples = 4 calls)
    assert len(captured_calls) == 4, (
        f"validate over 2 examples must make 4 invoke calls (init+validate each), "
        f"got {len(captured_calls)}: {captured_calls!r}"
    )

    # Terraform selects the working directory with the global -chdir=<dir> flag placed
    # BEFORE the subcommand; the directory must never be passed as a positional argument
    # (that fails with "Too many command line arguments. Did you mean to use -chdir?").
    for call in captured_calls:
        binary, *args = call
        assert binary == "terraform", f"validate must invoke terraform, got {binary!r}."
        assert args[0].startswith("-chdir="), (
            f"first arg must be the global -chdir=<dir> flag before the subcommand, got {args!r}."
        )
        chdir_dir = args[0].removeprefix("-chdir=")
        assert chdir_dir in (str(ex1), str(ex2)), (
            f"-chdir must target an example directory, got {chdir_dir!r}."
        )
        # The example directory must NOT also appear as a positional path argument.
        assert chdir_dir not in args[1:], (
            f"example dir must not be passed positionally in addition to -chdir, got {args!r}."
        )

    # Each captured call is [binary, -chdir=<dir>, <subcommand>, ...]; the subcommand is
    # therefore the third element (index 2), immediately after the global -chdir flag.
    init_calls = [c for c in captured_calls if "init" in c]
    validate_calls = [c for c in captured_calls if "validate" in c]
    assert len(init_calls) == 2, f"expected 2 init calls, got {init_calls!r}"
    assert len(validate_calls) == 2, f"expected 2 validate calls, got {validate_calls!r}"
    for init_call in init_calls:
        # terraform -chdir=<dir> init -backend=false  (subcommand after the global flag)
        assert init_call[2] == "init", f"subcommand must follow -chdir, got {init_call!r}."
        assert "-backend=false" in init_call, (
            f"init must run offline with -backend=false, got {init_call!r}."
        )
    for validate_call in validate_calls:
        assert validate_call[2] == "validate", (
            f"subcommand must follow -chdir, got {validate_call!r}."
        )


# ---------------------------------------------------------------------------
# AC-1: plan subcommand -- runs per examples/<ex> with init -backend=false
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_plan_runs_init_and_plan_per_example(tmp_path: Path) -> None:
    """plan must run 'terraform init -backend=false' then 'terraform plan' per example."""
    mod = _import_run_terraform()

    module_dir = tmp_path / "my-module"
    examples_dir = module_dir / "examples"
    ex1 = examples_dir / "default"
    ex1.mkdir(parents=True)
    (ex1 / "main.tf").write_text("# default example\n")

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="plan",
            extra_args=[str(module_dir)],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    # One example -> 2 calls (init + plan), each using the global -chdir=<dir> flag
    # before the subcommand rather than a positional path argument.
    assert len(captured_calls) == 2, (
        f"plan over 1 example must make 2 invoke calls (init+plan), "
        f"got {len(captured_calls)}: {captured_calls!r}"
    )
    for call in captured_calls:
        binary, *args = call
        assert binary == "terraform", f"plan must invoke terraform, got {binary!r}."
        assert args[0] == f"-chdir={ex1}", (
            f"first arg must be -chdir={ex1!r} before the subcommand, got {args!r}."
        )
        assert str(ex1) not in args[1:], (
            f"example dir must not be passed positionally in addition to -chdir, got {args!r}."
        )

    # Each captured call is [binary, -chdir=<dir>, <subcommand>, ...]; the subcommand is
    # the third element (index 2), immediately after the global -chdir flag.
    init_call = captured_calls[0]
    plan_call = captured_calls[1]
    assert init_call[2] == "init", f"subcommand must follow -chdir, got {init_call!r}."
    assert "-backend=false" in init_call, (
        f"plan init must run offline with -backend=false, got {init_call!r}."
    )
    assert plan_call[2] == "plan", f"subcommand must follow -chdir, got {plan_call!r}."


# ---------------------------------------------------------------------------
# AC-1: TerraformCommandError raised on non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_run_terraform_raises_on_nonzero_exit(exit_code: int) -> None:
    """A non-zero binary exit must raise TerraformCommandError with actionable ERROR: message."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], exit_code, stdout="", stderr=f"terraform error rc={exit_code}"
        )

    with (
        patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke),
        pytest.raises(mod.TerraformCommandError) as exc_info,
    ):
        mod.run_terraform_command(
            subcommand="fmt",
            extra_args=["--check", "."],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    err = exc_info.value
    assert err.exit_code == exit_code, (
        f"TerraformCommandError.exit_code must be {exit_code}, got {err.exit_code!r}."
    )
    err_msg = str(err)
    assert err_msg.startswith("ERROR:"), (
        f"TerraformCommandError message must start with 'ERROR:', got: {err_msg!r}"
    )
    assert str(exit_code) in err_msg, (
        f"TerraformCommandError message must include exit code, got: {err_msg!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 2 on missing subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_cli_exits_2_when_no_subcommand() -> None:
    """CLI must exit 2 when invoked without a subcommand."""
    mod = _import_run_terraform()

    with (
        patch.object(sys, "argv", ["run_terraform"]),
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
def test_run_terraform_cli_exits_2_on_unknown_subcommand(capsys) -> None:
    """CLI must exit 2 with ERROR: on unknown subcommand."""
    mod = _import_run_terraform()

    with (
        patch.object(sys, "argv", ["run_terraform", "unknown-subcommand"]),
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
def test_run_terraform_cli_exits_1_on_missing_binary(capsys) -> None:
    """CLI must exit 1 with 'ERROR: required binary terraform not found; run make tools-ensure'."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{binary}'")

    with (
        patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform", "fmt", "--check", "."]),
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
    assert "tools-ensure" in captured.err, (
        f"Missing binary error must mention 'make tools-ensure', got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 0 on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_cli_exits_zero_on_success() -> None:
    """CLI main must exit 0 when the terraform binary succeeds."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with (
        patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform", "fmt", "--check", "."]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 0, f"CLI must exit 0 on success, got {exc_info.value.code!r}."


# ---------------------------------------------------------------------------
# AC-1: CLI exits non-zero on TerraformCommandError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_cli_exits_nonzero_on_failure() -> None:
    """CLI main must exit non-zero when the terraform binary fails."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 1, stdout="", stderr="terraform failed")

    with (
        patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform", "fmt", "--check", "."]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI main must exit non-zero on terraform failure, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: binary path is configurable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_accepts_custom_binary_path() -> None:
    """The terraform binary path must be configurable, not hard-coded inline."""
    mod = _import_run_terraform()
    custom_binary = "/usr/local/bin/terraform-1.9.0"

    captured_calls: list[str] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append(binary)
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="fmt",
            extra_args=["--check", "."],
            terraform_binary=custom_binary,
            tflint_binary="tflint",
        )

    assert captured_calls[0] == custom_binary, (
        f"Expected binary {custom_binary!r} as first call, got {captured_calls[0]!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: uses invoke_pinned_binary (DRY) for lint via tflint
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_lint_uses_invoke_pinned_binary() -> None:
    """lint must delegate to invoke_pinned_binary, not call subprocess.run directly."""
    mod = _import_run_terraform()

    captured_calls: list[dict] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append({"binary": binary, "args": args})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="lint",
            extra_args=["--chdir", "/some/path"],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    assert len(captured_calls) >= 1, (
        f"lint must call invoke_pinned_binary at least once, got {len(captured_calls)}."
    )


# ---------------------------------------------------------------------------
# AC-1: stdout/stderr passthrough on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_passes_stdout_on_success(capsys) -> None:
    """On success, run_terraform_command must pass binary stdout to sys.stdout."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout="terraform fmt ok\n", stderr=""
        )

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="fmt",
            extra_args=["--check", "."],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    captured = capsys.readouterr()
    assert "terraform fmt ok" in captured.out, f"Expected stdout passthrough, got: {captured.out!r}"


@pytest.mark.unit
def test_run_terraform_passes_stderr_on_success(capsys) -> None:
    """On success, run_terraform_command must pass binary stderr to sys.stderr."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout="", stderr="warning: terraform note\n"
        )

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="fmt",
            extra_args=["--check", "."],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    captured = capsys.readouterr()
    assert "warning" in captured.err, f"Expected stderr passthrough, got: {captured.err!r}"


# ---------------------------------------------------------------------------
# Coverage: ValueError raised on unsupported subcommand in library function
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_command_raises_value_error_on_unsupported_subcommand() -> None:
    """run_terraform_command must raise ValueError when given an unsupported subcommand."""
    mod = _import_run_terraform()

    with pytest.raises(ValueError, match="Unsupported subcommand"):
        mod.run_terraform_command(
            subcommand="totally-unknown",
            extra_args=[],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )


# ---------------------------------------------------------------------------
# Coverage: validate/plan with no examples dir falls back to module_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_validate_fallback_when_no_examples_dir(tmp_path: Path) -> None:
    """validate must fall back to running on module_path directly when examples/ is missing."""
    mod = _import_run_terraform()

    module_dir = tmp_path / "my-module"
    module_dir.mkdir(parents=True)
    # No examples/ directory

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terraform_command(
            subcommand="validate",
            extra_args=[str(module_dir)],
            terraform_binary="terraform",
            tflint_binary="tflint",
        )

    assert len(captured_calls) == 2, (
        f"validate must still run init+validate on the module dir when no examples/ "
        f"dir exists, got {len(captured_calls)} calls: {captured_calls!r}"
    )
    # Falls back to the module path itself, still via the global -chdir flag.
    for call in captured_calls:
        binary, *args = call
        assert binary == "terraform", f"fallback must invoke terraform, got {binary!r}."
        assert args[0] == f"-chdir={module_dir}", (
            f"fallback must target the module dir via -chdir, got {args!r}."
        )


# ---------------------------------------------------------------------------
# Coverage: FileNotFoundError with tflint binary name detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_cli_exits_1_on_missing_tflint_binary(capsys) -> None:
    """CLI must exit 1 naming 'tflint' when tflint binary is missing."""
    mod = _import_run_terraform()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'tflint'")

    with (
        patch("scripts.run_terraform.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(sys, "argv", ["run_terraform", "lint", "--chdir", "some/path"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 1, (
        f"CLI must exit 1 on missing tflint binary, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert "tflint" in captured.err, (
        f"Missing tflint error must name 'tflint', got: {captured.err!r}"
    )
    assert "tools-ensure" in captured.err, (
        f"Error must mention 'make tools-ensure', got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Coverage: __main__ block -- executed via importlib with __name__ == '__main__'
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terraform_dunder_main_block() -> None:
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
        source_path = str(repo_root / "scripts" / "run_terraform.py")
        spec = importlib.util.spec_from_file_location("__main__", source_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert module is not None

        with (
            patch.object(sys, "argv", ["run_terraform", "fmt", "--check", "."]),
            pytest.raises(SystemExit) as exc_info,
        ):
            assert spec.loader is not None
            spec.loader.exec_module(module)
    finally:
        br.invoke_pinned_binary = orig_invoke

    assert exc_info.value.code == 0, (
        f"__main__ block must exit 0 on success, got {exc_info.value.code!r}."
    )
