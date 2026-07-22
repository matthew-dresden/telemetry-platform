"""Unit tests for scripts.run_go -- pinned Go toolchain wrapper.

Tests assert that:
- AC-1: The module is importable and backs the five Makefile go-* targets
- AC-1: All subprocess calls go through scripts.binary_runner.invoke_pinned_binary (DRY)
- AC-1: GoCommandError raised on non-zero exit; missing binary emits correct ERROR: message
- AC-1: Parametrized over fmt/lint/vuln/unit-test-coverage/unit-test-coverage-json
- AC-1: run_go discovers every go.mod dir under providers/ and fails closed on zero discovery
- AC-1: unit-test-coverage requires --threshold N; missing threshold exits 2
- AC-5: 100 percent line coverage on scripts.run_go
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


def _import_run_go():
    """Import (or re-import) scripts.run_go."""
    import scripts.run_go as m

    return importlib.reload(m)


# A non-empty `go list ./...` stdout used by the coverage-subcommand fakes so
# _has_default_build_packages() sees at least one default-build package and the
# coverage subcommands proceed to `go test` (instead of skipping the module).
_FAKE_GO_LIST_STDOUT = "example.com/mymod\n"


def _fake_go_result(
    binary: str,
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    """Build a CompletedProcess for a faked invoke_pinned_binary call.

    Returns a non-empty package listing for `go list ./...` so the
    coverage-subcommand precheck (_has_default_build_packages) treats the module
    as having default-build packages; otherwise returns the caller-provided
    stdout/stderr/returncode for the real subcommand invocation.
    """
    if args[:1] == ["list"]:
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout=_FAKE_GO_LIST_STDOUT, stderr=""
        )
    return subprocess.CompletedProcess([binary, *args], returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# AC-1: module is importable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_module_is_importable() -> None:
    """scripts.run_go must be importable without ModuleNotFoundError."""
    mod = _import_run_go()
    assert hasattr(mod, "run_go_command"), "scripts.run_go must expose run_go_command function."
    assert hasattr(mod, "GoCommandError"), (
        "scripts.run_go must expose GoCommandError exception class."
    )
    assert hasattr(mod, "main"), "scripts.run_go must expose a main() entry point."


# ---------------------------------------------------------------------------
# AC-1: argv construction per subcommand
# ---------------------------------------------------------------------------

_SUBCOMMAND_ARGV_CASES = [
    # (subcommand, extra_args, expected_argv_fragments)
    ("fmt", [], ["fmt", "."]),
    # lint and vuln must carry `-tags terratest` so go vet / govulncheck keep
    # covering the build-tagged terratest test files (excluded from the default
    # build). The tag is asserted here to lock that contract.
    ("lint", [], ["vet", "-tags", "terratest", "./..."]),
    ("vuln", [], ["-tags", "terratest", "./..."]),
    ("unit-test-coverage-json", [], ["test", "-v", "-coverprofile"]),
]


@pytest.mark.unit
@pytest.mark.parametrize("subcommand,extra_args,expected_fragments", _SUBCOMMAND_ARGV_CASES)
def test_run_go_builds_correct_argv(
    subcommand: str,
    extra_args: list[str],
    expected_fragments: list[str],
    tmp_path: Path,
) -> None:
    """run_go_command must build the argv containing expected fragments for each subcommand."""
    mod = _import_run_go()

    # Create a fake go.mod so discovery finds one dir
    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return _fake_go_result(binary, args)

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand=subcommand,
            extra_args=extra_args,
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    # Exclude the `go list` precheck calls from the argv-fragment assertion so
    # the expected fragments reflect only the real subcommand invocation.
    captured_calls = [c for c in captured_calls if c[1:2] != ["list"]]
    assert len(captured_calls) >= 1, (
        f"Expected at least one invoke_pinned_binary call for subcommand={subcommand!r}, "
        f"got {len(captured_calls)}."
    )
    all_args = " ".join(str(a) for call in captured_calls for a in call)
    for fragment in expected_fragments:
        assert fragment in all_args, (
            f"Expected {fragment!r} in combined argv output {all_args!r} "
            f"for subcommand={subcommand!r}."
        )


@pytest.mark.unit
def test_run_go_unit_test_coverage_builds_correct_argv(tmp_path: Path) -> None:
    """unit-test-coverage subcommand must pass --threshold N to coverage check."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        return _fake_go_result(binary, args, stdout="ok coverage: 95%")

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand="unit-test-coverage",
            extra_args=["--threshold", "90"],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    # Exclude the `go list` precheck calls so the assertions below reflect only
    # the real `go test` invocation.
    captured_calls = [c for c in captured_calls if c[1:2] != ["list"]]
    all_args = " ".join(str(a) for call in captured_calls for a in call)
    assert "test" in all_args, (
        f"unit-test-coverage must invoke 'go test', got combined argv: {all_args!r}"
    )
    # unit-test-coverage must NOT carry the terratest build tag: the AWS-dependent
    # terratest tests (//go:build terratest) must never compile or run in the
    # unit-coverage build -- they run only via make tf-test.
    assert "terratest" not in all_args, (
        f"unit-test-coverage must NOT pass '-tags terratest' (would pull in the "
        f"AWS-dependent terratest tests); got combined argv: {all_args!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: all subprocess calls go through invoke_pinned_binary (DRY)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["fmt", "lint", "unit-test-coverage-json"])
def test_run_go_uses_invoke_pinned_binary(subcommand: str, tmp_path: Path) -> None:
    """run_go_command must delegate subprocess calls to invoke_pinned_binary using go_binary."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    captured_calls: list[dict] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append({"binary": binary, "args": args})
        return _fake_go_result(binary, args)

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand=subcommand,
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    assert len(captured_calls) >= 1, (
        f"Expected at least one invoke_pinned_binary call for subcommand={subcommand!r}, "
        f"got {len(captured_calls)}."
    )
    binaries = [c["binary"] for c in captured_calls]
    assert all(b == "go" for b in binaries), (
        f"All invoke_pinned_binary calls must use binary='go', got {binaries!r}."
    )


@pytest.mark.unit
def test_run_go_vuln_uses_govulncheck_binary(tmp_path: Path) -> None:
    """vuln subcommand must delegate to govulncheck_binary, not go_binary."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    captured_calls: list[dict] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append({"binary": binary, "args": args})
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand="vuln",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
            govulncheck_binary="govulncheck",
        )

    assert len(captured_calls) >= 1, (
        "Expected at least one invoke_pinned_binary call for subcommand='vuln'."
    )
    binaries = [c["binary"] for c in captured_calls]
    assert all(b == "govulncheck" for b in binaries), (
        f"vuln subcommand must use govulncheck_binary, got {binaries!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: GoCommandError raised on non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_run_go_raises_on_nonzero_exit(exit_code: int, tmp_path: Path) -> None:
    """A non-zero go binary exit must raise GoCommandError with actionable ERROR: message."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], exit_code, stdout="", stderr=f"go error rc={exit_code}"
        )

    with (
        patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke),
        pytest.raises(mod.GoCommandError) as exc_info,
    ):
        mod.run_go_command(
            subcommand="fmt",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    err = exc_info.value
    assert err.exit_code == exit_code, (
        f"GoCommandError.exit_code must be {exit_code}, got {err.exit_code!r}."
    )
    err_msg = str(err)
    assert err_msg.startswith("ERROR:"), (
        f"GoCommandError message must start with 'ERROR:', got: {err_msg!r}"
    )
    assert str(exit_code) in err_msg, (
        f"GoCommandError message must include exit code {exit_code}, got: {err_msg!r}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["fmt", "lint", "vuln", "unit-test-coverage-json"])
def test_run_go_does_not_raise_on_zero_exit(subcommand: str, tmp_path: Path) -> None:
    """A zero go binary exit must not raise any exception."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return _fake_go_result(binary, args)

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand=subcommand,
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )


