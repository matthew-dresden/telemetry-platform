"""Unit tests for scripts.lock_branch.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases:
- --action lock: records the run-id and timestamp marker and PUTs the protection lock
- --action unlock: on a locked branch -> unlocks
- --action unlock: on an already-unlocked branch -> idempotent (exit 0, no error, D47)
- missing protection rule (404) -> exit non-zero (B30)

AC-15:
- lock_branch engages the branch-protection lock and records the run-id/timestamp marker
- idempotent on --action unlock of an already-unlocked branch (D47)
- raises loudly on a missing protection rule (B30)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.lock_branch import (
    lock_branch,
    unlock_branch,
    write_lock_marker,
)

# ---------------------------------------------------------------------------
# lock_branch -- writes marker and enables branch protection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_lock_writes_marker_and_puts_protection() -> None:
    """lock_branch must write the lock marker variable and enable branch protection.

    AC-15: --action lock records run-id + timestamp and PUTs the protection lock.
    """
    calls_made: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        cmd_str = " ".join(cmd)
        # GET branch protection returns a valid (all-off) existing rule so the
        # lock can re-PUT the complete configuration.
        if "protection" in cmd_str and "--method" not in cmd_str:
            return MagicMock(
                returncode=0,
                stdout='{"required_status_checks":null,"enforce_admins":{"enabled":false},'
                '"required_pull_request_reviews":null,"restrictions":null,'
                '"lock_branch":{"enabled":false}}',
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        lock_branch(
            repo="example-org/telemetry-platform",
            branch="main",
            run_id="42",
        )

    gh_cmds = [cmd for cmd in calls_made if cmd and cmd[0] == "gh"]
    assert len(gh_cmds) >= 2, (
        f"lock_branch must make at least 2 gh API calls (marker + protection). Got: {gh_cmds!r}."
    )

    # At least one call must set the lock marker variable.
    marker_calls = [cmd for cmd in gh_cmds if "variables" in " ".join(cmd)]
    assert len(marker_calls) >= 1, (
        f"lock_branch must write the lock marker variable. gh calls: {gh_cmds!r}."
    )

    # At least one call must set the branch protection (branch_protection or --method PUT).
    protection_calls = [
        cmd
        for cmd in gh_cmds
        if "branch_protection" in " ".join(cmd) or "protection" in " ".join(cmd)
    ]
    assert len(protection_calls) >= 1, (
        f"lock_branch must engage branch protection. gh calls: {gh_cmds!r}."
    )


@pytest.mark.unit
def test_lock_branch_lock_raises_on_protection_404() -> None:
    """lock_branch must raise when the branch protection rule is not found (B30).

    AC-15 / B30: a missing protection rule is a hard failure -- fail fast.
    """
    call_count = [0]

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        call_count[0] += 1
        # The variable write call succeeds; the protection call returns 404.
        if "protection" in " ".join(cmd) or "branch_protection" in " ".join(cmd):
            return MagicMock(returncode=1, stdout="", stderr="HTTP 422: Not Found (status: 404)")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("scripts.lock_branch.subprocess.run", side_effect=fake_run),
        pytest.raises((RuntimeError, SystemExit)) as exc_info,
    ):
        lock_branch(
            repo="example-org/telemetry-platform",
            branch="main",
            run_id="42",
        )

    if isinstance(exc_info.value, SystemExit):
        assert exc_info.value.code != 0, "B30: missing protection rule must exit non-zero."
    else:
        assert (
            "protection" in str(exc_info.value).lower()
            or "404" in str(exc_info.value)
            or "B30" in str(exc_info.value)
        ), f"RuntimeError must mention protection/404/B30. Got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# unlock_branch -- removes branch protection lock
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_unlock_removes_protection() -> None:
    """unlock_branch must remove the branch protection lock.

    AC-15: --action unlock on a locked branch must disable the lock.
    """
    calls_made: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        cmd_str = " ".join(cmd)
        # Simulate the branch being locked (variable exists).
        if "variables" in cmd_str and "--method" not in cmd_str:
            return MagicMock(
                returncode=0, stdout='{"value": "{\\"run_id\\": \\"42\\"}"}', stderr=""
            )
        # GET branch protection returns a valid existing rule.
        if "protection" in cmd_str and "--method" not in cmd_str:
            return MagicMock(
                returncode=0,
                stdout='{"required_status_checks":null,"enforce_admins":{"enabled":false},'
                '"required_pull_request_reviews":null,"restrictions":null,'
                '"lock_branch":{"enabled":true}}',
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        unlock_branch(
            repo="example-org/telemetry-platform",
            branch="main",
        )

    gh_cmds = [cmd for cmd in calls_made if cmd and cmd[0] == "gh"]
    assert len(gh_cmds) >= 1, f"unlock_branch must make gh API calls. Got: {gh_cmds!r}."


@pytest.mark.unit
def test_lock_branch_unlock_idempotent_when_not_locked() -> None:
    """unlock_branch must be idempotent when the branch is not locked (D47).

    AC-15 / D47: --action unlock on an already-unlocked branch must not error.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        # Simulate: variable does not exist (404) -> branch is not locked.
        if "variables" in " ".join(cmd) and "--method" not in " ".join(cmd):
            return MagicMock(returncode=1, stdout="", stderr="Not Found (status: 404)")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        # Must not raise -- idempotent (D47)
        unlock_branch(
            repo="example-org/telemetry-platform",
            branch="main",
        )


