"""Unit tests for scripts/tg_output.py.

Implements the docs/release-pipeline.md per-guard pytest matrix for tg_output.
Tests assert:
  - value-present pass (writes <name>=<value> to the OUTPUT file)
  - no-value failure (exit non-zero with a specific exception)
"""

from __future__ import annotations

import pathlib

import pytest

from scripts.tg_output import (
    EmptyOutputValueError,
    parse_raw_output,
    write_output_pair,
)

# ---------------------------------------------------------------------------
# parse_raw_output tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_value,description",
    [
        ("arn:aws:s3:::my-bucket", "S3 ARN"),
        ("us-east-1", "region string"),
        ("https://example.com/callback", "callback URL"),
        ("123456789012", "account id"),
    ],
)
def test_parse_raw_output_returns_value_when_present(raw_value: str, description: str) -> None:
    """parse_raw_output returns the stripped value when non-empty."""
    result = parse_raw_output(
        name="my_output",
        raw_value=raw_value,
    )
    assert result == raw_value.strip()


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_value,description",
    [
        ("", "empty string"),
        ("   ", "whitespace only"),
    ],
)
def test_parse_raw_output_raises_for_empty_value(raw_value: str, description: str) -> None:
    """parse_raw_output raises EmptyOutputValueError when the value is empty."""
    with pytest.raises(EmptyOutputValueError, match="my_output"):
        parse_raw_output(
            name="my_output",
            raw_value=raw_value,
        )


# ---------------------------------------------------------------------------
# write_output_pair tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_write_output_pair_writes_name_equals_value(tmp_path: pathlib.Path) -> None:
    """write_output_pair writes '<name>=<value>' to the output file."""
    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")

    write_output_pair(
        output_path=str(output_file),
        name="bucket_arn",
        value="arn:aws:s3:::my-bucket",
    )

    content = output_file.read_text(encoding="utf-8")
    assert "bucket_arn=arn:aws:s3:::my-bucket" in content


@pytest.mark.unit
def test_write_output_pair_appends_to_existing_file(tmp_path: pathlib.Path) -> None:
    """write_output_pair appends to an existing output file (does not overwrite)."""
    output_file = tmp_path / "github_output"
    output_file.write_text("existing_key=existing_value\n", encoding="utf-8")

    write_output_pair(
        output_path=str(output_file),
        name="new_key",
        value="new_value",
    )

    content = output_file.read_text(encoding="utf-8")
    assert "existing_key=existing_value" in content
    assert "new_key=new_value" in content


@pytest.mark.unit
def test_write_output_pair_creates_file_if_not_exists(tmp_path: pathlib.Path) -> None:
    """write_output_pair creates the output file if it does not exist."""
    output_file = tmp_path / "new_output_file"
    assert not output_file.exists()

    write_output_pair(
        output_path=str(output_file),
        name="my_key",
        value="my_value",
    )

    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "my_key=my_value" in content


# ---------------------------------------------------------------------------
# main() entry point tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_0_for_valid_output(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 0 when terragrunt output returns a valid value."""
    import sys

    import scripts.tg_output as mod

    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")

    def mock_fetch(unit_dir: str, output_name: str) -> str:
        return "arn:aws:s3:::my-bucket\n"

    monkeypatch.setattr(mod, "fetch_terragrunt_output", mock_fetch)
    original_argv = sys.argv
    try:
        sys.argv = [
            "tg_output",
            "--unit",
            str(tmp_path),
            "--name",
            "bucket_arn",
            "--output",
            str(output_file),
        ]
        result = mod.main()
    finally:
        sys.argv = original_argv
    assert result == 0
    content = output_file.read_text(encoding="utf-8")
    assert "bucket_arn=arn:aws:s3:::my-bucket" in content


@pytest.mark.unit
def test_main_returns_1_for_empty_output(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when terragrunt output returns an empty value."""
    import sys

    import scripts.tg_output as mod

    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")

    def mock_fetch(unit_dir: str, output_name: str) -> str:
        return ""

    monkeypatch.setattr(mod, "fetch_terragrunt_output", mock_fetch)
    original_argv = sys.argv
    try:
        sys.argv = [
            "tg_output",
            "--unit",
            str(tmp_path),
            "--name",
            "bucket_arn",
            "--output",
            str(output_file),
        ]
        result = mod.main()
    finally:
        sys.argv = original_argv
    assert result == 1


@pytest.mark.unit
def test_main_returns_1_when_fetch_raises(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when the terragrunt subprocess fails."""
    import sys

    import scripts.tg_output as mod

    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")

    def mock_fetch(unit_dir: str, output_name: str) -> str:
        raise RuntimeError("terragrunt command failed")

    monkeypatch.setattr(mod, "fetch_terragrunt_output", mock_fetch)
    original_argv = sys.argv
    try:
        sys.argv = [
            "tg_output",
            "--unit",
            str(tmp_path),
            "--name",
            "bucket_arn",
            "--output",
            str(output_file),
        ]
        result = mod.main()
    finally:
        sys.argv = original_argv
    assert result == 1


@pytest.mark.unit
def test_fetch_terragrunt_output_raises_on_subprocess_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch_terragrunt_output raises RuntimeError when the subprocess exits non-zero.

    The non-zero subprocess result is injected by stubbing subprocess.run, so the
    test exercises the real returncode-check branch in fetch_terragrunt_output
    deterministically -- it does not depend on a real terragrunt binary being
    installed or on how that binary behaves in a config-less directory (ambient
    state that differs between developer machines and CI's clean environment).
    """
    import subprocess

    from scripts.tg_output import fetch_terragrunt_output

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["terragrunt", "output", "-raw", "nonexistent_output"],
            returncode=1,
            stdout="",
            stderr="terragrunt: no value for output 'nonexistent_output'",
        )

    monkeypatch.setattr("scripts.tg_output.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="failed"):
        fetch_terragrunt_output(unit_dir=str(tmp_path), output_name="nonexistent_output")
