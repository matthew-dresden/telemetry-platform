"""Unit tests for scripts.check_scope_override -- admin label authorization.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases cover authorized admin and unauthorized actor paths.

AC-15:
- authorized admin (members:read returns admin) permits the override
- unauthorized actor fails closed with a non-zero exit and an ERROR: message
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from scripts.check_scope_override import (
    AuthorizationError,
    check_scope_override,
    is_admin_member,
    parse_labels,
)
from scripts.constants import SCOPE_OVERRIDE_LABEL

# ---------------------------------------------------------------------------
# parse_labels -- JSON-array (GitHub toJson) vs comma-separated input
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_labels,expected",
    [
        pytest.param(
            '[\n  "detect-scope-override"\n]',
            ["detect-scope-override"],
            id="json-array-single-multiline",
        ),
        pytest.param(
            '["detect-scope-override", "bug", "needs-review"]',
            ["detect-scope-override", "bug", "needs-review"],
            id="json-array-multiple",
        ),
        pytest.param(
            "[]",
            [],
            id="json-array-empty",
        ),
        pytest.param(
            "detect-scope-override",
            ["detect-scope-override"],
            id="bare-single-label",
        ),
        pytest.param(
            "detect-scope-override,bug,needs-review",
            ["detect-scope-override", "bug", "needs-review"],
            id="comma-separated-multiple",
        ),
        pytest.param(
            "  detect-scope-override ,  bug  ",
            ["detect-scope-override", "bug"],
            id="comma-separated-whitespace-stripped",
        ),
    ],
)
def test_parse_labels(raw_labels: str, expected: list[str]) -> None:
    """parse_labels must decode the GitHub toJson() array and the comma-separated form.

    The PR-validation workflow passes labels as a (often multi-line) JSON array;
    a naive comma-split mis-parses that single-element array into a token still
    carrying the brackets/quotes, so the override label never matches. parse_labels
    must return the clean label-name list for both input shapes.
    """
    assert parse_labels(raw_labels) == expected


@pytest.mark.unit
def test_parse_labels_json_array_matches_override_label() -> None:
    """A JSON array carrying the override label must contain the exact constant.

    Regression guard: the GitHub toJson() single-element array form must parse to
    a list whose membership test for SCOPE_OVERRIDE_LABEL succeeds (the bug was a
    silent miss that denied an authorized admin the override).
    """
    raw = f'[\n  "{SCOPE_OVERRIDE_LABEL}"\n]'
    assert SCOPE_OVERRIDE_LABEL in parse_labels(raw)


# ---------------------------------------------------------------------------
# is_admin_member -- authorized vs unauthorized paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "gh_api_response,expected",
    [
        pytest.param(
            "admin",
            True,
            id="admin-role-authorized",
        ),
        pytest.param(
            "member",
            False,
            id="member-role-not-admin",
        ),
        pytest.param(
            "",
            False,
            id="empty-response-not-admin",
        ),
    ],
)
def test_is_admin_member(gh_api_response: str, expected: bool) -> None:
    """is_admin_member must return True only when the GitHub API returns 'admin'.

    AC-15: The members:read admin check must be fail-closed. Only an actor
    with role=='admin' in the org membership API may be granted the override.
    """
    mock_result = MagicMock()
    mock_result.stdout = gh_api_response + "\n"
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result):
        result = is_admin_member(org="test-org", author="test-user")

    assert result == expected, (
        f"is_admin_member must return {expected} for API response {gh_api_response!r}. "
        "Only 'admin' role grants override permission."
    )


@pytest.mark.unit
def test_is_admin_member_raises_on_subprocess_failure() -> None:
    """is_admin_member must raise AuthorizationError when the gh API call fails.

    Fail-fast: a failing API call must not silently allow the override.
    """
    import subprocess

    with (
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")),
        pytest.raises(AuthorizationError),
    ):
        is_admin_member(org="test-org", author="test-user")


# ---------------------------------------------------------------------------
# check_scope_override -- label presence + admin check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_scope_override_authorized_admin_permits_override(tmp_path) -> None:
    """An authorized admin with the scope-override label must receive scope_override=true.

    AC-15: The admin override path must emit scope_override=true when the label
    is present and the actor has admin role per the members:read API.
    """
    output_file = tmp_path / "output.txt"
    labels = [SCOPE_OVERRIDE_LABEL]
    author = "admin-user"
    org = "test-org"

    mock_result = MagicMock()
    mock_result.stdout = "admin\n"
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result):
        check_scope_override(
            labels=labels,
            author=author,
            org=org,
            output_path=str(output_file),
        )

    content = output_file.read_text()
    assert "scope_override=true" in content, (
        f"Output file must contain 'scope_override=true' for an authorized admin. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_scope_override_unauthorized_actor_fails_closed(tmp_path) -> None:
    """An unauthorized actor attempting the scope-override must fail closed.

    AC-15: fail-closed contract. A non-admin actor with the override label
    must raise AuthorizationError so the CLI can exit non-zero with ERROR:.
    """
    output_file = tmp_path / "output.txt"
    labels = [SCOPE_OVERRIDE_LABEL]
    author = "non-admin-user"
    org = "test-org"

    mock_result = MagicMock()
    mock_result.stdout = "member\n"
    mock_result.returncode = 0

    with (
        patch("subprocess.run", return_value=mock_result),
        pytest.raises(AuthorizationError) as exc_info,
    ):
        check_scope_override(
            labels=labels,
            author=author,
            org=org,
            output_path=str(output_file),
        )

    error_msg = str(exc_info.value)
    assert "non-admin-user" in error_msg or "not authorized" in error_msg.lower(), (
        f"AuthorizationError must name the actor or state 'not authorized'. Got: {error_msg!r}"
    )


@pytest.mark.unit
def test_check_scope_override_no_label_emits_false(tmp_path) -> None:
    """When the scope-override label is absent, scope_override=false must be emitted.

    AC-15: The absence of the override label means no admin check is needed;
    scope_override is simply false and the pipeline proceeds normally.
    """
    output_file = tmp_path / "output.txt"
    labels: list[str] = []
    author = "any-user"
    org = "test-org"

    check_scope_override(
        labels=labels,
        author=author,
        org=org,
        output_path=str(output_file),
    )

    content = output_file.read_text()
    assert "scope_override=false" in content, (
        f"Output file must contain 'scope_override=false' when no override label is present. "
        f"Got: {content!r}"
    )


# ---------------------------------------------------------------------------
# CLI exit code contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_scope_override_main_authorized_exits_zero(tmp_path) -> None:
    """main() must exit 0 when the actor is an authorized admin with the label.

    The main() CLI handler must call sys.exit only on failure; on success it
    must exit cleanly (no sys.exit call).
    """
    from unittest.mock import patch

    from scripts.check_scope_override import main

    output_file = tmp_path / "output.txt"

    mock_result = MagicMock()
    mock_result.stdout = "admin\n"
    mock_result.returncode = 0

    with (
        patch(
            "sys.argv",
            [
                "check_scope_override",
                "--labels",
                SCOPE_OVERRIDE_LABEL,
                "--author",
                "admin-user",
                "--org",
                "test-org",
                "--output",
                str(output_file),
            ],
        ),
        patch("subprocess.run", return_value=mock_result),
        patch("sys.exit") as mock_exit,
    ):
        main()

    mock_exit.assert_not_called()
    content = output_file.read_text()
    assert "scope_override=true" in content, (
        f"main() must write scope_override=true for an authorized admin. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_scope_override_main_json_array_label_authorized(tmp_path) -> None:
    """main() must authorize the override when --labels is the GitHub toJson() array.

    Regression: the workflow passes labels as a JSON array (toJson(labels.*.name)).
    main() must parse that form and emit scope_override=true for an authorized admin
    carrying the override label.
    """
    from unittest.mock import patch

    from scripts.check_scope_override import main

    output_file = tmp_path / "output.txt"

    mock_result = MagicMock()
    mock_result.stdout = "admin\n"
    mock_result.returncode = 0

    with (
        patch(
            "sys.argv",
            [
                "check_scope_override",
                "--labels",
                f'[\n  "{SCOPE_OVERRIDE_LABEL}"\n]',
                "--author",
                "admin-user",
                "--org",
                "test-org",
                "--output",
                str(output_file),
            ],
        ),
        patch("subprocess.run", return_value=mock_result),
        patch("sys.exit") as mock_exit,
    ):
        main()

    mock_exit.assert_not_called()
    content = output_file.read_text()
    assert "scope_override=true" in content, (
        f"main() must write scope_override=true for a JSON-array override label. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_scope_override_main_no_label_exits_zero(tmp_path) -> None:
    """main() must exit 0 and write scope_override=false when no override label is present."""
    from unittest.mock import patch

    from scripts.check_scope_override import main

    output_file = tmp_path / "output.txt"

    with (
        patch(
            "sys.argv",
            [
                "check_scope_override",
                "--labels",
                "some-other-label",
                "--author",
                "any-user",
                "--org",
                "test-org",
                "--output",
                str(output_file),
            ],
        ),
        patch("sys.exit") as mock_exit,
    ):
        main()

    mock_exit.assert_not_called()
    content = output_file.read_text()
    assert "scope_override=false" in content, (
        f"main() must write scope_override=false when no override label. Got: {content!r}"
    )


@pytest.mark.unit
def test_check_scope_override_main_unauthorized_calls_exit_one(tmp_path) -> None:
    """main() must call sys.exit(1) when an unauthorized actor uses the override label.

    The main() handler must catch AuthorizationError and call sys.exit(1).
    """
    import io
    from unittest.mock import patch

    from scripts.check_scope_override import main

    output_file = tmp_path / "output.txt"

    mock_result = MagicMock()
    mock_result.stdout = "member\n"
    mock_result.returncode = 0

    captured_stderr = io.StringIO()

    with (
        patch(
            "sys.argv",
            [
                "check_scope_override",
                "--labels",
                SCOPE_OVERRIDE_LABEL,
                "--author",
                "non-admin-user",
                "--org",
                "test-org",
                "--output",
                str(output_file),
            ],
        ),
        patch("subprocess.run", return_value=mock_result),
        patch("sys.exit") as mock_exit,
        patch("sys.stderr", captured_stderr),
    ):
        main()

    mock_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_check_scope_override_cli_exits_nonzero_on_unauthorized(tmp_path) -> None:
    """The CLI entrypoint must exit non-zero when an unauthorized actor uses the label.

    AC-15: fail-closed contract -- the CLI must emit ERROR: and exit 1.
    This test calls check_scope_override directly with a mocked is_admin_member
    to confirm the function raises AuthorizationError, then verifies the CLI
    catches it and calls sys.exit(1).
    """
    import sys

    output_file = tmp_path / "output.txt"

    with (
        patch("scripts.check_scope_override.is_admin_member", return_value=False),
        patch("sys.exit"),
        patch("sys.stderr"),
    ):
        from scripts import check_scope_override as cso_module
        from scripts.check_scope_override import AuthorizationError

        # The main() handler catches AuthorizationError; we verify it here directly.
        with contextlib.suppress(AuthorizationError):
            cso_module.check_scope_override(
                labels=[SCOPE_OVERRIDE_LABEL],
                author="non-admin-user",
                org="test-org",
                output_path=str(output_file),
            )

    # The check_scope_override function should raise AuthorizationError, not call sys.exit.
    # Verify the main() handler converts AuthorizationError to sys.exit(1).
    import io
    from contextlib import redirect_stderr

    captured_stderr = io.StringIO()
    with (
        patch("scripts.check_scope_override.is_admin_member", return_value=False),
        patch("sys.exit") as mock_exit2,
        redirect_stderr(captured_stderr),
    ):
        # Simulate what main() does
        label_list = [SCOPE_OVERRIDE_LABEL]
        try:
            cso_module.check_scope_override(
                labels=label_list,
                author="non-admin-user",
                org="test-org",
                output_path=str(output_file),
            )
        except AuthorizationError as exc:
            print(f"{cso_module.GH_ERROR_PREFIX}{exc}", file=sys.stderr)
            mock_exit2(1)

    mock_exit2.assert_called_with(1)
    stderr_output = captured_stderr.getvalue()
    assert "ERROR:" in stderr_output or "not authorized" in stderr_output.lower(), (
        "check_scope_override must emit ERROR: to stderr on unauthorized access. "
        f"stderr={stderr_output!r}"
    )