# ---------------------------------------------------------------------------
# write_lock_marker -- persists the run-id and timestamp
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_write_lock_marker_includes_run_id_and_timestamp() -> None:
    """write_lock_marker must persist run_id and locked_at_epoch in the variable.

    AC-15: the lock marker must be a JSON object with 'run_id' and 'locked_at_epoch'.
    """
    import json

    written_values: list[str] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        # Capture the value passed to the gh API call.
        if "--field" in cmd:
            for i, token in enumerate(cmd):
                if token == "--field" and i + 1 < len(cmd):
                    field = cmd[i + 1]
                    if field.startswith("value="):
                        written_values.append(field[len("value=") :])
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        write_lock_marker(
            repo="example-org/telemetry-platform",
            branch="main",
            run_id="99",
        )

    assert len(written_values) >= 1, (
        f"write_lock_marker must write a value to the gh variable API. "
        f"Captured values: {written_values!r}."
    )
    marker_data = json.loads(written_values[0])
    assert "run_id" in marker_data, f"Lock marker must include 'run_id'. Got: {marker_data!r}."
    assert "locked_at_epoch" in marker_data, (
        f"Lock marker must include 'locked_at_epoch'. Got: {marker_data!r}."
    )
    assert marker_data["run_id"] == "99", (
        f"Lock marker run_id must match the provided run_id. Got: {marker_data!r}."
    )


# ---------------------------------------------------------------------------
# CLI dispatch parametrized
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "action,extra_argv",
    [
        pytest.param("lock", ["--run-id", "123"], id="cli-lock-action"),
        pytest.param("unlock", [], id="cli-unlock-action"),
    ],
)
def test_lock_branch_cli_dispatch(action: str, extra_argv: list[str]) -> None:
    """The CLI must dispatch --action lock/unlock to the correct function.

    AC-15: CLI entrypoint correctly routes lock/unlock actions.
    """
    from scripts.lock_branch import main

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        # Simulate: variable not found for unlock check.
        if "variables" in cmd_str and "--method" not in cmd_str:
            return MagicMock(returncode=1, stdout="", stderr="Not Found (status: 404)")
        # GET branch protection (lock path) returns a valid existing rule.
        if "protection" in cmd_str and "--method" not in cmd_str:
            return MagicMock(
                returncode=0,
                stdout='{"required_status_checks":null,"enforce_admins":{"enabled":false},'
                '"required_pull_request_reviews":null,"restrictions":null,'
                '"lock_branch":{"enabled":false}}',
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    argv = [
        "lock_branch",
        "--action",
        action,
        "--repo",
        "example-org/telemetry-platform",
        "--branch",
        "main",
    ] + extra_argv

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run), patch("sys.argv", argv):
        # Should not raise for either action
        main()


