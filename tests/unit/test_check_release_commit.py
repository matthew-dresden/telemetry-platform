"""Unit tests for scripts.check_release_commit.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases: bot + chore(release): -> skip; human + chore(release): -> NO skip (B16);
non-release commit -> NO skip.

AC-15 / B16:
- should_skip=true only when message starts with chore(release): AND actor is bot
- a human-authored chore(release): must NOT skip (closes B16)
- a non-release commit must NOT skip
"""

from __future__ import annotations

import pytest

from scripts.check_release_commit import check_release_commit

# ---------------------------------------------------------------------------
# Parametrized cases for check_release_commit
# ---------------------------------------------------------------------------

_CHECK_RELEASE_CASES = [
    # Bot + chore(release): -> should_skip=true
    pytest.param(
        "chore(release): providers/aws/primitives/kms-key v0.1.0",
        "github-actions[bot]",
        True,
        id="bot-release-commit-skip",
    ),
    pytest.param(
        "chore(release): monorepo-config v1.0.0",
        "github-actions[bot]",
        True,
        id="bot-release-config-commit-skip",
    ),
    # Human + chore(release): -> should_skip=false (B16 bypass close)
    pytest.param(
        "chore(release): providers/aws/primitives/kms-key v0.1.0",
        "alice-developer",
        False,
        id="human-release-commit-no-skip-b16",
    ),
    pytest.param(
        "chore(release): fake release by human",
        "bob-admin",
        False,
        id="human-release-commit-no-skip-admin-b16",
    ),
    # Non-release commit (any actor) -> should_skip=false
    pytest.param(
        "feat: add telemetry exporter",
        "github-actions[bot]",
        False,
        id="bot-non-release-commit-no-skip",
    ),
    pytest.param(
        "fix: correct ARN construction",
        "alice-developer",
        False,
        id="human-non-release-commit-no-skip",
    ),
    pytest.param(
        "chore: update tool versions",
        "github-actions[bot]",
        False,
        id="bot-chore-not-release-no-skip",
    ),
    # Message starts similarly but does NOT start with exact prefix
    pytest.param(
        "chore(release-prep): update version placeholders",
        "github-actions[bot]",
        False,
        id="similar-prefix-no-skip",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("commit_message,actor,expected_skip", _CHECK_RELEASE_CASES)
def test_check_release_commit(
    commit_message: str,
    actor: str,
    expected_skip: bool,
) -> None:
    """check_release_commit returns should_skip=True only for bot + chore(release): commits.

    AC-15 / B16: a human-authored chore(release): commit must NOT skip so a contributor
    cannot forge a release-skip by crafting a commit message matching the release prefix.
    """
    result = check_release_commit(commit_message=commit_message, actor=actor)

    assert result.should_skip == expected_skip, (
        f"Expected should_skip={expected_skip} for commit_message={commit_message!r} "
        f"and actor={actor!r}, got should_skip={result.should_skip}. "
        "check_release_commit must enforce both the prefix AND bot actor conditions (B16)."
    )


# ---------------------------------------------------------------------------
# main() CLI function coverage (direct invocation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_release_commit_main_bot_writes_skip_true(tmp_path) -> None:
    """main() writes should_skip=true for bot + release commit."""
    import io
    from unittest.mock import patch

    from scripts.check_release_commit import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")

    with (
        patch(
            "sys.argv",
            [
                "check_release_commit",
                "--commit-message",
                "chore(release): kms-key v0.1.0",
                "--actor",
                "github-actions[bot]",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", io.StringIO()),
    ):
        main()

    content = output_file.read_text()
    assert "should_skip=true" in content, (
        f"main() must write should_skip=true for bot release commit. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_release_commit_main_human_writes_skip_false(tmp_path) -> None:
    """main() writes should_skip=false for human + release commit (B16)."""
    import io
    from unittest.mock import patch

    from scripts.check_release_commit import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")

    with (
        patch(
            "sys.argv",
            [
                "check_release_commit",
                "--commit-message",
                "chore(release): kms-key v0.1.0",
                "--actor",
                "alice-developer",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", io.StringIO()),
    ):
        main()

    content = output_file.read_text()
    assert "should_skip=false" in content, (
        f"main() must write should_skip=false for human release commit. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_release_commit_main_nonrelease_writes_skip_false(tmp_path) -> None:
    """main() writes should_skip=false for non-release commit."""
    import io
    from unittest.mock import patch

    from scripts.check_release_commit import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")

    with (
        patch(
            "sys.argv",
            [
                "check_release_commit",
                "--commit-message",
                "feat: add new exporter",
                "--actor",
                "github-actions[bot]",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.stdout", io.StringIO()),
    ):
        main()

    content = output_file.read_text()
    assert "should_skip=false" in content, (
        f"main() must write should_skip=false for non-release commit. Got: {content!r}"
    )


# ---------------------------------------------------------------------------
# CLI: writes should_skip to output file
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_release_commit_cli_bot_writes_skip_true(tmp_path) -> None:
    """The CLI handler writes should_skip=true to the output file for bot + release commit."""
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
            "scripts.check_release_commit",
            "--commit-message",
            "chore(release): kms-key v0.1.0",
            "--actor",
            "github-actions[bot]",
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert result.returncode == 0, (
        f"CLI must exit 0 on success. Got returncode={result.returncode}, stderr={result.stderr!r}."
    )
    content = output_file.read_text()
    assert "should_skip=true" in content, (
        f"Output file must contain 'should_skip=true' for bot release commit. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_release_commit_cli_human_writes_skip_false(tmp_path) -> None:
    """The CLI handler writes should_skip=false for human-authored chore(release): (B16)."""
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
            "scripts.check_release_commit",
            "--commit-message",
            "chore(release): kms-key v0.1.0",
            "--actor",
            "alice-developer",
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert result.returncode == 0, (
        f"CLI must exit 0 on success. Got returncode={result.returncode}, stderr={result.stderr!r}."
    )
    content = output_file.read_text()
    assert "should_skip=false" in content, (
        f"Output file must contain 'should_skip=false' for human release commit (B16). "
        f"Got: {content!r}"
    )