# ---------------------------------------------------------------------------
# AC-1: fail-closed on zero go.mod discovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_fails_closed_on_zero_gomod_dirs(tmp_path: Path) -> None:
    """run_go must fail closed with GoCommandError exit 1 when zero go.mod dirs are found."""
    mod = _import_run_go()

    # providers dir exists but has no go.mod files
    providers_dir = tmp_path / "providers"
    providers_dir.mkdir(parents=True)

    with pytest.raises(mod.GoCommandError) as exc_info:
        mod.run_go_command(
            subcommand="fmt",
            extra_args=[],
            providers_root=str(providers_dir),
            go_binary="go",
        )

    err = exc_info.value
    err_msg = str(err)
    assert err_msg.startswith("ERROR:"), (
        f"Zero discovery error must start with 'ERROR:', got: {err_msg!r}"
    )
    assert "go.mod" in err_msg.lower() or "providers" in err_msg.lower(), (
        f"Zero discovery error must mention go.mod or providers, got: {err_msg!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: missing binary emits required ERROR: message
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_cli_exits_1_on_missing_binary(tmp_path: Path, capsys) -> None:
    """CLI must exit 1 with 'ERROR: required binary go not found; run make tools-ensure'."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: '{binary}'")

    fake_gomod_dirs = [str(tmp_path / "providers" / "aws" / "references" / "mymod")]
    with (
        patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke),
        patch("scripts.run_go._discover_gomod_dirs", return_value=fake_gomod_dirs),
        patch.object(sys, "argv", ["run_go", "fmt"]),
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
    assert "go" in captured.err, (
        f"Missing binary error must name the binary 'go', got: {captured.err!r}"
    )
    assert "tools-ensure" in captured.err, (
        f"Missing binary error must mention 'make tools-ensure', got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 2 on missing subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_cli_exits_2_when_no_subcommand() -> None:
    """CLI must exit with code 2 when invoked without a subcommand."""
    mod = _import_run_go()

    with (
        patch.object(sys, "argv", ["run_go"]),
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
def test_run_go_cli_exits_2_on_unknown_subcommand(capsys) -> None:
    """CLI must exit 2 with ERROR: on unknown subcommand."""
    mod = _import_run_go()

    with (
        patch.object(sys, "argv", ["run_go", "unknown-subcommand"]),
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
# AC-1: CLI exits 2 when unit-test-coverage missing --threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_cli_exits_2_when_threshold_missing(capsys) -> None:
    """CLI must exit 2 with ERROR: when unit-test-coverage is missing --threshold."""
    mod = _import_run_go()

    with (
        patch.object(sys, "argv", ["run_go", "unit-test-coverage"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 when --threshold missing, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 0 on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "subcommand,argv_suffix",
    [
        ("fmt", []),
        ("lint", []),
        ("vuln", []),
        ("unit-test-coverage-json", []),
    ],
)
def test_run_go_cli_exits_zero_on_success(
    subcommand: str, argv_suffix: list[str], tmp_path: Path
) -> None:
    """CLI main must exit 0 when the go binary succeeds."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return _fake_go_result(binary, args)

    argv = ["run_go", subcommand, *argv_suffix]
    with (
        patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke),
        patch("scripts.run_go._discover_gomod_dirs", return_value=[str(gomod_dir)]),
        patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 0, (
        f"CLI main must exit 0 on success for subcommand={subcommand!r}, "
        f"got {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_run_go_cli_unit_test_coverage_exits_zero_on_success(tmp_path: Path) -> None:
    """CLI main must exit 0 for unit-test-coverage --threshold N when go succeeds."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return _fake_go_result(binary, args, stdout="ok coverage: 95%")

    with (
        patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke),
        patch("scripts.run_go._discover_gomod_dirs", return_value=[str(gomod_dir)]),
        patch.object(sys, "argv", ["run_go", "unit-test-coverage", "--threshold", "90"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code == 0, (
        f"CLI must exit 0 for unit-test-coverage success, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exits 1 on GoCommandError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_cli_exits_nonzero_on_failure(tmp_path: Path) -> None:
    """CLI main must exit non-zero when the go binary fails."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 1, stdout="", stderr="go failed")

    with (
        patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke),
        patch("scripts.run_go._discover_gomod_dirs", return_value=[str(gomod_dir)]),
        patch.object(sys, "argv", ["run_go", "fmt"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI main must exit non-zero on go failure, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: binary path is configurable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_accepts_custom_binary_path(tmp_path: Path) -> None:
    """The go binary path must be configurable, not hard-coded inline."""
    mod = _import_run_go()
    custom_binary = "/usr/local/go/bin/go1.22"

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    captured_calls: list[str] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append(binary)
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand="fmt",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary=custom_binary,
        )

    assert all(b == custom_binary for b in captured_calls), (
        f"Expected all invoke_pinned_binary calls to use {custom_binary!r}, got {captured_calls!r}."
    )


# ---------------------------------------------------------------------------
# AC-1: stdout/stderr passthrough on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_passes_stdout_on_success(capsys, tmp_path: Path) -> None:
    """On success, run_go_command must pass binary stdout to sys.stdout."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="go fmt ok\n", stderr="")

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand="fmt",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    captured = capsys.readouterr()
    assert "go fmt ok" in captured.out, f"Expected stdout passthrough, got: {captured.out!r}"


@pytest.mark.unit
def test_run_go_passes_stderr_on_success(capsys, tmp_path: Path) -> None:
    """On success, run_go_command must pass binary stderr to sys.stderr."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout="", stderr="warning: go lint note\n"
        )

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand="lint",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    captured = capsys.readouterr()
    assert "warning" in captured.err, f"Expected stderr passthrough, got: {captured.err!r}"


# ---------------------------------------------------------------------------
# AC-1: iterates multiple go.mod dirs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_iterates_all_gomod_dirs(tmp_path: Path) -> None:
    """run_go must iterate all discovered go.mod dirs, not just the first."""
    mod = _import_run_go()

    for name in ["mod_a", "mod_b", "mod_c"]:
        d = tmp_path / "providers" / "aws" / "references" / name
        d.mkdir(parents=True)
        (d / "go.mod").write_text(f"module example.com/{name}\n\ngo 1.21\n")

    invoked_dirs: list[str] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        invoked_dirs.append(args[-1] if args else "")
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand="fmt",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    assert len(invoked_dirs) >= 3, (
        f"run_go must invoke the binary for each go.mod dir; "
        f"expected >=3 calls, got {len(invoked_dirs)}."
    )


# ---------------------------------------------------------------------------
# Coverage: ValueError raised on unsupported subcommand in library function
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_command_raises_value_error_on_unsupported_subcommand(tmp_path: Path) -> None:
    """run_go_command must raise ValueError when given an unsupported subcommand directly."""
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    with pytest.raises(ValueError, match="Unsupported subcommand"):
        mod.run_go_command(
            subcommand="totally-unknown",
            extra_args=[],
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )


# ---------------------------------------------------------------------------
# Coverage: __main__ block -- executed via importlib with __name__ == '__main__'
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_go_dunder_main_block(tmp_path: Path) -> None:
    """The if __name__ == '__main__' block must execute main() when run as module."""
    import importlib.util

    import scripts.binary_runner as br

    gomod_dir = tmp_path / "providers" / "aws" / "references" / "mymod"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/mymod\n\ngo 1.21\n")

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    orig_invoke = br.invoke_pinned_binary
    br.invoke_pinned_binary = fake_invoke
    try:
        source_path = str(REPO_ROOT / "scripts" / "run_go.py")
        spec = importlib.util.spec_from_file_location("__main__", source_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert module is not None

        with (
            patch.object(sys, "argv", ["run_go", "fmt"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            assert spec.loader is not None
            spec.loader.exec_module(module)
    finally:
        br.invoke_pinned_binary = orig_invoke

    assert exc_info.value.code == 0, (
        f"__main__ block must exit 0 on success, got {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# Coverage: _resolve_govulncheck_binary paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_govulncheck_binary_found_on_path(tmp_path: Path) -> None:
    """_resolve_govulncheck_binary must return 'govulncheck' when it is on PATH."""
    mod = _import_run_go()

    with patch("shutil.which", return_value="/usr/bin/govulncheck"):
        result = mod._resolve_govulncheck_binary(go_binary="go")

    assert result == "govulncheck", (
        f"Expected 'govulncheck' when binary is on PATH, got {result!r}."
    )


@pytest.mark.unit
def test_resolve_govulncheck_binary_falls_back_to_gopath(tmp_path: Path) -> None:
    """_resolve_govulncheck_binary must fall back to GOPATH/bin when not on PATH."""
    mod = _import_run_go()

    fake_gopath = str(tmp_path / "gopath")
    govulncheck_path = tmp_path / "gopath" / "bin" / "govulncheck"
    govulncheck_path.parent.mkdir(parents=True)
    govulncheck_path.write_text("#!/bin/sh\necho govulncheck")

    with (
        patch("shutil.which", return_value=None),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["go", "env", "GOPATH"], 0, stdout=fake_gopath + "\n", stderr=""
            ),
        ),
    ):
        result = mod._resolve_govulncheck_binary(go_binary="go")

    assert result == str(govulncheck_path), f"Expected GOPATH/bin path, got {result!r}."


@pytest.mark.unit
def test_resolve_govulncheck_binary_raises_when_go_env_fails() -> None:
    """_resolve_govulncheck_binary must raise GoCommandError when 'go env GOPATH' fails."""
    mod = _import_run_go()

    with (
        patch("shutil.which", return_value=None),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["go", "env", "GOPATH"], 1, stdout="", stderr="go env failed"
            ),
        ),
        pytest.raises(mod.GoCommandError) as exc_info,
    ):
        mod._resolve_govulncheck_binary(go_binary="go")

    assert exc_info.value.exit_code == 1, (
        f"GoCommandError exit_code must be 1 when go env fails, got {exc_info.value.exit_code!r}."
    )


@pytest.mark.unit
def test_resolve_govulncheck_binary_raises_when_not_in_gopath(tmp_path: Path) -> None:
    """_resolve_govulncheck_binary must raise GoCommandError when not found in GOPATH/bin."""
    mod = _import_run_go()

    fake_gopath = str(tmp_path / "gopath")
    # Do NOT create the govulncheck binary

    with (
        patch("shutil.which", return_value=None),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["go", "env", "GOPATH"], 0, stdout=fake_gopath + "\n", stderr=""
            ),
        ),
        pytest.raises(mod.GoCommandError) as exc_info,
    ):
        mod._resolve_govulncheck_binary(go_binary="go")

    assert exc_info.value.exit_code == 1, (
        f"GoCommandError exit_code must be 1 when govulncheck not in GOPATH/bin, "
        f"got {exc_info.value.exit_code!r}."
    )


# ---------------------------------------------------------------------------
# _has_default_build_packages: detect modules with no untagged packages
# (e.g. all sources are //go:build terratest live-integration tests)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_has_default_build_packages_true_when_go_list_lists_packages() -> None:
    """Returns True when `go list ./...` lists at least one package."""
    mod = _import_run_go()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        assert args[:2] == ["list", "./..."]
        return subprocess.CompletedProcess(
            [binary, *args], 0, stdout="example.com/mymod/tests\n", stderr=""
        )

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        assert mod._has_default_build_packages("go", "/some/module") is True


@pytest.mark.unit
def test_has_default_build_packages_false_when_go_list_empty_rc_zero() -> None:
    """Returns False when `go list ./...` exits 0 with empty stdout (no untagged pkgs)."""
    mod = _import_run_go()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        assert mod._has_default_build_packages("go", "/some/module") is False


@pytest.mark.unit
def test_has_default_build_packages_false_on_matched_no_packages_marker() -> None:
    """Returns False when stdout is empty and stderr carries the no-packages marker.

    Mirrors `go list ./...` emitting `go: warning: "./..." matched no packages`
    (sometimes with a non-zero exit) when all sources are build-tagged out.
    """
    mod = _import_run_go()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args],
            1,
            stdout="",
            stderr='go: warning: "./..." matched no packages\n',
        )

    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        assert mod._has_default_build_packages("go", "/some/module") is False