# ---------------------------------------------------------------------------
# Additional coverage: write_lock_marker PATCH failure -> POST success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_write_lock_marker_falls_back_to_post() -> None:
    """write_lock_marker must retry with POST when PATCH returns non-zero.

    AC-15: the lock marker creation must succeed even if the variable is new.
    """
    calls_made: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls_made.append(cmd)
        cmd_str = " ".join(cmd)
        if "--method PATCH" in cmd_str or ("--method" in cmd_str and "PATCH" in cmd_str):
            return MagicMock(returncode=1, stdout="", stderr="Not Found")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        write_lock_marker(
            repo="example-org/telemetry-platform",
            branch="main",
            run_id="42",
        )

    methods = [" ".join(cmd) for cmd in calls_made]
    has_patch = any("PATCH" in m for m in methods)
    has_post = any("POST" in m for m in methods)
    assert has_patch and has_post, (
        f"write_lock_marker must try PATCH then POST. Methods: {methods!r}."
    )


@pytest.mark.unit
def test_lock_branch_write_lock_marker_raises_when_both_fail() -> None:
    """write_lock_marker must raise RuntimeError when both PATCH and POST fail.

    AC-15: no silent failures on variable write.
    """
    with patch("scripts.lock_branch.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="forbidden")
        with pytest.raises(RuntimeError) as exc_info:
            write_lock_marker(
                repo="example-org/telemetry-platform",
                branch="main",
                run_id="42",
            )
    assert (
        "lock marker" in str(exc_info.value).lower() or "variable" in str(exc_info.value).lower()
    ), f"RuntimeError must mention lock marker. Got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# unlock_branch raises on unexpected delete failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_unlock_raises_on_delete_failure() -> None:
    """unlock_branch must raise RuntimeError when variable delete fails unexpectedly.

    AC-15: no silent failures on variable delete.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        # Branch is locked (variable found)
        if "variables" in cmd_str and "--method" not in cmd_str:
            return MagicMock(returncode=0, stdout='{"run_id": "42"}', stderr="")
        # DELETE fails with 500
        if "DELETE" in cmd_str:
            return MagicMock(returncode=1, stdout="", stderr="internal server error 500")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("scripts.lock_branch.subprocess.run", side_effect=fake_run),
        pytest.raises(RuntimeError) as exc_info,
    ):
        unlock_branch(
            repo="example-org/telemetry-platform",
            branch="main",
        )

    assert "error" in str(exc_info.value).lower() or "failed" in str(exc_info.value).lower(), (
        f"RuntimeError must mention delete failure. Got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# CLI: missing repo / branch -> exit non-zero
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_cli_exits_nonzero_when_missing_repo() -> None:
    """The CLI must exit non-zero when --repo is not provided.

    AC-15: fail fast on missing required arguments.
    """
    from scripts.lock_branch import main

    with (
        patch("sys.argv", ["lock_branch", "--action", "lock", "--branch", "main", "--run-id", "1"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero when --repo is missing. Got: {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_lock_branch_cli_exits_nonzero_when_missing_run_id() -> None:
    """The CLI must exit non-zero for --action lock when --run-id is missing.

    AC-15: fail fast on missing run-id for lock action.
    """
    from scripts.lock_branch import main

    argv = [
        "lock_branch",
        "--action",
        "lock",
        "--repo",
        "example-org/telemetry-platform",
        "--branch",
        "main",
    ]
    with patch("sys.argv", argv), pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero when --run-id is missing for lock. Got: {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# _enable_branch_protection non-404 error path (line 161)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_enable_protection_raises_on_non_404_error() -> None:
    """_enable_branch_protection must raise on non-404 errors (permission failure).

    AC-15: any API error that is not a missing rule must still raise loudly.
    """
    from scripts.lock_branch import _enable_branch_protection

    with patch("scripts.lock_branch.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="403 Forbidden: insufficient scope"
        )
        with pytest.raises(RuntimeError) as exc_info:
            _enable_branch_protection(
                repo="example-org/telemetry-platform",
                branch="main",
            )

    assert (
        "enable branch protection" in str(exc_info.value).lower()
        or "failed" in str(exc_info.value).lower()
        or "administration" in str(exc_info.value).lower()
    ), f"RuntimeError must mention enable failure. Got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# Regression: the lock PUT must send the COMPLETE protection body (not just
# lock_branch), preserving the existing rule. A bare {lock_branch:true} body is
# rejected by GitHub with HTTP 422 ("required_status_checks wasn't supplied").
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_enable_sends_complete_protection_body() -> None:
    """_enable_branch_protection must PUT the full protection config with lock_branch.

    AC-15 / B30: the lock reads the existing rule (GET) and re-PUTs the complete
    configuration (required_status_checks, enforce_admins,
    required_pull_request_reviews, restrictions) with lock_branch=true; sending
    only lock_branch yields HTTP 422.
    """
    import json as _json

    captured: dict[str, str | None] = {}

    existing_rule = {
        "required_status_checks": {"strict": True, "contexts": ["ci"]},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": None,
        "restrictions": None,
    }

    def fake_run(cmd: list[str], input: str | None = None, **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        if "protection" in cmd_str and "--method" not in cmd_str:
            return MagicMock(returncode=0, stdout=_json.dumps(existing_rule), stderr="")
        if "protection" in cmd_str and "PUT" in cmd_str:
            captured["body"] = input
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        from scripts.lock_branch import _enable_branch_protection

        _enable_branch_protection(repo="example-org/telemetry-platform", branch="main")

    assert captured.get("body"), "lock PUT must receive a JSON request body via stdin."
    body = _json.loads(captured["body"])
    for key in (
        "required_status_checks",
        "enforce_admins",
        "required_pull_request_reviews",
        "restrictions",
    ):
        assert key in body, (
            f"PUT body must include {key!r} (complete config, not just lock_branch). Got: {body!r}"
        )
    assert body["lock_branch"] is True, "lock_branch must be True on lock."
    # Existing settings must be preserved, not wiped.
    assert body["enforce_admins"] is True, "enforce_admins must be preserved."
    assert body["required_status_checks"] == {"strict": True, "contexts": ["ci"]}, (
        "required_status_checks must be preserved."
    )


@pytest.mark.unit
def test_lock_branch_unlock_sends_complete_protection_body_lock_false() -> None:
    """_disable_branch_lock must PUT the full config with lock_branch=false."""
    import json as _json

    captured: dict[str, str | None] = {}

    def fake_run(cmd: list[str], input: str | None = None, **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        if "protection" in cmd_str and "--method" not in cmd_str:
            return MagicMock(
                returncode=0,
                stdout='{"required_status_checks":null,"enforce_admins":{"enabled":false},'
                '"required_pull_request_reviews":null,"restrictions":null}',
                stderr="",
            )
        if "protection" in cmd_str and "PUT" in cmd_str:
            captured["body"] = input
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.lock_branch.subprocess.run", side_effect=fake_run):
        from scripts.lock_branch import _disable_branch_lock

        _disable_branch_lock(repo="example-org/telemetry-platform", branch="main")

    assert captured.get("body"), "unlock PUT must receive a JSON request body via stdin."
    body = _json.loads(captured["body"])
    assert body["lock_branch"] is False, "lock_branch must be False on unlock."
    assert "required_status_checks" in body, "PUT body must be the complete config."


# ---------------------------------------------------------------------------
# _disable_branch_lock error paths (lines 192-200)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "stderr_msg,expected_fragment",
    [
        pytest.param(
            "HTTP 404 Not Found",
            "B30",
            id="disable-protection-404-raises-b30",
        ),
        pytest.param(
            "403 Forbidden: insufficient scope",
            "disable",
            id="disable-protection-non-404-raises-failed",
        ),
    ],
)
def test_lock_branch_disable_protection_raises_on_errors(
    stderr_msg: str, expected_fragment: str
) -> None:
    """_disable_branch_lock must raise RuntimeError on API failures.

    AC-15: no silent failures when disabling branch protection during unlock.
    """
    from scripts.lock_branch import _disable_branch_lock

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        cmd_str = " ".join(cmd)
        is_get_protection = "protection" in cmd_str and "--method" not in cmd_str
        if "404" in stderr_msg:
            # Missing protection rule surfaces on the GET (B30).
            if is_get_protection:
                return MagicMock(returncode=1, stdout="", stderr=stderr_msg)
            return MagicMock(returncode=0, stdout="", stderr="")
        # Protection exists (GET ok); the PUT that disables the lock fails.
        if is_get_protection:
            return MagicMock(
                returncode=0,
                stdout='{"required_status_checks":null,"enforce_admins":{"enabled":false},'
                '"required_pull_request_reviews":null,"restrictions":null}',
                stderr="",
            )
        return MagicMock(returncode=1, stdout="", stderr=stderr_msg)

    with (
        patch("scripts.lock_branch.subprocess.run", side_effect=fake_run),
        pytest.raises(RuntimeError) as exc_info,
    ):
        _disable_branch_lock(
            repo="example-org/telemetry-platform",
            branch="main",
        )

    assert expected_fragment.lower() in str(exc_info.value).lower(), (
        f"RuntimeError must mention {expected_fragment!r}. Got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# _is_branch_locked with null/empty value (line 232)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_is_branch_locked_returns_false_for_null_value() -> None:
    """_is_branch_locked must return False when the variable value is 'null'.

    AC-15: a null variable value must be treated as unlocked.
    """
    from scripts.lock_branch import _is_branch_locked

    with patch("scripts.lock_branch.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="null\n", stderr="")
        result = _is_branch_locked(
            repo="example-org/telemetry-platform",
            branch="main",
        )

    assert result is False, f"_is_branch_locked must return False for null value. Got: {result!r}"


# ---------------------------------------------------------------------------
# CLI missing --branch (lines 351-355)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_cli_exits_nonzero_when_missing_branch() -> None:
    """The CLI must exit non-zero when --branch is not provided.

    AC-15: fail fast on missing required arguments.
    """
    from scripts.lock_branch import main

    missing_branch_argv = [
        "lock_branch",
        "--action",
        "lock",
        "--repo",
        "owner/repo",
        "--run-id",
        "1",
    ]
    with (
        patch("sys.argv", missing_branch_argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero when --branch is missing. Got: {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# CLI RuntimeError -> exit non-zero (lines 372-373)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_lock_branch_cli_exits_nonzero_on_runtime_error() -> None:
    """The CLI must exit non-zero when a RuntimeError is raised during lock.

    AC-15: no silent failures from lock_branch or unlock_branch.
    """
    from scripts.lock_branch import main

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        return MagicMock(returncode=1, stdout="", stderr="500 Internal Server Error")

    argv = [
        "lock_branch",
        "--action",
        "lock",
        "--repo",
        "example-org/telemetry-platform",
        "--branch",
        "main",
        "--run-id",
        "42",
    ]
    with (
        patch("scripts.lock_branch.subprocess.run", side_effect=fake_run),
        patch("sys.argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero on RuntimeError. Got: {exc_info.value.code!r}."
    )
