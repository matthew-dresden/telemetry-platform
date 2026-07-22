"""Unit tests for scripts.binary_runner -- shared pinned-binary subprocess helper.

Tests assert that:
- AC-1: invoke_pinned_binary builds the correct argv from binary + args
- AC-1: invoke_pinned_binary returns CompletedProcess with the binary's exit code
- AC-1: invoke_pinned_binary passes capture_output correctly to subprocess.run
- AC-1: invoke_pinned_binary propagates FileNotFoundError when binary is missing
- AC-CWD-001: invoke_pinned_binary accepts an optional cwd parameter
- AC-CWD-002: when cwd is None (default), subprocess.run receives cwd=None
- AC-CWD-003: when cwd is set, subprocess.run receives the exact directory value
- AC-CWD-004: FileNotFoundError and check=False contracts are preserved
- AC-ENV-001: when env is None (default), subprocess.run receives env=None
- AC-ENV-002: when env is set, subprocess.run receives the exact env dict
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


def _import_binary_runner():
    """Import (or re-import) scripts.binary_runner."""
    import scripts.binary_runner as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-1: argv construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_builds_correct_argv() -> None:
    """invoke_pinned_binary must prepend the binary name to the args list."""
    mod = _import_binary_runner()

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="opa", args=["check", "policies"])

    assert len(captured_calls) == 1, f"Expected 1 subprocess.run call, got {len(captured_calls)}."
    assert captured_calls[0] == ["opa", "check", "policies"], (
        f"Unexpected argv: {captured_calls[0]!r}"
    )


@pytest.mark.unit
def test_invoke_pinned_binary_empty_args() -> None:
    """invoke_pinned_binary with empty args must pass only the binary name."""
    mod = _import_binary_runner()

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="actionlint", args=[])

    assert captured_calls[0] == ["actionlint"], (
        f"Expected ['actionlint'], got {captured_calls[0]!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: return value carries exit code
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [0, 1, 2, 127])
def test_invoke_pinned_binary_returns_completed_process(exit_code: int) -> None:
    """invoke_pinned_binary must return the CompletedProcess from subprocess.run."""
    mod = _import_binary_runner()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, exit_code, stdout="out", stderr="err")

    with patch("subprocess.run", side_effect=fake_run):
        result = mod.invoke_pinned_binary(binary="opa", args=["check", "policies"])

    assert result.returncode == exit_code, (
        f"Expected returncode {exit_code}, got {result.returncode}"
    )


# ---------------------------------------------------------------------------
# AC-1: capture_output flag is forwarded
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("capture", [True, False])
def test_invoke_pinned_binary_forwards_capture_output(capture: bool) -> None:
    """invoke_pinned_binary must forward capture_output to subprocess.run."""
    mod = _import_binary_runner()

    received_kwargs: dict = {}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="opa", args=[], capture_output=capture)

    assert received_kwargs.get("capture_output") == capture, (
        f"Expected capture_output={capture}, got {received_kwargs.get('capture_output')!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: text mode is always enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_uses_text_mode() -> None:
    """invoke_pinned_binary must always run subprocess.run in text mode."""
    mod = _import_binary_runner()

    received_kwargs: dict = {}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="opa", args=["fmt"])

    assert received_kwargs.get("text") is True, (
        f"Expected text=True, got {received_kwargs.get('text')!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: FileNotFoundError propagates when binary is missing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_propagates_file_not_found() -> None:
    """invoke_pinned_binary must propagate FileNotFoundError when binary is absent."""
    mod = _import_binary_runner()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        raise FileNotFoundError(f"No such file: {argv[0]}")

    with (
        patch("subprocess.run", side_effect=fake_run),
        pytest.raises(FileNotFoundError),
    ):
        mod.invoke_pinned_binary(binary="/nonexistent/binary", args=[])


# ---------------------------------------------------------------------------
# AC-CWD-002: default cwd=None is forwarded to subprocess.run
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_default_cwd_is_none() -> None:
    """When cwd is omitted, subprocess.run must receive cwd=None (AC-CWD-002)."""
    mod = _import_binary_runner()

    received_kwargs: dict = {}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="opa", args=["check"])

    assert "cwd" in received_kwargs, "subprocess.run was not called with a cwd kwarg"
    assert received_kwargs["cwd"] is None, f"Expected cwd=None, got {received_kwargs['cwd']!r}"


# ---------------------------------------------------------------------------
# AC-CWD-003: explicit cwd is forwarded verbatim to subprocess.run
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_forwards_cwd_to_subprocess(tmp_path: Path) -> None:
    """When cwd=<dir> is passed, subprocess.run must receive cwd=<dir> (AC-CWD-003).

    This mirrors how run_go.py calls invoke_pinned_binary(..., cwd=module_dir):
    if the parameter were absent, invoke_pinned_binary would raise
    TypeError: unexpected keyword argument 'cwd'.
    """
    mod = _import_binary_runner()

    received_kwargs: dict = {}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="go", args=["vet", "./..."], cwd=str(tmp_path))

    assert received_kwargs.get("cwd") == str(tmp_path), (
        f"Expected cwd={str(tmp_path)!r}, got {received_kwargs.get('cwd')!r}"
    )


# ---------------------------------------------------------------------------
# AC-CWD-004: check=False contract is preserved alongside cwd
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_check_false_with_cwd(tmp_path: Path) -> None:
    """subprocess.run must always be called with check=False even when cwd is set (AC-CWD-004)."""
    mod = _import_binary_runner()

    received_kwargs: dict = {}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = mod.invoke_pinned_binary(binary="go", args=["lint"], cwd=str(tmp_path))

    assert received_kwargs.get("check") is False, (
        f"Expected check=False, got {received_kwargs.get('check')!r}"
    )
    assert result.returncode == 1, (
        f"Expected returncode=1 (verbatim propagation), got {result.returncode}"
    )


# ---------------------------------------------------------------------------
# AC-ENV-001: default env=None is forwarded to subprocess.run
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_default_env_is_none() -> None:
    """When env is omitted, subprocess.run must receive env=None (AC-ENV-001)."""
    mod = _import_binary_runner()

    received_kwargs: dict = {}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="go", args=["version"])

    assert "env" in received_kwargs, "subprocess.run was not called with an env kwarg"
    assert received_kwargs["env"] is None, f"Expected env=None, got {received_kwargs['env']!r}"


# ---------------------------------------------------------------------------
# AC-ENV-002: explicit env dict is forwarded verbatim to subprocess.run
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_pinned_binary_forwards_env_to_subprocess() -> None:
    """When env=<dict> is passed, subprocess.run must receive env=<dict> (AC-ENV-002)."""
    mod = _import_binary_runner()

    received_kwargs: dict = {}
    custom_env = {"TERRATEST_RUN_ID": "tt-test-run-id", "TF_PLUGIN_CACHE_DIR": "/tmp/cache"}

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        received_kwargs.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.invoke_pinned_binary(binary="go", args=["test", "./..."], env=custom_env)

    assert received_kwargs.get("env") == custom_env, (
        f"Expected env={custom_env!r}, got {received_kwargs.get('env')!r}"
    )
