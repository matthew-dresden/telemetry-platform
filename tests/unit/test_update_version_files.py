"""Unit tests for scripts.update_version_files.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases: version written into module/config version files;
missing target file raises and exits non-zero.

AC-15:
- derived version is written into the module version files
- missing target file raises ValueError and exits non-zero
"""

from __future__ import annotations

import pytest

from scripts.update_version_files import update_version_files

# ---------------------------------------------------------------------------
# Version file write cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_update_version_files_module_scope_writes_version(tmp_path) -> None:
    """update_version_files writes the version into <module_path>/VERSION for module scope.

    AC-15: the VERSION file for the module must contain the new version.
    """
    module_path = "providers/aws/primitives/kms-key"
    version_file = tmp_path / module_path / "VERSION"
    version_file.parent.mkdir(parents=True)
    version_file.write_text("0.0.0\n")

    update_version_files(
        scope="module",
        module_path=module_path,
        version="0.1.0",
        repo_root=str(tmp_path),
    )

    content = version_file.read_text().strip()
    assert content == "0.1.0", (
        f"VERSION file must contain '0.1.0' after update, got {content!r}. "
        "update_version_files must write the new version into <module_path>/VERSION."
    )


@pytest.mark.unit
def test_update_version_files_config_scope_writes_root_version(tmp_path) -> None:
    """update_version_files writes the version into the repo-root VERSION for config scope.

    AC-15: for scope=config, the repo-root VERSION file must be updated.
    """
    root_version_file = tmp_path / "VERSION"
    root_version_file.write_text("0.0.0\n")

    update_version_files(
        scope="config",
        module_path="",
        version="1.0.0",
        repo_root=str(tmp_path),
    )

    content = root_version_file.read_text().strip()
    assert content == "1.0.0", (
        f"Root VERSION file must contain '1.0.0' after config-scope update, got {content!r}. "
        "update_version_files must write the version into the repo-root VERSION for config scope."
    )


@pytest.mark.unit
def test_update_version_files_missing_version_file_raises(tmp_path) -> None:
    """update_version_files must raise FileNotFoundError when the VERSION file is missing.

    Fail-fast: a missing VERSION file is a hard error, not a recoverable condition.
    """
    module_path = "providers/aws/primitives/kms-key"
    # Do NOT create the VERSION file

    with pytest.raises(FileNotFoundError) as exc_info:
        update_version_files(
            scope="module",
            module_path=module_path,
            version="0.1.0",
            repo_root=str(tmp_path),
        )

    msg = str(exc_info.value)
    assert "VERSION" in msg or "version" in msg.lower() or module_path in msg, (
        f"FileNotFoundError must mention the missing VERSION file or module path. Got: {msg!r}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "scope,module_path,version",
    [
        pytest.param("module", "providers/aws/primitives/s3-bucket", "1.2.3", id="module-s3"),
        pytest.param("module", "providers/aws/references/vpc-network", "0.5.0", id="module-ref"),
        pytest.param("config", "", "2.0.0", id="config-major"),
    ],
)
def test_update_version_files_parametrized_writes_correct_version(
    tmp_path, scope: str, module_path: str, version: str
) -> None:
    """update_version_files writes the correct version for various scope/module combinations."""
    if scope == "module":
        version_file = tmp_path / module_path / "VERSION"
        version_file.parent.mkdir(parents=True)
        version_file.write_text("0.0.0\n")
    else:
        version_file = tmp_path / "VERSION"
        version_file.write_text("0.0.0\n")

    update_version_files(
        scope=scope,
        module_path=module_path,
        version=version,
        repo_root=str(tmp_path),
    )

    content = version_file.read_text().strip()
    assert content == version, (
        f"VERSION file must contain {version!r} after update (scope={scope!r}, "
        f"module_path={module_path!r}), got {content!r}."
    )


# ---------------------------------------------------------------------------
# main() direct call coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_update_version_files_main_module_scope(tmp_path) -> None:
    """main() updates the VERSION file for module scope."""
    import io
    from unittest.mock import patch

    from scripts.update_version_files import main

    module_path = "providers/aws/primitives/kms-key"
    version_file = tmp_path / module_path / "VERSION"
    version_file.parent.mkdir(parents=True)
    version_file.write_text("0.0.0\n")

    captured = io.StringIO()

    with (
        patch(
            "sys.argv",
            [
                "update_version_files",
                "--scope",
                "module",
                "--module-path",
                module_path,
                "--version",
                "0.1.0",
                "--repo-root",
                str(tmp_path),
            ],
        ),
        patch("sys.stdout", captured),
    ):
        main()

    assert version_file.read_text().strip() == "0.1.0", (
        "main() must update the VERSION file for module scope."
    )


@pytest.mark.unit
def test_update_version_files_main_config_scope(tmp_path) -> None:
    """main() updates the root VERSION file for config scope."""
    import io
    from unittest.mock import patch

    from scripts.update_version_files import main

    root_version = tmp_path / "VERSION"
    root_version.write_text("0.0.0\n")

    captured = io.StringIO()

    with (
        patch(
            "sys.argv",
            [
                "update_version_files",
                "--scope",
                "config",
                "--version",
                "1.0.0",
                "--repo-root",
                str(tmp_path),
            ],
        ),
        patch("sys.stdout", captured),
    ):
        main()

    assert root_version.read_text().strip() == "1.0.0", (
        "main() must update the root VERSION file for config scope."
    )


@pytest.mark.unit
def test_update_version_files_main_missing_file_exits_nonzero(tmp_path) -> None:
    """main() exits non-zero when the VERSION file is missing."""
    import io
    from unittest.mock import patch

    from scripts.update_version_files import main

    with (
        patch(
            "sys.argv",
            [
                "update_version_files",
                "--scope",
                "module",
                "--module-path",
                "providers/aws/primitives/nonexistent",
                "--version",
                "0.1.0",
                "--repo-root",
                str(tmp_path),
            ],
        ),
        patch("sys.stderr", io.StringIO()),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1, (
        f"main() must exit with code 1 for missing VERSION file. Got: {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# CLI exit code contract (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_update_version_files_cli_exits_nonzero_on_missing_file(tmp_path) -> None:
    """The CLI must exit non-zero when the target VERSION file does not exist."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[2])

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.update_version_files",
            "--scope",
            "module",
            "--module-path",
            "providers/aws/primitives/nonexistent-module",
            "--version",
            "0.1.0",
            "--repo-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert result.returncode != 0, (
        f"CLI must exit non-zero when the VERSION file is missing. "
        f"Got returncode={result.returncode}."
    )
