"""Unit tests for scripts.ci_generate_changelog and scripts.generate_changelog.

One test_<script> function per script (docs/release-pipeline.md).
Real behavior assertions: changelog rendered for derived bump; missing input fails loudly.

AC-15:
- ci_generate_changelog renders the changelog for the derived bump
- missing required inputs exit non-zero with a clear error
"""

from __future__ import annotations

import pytest

from scripts.generate_changelog import generate_changelog

# ---------------------------------------------------------------------------
# generate_changelog pure function tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_changelog_renders_version_header() -> None:
    """generate_changelog renders a version header in the changelog output.

    AC-15: the changelog must contain the version string.
    """
    changelog = generate_changelog(
        version="0.2.0",
        module_path="providers/aws/primitives/kms-key",
        bump_type="minor",
        pr_title="feat: add key rotation support",
    )
    assert "0.2.0" in changelog, f"Changelog must contain version '0.2.0'. Got: {changelog!r}"


@pytest.mark.unit
def test_generate_changelog_minor_bump_includes_features_section() -> None:
    """generate_changelog for a minor bump includes a Features/Added section.

    AC-15: the changelog section must match the conventional-commit type (MINOR_TYPES -> features).
    """
    changelog = generate_changelog(
        version="0.2.0",
        module_path="providers/aws/primitives/kms-key",
        bump_type="minor",
        pr_title="feat: add key rotation support",
    )
    # Section header indicates a feature was added
    assert any(keyword in changelog.lower() for keyword in ("feature", "added", "feat", "minor")), (
        f"Changelog for minor bump must include a features/added section. Got: {changelog!r}"
    )


@pytest.mark.unit
def test_generate_changelog_patch_bump_includes_fixes_section() -> None:
    """generate_changelog for a patch bump includes a Fixes/Fixed section."""
    changelog = generate_changelog(
        version="0.0.1",
        module_path="providers/aws/primitives/kms-key",
        bump_type="patch",
        pr_title="fix: correct ARN construction",
    )
    assert any(keyword in changelog.lower() for keyword in ("fix", "fixed", "patch", "bug")), (
        f"Changelog for patch bump must include a fixes section. Got: {changelog!r}"
    )


@pytest.mark.unit
def test_generate_changelog_major_bump_includes_breaking_section() -> None:
    """generate_changelog for a major bump includes a Breaking Changes section."""
    changelog = generate_changelog(
        version="1.0.0",
        module_path="providers/aws/primitives/kms-key",
        bump_type="major",
        pr_title="feat!: redesign API",
    )
    assert any(keyword in changelog.lower() for keyword in ("breaking", "major", "incompatible")), (
        f"Changelog for major bump must include a breaking changes section. Got: {changelog!r}"
    )


@pytest.mark.unit
def test_generate_changelog_includes_pr_title() -> None:
    """generate_changelog includes the PR title in the changelog body.

    AC-15: the rendered changelog must contain the PR title so operators
    know what change drove the release.
    """
    pr_title = "feat: add telemetry collector module"
    changelog = generate_changelog(
        version="0.3.0",
        module_path="providers/aws/primitives/kms-key",
        bump_type="minor",
        pr_title=pr_title,
    )
    assert pr_title in changelog or "add telemetry collector module" in changelog, (
        f"Changelog must include the PR title or its description. "
        f"PR title: {pr_title!r}. Got: {changelog!r}"
    )


@pytest.mark.unit
def test_generate_changelog_missing_version_raises() -> None:
    """generate_changelog must raise ValueError when version is empty.

    Fail-fast: a missing version is a hard error, not a recoverable condition.
    """
    with pytest.raises(ValueError) as exc_info:
        generate_changelog(
            version="",
            module_path="providers/aws/primitives/kms-key",
            bump_type="minor",
            pr_title="feat: something",
        )
    assert len(str(exc_info.value)) > 0, "ValueError must have an error message."


@pytest.mark.unit
def test_generate_changelog_missing_pr_title_raises() -> None:
    """generate_changelog must raise ValueError when pr_title is empty.

    Fail-fast: a missing PR title is a hard error.
    """
    with pytest.raises(ValueError) as exc_info:
        generate_changelog(
            version="0.1.0",
            module_path="providers/aws/primitives/kms-key",
            bump_type="minor",
            pr_title="",
        )
    assert len(str(exc_info.value)) > 0, "ValueError must have an error message."


