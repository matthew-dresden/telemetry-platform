"""Tests for the scoped bandit baseline configuration.

AC-BANDIT-SCOPE-001: Verifies the baseline covers only B404/B603 in scripts/ensure_tools.py.
AC-BANDIT-SCOPE-002: Asserts that a new script with bare subprocess usage still triggers
B404/B603 when run without the baseline (proving the scanner stays live for new code).
AC-BANDIT-SCOPE-003: Asserts that [tool.bandit].skips in pyproject.toml is empty
(no global suppression of B404 or B603).
AC-BANDIT-SCOPE-004: The baseline does not suppress HIGH or MEDIUM findings.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.mark.unit
def test_pyproject_toml_bandit_skips_is_empty() -> None:
    """AC-BANDIT-SCOPE-003: [tool.bandit].skips must remain empty in pyproject.toml.

    No global suppression of B404 or B603 is allowed. The scoped baseline file
    is the only mechanism for accepting known-good subprocess findings.
    """
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found at {pyproject_path}"

    with pyproject_path.open("rb") as f:
        config = tomllib.load(f)

    bandit_config = config.get("tool", {}).get("bandit", {})
    skips = bandit_config.get("skips", [])

    assert skips == [], (
        f"[tool.bandit].skips must be empty to prevent global security suppression. "
        f"Found: {skips!r}. Use the .bandit-baseline.json file for scoped suppression."
    )


@pytest.mark.unit
def test_new_bare_subprocess_script_triggers_b404_b603() -> None:
    """AC-BANDIT-SCOPE-002: Bandit without the baseline still catches B404/B603 in new scripts.

    Creates a temporary script with bare subprocess usage and asserts bandit exits
    non-zero, proving the scanner remains live for new code not covered by the baseline.
    """
    bare_subprocess_code = textwrap.dedent(
        """\
        import subprocess
        subprocess.run(["ls"])
        """
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_script = Path(tmpdir) / "bare_subprocess_script.py"
        tmp_script.write_text(bare_subprocess_code, encoding="utf-8")

        result = subprocess.run(
            ["uv", "run", "bandit", str(tmp_script)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

    # bandit exits 1 when it finds issues
    assert result.returncode != 0, (
        "bandit should exit non-zero when it finds B404/B603 in a bare subprocess "
        f"script. Exit code: {result.returncode}.\nStdout: {result.stdout}\n"
        f"Stderr: {result.stderr}"
    )

    combined_output = result.stdout + result.stderr
    assert "B404" in combined_output or "B603" in combined_output, (
        "bandit output should contain B404 or B603 for a script that imports and "
        f"calls subprocess. Output: {combined_output}"
    )


@pytest.mark.unit
def test_bandit_baseline_file_exists() -> None:
    """The .bandit-baseline.json file must exist at the repo root."""
    baseline_path = REPO_ROOT / ".bandit-baseline.json"
    assert baseline_path.exists(), (
        f".bandit-baseline.json not found at {baseline_path}. "
        "This file must be generated and committed to scope bandit findings "
        "for scripts that legitimately use subprocess with list-based commands."
    )


@pytest.mark.unit
def test_bandit_baseline_is_valid_json() -> None:
    """The .bandit-baseline.json file must be valid JSON with a 'results' key."""
    baseline_path = REPO_ROOT / ".bandit-baseline.json"
    assert baseline_path.exists(), f".bandit-baseline.json not found at {baseline_path}"

    with baseline_path.open(encoding="utf-8") as f:
        baseline = json.load(f)

    assert "results" in baseline, (
        f".bandit-baseline.json must contain a 'results' key. Found keys: {list(baseline.keys())}"
    )


@pytest.mark.unit
def test_bandit_baseline_scoped_to_ensure_tools_only() -> None:
    """AC-BANDIT-SCOPE-001: The baseline must only contain B404/B603 findings for ensure_tools.py.

    The task scope restricts the baseline to the two false-positive finding types in
    scripts/ensure_tools.py (B404: import subprocess, B603: subprocess without shell=True).
    No other scripts or finding types must appear in the baseline.
    """
    baseline_path = REPO_ROOT / ".bandit-baseline.json"
    assert baseline_path.exists(), f".bandit-baseline.json not found at {baseline_path}"

    with baseline_path.open(encoding="utf-8") as f:
        baseline = json.load(f)

    results = baseline.get("results", [])

    # All results must be from ensure_tools.py
    wrong_file_findings = [r for r in results if "ensure_tools.py" not in r.get("filename", "")]
    assert wrong_file_findings == [], (
        "The baseline must only contain findings for scripts/ensure_tools.py. "
        f"Found findings from other files: "
        f"{[(r['filename'], r['test_id']) for r in wrong_file_findings]}"
    )

    # All results must be B404 or B603 only
    wrong_test_findings = [r for r in results if r.get("test_id") not in ("B404", "B603")]
    assert wrong_test_findings == [], (
        "The baseline must only suppress B404 (import subprocess) and B603 "
        "(subprocess without shell=True) -- no other finding types. "
        f"Found unauthorized finding types: "
        f"{[(r['test_id'], r.get('filename')) for r in wrong_test_findings]}"
    )


@pytest.mark.unit
def test_bandit_baseline_does_not_suppress_high_medium() -> None:
    """The .bandit-baseline.json must not contain any HIGH or MEDIUM severity findings.

    AC-BANDIT-SCOPE-004: The baseline only covers known LOW-severity false positives.
    Any HIGH or MEDIUM finding must still cause bandit to exit non-zero.
    """
    baseline_path = REPO_ROOT / ".bandit-baseline.json"
    assert baseline_path.exists(), f".bandit-baseline.json not found at {baseline_path}"

    with baseline_path.open(encoding="utf-8") as f:
        baseline = json.load(f)

    high_medium_findings = [
        r
        for r in baseline.get("results", [])
        if r.get("issue_severity", "").upper() in ("HIGH", "MEDIUM")
    ]

    assert high_medium_findings == [], (
        "The baseline file must not suppress HIGH or MEDIUM severity findings. "
        f"Found suppressed HIGH/MEDIUM findings: {high_medium_findings}"
    )


@pytest.mark.unit
def test_make_py_security_passes_with_baseline() -> None:
    """AC-BANDIT-SCOPE-001: make py-security exits 0 with the scoped baseline.

    Runs make py-security in the repo root and asserts exit code 0, proving
    the Makefile target uses the baseline and ensure_tools.py is on the scan path.
    """
    env = os.environ.copy()
    # Unset VIRTUAL_ENV to avoid uv warning about venv mismatch
    env.pop("VIRTUAL_ENV", None)

    result = subprocess.run(
        ["make", "py-security"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )

    assert result.returncode == 0, (
        f"make py-security should exit 0 with the scoped baseline. "
        f"Exit code: {result.returncode}.\n"
        f"Stdout: {result.stdout}\nStderr: {result.stderr}"
    )
