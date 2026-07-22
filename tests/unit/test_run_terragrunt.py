"""Unit tests for scripts.run_terragrunt -- pinned terragrunt binary wrapper.

Tests assert that:
- AC-FIX-001: hcl-format-check maps to 'terragrunt hcl format --check' (v1.0.7+), not the
  removed 'terragrunt hclfmt --check'
- AC-FIX-002: All subprocess calls go through scripts.binary_runner.invoke_pinned_binary (DRY)
- AC-FIX-003: TerragruntCommandError raised on non-zero exit; unknown subcommand fails fast
- AC-FIX-004: Parametrized over validate/hcl-format-check/plan/apply; real assertions only
- AC-FIX-005: Import resolves (no ModuleNotFoundError) so make tg-validate can dispatch
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_run_terragrunt():
    """Import (or re-import) scripts.run_terragrunt."""
    import scripts.run_terragrunt as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-FIX-001 / AC-FIX-005: module is importable (no ModuleNotFoundError)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_module_is_importable() -> None:
    """scripts.run_terragrunt must be importable without ModuleNotFoundError.

    This is the minimal requirement backing AC-FIX-005: once this passes,
    'make tg-validate' can import and dispatch to the terragrunt binary
    instead of raising ModuleNotFoundError.
    """
    mod = _import_run_terragrunt()
    assert hasattr(mod, "run_terragrunt_command"), (
        "scripts.run_terragrunt must expose a run_terragrunt_command function."
    )
    assert hasattr(mod, "TerragruntCommandError"), (
        "scripts.run_terragrunt must expose a TerragruntCommandError exception class."
    )
    assert hasattr(mod, "main"), "scripts.run_terragrunt must expose a main() entry point."


# ---------------------------------------------------------------------------
# AC-FIX-001 / AC-FIX-004: argv construction for each supported subcommand
# ---------------------------------------------------------------------------

_SUBCOMMAND_ARGV_CASES = [
    # (subcommand, extra_args, expected_binary_args_contain)
    # validate -> terragrunt hcl validate (recursive HCL validation; the bare
    # `terragrunt validate` passthrough needs a terragrunt.hcl in cwd, but this repo's
    # root config is root.hcl, so it failed at the repo root).
    (
        "validate",
        [],
        ["hcl", "validate"],
    ),
    # validate with change-scoped include-dir flags forwarded (same pattern as plan/apply)
    (
        "validate",
        ["--queue-include-dir", "terragrunt/live/telemetry/us-east-1/sandbox/000/portal/000"],
        [
            "hcl",
            "validate",
            "--queue-include-dir",
            "terragrunt/live/telemetry/us-east-1/sandbox/000/portal/000",
        ],
    ),
    # hcl-format-check -> terragrunt hcl format --check (v1.0.7+)
    (
        "hcl-format-check",
        [],
        ["hcl", "format", "--check"],
    ),
    # plan -> terragrunt run --all plan
    (
        "plan",
        [],
        ["run", "--all", "plan"],
    ),
    # apply -> terragrunt run --all apply
    (
        "apply",
        [],
        ["run", "--all", "apply"],
    ),
    # plan with include-dir flags forwarded
    (
        "plan",
        ["--include-dir", "terragrunt/sandbox/us-east-1/analytics"],
        ["run", "--all", "plan", "--include-dir", "terragrunt/sandbox/us-east-1/analytics"],
    ),
    # apply with include-dir flags forwarded
    (
        "apply",
        ["--include-dir", "terragrunt/sandbox/us-east-1/analytics"],
        ["run", "--all", "apply", "--include-dir", "terragrunt/sandbox/us-east-1/analytics"],
    ),
    # destroy -> terragrunt run --all destroy (ephemeral teardown lane)
    (
        "destroy",
        [],
        ["run", "--all", "destroy"],
    ),
    # destroy with change-scoped include-dir flags forwarded (same pattern as apply)
    (
        "destroy",
        ["--queue-include-dir", "terragrunt/live/telemetry/us-east-1/sandbox/000/collector/000"],
        [
            "run",
            "--all",
            "destroy",
            "--queue-include-dir",
            "terragrunt/live/telemetry/us-east-1/sandbox/000/collector/000",
        ],
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("subcommand,extra_args,expected_contains", _SUBCOMMAND_ARGV_CASES)
def test_run_terragrunt_builds_correct_argv(
    subcommand: str,
    extra_args: list[str],
    expected_contains: list[str],
) -> None:
    """run_terragrunt_command must build the argv expected by the pinned terragrunt binary.

    Verifies that:
    - validate maps to 'terragrunt hcl validate [extra_args]'
    - hcl-format-check maps to 'terragrunt hcl format --check' (v1.0.7+)
    - plan maps to 'terragrunt run --all plan [extra_args]'
    - apply maps to 'terragrunt run --all apply [extra_args]'
    """
    mod = _import_run_terragrunt()

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_terragrunt_command(
            subcommand=subcommand,
            extra_args=extra_args,
            terragrunt_binary="terragrunt",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one subprocess.run call for subcommand={subcommand!r}, "
        f"got {len(captured_calls)}."
    )
    actual_argv = captured_calls[0]
    assert actual_argv[0] == "terragrunt", (
        f"Expected binary 'terragrunt' as argv[0], got {actual_argv[0]!r}."
    )
    for expected_part in expected_contains:
        assert expected_part in actual_argv, (
            f"Expected {expected_part!r} in argv {actual_argv!r} "
            f"for subcommand={subcommand!r} extra_args={extra_args!r}."
        )
    # apply + destroy MUST be non-interactive in CI (no TTY); plan/validate/format must NOT.
    if subcommand in ("apply", "destroy"):
        assert "--non-interactive" in actual_argv, (
            f"{subcommand!r} must pass --non-interactive so `terragrunt run --all {subcommand}` "
            "auto-answers the run-queue confirmation in a TTY-less CI runner; "
            f"got {actual_argv!r}."
        )
    else:
        assert "--non-interactive" not in actual_argv, (
            f"{subcommand!r} must NOT inject --non-interactive (only apply/destroy prompt); "
            f"got {actual_argv!r}."
        )


# ---------------------------------------------------------------------------
# AC-FIX-002: every subprocess call goes through invoke_pinned_binary (DRY)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["validate", "hcl-format-check", "plan", "apply"])
def test_run_terragrunt_uses_invoke_pinned_binary(subcommand: str) -> None:
    """run_terragrunt_command must delegate subprocess calls to invoke_pinned_binary.

    This enforces DRY: no direct subprocess.run calls inside run_terragrunt.py.
    The boundary is patched at scripts.binary_runner.invoke_pinned_binary.
    """
    mod = _import_run_terragrunt()

    captured_calls: list[dict] = []

    def fake_invoke(binary: str, args: list[str], capture_output: bool = False):
        captured_calls.append({"binary": binary, "args": args})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_terragrunt.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_terragrunt_command(
            subcommand=subcommand,
            extra_args=[],
            terragrunt_binary="terragrunt",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one invoke_pinned_binary call for subcommand={subcommand!r}, "
        f"got {len(captured_calls)}. "
        "All subprocess calls must go through binary_runner to stay DRY."
    )
    assert captured_calls[0]["binary"] == "terragrunt", (
        f"invoke_pinned_binary must be called with binary='terragrunt', "
        f"got {captured_calls[0]['binary']!r}."
    )


# ---------------------------------------------------------------------------
# AC-FIX-003: TerragruntCommandError raised on non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_run_terragrunt_raises_on_nonzero_exit(exit_code: int) -> None:
    """A non-zero terragrunt exit must raise TerragruntCommandError.

    The error must carry subcommand, argv, exit_code, and stderr_output
    as documented in AC-FIX-003, and the message must start with 'ERROR:'.
    """
    mod = _import_run_terragrunt()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            argv, exit_code, stdout="", stderr=f"terragrunt error rc={exit_code}"
        )

    with (
        patch("subprocess.run", side_effect=fake_run),
        pytest.raises(mod.TerragruntCommandError) as exc_info,
    ):
        mod.run_terragrunt_command(
            subcommand="validate",
            extra_args=[],
            terragrunt_binary="terragrunt",
        )

    err = exc_info.value
    assert err.subcommand == "validate", (
        f"TerragruntCommandError.subcommand must be 'validate', got {err.subcommand!r}."
    )
    assert err.exit_code == exit_code, (
        f"TerragruntCommandError.exit_code must be {exit_code}, got {err.exit_code!r}."
    )
    assert err.stderr_output == f"terragrunt error rc={exit_code}", (
        f"TerragruntCommandError.stderr_output mismatch: {err.stderr_output!r}."
    )
    assert err.argv, "TerragruntCommandError.argv must be non-empty."
    err_msg = str(err)
    assert err_msg.startswith("ERROR:"), (
        f"TerragruntCommandError message must start with 'ERROR:', got: {err_msg!r}"
    )
    assert str(exit_code) in err_msg, (
        f"TerragruntCommandError message must include exit code {exit_code}, got: {err_msg!r}"
    )


# ---------------------------------------------------------------------------
# AC-FIX-003: zero exit does not raise
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["validate", "hcl-format-check", "plan", "apply"])
def test_run_terragrunt_does_not_raise_on_zero_exit(subcommand: str) -> None:
    """A zero terragrunt exit must not raise any exception."""
    mod = _import_run_terragrunt()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_terragrunt_command(
            subcommand=subcommand,
            extra_args=[],
            terragrunt_binary="terragrunt",
        )


# ---------------------------------------------------------------------------
# AC-FIX-003: unknown subcommand fails fast (non-zero exit, actionable message)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_cli_exits_nonzero_on_unknown_subcommand() -> None:
    """An unknown subcommand must cause main() to exit non-zero with an actionable message."""
    mod = _import_run_terragrunt()

    with (
        patch.object(sys, "argv", ["run_terragrunt", "unknown-subcommand"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero on unknown subcommand, got {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_run_terragrunt_unknown_subcommand_prints_error(capsys) -> None:
    """An unknown subcommand must print an actionable error message to stderr."""
    mod = _import_run_terragrunt()

    with (
        patch.object(sys, "argv", ["run_terragrunt", "unknown-subcommand"]),
        pytest.raises(SystemExit),
    ):
        mod.main()

    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Unknown subcommand must print an ERROR: message to stderr, got: {captured.err!r}"
    )
    assert "unknown-subcommand" in captured.err, (
        f"The error message must name the unknown subcommand, got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-FIX-003: CLI main exits non-zero on TerragruntCommandError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_cli_exits_nonzero_on_failure() -> None:
    """CLI main must exit non-zero when the terragrunt binary fails."""
    mod = _import_run_terragrunt()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="terragrunt failed")

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch.object(sys, "argv", ["run_terragrunt", "validate"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI main must exit non-zero on terragrunt failure, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-FIX-001: CLI main exits zero on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["validate", "hcl-format-check", "plan", "apply"])
def test_run_terragrunt_cli_exits_zero_on_success(subcommand: str) -> None:
    """CLI main must exit 0 when the terragrunt binary succeeds."""
    mod = _import_run_terragrunt()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    argv_map = {
        "validate": ["run_terragrunt", "validate"],
        "hcl-format-check": ["run_terragrunt", "hcl-format-check"],
        "plan": ["run_terragrunt", "plan"],
        "apply": ["run_terragrunt", "apply"],
    }

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch.object(sys, "argv", argv_map[subcommand]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 0, (
        f"CLI main must exit 0 on success for subcommand={subcommand!r}, "
        f"got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-FIX-001: CLI exits 2 when no subcommand given
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_cli_exits_2_when_no_subcommand() -> None:
    """CLI must exit with code 2 when invoked without a subcommand."""
    mod = _import_run_terragrunt()

    with (
        patch.object(sys, "argv", ["run_terragrunt"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 when no subcommand given, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-FIX-002: binary path is configurable, never hard-coded
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_accepts_custom_binary_path() -> None:
    """The terragrunt binary path must be configurable, not hard-coded inline."""
    mod = _import_run_terragrunt()
    custom_binary = "/usr/local/bin/terragrunt-0.67.0"

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_terragrunt_command(
            subcommand="validate",
            extra_args=[],
            terragrunt_binary=custom_binary,
        )

    assert captured_calls[0][0] == custom_binary, (
        f"Expected binary {custom_binary!r} as argv[0], got {captured_calls[0][0]!r}. "
        "The binary path must be configurable, not hard-coded."
    )


# ---------------------------------------------------------------------------
# AC-FIX-001: stdout/stderr passthrough on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_passes_stdout_on_success(capsys) -> None:
    """On success, run_terragrunt_command must pass binary stdout to sys.stdout."""
    mod = _import_run_terragrunt()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="Terragrunt ok\n", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_terragrunt_command(
            subcommand="validate",
            extra_args=[],
            terragrunt_binary="terragrunt",
        )

    captured = capsys.readouterr()
    assert "Terragrunt ok" in captured.out, f"Expected stdout passthrough, got: {captured.out!r}"


@pytest.mark.unit
def test_run_terragrunt_passes_stderr_on_success(capsys) -> None:
    """On success, run_terragrunt_command must pass binary stderr to sys.stderr."""
    mod = _import_run_terragrunt()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="warning: something\n")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_terragrunt_command(
            subcommand="validate",
            extra_args=[],
            terragrunt_binary="terragrunt",
        )

    captured = capsys.readouterr()
    assert "warning" in captured.err, f"Expected stderr passthrough, got: {captured.err!r}"


# ---------------------------------------------------------------------------
# Library-level ValueError for unsupported subcommand (line 115 coverage)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_command_raises_value_error_on_unsupported_subcommand() -> None:
    """run_terragrunt_command must raise ValueError on unsupported subcommand."""
    mod = _import_run_terragrunt()

    with pytest.raises(ValueError) as exc_info:
        mod.run_terragrunt_command(
            subcommand="unsupported-cmd",
            extra_args=[],
            terragrunt_binary="terragrunt",
        )

    assert "Unsupported subcommand" in str(exc_info.value), (
        f"ValueError must mention unsupported subcommand, got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# AC-FIX-006: CLI exits 1 with actionable message when pinned binary is absent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_cli_exits_1_on_missing_binary(capsys) -> None:
    """CLI main must exit 1 with 'ERROR: required binary ... not found; run make tools-ensure'.

    When invoke_pinned_binary raises FileNotFoundError (terragrunt binary absent),
    main() must catch it and emit the standard missing-binary message to stderr,
    then exit 1 -- never exit with a raw Python traceback.
    """
    mod = _import_run_terragrunt()

    with (
        patch(
            "scripts.run_terragrunt.invoke_pinned_binary",
            side_effect=FileNotFoundError("terragrunt"),
        ),
        patch.object(sys, "argv", ["run_terragrunt", "validate"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 1, (
        f"CLI must exit 1 when pinned binary is absent, got {exc_info.value.code!r}."
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Missing-binary error must start with 'ERROR:', got: {captured.err!r}"
    )
    assert "terragrunt" in captured.err, (
        f"Missing-binary error must name the binary, got: {captured.err!r}"
    )
    assert "tools-ensure" in captured.err, (
        f"Missing-binary error must mention 'make tools-ensure', got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# __main__ block (line 191 coverage)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_terragrunt_dunder_main_block() -> None:
    """The if __name__ == '__main__' guard must invoke main() when run as __main__."""
    import importlib.util
    import subprocess as subprocess_mod

    # Load the source file as __main__ so the `if __name__ == "__main__": main()` block runs.
    # Patch subprocess.run so that the validate subcommand succeeds offline.
    terragrunt_source = (
        __import__("pathlib").Path(__file__).parent.parent.parent / "scripts" / "run_terragrunt.py"
    )
    spec = importlib.util.spec_from_file_location("__main__", str(terragrunt_source))
    new_mod = importlib.util.module_from_spec(spec)

    orig_argv = sys.argv[:]
    try:
        sys.argv = ["scripts.run_terragrunt", "validate"]

        def fake_run(argv, **kwargs) -> subprocess_mod.CompletedProcess:
            return subprocess_mod.CompletedProcess(argv, 0, stdout="", stderr="")

        with (
            patch("subprocess.run", side_effect=fake_run),
            pytest.raises(SystemExit) as exc_info,
        ):
            spec.loader.exec_module(new_mod)
    finally:
        sys.argv = orig_argv

    assert exc_info.value.code == 0, (
        f"__main__ block must exit 0 on success, got {exc_info.value.code!r}."
    )