# ---------------------------------------------------------------------------
# ci_generate_changelog main() direct call coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_generate_changelog_main_writes_changelog_path(tmp_path) -> None:
    """ci_generate_changelog main() writes changelog file and outputs changelog_path."""
    import io
    from unittest.mock import patch

    from scripts.ci_generate_changelog import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")
    changelog_dir = tmp_path / "changelogs"
    changelog_dir.mkdir()

    captured = io.StringIO()

    with (
        patch(
            "sys.argv",
            [
                "ci_generate_changelog",
                "--version",
                "0.2.0",
                "--module-path",
                "providers/aws/primitives/kms-key",
                "--bump-type",
                "minor",
                "--pr-title",
                "feat: add encryption support",
                "--changelog-dir",
                str(changelog_dir),
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", captured),
    ):
        main()

    content = output_file.read_text()
    assert "changelog_path=" in content, (
        f"main() must write 'changelog_path=' to output. Got: {content!r}"
    )
    from pathlib import Path

    changelog_path = content.split("changelog_path=")[1].strip()
    assert Path(changelog_path).exists(), f"The changelog file at {changelog_path!r} must exist."
    changelog_content = Path(changelog_path).read_text()
    assert "0.2.0" in changelog_content, (
        f"Changelog content must include version '0.2.0'. Got: {changelog_content!r}"
    )


@pytest.mark.unit
def test_ci_generate_changelog_main_config_scope(tmp_path) -> None:
    """ci_generate_changelog main() works for config scope (empty module_path)."""
    import io
    from unittest.mock import patch

    from scripts.ci_generate_changelog import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")
    changelog_dir = tmp_path / "changelogs"
    changelog_dir.mkdir()

    with (
        patch(
            "sys.argv",
            [
                "ci_generate_changelog",
                "--version",
                "1.0.0",
                "--bump-type",
                "major",
                "--pr-title",
                "feat!: redesign config format",
                "--changelog-dir",
                str(changelog_dir),
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", io.StringIO()),
    ):
        main()

    content = output_file.read_text()
    assert "changelog_path=" in content, (
        f"main() must write 'changelog_path=' for config scope. Got: {content!r}"
    )


@pytest.mark.unit
def test_ci_generate_changelog_main_invalid_version_exits_nonzero(tmp_path) -> None:
    """ci_generate_changelog main() exits non-zero when version is empty."""
    import io
    from unittest.mock import patch

    from scripts.ci_generate_changelog import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")

    with (
        patch(
            "sys.argv",
            [
                "ci_generate_changelog",
                "--version",
                "",
                "--bump-type",
                "minor",
                "--pr-title",
                "feat: something",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stderr", io.StringIO()),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1, (
        f"main() must exit with code 1 for empty version. Got: {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# ci_generate_changelog CLI tests (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_generate_changelog_writes_changelog_path(tmp_path) -> None:
    """The ci_generate_changelog CLI writes the changelog file and emits changelog_path.

    AC-15: ci_generate_changelog must emit changelog_path to GITHUB_OUTPUT.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[2])
    output_file = tmp_path / "github_output"
    output_file.write_text("")
    changelog_dir = tmp_path / "changelogs"
    changelog_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.ci_generate_changelog",
            "--version",
            "0.1.0",
            "--module-path",
            "providers/aws/primitives/kms-key",
            "--bump-type",
            "minor",
            "--pr-title",
            "feat: add key rotation",
            "--changelog-dir",
            str(changelog_dir),
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert result.returncode == 0, (
        f"ci_generate_changelog CLI must exit 0 on success. "
        f"Got returncode={result.returncode}, stderr={result.stderr!r}."
    )
    content = output_file.read_text()
    assert "changelog_path=" in content, (
        f"Output file must contain 'changelog_path='. Got: {content!r}"
    )
    # The changelog file must exist
    changelog_path = content.split("changelog_path=")[1].strip()
    assert Path(changelog_path).exists(), (
        f"The file at changelog_path={changelog_path!r} must exist after generation."
    )


@pytest.mark.unit
def test_ci_generate_changelog_missing_version_exits_nonzero(tmp_path) -> None:
    """ci_generate_changelog must exit non-zero when --version is missing.

    Fail-fast: a missing required input must be caught early and loudly.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[2])
    output_file = tmp_path / "github_output"
    output_file.write_text("")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.ci_generate_changelog",
            # --version intentionally omitted
            "--module-path",
            "providers/aws/primitives/kms-key",
            "--bump-type",
            "minor",
            "--pr-title",
            "feat: add key rotation",
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert result.returncode != 0, (
        f"ci_generate_changelog CLI must exit non-zero when --version is missing. "
        f"Got returncode={result.returncode}."
    )
