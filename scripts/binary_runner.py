"""Shared helper for invoking pinned binaries via subprocess.

This module provides the single, authoritative subprocess wrapper consumed by
all quality-gate wrappers (run_opa.py, run_actionlint.py, etc.) so that the
subprocess boilerplate remains DRY and consistent across wrappers.

Architecture:
- invoke_pinned_binary(): one responsibility -- build argv and call subprocess.run.
"""

from __future__ import annotations

import subprocess


def invoke_pinned_binary(
    binary: str,
    args: list[str],
    capture_output: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke a pinned binary with the given arguments.

    Args:
        binary: Path or name of the pinned binary to invoke.
        args: Arguments to pass after the binary name.
        capture_output: If True, capture stdout/stderr; otherwise inherit.
        cwd: Working directory for the subprocess.  When None (the default)
            the process inherits the caller's working directory, preserving
            the pre-change contract.
        env: Optional environment dictionary for the subprocess.  When None
            (the default) the subprocess inherits the caller's environment.

    Returns:
        CompletedProcess result from the subprocess call.

    Raises:
        FileNotFoundError: If the binary cannot be found on PATH.
    """
    argv = [binary, *args]
    return subprocess.run(
        argv,
        capture_output=capture_output,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
    )
