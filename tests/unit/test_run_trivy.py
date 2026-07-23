"""Unit tests for scripts.run_trivy -- pinned Trivy security scanner wrapper.

Tests assert that:
- AC-1: The module is importable and backs the tf-security and tg-security Makefile targets
- AC-1: All subprocess calls go through scripts.binary_runner.invoke_pinned_binary (DRY)
- AC-1: TrivyCommandError raised on non-zero exit; missing binary emits correct ERROR: message
- AC-1: Passes 'config --exit-code 1 --ignorefile .trivyignore <path>'
  (trivy 0.71.0 'config' is the misconfig scanner and rejects the '--scanners' flag)
- AC-1: CLI exits 2 on missing subcommand, 1 on missing binary, propagates wrapped exit code
- AC-5: 100 percent line coverage on scripts.run_trivy
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from unittest.mock import patch

import pytest


def _import_run_trivy():
    """Import (or re-import) scripts.run_trivy."""
    import scripts.run_trivy as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-1: module is importable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_module_is_importable() -> None:
    """scripts.run_trivy must be importable without ModuleNotFoundError."""
    mod = _import_run_trivy()
    assert hasattr(mod, "run_trivy_command"), (
        "scripts.run_trivy must expose run_trivy_command function."
    )
    assert hasattr(mod, "TrivyCommandError"), (
        "scripts.run_trivy must expose TrivyCommandError exception class."
    )
    assert hasattr(mod, "main"), "scripts.run_trivy must expose a main() entry point."


# ---------------------------------------------------------------------------
# AC-1: correct argv for 'config' subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "scan_path,expected_fragments",
    [
        (
            "providers/aws/references/my-module",
            [
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "providers/aws/references/my-module",
            ],
        ),
        (
            "terragrunt/",
            [
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "terragrunt/",
            ],
        ),
    ],
)
def test_run_trivy_builds_correct_argv(scan_path: str, expected_fragments: list[str]) -> None:
    """run_trivy_command must build the correct trivy config argv."""
    mod = _import_run_trivy()

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        captured_calls.append([binary, *args])
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                scan_path,
            ],
            trivy_binary="trivy",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one invoke_pinned_binary call, got {len(captured_calls)}."
    )
    actual_argv = captured_calls[0]
    assert actual_argv[0] == "trivy", f"Expected binary 'trivy' as argv[0], got {actual_argv[0]!r}."
    for fragment in expected_fragments:
        assert fragment in actual_argv, f"Expected {fragment!r} in argv {actual_argv!r}."
    # trivy 0.71.0 'config' is the misconfiguration scanner and rejects '--scanners';
    # the canonical invocation must never reintroduce that flag (regression guard).
    assert "--scanners" not in actual_argv, (
        f"trivy 'config' must not be passed '--scanners' (unknown flag in 0.71.0), "
        f"got argv {actual_argv!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: all subprocess calls go through invoke_pinned_binary (DRY)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_uses_invoke_pinned_binary() -> None:
    """run_trivy_command must delegate all subprocess calls to invoke_pinned_binary."""
    mod = _import_run_trivy()

    captured_calls: list[dict] = []

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        captured_calls.append({"binary": binary, "args": args})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "some/path",
            ],
            trivy_binary="trivy",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one invoke_pinned_binary call, got {len(captured_calls)}."
    )
    assert captured_calls[0]["binary"] == "trivy", (
        f"invoke_pinned_binary must be called with binary='trivy', "
        f"got {captured_calls[0]['binary']!r}."
    )


@pytest.mark.unit
def test_run_trivy_exports_offline_terratest_run_id() -> None:
    """run_trivy_command must export a static TF_VAR_terratest_run_id placeholder so the
    run-id-scoped fixture names resolve during the offline scan (no false CloudFront/S3
    access-logging findings)."""
    import scripts.constants as constants

    mod = _import_run_trivy()

    captured_env: list[dict] = []

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        captured_env.append(env or {})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(args=["config", "some/path"], trivy_binary="trivy")

    assert len(captured_env) == 1
    assert (
        captured_env[0].get("TF_VAR_terratest_run_id") == constants.TERRATEST_OFFLINE_SCAN_RUN_ID
    ), (
        "run_trivy must export TF_VAR_terratest_run_id=<offline placeholder> so trivy resolves "
        "the run-id-scoped fixture names (otherwise AWS-0010/AWS-0089 false-fire)."
    )


@pytest.mark.unit
def test_run_trivy_preserves_caller_terratest_run_id() -> None:
    """If TF_VAR_terratest_run_id is already set by the caller, run_trivy must NOT clobber it
    (setdefault semantics)."""
    mod = _import_run_trivy()

    captured_env: list[dict] = []

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        captured_env.append(env or {})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with (
        patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke),
        patch.dict(os.environ, {"TF_VAR_terratest_run_id": "caller-supplied-value"}),
    ):
        mod.run_trivy_command(args=["config", "some/path"], trivy_binary="trivy")

    assert captured_env[0].get("TF_VAR_terratest_run_id") == "caller-supplied-value"


# ---------------------------------------------------------------------------
# AC-1: TrivyCommandError raised on non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_run_trivy_raises_on_nonzero_exit(exit_code: int) -> None:
    """A non-zero trivy exit must raise TrivyCommandError with actionable ERROR: message."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess(
            [binary, *args], exit_code, stdout="", stderr=f"trivy error rc={exit_code}"
        )

    with (
        patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke),
        pytest.raises(mod.TrivyCommandError) as exc_info,
    ):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "some/path",
            ],
            trivy_binary="trivy",
        )

    err = exc_info.value
    assert err.exit_code == exit_code, (
        f"TrivyCommandError.exit_code must be {exit_code}, got {err.exit_code!r}."
    )
    err_msg = str(err)
    assert err_msg.startswith("ERROR:"), (
        f"TrivyCommandError message must start with 'ERROR:', got: {err_msg!r}"
    )
    assert str(exit_code) in err_msg, (
        f"TrivyCommandError message must include exit code, got: {err_msg!r}"
    )


