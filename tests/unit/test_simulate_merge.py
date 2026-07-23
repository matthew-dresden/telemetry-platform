"""Unit tests for scripts.simulate_merge.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases:
- clean dry-run merge -> success
- conflicting dry-run merge -> reported failure (B14)

AC-14 / B14:
- simulate_merge performs a dry-run merge and reports mergeability without
  mutating the branch
- a successful dry-run merge returns without error
- a conflicting dry-run merge raises with a clear message
- the branch is not mutated (no actual commit or push)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.simulate_merge import SimulateMergeResult, simulate_merge

# ---------------------------------------------------------------------------
# simulate_merge parametrized cases
# ---------------------------------------------------------------------------

_SIMULATE_CASES = [
    pytest.param(
        0,  # git merge-tree exit code -> 0 means no conflicts
        "",  # stdout (no conflict lines)
        SimulateMergeResult.SUCCESS,
        id="clean-merge-success",
    ),
    pytest.param(
        1,  # git merge-tree exit code -> 1 means conflicts
        "CONFLICT (content): Merge conflict in providers/aws/primitives/kms-key/main.tf",
        SimulateMergeResult.CONFLICT,
        id="conflicting-merge-failure",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("exit_code,stdout,expected_result", _SIMULATE_CASES)
def test_simulate_merge_dry_run(
    exit_code: int,
    stdout: str,
    expected_result: SimulateMergeResult,
) -> None:
    """simulate_merge must return the correct SimulateMergeResult.

    AC-14 / B14: a clean dry-run returns SUCCESS; a conflict returns CONFLICT.
    """
    merge_base_sha = "abc1234def5678901234567890123456789012345"

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "merge-base" in cmd:
            return MagicMock(returncode=0, stdout=f"{merge_base_sha}\n", stderr="")
        # merge-tree call
        return MagicMock(returncode=exit_code, stdout=stdout, stderr="")

    with patch("scripts.simulate_merge.subprocess.run", side_effect=fake_run):
        result = simulate_merge(head_ref="HEAD", base_ref="origin/main")

    assert result == expected_result, (
        f"Expected {expected_result!r} for exit_code={exit_code}, "
        f"stdout={stdout!r}. Got {result!r}."
    )


# ---------------------------------------------------------------------------
# simulate_merge does not mutate the branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_does_not_call_git_commit_or_push() -> None:
    """simulate_merge must not call git commit or git push.

    AC-14 / B14: a dry-run merge must be non-mutating.
    """
    calls_made: list[list[str]] = []
    merge_base_sha = "abc1234def5678901234567890123456789012345"

    def capture_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        if "merge-base" in cmd:
            return MagicMock(returncode=0, stdout=f"{merge_base_sha}\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.simulate_merge.subprocess.run", side_effect=capture_run):
        simulate_merge(head_ref="HEAD", base_ref="origin/main")

    mutating_cmds = [
        cmd
        for cmd in calls_made
        if cmd and cmd[0] == "git" and any(action in cmd for action in ("commit", "push", "merge"))
    ]
    # merge-tree is acceptable (it's a dry-run operation); actual merge is not
    actual_merge = [cmd for cmd in mutating_cmds if "merge" in cmd and "tree" not in cmd]
    assert len(actual_merge) == 0, (
        f"simulate_merge must not call 'git merge' (only git merge-tree). "
        f"Mutating git commands found: {actual_merge!r}."
    )
    assert not any("commit" in cmd for cmd in calls_made if cmd and cmd[0] == "git"), (
        f"simulate_merge must not call 'git commit'. Commands: {calls_made!r}."
    )
    assert not any("push" in cmd for cmd in calls_made if cmd and cmd[0] == "git"), (
        f"simulate_merge must not call 'git push'. Commands: {calls_made!r}."
    )


# ---------------------------------------------------------------------------
# simulate_merge raises on unexpected git errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_raises_on_unexpected_git_error() -> None:
    """simulate_merge must raise RuntimeError on unexpected git subprocess errors.

    AC-14: no silent failures -- unexpected subprocess errors must propagate.
    """
    with patch("scripts.simulate_merge.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("git binary not found")

        with pytest.raises((OSError, RuntimeError)):
            simulate_merge(head_ref="HEAD", base_ref="origin/main")


# ---------------------------------------------------------------------------
# CLI main -- calls simulate_merge and exits correctly
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_main_exits_zero_on_success() -> None:
    """main() must exit 0 when the merge simulation succeeds.

    AC-14: a clean merge simulation must not fail the pipeline.
    """
    merge_base_sha = "abc1234def5678901234567890123456789012345"

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "merge-base" in cmd:
            return MagicMock(returncode=0, stdout=f"{merge_base_sha}\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.simulate_merge.subprocess.run", side_effect=fake_run):
        from scripts.simulate_merge import main

        with (
            patch(
                "sys.argv", ["simulate_merge", "--head-ref", "HEAD", "--base-ref", "origin/main"]
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code == 0, (
        f"main() must exit 0 on clean merge. Got code {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_simulate_merge_main_exits_nonzero_on_conflict() -> None:
    """main() must exit non-zero when the merge simulation detects a conflict.

    AC-14 / B14: a conflicting dry-run must fail the pipeline step.
    """
    merge_base_sha = "abc1234def5678901234567890123456789012345"

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "merge-base" in cmd:
            return MagicMock(returncode=0, stdout=f"{merge_base_sha}\n", stderr="")
        return MagicMock(
            returncode=1,
            stdout="CONFLICT (content): Merge conflict in main.tf",
            stderr="",
        )

    with patch("scripts.simulate_merge.subprocess.run", side_effect=fake_run):
        from scripts.simulate_merge import main

        with (
            patch(
                "sys.argv", ["simulate_merge", "--head-ref", "HEAD", "--base-ref", "origin/main"]
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero on conflict. Got code {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# simulate_merge raises when merge-base fails (line 76)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_raises_on_merge_base_failure() -> None:
    """simulate_merge must raise RuntimeError when git merge-base fails.

    AC-14: a git merge-base failure must propagate as a RuntimeError.
    """
    with patch("scripts.simulate_merge.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="fatal: Not a valid commit name 'bad-ref'"
        )
        with pytest.raises(RuntimeError) as exc_info:
            simulate_merge(head_ref="bad-ref", base_ref="origin/main")

    assert (
        "merge-base" in str(exc_info.value).lower()
        or "failed" in str(exc_info.value).lower()
        or "bad-ref" in str(exc_info.value)
    ), f"RuntimeError must mention merge-base failure. Got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# simulate_merge must NOT false-positive on "CONFLICT" appearing in clean stdout
# (B14 false-positive regression guard -- the bug this fix removes)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_no_false_positive_on_conflict_word_in_clean_output() -> None:
    """A clean merge (exit 0) must return SUCCESS even if 'CONFLICT' appears in stdout.

    AC-14 / B14: the `git merge-tree --write-tree` mode signals conflicts only via
    the exit code (0 == clean). The merged-tree OID and any informational output
    must never be content-scanned for the literal substring "CONFLICT" -- doing so
    produced a false positive whenever a merged file legitimately contained the word
    "CONFLICT" in its own source (e.g. this module's own code/tests). This guards
    against re-introducing that substring heuristic.
    """
    merge_base_sha = "abc1234def5678901234567890123456789012345"

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "merge-base"]:
            return MagicMock(returncode=0, stdout=f"{merge_base_sha}\n", stderr="")
        # Clean merge: exit 0. stdout carries the merged tree OID plus diff content
        # that happens to contain the literal word "CONFLICT" (e.g. a source file
        # that documents conflict handling). This must NOT be treated as a conflict.
        return MagicMock(
            returncode=0,
            stdout=(
                "1234567890abcdef1234567890abcdef12345678\n"
                "+    return SimulateMergeResult.CONFLICT  # docstring line in merged file\n"
            ),
            stderr="",
        )

    with patch("scripts.simulate_merge.subprocess.run", side_effect=fake_run):
        result = simulate_merge(head_ref="HEAD", base_ref="origin/main")

    assert result == SimulateMergeResult.SUCCESS, (
        "simulate_merge must return SUCCESS on a clean merge (exit 0) regardless of "
        f"the literal 'CONFLICT' substring appearing in stdout. Got: {result!r}"
    )


# ---------------------------------------------------------------------------
# simulate_merge raises on an unexpected (non-0, non-1) merge-tree exit code
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_raises_on_unexpected_merge_tree_exit_code() -> None:
    """An unexpected merge-tree exit code (not 0/1) must raise RuntimeError.

    AC-14: a hard git failure (e.g. exit 128) is not a conflict and must fail loudly
    rather than be silently treated as a conflict or a clean merge.
    """
    merge_base_sha = "abc1234def5678901234567890123456789012345"

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if cmd[:2] == ["git", "merge-base"]:
            return MagicMock(returncode=0, stdout=f"{merge_base_sha}\n", stderr="")
        return MagicMock(returncode=128, stdout="", stderr="fatal: bad object")

    with (
        patch("scripts.simulate_merge.subprocess.run", side_effect=fake_run),
        pytest.raises(RuntimeError) as exc_info,
    ):
        simulate_merge(head_ref="HEAD", base_ref="origin/main")

    assert "128" in str(exc_info.value) or "unexpected" in str(exc_info.value).lower(), (
        f"RuntimeError must mention the unexpected exit code. Got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# main() RuntimeError path (lines 133-135)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simulate_merge_main_exits_nonzero_on_runtime_error() -> None:
    """main() must exit non-zero when simulate_merge raises a RuntimeError.

    AC-14: no silent failures in the main execution path.
    """
    with patch("scripts.simulate_merge.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="fatal: not a git repository"
        )

        from scripts.simulate_merge import main

        with (
            patch(
                "sys.argv",
                ["simulate_merge", "--head-ref", "HEAD", "--base-ref", "origin/main"],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero on RuntimeError. Got code {exc_info.value.code!r}."
    )
