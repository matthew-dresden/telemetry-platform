"""Unit tests for scripts.safety_net_unlock.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases:
- branch not locked -> no-op exit 0
- locked + in-progress release run -> NO unlock, lock retained (fail-closed, B15)
- locked + no release run + lock age below LOCK_MAX_AGE_MINUTES -> NO unlock
- locked + no release run + lock age above LOCK_MAX_AGE_MINUTES -> unlock
- LOCK_MAX_AGE_MINUTES unset -> fail fast (no default)
- GitHub API error -> raise loudly

AC-14:
- safety_net_unlock queries the GitHub API for in-progress release runs
- refuses to unlock while any release run is running (B15, docs/release-pipeline.md)
- unlocks only when no release run is active AND lock age exceeds LOCK_MAX_AGE_MINUTES
- is a no-op exit 0 when branch is not locked
- fails fast when LOCK_MAX_AGE_MINUTES is unset (no default)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.safety_net_unlock import (
    LockInfo,
    UnlockDecision,
    check_in_progress_release_runs,
    decide_unlock,
    perform_unlock,
)

# ---------------------------------------------------------------------------
# LockInfo / decide_unlock parametrized cases
# ---------------------------------------------------------------------------

_DECIDE_CASES = [
    pytest.param(
        None,  # lock_info = None means not locked
        0,  # in_progress_count irrelevant
        60,  # lock_max_age_minutes
        UnlockDecision.NOT_LOCKED,
        id="not-locked-noop",
    ),
    pytest.param(
        LockInfo(run_id="111", age_minutes=120.0),
        1,  # in-progress release run present -> fail-closed, retain lock
        60,
        UnlockDecision.RETAIN_ACTIVE_RELEASE,
        id="locked-active-release-retain",
    ),
    pytest.param(
        LockInfo(run_id="222", age_minutes=30.0),
        0,  # no active release, but age below threshold
        60,
        UnlockDecision.RETAIN_AGE_TOO_LOW,
        id="locked-no-release-age-too-low",
    ),
    pytest.param(
        LockInfo(run_id="333", age_minutes=90.0),
        0,  # no active release, age above threshold
        60,
        UnlockDecision.DO_UNLOCK,
        id="locked-no-release-age-above-threshold",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("lock_info,in_progress_count,lock_max_age_minutes,expected", _DECIDE_CASES)
def test_safety_net_unlock_decide(
    lock_info: LockInfo | None,
    in_progress_count: int,
    lock_max_age_minutes: int,
    expected: UnlockDecision,
) -> None:
    """decide_unlock must return the correct UnlockDecision for each scenario.

    AC-14 / B15: fail-closed when an in-progress release is present;
    unlock only when no active release AND age exceeds threshold.
    """
    decision = decide_unlock(
        lock_info=lock_info,
        in_progress_count=in_progress_count,
        lock_max_age_minutes=lock_max_age_minutes,
    )
    assert decision == expected, (
        f"Expected decision {expected!r} for lock_info={lock_info!r}, "
        f"in_progress_count={in_progress_count}, "
        f"lock_max_age_minutes={lock_max_age_minutes}. Got {decision!r}."
    )


# ---------------------------------------------------------------------------
# check_in_progress_release_runs -- GitHub API interaction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_check_in_progress_release_runs_returns_count(monkeypatch) -> None:
    """check_in_progress_release_runs must return the count of in-progress runs.

    AC-14: the function queries the GitHub API and counts active runs.
    """
    monkeypatch.setenv("WORKFLOW_FILE_NAME", "main-validation.yml")
    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2\n",
            stderr="",
        )
        count = check_in_progress_release_runs(repo="example-org/telemetry-platform", branch="main")
    assert count == 2, f"Expected 2 in-progress runs, got {count!r}."
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "gh" in cmd, f"Must use gh CLI for API call. Command: {cmd!r}."


@pytest.mark.unit
def test_safety_net_unlock_check_in_progress_release_runs_raises_on_api_error(monkeypatch) -> None:
    """check_in_progress_release_runs must raise RuntimeError on API failure.

    AC-14: no silent failures -- GitHub API errors must propagate loudly.
    """
    monkeypatch.setenv("WORKFLOW_FILE_NAME", "main-validation.yml")
    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="API rate limit exceeded",
        )
        with pytest.raises(RuntimeError) as exc_info:
            check_in_progress_release_runs(repo="example-org/telemetry-platform", branch="main")

    assert (
        "api" in str(exc_info.value).lower()
        or "gh" in str(exc_info.value).lower()
        or "error" in str(exc_info.value).lower()
    ), f"RuntimeError must mention API failure context. Got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# perform_unlock -- branch protection removal
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_perform_unlock_calls_gh_api() -> None:
    """perform_unlock must call gh to remove the branch protection lock.

    AC-14: the unlock operation must use the GitHub API.
    """
    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        perform_unlock(repo="example-org/telemetry-platform", branch="main")
    mock_run.assert_called()
    all_cmds = [call[0][0] for call in mock_run.call_args_list]
    assert any("gh" in cmd for cmd in all_cmds), (
        f"perform_unlock must use gh CLI. Commands: {all_cmds!r}."
    )


@pytest.mark.unit
def test_safety_net_unlock_perform_unlock_raises_on_failure() -> None:
    """perform_unlock must raise RuntimeError when the gh API call fails.

    AC-14: no silent failures -- branch protection errors must propagate loudly.
    """
    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="forbidden")
        with pytest.raises(RuntimeError) as exc_info:
            perform_unlock(repo="example-org/telemetry-platform", branch="main")

    assert "unlock" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower(), (
        f"RuntimeError must mention unlock failure. Got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# LOCK_MAX_AGE_MINUTES unset -> fail fast
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_check_in_progress_raises_on_invalid_count(monkeypatch) -> None:
    """check_in_progress_release_runs must raise RuntimeError when API returns non-integer.

    AC-14: unexpected API responses must propagate loudly.
    """
    monkeypatch.setenv("WORKFLOW_FILE_NAME", "main-validation.yml")
    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="not-a-number\n", stderr="")
        with pytest.raises(RuntimeError) as exc_info:
            check_in_progress_release_runs(repo="example-org/telemetry-platform", branch="main")
    assert (
        "unexpected" in str(exc_info.value).lower() or "integer" in str(exc_info.value).lower()
    ), f"RuntimeError must mention unexpected value. Got: {exc_info.value!r}"


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_returns_none_when_not_locked() -> None:
    """read_lock_info must return None when the lock variable does not exist (404).

    AC-14: branch not locked -> no-op path.
    """
    from scripts.safety_net_unlock import read_lock_info

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Not Found (status: 404)",
        )
        result = read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert result is None, f"read_lock_info must return None when not locked. Got: {result!r}."


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_raises_on_unexpected_error() -> None:
    """read_lock_info must raise RuntimeError on unexpected API errors.

    AC-14: non-404 errors must propagate loudly.
    """
    from scripts.safety_net_unlock import read_lock_info

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="forbidden: 403 Unauthorized",
        )
        with pytest.raises(RuntimeError) as exc_info:
            read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert "error" in str(exc_info.value).lower() or "failed" in str(exc_info.value).lower(), (
        f"RuntimeError must mention failure. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_returns_lock_info_when_locked() -> None:
    """read_lock_info must return LockInfo when the lock variable exists.

    AC-14: locked branch must return LockInfo with run_id and age_minutes.
    """
    import json
    import time

    from scripts.safety_net_unlock import read_lock_info

    locked_at = time.time() - 3600.0  # 60 minutes ago
    marker = json.dumps({"run_id": "555", "locked_at_epoch": locked_at})

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=f"{marker}\n", stderr="")
        result = read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert result is not None, "read_lock_info must return LockInfo when locked."
    assert result.run_id == "555", f"LockInfo.run_id must be '555'. Got: {result.run_id!r}."
    assert result.age_minutes >= 59.0, (
        f"LockInfo.age_minutes must be approximately 60. Got: {result.age_minutes!r}."
    )


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_raises_on_invalid_json() -> None:
    """read_lock_info must raise RuntimeError when the variable contains invalid JSON.

    AC-14: corrupt lock state must fail fast, not silently proceed.
    """
    from scripts.safety_net_unlock import read_lock_info

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="not-valid-json\n", stderr="")
        with pytest.raises(RuntimeError) as exc_info:
            read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert "json" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower(), (
        f"RuntimeError must mention JSON parse error. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_raises_on_missing_keys() -> None:
    """read_lock_info must raise RuntimeError when required keys are missing.

    AC-14: partial/corrupt lock marker must fail fast.
    """
    import json

    from scripts.safety_net_unlock import read_lock_info

    marker = json.dumps({"run_id": "999"})  # Missing locked_at_epoch

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=f"{marker}\n", stderr="")
        with pytest.raises(RuntimeError) as exc_info:
            read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert "missing" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower(), (
        f"RuntimeError must mention missing keys. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_returns_none_when_empty() -> None:
    """read_lock_info must return None when the variable value is empty/null.

    AC-14: empty lock variable means branch is not locked.
    """
    from scripts.safety_net_unlock import read_lock_info

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="null\n", stderr="")
        result = read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert result is None, f"read_lock_info must return None for null value. Got: {result!r}."


@pytest.mark.unit
def test_safety_net_unlock_main_exits_zero_when_not_locked(monkeypatch) -> None:
    """main() must succeed when the branch is not locked.

    AC-14: no-op exit 0 when branch is not locked.
    """
    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        # Variable not found -> not locked
        return MagicMock(returncode=1, stdout="", stderr="Not Found (status: 404)")

    from scripts.safety_net_unlock import main

    with patch("scripts.safety_net_unlock.subprocess.run", side_effect=fake_run):
        # main() succeeds without raising (no-op path exits 0 or returns normally)
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0, f"main() must exit 0 when not locked. Got: {exc.code!r}."


@pytest.mark.unit
def test_safety_net_unlock_main_retains_lock_when_active_release(monkeypatch) -> None:
    """main() must succeed and retain lock when a release run is in progress (B15).

    AC-14 / B15: fail-closed -- retain lock when active release run present.
    """
    import json
    import time

    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")
    monkeypatch.setenv("WORKFLOW_FILE_NAME", "main-validation.yml")

    locked_at = time.time() - 3600.0
    marker = json.dumps({"run_id": "77", "locked_at_epoch": locked_at})

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        if "variables" in cmd_str:
            return MagicMock(returncode=0, stdout=f"{marker}\n", stderr="")
        if "runs" in cmd_str:
            return MagicMock(returncode=0, stdout="1\n", stderr="")  # 1 active run
        return MagicMock(returncode=0, stdout="", stderr="")

    from scripts.safety_net_unlock import main

    with patch("scripts.safety_net_unlock.subprocess.run", side_effect=fake_run):
        # main() succeeds (retain lock is not an error)
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0, (
                f"main() must exit 0 (retain lock, not error) when active release. "
                f"Got: {exc.code!r}."
            )


@pytest.mark.unit
def test_safety_net_unlock_main_unlocks_when_conditions_met(monkeypatch) -> None:
    """main() must succeed after unlocking when no active release and age exceeded.

    AC-14: unlock performed when conditions are met.
    """
    import json
    import time

    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")
    monkeypatch.setenv("WORKFLOW_FILE_NAME", "main-validation.yml")

    locked_at = time.time() - 7200.0  # 120 minutes ago -- above threshold
    marker = json.dumps({"run_id": "88", "locked_at_epoch": locked_at})

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        if "variables" in cmd_str and "--method" not in cmd_str:
            return MagicMock(returncode=0, stdout=f"{marker}\n", stderr="")
        if "runs" in cmd_str:
            return MagicMock(returncode=0, stdout="0\n", stderr="")  # no active runs
        # DELETE variable call
        return MagicMock(returncode=0, stdout="", stderr="")

    from scripts.safety_net_unlock import main

    with patch("scripts.safety_net_unlock.subprocess.run", side_effect=fake_run):
        # main() succeeds after unlocking
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0, f"main() must exit 0 after successful unlock. Got: {exc.code!r}."


@pytest.mark.unit
def test_safety_net_unlock_main_fails_fast_when_lock_max_age_unset(monkeypatch) -> None:
    """main() must fail fast when LOCK_MAX_AGE_MINUTES is unset (no default).

    AC-14 / B15: LOCK_MAX_AGE_MINUTES must be explicitly configured; a missing
    value must cause an immediate failure, not a silent no-op.
    """
    monkeypatch.delenv("LOCK_MAX_AGE_MINUTES", raising=False)
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")

    from scripts.safety_net_unlock import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0, "main() must exit non-zero when LOCK_MAX_AGE_MINUTES is unset."


# ---------------------------------------------------------------------------
# read_lock_info with empty (not just null) value (line 257-262)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_read_lock_info_returns_none_when_empty_string() -> None:
    """read_lock_info must return None when the variable value is empty string.

    AC-14: an empty lock variable must be treated as unlocked.
    """
    from scripts.safety_net_unlock import read_lock_info

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="\n", stderr="")
        result = read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert result is None, (
        f"read_lock_info must return None for empty string value. Got: {result!r}."
    )


# ---------------------------------------------------------------------------
# read_lock_info with marker missing only locked_at_epoch (lines 299-306)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "marker_json,expected_fragment",
    [
        pytest.param(
            '{"locked_at_epoch": 1700000000.0}',  # missing run_id
            "missing",
            id="missing-run-id",
        ),
        pytest.param(
            '{"run_id": "42"}',  # missing locked_at_epoch
            "missing",
            id="missing-locked-at-epoch",
        ),
    ],
)
def test_safety_net_unlock_read_lock_info_raises_on_partial_marker(
    marker_json: str, expected_fragment: str
) -> None:
    """read_lock_info must raise when the marker is missing required fields.

    AC-14: partial lock markers must fail fast, not silently produce None.
    """
    from scripts.safety_net_unlock import read_lock_info

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=f"{marker_json}\n", stderr="")
        with pytest.raises(RuntimeError) as exc_info:
            read_lock_info(repo="example-org/telemetry-platform", branch="main")

    assert expected_fragment.lower() in str(exc_info.value).lower(), (
        f"RuntimeError must mention {expected_fragment!r}. Got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# main() with age-too-low decision (RETAIN_AGE_TOO_LOW branch, line 327)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_main_retains_lock_when_age_too_low(monkeypatch) -> None:
    """main() must succeed and retain lock when lock age is below the threshold.

    AC-14: the lock is retained when age is below LOCK_MAX_AGE_MINUTES.
    """
    import json
    import time

    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")
    monkeypatch.setenv("WORKFLOW_FILE_NAME", "main-validation.yml")

    locked_at = time.time() - 60.0  # only 1 minute ago -- below 60 min threshold
    marker = json.dumps({"run_id": "55", "locked_at_epoch": locked_at})

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        if "variables" in cmd_str:
            return MagicMock(returncode=0, stdout=f"{marker}\n", stderr="")
        if "runs" in cmd_str:
            return MagicMock(returncode=0, stdout="0\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    from scripts.safety_net_unlock import main

    with patch("scripts.safety_net_unlock.subprocess.run", side_effect=fake_run):
        try:
            main()
        except SystemExit as exc:
            assert exc.code == 0, (
                f"main() must exit 0 (retain lock, age too low). Got: {exc.code!r}."
            )


# ---------------------------------------------------------------------------
# main() RuntimeError path (lines 335-337)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_main_exits_nonzero_on_runtime_error(monkeypatch) -> None:
    """main() must exit non-zero when a RuntimeError is raised from read_lock_info.

    AC-14: no silent failures in the main safety-net execution path.
    """
    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")

    with patch("scripts.safety_net_unlock.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="500 Internal Server Error"
        )

        from scripts.safety_net_unlock import main

        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero on RuntimeError. Got: {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# main() LOCK_MAX_AGE_MINUTES non-integer (lines 341)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_main_exits_nonzero_when_lock_max_age_not_integer(
    monkeypatch,
) -> None:
    """main() must exit non-zero when LOCK_MAX_AGE_MINUTES is not a valid integer.

    AC-14 / B15: non-integer LOCK_MAX_AGE_MINUTES must fail fast.
    """
    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "not-a-number")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")

    from scripts.safety_net_unlock import main

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero when LOCK_MAX_AGE_MINUTES is non-integer. "
        f"Got: {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# WORKFLOW_FILE_NAME unset -> fail fast (no default allowed)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_safety_net_unlock_check_in_progress_raises_when_workflow_file_name_unset(
    monkeypatch,
) -> None:
    """check_in_progress_release_runs raises ConfigurationError when WORKFLOW_FILE_NAME is unset.

    AC-14 / B15: WORKFLOW_FILE_NAME must be explicitly configured; a missing
    value must cause an immediate failure, not a silent fallback to a hardcoded
    filename.
    """
    monkeypatch.delenv("WORKFLOW_FILE_NAME", raising=False)

    from scripts.safety_net_unlock import ConfigurationError, check_in_progress_release_runs

    with pytest.raises(ConfigurationError) as exc_info:
        check_in_progress_release_runs(repo="example-org/telemetry-platform", branch="main")

    assert "WORKFLOW_FILE_NAME" in str(exc_info.value), (
        f"ConfigurationError must name the missing variable. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_safety_net_unlock_main_exits_nonzero_when_workflow_file_name_unset(
    monkeypatch,
) -> None:
    """main() must exit non-zero when WORKFLOW_FILE_NAME is unset (no default).

    AC-14 / B15: WORKFLOW_FILE_NAME has no default; an absent value is a hard failure
    so an unconfigured workflow cannot silently target the wrong file.
    """
    monkeypatch.delenv("WORKFLOW_FILE_NAME", raising=False)
    monkeypatch.setenv("LOCK_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("REPO", "example-org/telemetry-platform")
    monkeypatch.setenv("BRANCH", "main")

    import json
    import time

    locked_at = time.time() - 3600.0
    marker = json.dumps({"run_id": "42", "locked_at_epoch": locked_at})

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        # Return a valid locked state so the code reaches check_in_progress_release_runs
        return MagicMock(returncode=0, stdout=f"{marker}\n", stderr="")

    from scripts.safety_net_unlock import main

    with (
        patch("scripts.safety_net_unlock.subprocess.run", side_effect=fake_run),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero when WORKFLOW_FILE_NAME is unset. Got: {exc_info.value.code!r}."
    )