@pytest.mark.unit
def test_run_trivy_does_not_raise_on_zero_exit() -> None:
    """A zero trivy exit must not raise any exception."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "some/path",
            ],
            trivy_binary="trivy",
        )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 2 on missing subcommand/args
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_cli_exits_2_when_no_args() -> None:
    """CLI must exit 2 when invoked without arguments."""
    mod = _import_run_trivy()

    with (
        patch.object(sys, "argv", ["run_trivy"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 when no args given, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 1 on missing binary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_cli_exits_1_on_missing_binary(capsys) -> None:
    """CLI must exit 1 with 'ERROR: required binary trivy not found; run make tools-ensure'."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{binary}'")

    with (
        patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(
            sys,
            "argv",
            [
                "run_trivy",
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                ".",
            ],
        ),
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
    assert "trivy" in captured.err, (
        f"Missing binary error must name the binary 'trivy', got: {captured.err!r}"
    )
    assert "tools-ensure" in captured.err, (
        f"Missing binary error must mention 'make tools-ensure', got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 0 on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_cli_exits_zero_on_success() -> None:
    """CLI main must exit 0 when trivy succeeds."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with (
        patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(
            sys,
            "argv",
            [
                "run_trivy",
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                ".",
            ],
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 0, f"CLI must exit 0 on success, got {exc_info.value.code!r}."


# ---------------------------------------------------------------------------
# AC-1: CLI exits non-zero on TrivyCommandError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_cli_exits_nonzero_on_failure() -> None:
    """CLI main must exit non-zero when trivy fails."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess([binary, *args], 1, stdout="", stderr="trivy finding")

    with (
        patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke),
        patch.object(
            sys,
            "argv",
            [
                "run_trivy",
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                ".",
            ],
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero on trivy failure, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: binary path is configurable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_accepts_custom_binary_path() -> None:
    """The trivy binary path must be configurable, not hard-coded inline."""
    mod = _import_run_trivy()
    custom_binary = "/usr/local/bin/trivy-0.57.0"

    captured_calls: list[str] = []

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        captured_calls.append(binary)
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "some/path",
            ],
            trivy_binary=custom_binary,
        )

    assert captured_calls[0] == custom_binary, (
        f"Expected binary {custom_binary!r}, got {captured_calls[0]!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: stdout/stderr passthrough on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_passes_stdout_on_success(capsys) -> None:
    """On success, run_trivy_command must pass binary stdout to sys.stdout."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="trivy ok\n", stderr="")

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "some/path",
            ],
            trivy_binary="trivy",
        )

    captured = capsys.readouterr()
    assert "trivy ok" in captured.out, f"Expected stdout passthrough, got: {captured.out!r}"


@pytest.mark.unit
def test_run_trivy_passes_stderr_on_success(capsys) -> None:
    """On success, run_trivy_command must pass binary stderr to sys.stderr."""
    mod = _import_run_trivy()

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout="", stderr="warning: trivy note\n"
        )

    with patch("scripts.run_trivy.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_trivy_command(
            args=[
                "config",
                "--exit-code",
                "1",
                "--ignorefile",
                ".trivyignore",
                "some/path",
            ],
            trivy_binary="trivy",
        )

    captured = capsys.readouterr()
    assert "warning" in captured.err, f"Expected stderr passthrough, got: {captured.err!r}"


# ---------------------------------------------------------------------------
# Coverage: __main__ block -- executed via importlib with __name__ == '__main__'
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_trivy_dunder_main_block() -> None:
    """The if __name__ == '__main__' block must execute main() when run as module."""
    import importlib.util
    from pathlib import Path

    import scripts.binary_runner as br

    def fake_invoke(
        binary: str,
        args: list[str],
        capture_output: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    orig_invoke = br.invoke_pinned_binary
    br.invoke_pinned_binary = fake_invoke
    try:
        repo_root = Path(__file__).parent.parent.parent
        source_path = str(repo_root / "scripts" / "run_trivy.py")
        spec = importlib.util.spec_from_file_location("__main__", source_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert module is not None

        with (
            patch.object(
                sys,
                "argv",
                [
                    "run_trivy",
                    "config",
                    "--exit-code",
                    "1",
                    "--ignorefile",
                    ".trivyignore",
                    ".",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            assert spec.loader is not None
            spec.loader.exec_module(module)
    finally:
        br.invoke_pinned_binary = orig_invoke

    assert exc_info.value.code == 0, (
        f"__main__ block must exit 0 on success, got {exc_info.value.code!r}."
    )