@pytest.mark.unit
def test_has_default_build_packages_raises_on_real_error() -> None:
    """Raises GoCommandError on a real failure (non-zero, empty stdout, no marker)."""
    mod = _import_run_go()

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        return subprocess.CompletedProcess(
            [binary, *args], 1, stdout="", stderr="go.mod: malformed module path\n"
        )

    with (
        patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke),
        pytest.raises(mod.GoCommandError) as exc_info,
    ):
        mod._has_default_build_packages("go", "/some/module")

    assert exc_info.value.exit_code == 1
    assert "malformed module path" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.parametrize("subcommand", ["unit-test-coverage", "unit-test-coverage-json"])
def test_coverage_skips_module_with_no_default_build_packages(
    subcommand: str, capsys, tmp_path: Path
) -> None:
    """Coverage subcommands SKIP modules whose only sources are build-tagged out.

    The `go list ./...` precheck returns empty stdout (no untagged packages), so
    `go test` must NOT be invoked for that module and no error is raised -- the
    terratest tests run only via make tf-test. This is the no-faking-coverage
    tolerance for all-terratest modules.
    """
    mod = _import_run_go()

    gomod_dir = tmp_path / "providers" / "aws" / "primitives" / "all-terratest"
    gomod_dir.mkdir(parents=True)
    (gomod_dir / "go.mod").write_text("module example.com/allterratest\n\ngo 1.26.4\n")

    captured_calls: list[list[str]] = []

    def fake_invoke(
        binary: str, args: list[str], capture_output: bool = False, cwd: str | None = None
    ):
        captured_calls.append([binary, *args])
        # go list -> empty (no untagged packages); never reached for `go test`.
        return subprocess.CompletedProcess([binary, *args], 0, stdout="", stderr="")

    extra = ["--threshold", "90"] if subcommand == "unit-test-coverage" else []
    with patch("scripts.run_go.invoke_pinned_binary", side_effect=fake_invoke):
        mod.run_go_command(
            subcommand=subcommand,
            extra_args=extra,
            providers_root=str(tmp_path / "providers"),
            go_binary="go",
        )

    # Only the `go list` precheck must have run; `go test` must be skipped.
    assert captured_calls == [["go", "list", "./..."]], (
        f"coverage subcommand must run only the `go list` precheck and skip "
        f"`go test` for an all-terratest module; got calls: {captured_calls!r}"
    )
    out = capsys.readouterr().out
    assert "SKIP:" in out, f"expected a SKIP: message for the skipped module; got: {out!r}"
    assert "terratest" in out, f"SKIP message must mention terratest; got: {out!r}"
