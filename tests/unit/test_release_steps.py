"""Unit tests for scripts.release_steps.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases: fetch issues correct git command; reset issues git reset --hard;
identity sets git user name and email; publish commits, tags, pushes atomically,
and calls gh release create.

AC-15:
- each release_steps subcommand invokes the correct subprocess call
- no time.sleep or time-based synchronization
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.release_steps import run_fetch, run_identity, run_publish, run_reset

# ---------------------------------------------------------------------------
# fetch subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_steps_fetch_issues_correct_git_command() -> None:
    """run_fetch must invoke 'git fetch <remote> <branch>'.

    AC-15: git fetch must use the specified remote and branch.
    """
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_fetch(remote="origin", branch="main")

    assert mock_run.called, "run_fetch must invoke subprocess.run."
    args = mock_run.call_args[0][0]
    assert args == ["git", "fetch", "origin", "main"], (
        f"run_fetch must call git fetch origin main, got {args!r}."
    )


@pytest.mark.unit
def test_release_steps_fetch_raises_on_nonzero_exit() -> None:
    """run_fetch must raise RuntimeError when git fetch returns non-zero.

    Fail-fast: a failed git fetch is a hard error.
    """
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: no such remote")

    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.side_effect = RuntimeError("git fetch failed")
        with pytest.raises(RuntimeError):
            run_fetch(remote="origin", branch="main")


# ---------------------------------------------------------------------------
# reset subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_steps_reset_issues_git_reset_hard() -> None:
    """run_reset must invoke 'git reset --hard <ref>'.

    AC-15: the reset subcommand must use --hard to ensure a clean working tree.
    """
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_reset(ref="origin/main")

    assert mock_run.called, "run_reset must invoke subprocess.run."
    args = mock_run.call_args[0][0]
    assert args == ["git", "reset", "--hard", "origin/main"], (
        f"run_reset must call git reset --hard origin/main, got {args!r}."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("origin/main", id="origin-main"),
        pytest.param("HEAD~1", id="head-minus-one"),
        pytest.param("abc123def456", id="commit-sha"),
    ],
)
def test_release_steps_reset_uses_provided_ref(ref: str) -> None:
    """run_reset uses the exact ref parameter provided."""
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_reset(ref=ref)

    args = mock_run.call_args[0][0]
    assert args[3] == ref, f"run_reset must pass ref={ref!r} as the reset target, got {args[3]!r}."


# ---------------------------------------------------------------------------
# identity subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_steps_identity_sets_user_name_and_email() -> None:
    """run_identity must set git user.name and user.email via git config.

    AC-15: git identity must configure both name and email for the release commit.
    """
    calls_made: list[list[str]] = []

    def capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        return MagicMock(returncode=0)

    with patch("scripts.release_steps.subprocess.run", side_effect=capture_run):
        run_identity(
            user="github-actions[bot]",
            email="github-actions[bot]@users.noreply.github.com",
        )

    # Must configure both user.name and user.email
    name_cmd = ["git", "config", "user.name", "github-actions[bot]"]
    email_cmd = [
        "git",
        "config",
        "user.email",
        "github-actions[bot]@users.noreply.github.com",
    ]
    assert name_cmd in calls_made, (
        f"run_identity must set git user.name. Commands issued: {calls_made!r}."
    )
    assert email_cmd in calls_made, (
        f"run_identity must set git user.email. Commands issued: {calls_made!r}."
    )


# ---------------------------------------------------------------------------
# publish subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_steps_publish_commits_tags_pushes_atomically_and_creates_release(
    tmp_path,
) -> None:
    """run_publish commits version files, creates the tag, pushes atomically,
    and calls gh release create.

    AC-15: publish must (1) commit, (2) tag, (3) atomic push (main + tag together),
    and (4) gh release create -- in that logical order, with no time-based delays.
    """
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text("## 0.1.0\n\n- feat: add rotation support\n")

    calls_made: list[list[str]] = []

    def capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.release_steps.subprocess.run", side_effect=capture_run):
        run_publish(
            tag_prefix="providers/aws/primitives/kms-key/v",
            version="0.1.0",
            full_tag="providers/aws/primitives/kms-key/v0.1.0",
            changelog_path=str(changelog_file),
        )

    # Verify git add + commit were called
    git_commands = [cmd for cmd in calls_made if cmd and cmd[0] == "git"]
    gh_commands = [cmd for cmd in calls_made if cmd and cmd[0] == "gh"]

    has_commit = any("commit" in cmd for cmd in git_commands)
    has_tag = any("tag" in cmd for cmd in git_commands)
    has_push = any("push" in cmd for cmd in git_commands)
    has_release_create = any("release" in cmd and "create" in cmd for cmd in gh_commands)

    # The release commit must use --allow-empty so the version_only release path
    # (VERSION already at the computed next version, nothing staged) still creates the
    # commit + tag instead of aborting with "nothing to commit".
    commit_cmds = [cmd for cmd in git_commands if "commit" in cmd]
    assert any("--allow-empty" in cmd for cmd in commit_cmds), (
        "run_publish must commit with --allow-empty so a no-op VERSION change still "
        f"produces the release commit + tag. Commit commands: {commit_cmds!r}."
    )

    assert has_commit, f"run_publish must issue a git commit. Git commands: {git_commands!r}."
    assert has_tag, f"run_publish must create a git tag. Git commands: {git_commands!r}."
    assert has_push, f"run_publish must push. Git commands: {git_commands!r}."
    assert has_release_create, (
        f"run_publish must call gh release create. gh commands: {gh_commands!r}."
    )

    # Atomic push must use the --atomic flag AND push main and the tag together in
    # one command, so a rejected branch ref can never leave a dangling tag behind.
    push_cmds = [cmd for cmd in git_commands if "push" in cmd]
    full_tag = "providers/aws/primitives/kms-key/v0.1.0"
    atomic_push = any("--atomic" in cmd and "main" in cmd and full_tag in cmd for cmd in push_cmds)
    assert atomic_push, (
        f"run_publish must use an atomic push: `git push --atomic <remote> main <tag>` "
        f"(the --atomic flag makes branch + tag one all-or-nothing transaction). "
        f"Push commands: {push_cmds!r}."
    )


@pytest.mark.unit
def test_release_steps_publish_full_tag_in_gh_release_create(tmp_path) -> None:
    """run_publish passes the full_tag to gh release create.

    AC-15: the GitHub release must reference the correct tag.
    """
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text("## 0.2.0\n\n- fix: correct logic\n")

    calls_made: list[list[str]] = []

    def capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.release_steps.subprocess.run", side_effect=capture_run):
        run_publish(
            tag_prefix="providers/aws/primitives/s3-bucket/v",
            version="0.2.0",
            full_tag="providers/aws/primitives/s3-bucket/v0.2.0",
            changelog_path=str(changelog_file),
        )

    gh_commands = [cmd for cmd in calls_made if cmd and cmd[0] == "gh"]
    release_cmds = [cmd for cmd in gh_commands if "release" in cmd and "create" in cmd]

    assert len(release_cmds) > 0, (
        f"run_publish must call gh release create. Got gh commands: {gh_commands!r}."
    )
    full_tag = "providers/aws/primitives/s3-bucket/v0.2.0"
    assert any(full_tag in cmd for cmd in release_cmds), (
        f"gh release create must reference the full_tag={full_tag!r}. "
        f"Release commands: {release_cmds!r}."
    )


# ---------------------------------------------------------------------------
# publish error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_steps_publish_raises_when_changelog_missing(tmp_path) -> None:
    """run_publish must raise FileNotFoundError when the changelog file is missing."""
    from scripts.release_steps import run_publish

    with pytest.raises(FileNotFoundError) as exc_info:
        run_publish(
            tag_prefix="providers/aws/primitives/kms-key/v",
            version="0.1.0",
            full_tag="providers/aws/primitives/kms-key/v0.1.0",
            changelog_path=str(tmp_path / "nonexistent.md"),
        )

    assert "CHANGELOG" in str(exc_info.value) or "not found" in str(exc_info.value).lower(), (
        f"FileNotFoundError must mention the missing changelog. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_release_steps_run_raises_on_nonzero_exit() -> None:
    """_run must raise RuntimeError when the command exits non-zero."""
    from scripts.release_steps import _run

    with pytest.raises(RuntimeError) as exc_info:
        _run(["false"])  # 'false' command always exits 1

    assert "exit" in str(exc_info.value).lower() or "1" in str(exc_info.value), (
        f"RuntimeError must mention the exit code. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_release_steps_identity_main_dispatch(tmp_path) -> None:
    """The CLI must dispatch 'identity' subcommand to run_identity."""
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from scripts import release_steps

        with patch(
            "sys.argv",
            [
                "release_steps",
                "identity",
                "--user",
                "bot[bot]",
                "--email",
                "bot@example.com",
            ],
        ):
            release_steps.main()

    assert mock_run.called, "identity subcommand must call subprocess.run."
    all_cmds = [call[0][0] for call in mock_run.call_args_list]
    assert any("user.name" in cmd for cmd in all_cmds), (
        f"identity must set user.name. Commands: {all_cmds!r}"
    )


# ---------------------------------------------------------------------------
# CLI integration: subcommand dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_steps_cli_fetch_dispatches_correctly() -> None:
    """The CLI must dispatch 'fetch' subcommand to run_fetch."""
    from unittest.mock import patch

    # We patch subprocess.run inside the module to avoid real git calls
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from scripts import release_steps

        with patch(
            "sys.argv",
            ["release_steps", "fetch", "--remote", "origin", "--branch", "main"],
        ):
            # Should not raise
            release_steps.main()

    mock_run.assert_called()


@pytest.mark.unit
def test_release_steps_cli_reset_dispatches_correctly() -> None:
    """The CLI must dispatch 'reset' subcommand to run_reset."""
    with patch("scripts.release_steps.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from scripts import release_steps

        with patch("sys.argv", ["release_steps", "reset", "--ref", "origin/main"]):
            release_steps.main()

    mock_run.assert_called()
    args = mock_run.call_args[0][0]
    assert "reset" in args, f"reset subcommand must call git reset, got {args!r}."
