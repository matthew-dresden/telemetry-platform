"""Unit tests for scripts.check_merged_pr_override -- no-PR-payload scope=all override.

One test_<script> module per script (docs/release-pipeline.md).

Two events lack a PR event payload and resolve the ``detect-scope-override`` label + author
of the relevant PR via the GitHub API:

Push (post-merge) path -- resolves the merged PR from the commit SHA:
- no associated PR (direct push)        -> scope_override=false
- PR present, label absent              -> scope_override=false
- PR present, label + admin author      -> scope_override=true
- PR present, label + non-admin author  -> AuthorizationError (exit 1)
- PR present, label + missing author    -> MergedPrLookupError (exit 1)
- gh api failure                        -> MergedPrLookupError (exit 1)

Merge-queue (merge_group) path -- resolves the queued PR from the head ref's embedded number:
- well-formed ref parses the PR number  -> PR resolved via repos/{repo}/pulls/{number}
- malformed ref (no PR number)          -> MergedPrLookupError (exit 1, never silent false)
- queued PR label + admin author        -> scope_override=true
- queued PR label absent                -> scope_override=false
- queued PR label + non-admin author    -> AuthorizationError (exit 1)
- gh api returns no PR object           -> MergedPrLookupError (exit 1)

AC-15 / D43: an admin scope-override PR must validate as scope=all on the push AND
merge_group paths too, and a non-admin must never widen the build scope (fail-closed).
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from scripts.check_merged_pr_override import (
    MergedPrLookupError,
    extract_author,
    extract_label_names,
    fetch_merged_pr,
    fetch_pr_by_number,
    parse_pr_number_from_merge_group_ref,
    resolve_merge_group_scope_override,
    resolve_push_scope_override,
)
from scripts.check_scope_override import AuthorizationError
from scripts.constants import OUTPUT_KEY_SCOPE_OVERRIDE, SCOPE_OVERRIDE_LABEL

_REPO = "example-org/telemetry-platform"
_ORG = "example-org"
_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _pr(labels: list[str] | None = None, author: str | None = "admin-user") -> dict:
    """Build a minimal commits/{sha}/pulls PR object for tests."""
    pr: dict = {}
    if labels is not None:
        pr["labels"] = [{"name": name} for name in labels]
    if author is not None:
        pr["user"] = {"login": author}
    return pr


# ---------------------------------------------------------------------------
# extract_label_names / extract_author -- shape parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "pr,expected",
    [
        pytest.param(
            {"labels": [{"name": SCOPE_OVERRIDE_LABEL}, {"name": "bug"}]},
            [SCOPE_OVERRIDE_LABEL, "bug"],
            id="two-labels",
        ),
        pytest.param({"labels": []}, [], id="empty-labels"),
        pytest.param({}, [], id="no-labels-key"),
        pytest.param(
            {"labels": [{"name": "  "}, {"name": "keep"}, {"noname": "x"}]},
            ["keep"],
            id="blank-and-malformed-skipped",
        ),
    ],
)
def test_extract_label_names(pr: dict, expected: list[str]) -> None:
    """extract_label_names returns the clean, non-empty label-name list."""
    assert extract_label_names(pr) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "pr,expected",
    [
        pytest.param({"user": {"login": "octocat"}}, "octocat", id="present"),
        pytest.param({"user": {"login": "  spaced  "}}, "spaced", id="stripped"),
        pytest.param({}, None, id="no-user"),
        pytest.param({"user": {}}, None, id="no-login"),
        pytest.param({"user": {"login": ""}}, None, id="empty-login"),
    ],
)
def test_extract_author(pr: dict, expected: str | None) -> None:
    """extract_author returns the PR author login, or None when absent/blank."""
    assert extract_author(pr) == expected


# ---------------------------------------------------------------------------
# fetch_merged_pr -- API shape + failure handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fetch_merged_pr_returns_first_pr() -> None:
    """fetch_merged_pr returns the first PR object from a non-empty array."""
    mock_result = MagicMock()
    mock_result.stdout = '[{"number": 104, "labels": [{"name": "detect-scope-override"}]}]'
    mock_result.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch("subprocess.run", return_value=mock_result),
    ):
        pr = fetch_merged_pr(repo=_REPO, commit_sha=_SHA)

    assert pr is not None
    assert pr["number"] == 104


@pytest.mark.unit
@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("[]", id="empty-array"),
        pytest.param("", id="empty-output"),
    ],
)
def test_fetch_merged_pr_returns_none_when_no_pr(stdout: str) -> None:
    """A commit with no associated PR yields None (a direct push is never an override)."""
    mock_result = MagicMock()
    mock_result.stdout = stdout
    mock_result.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch("subprocess.run", return_value=mock_result),
    ):
        assert fetch_merged_pr(repo=_REPO, commit_sha=_SHA) is None


@pytest.mark.unit
def test_fetch_merged_pr_raises_when_gh_missing() -> None:
    """A missing gh binary fails fast (fail-closed), never silently returning None."""
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(MergedPrLookupError),
    ):
        fetch_merged_pr(repo=_REPO, commit_sha=_SHA)


@pytest.mark.unit
def test_fetch_merged_pr_raises_on_api_failure() -> None:
    """A failing gh api call fails fast (fail-closed)."""
    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh", stderr="boom"),
        ),
        pytest.raises(MergedPrLookupError),
    ):
        fetch_merged_pr(repo=_REPO, commit_sha=_SHA)


# ---------------------------------------------------------------------------
# resolve_push_scope_override -- end-to-end branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_no_pr_writes_false(tmp_path) -> None:
    """No associated PR -> scope_override=false written, returns False."""
    output_file = tmp_path / "output.txt"
    with patch("scripts.check_merged_pr_override.fetch_merged_pr", return_value=None):
        result = resolve_push_scope_override(
            commit_sha=_SHA, repo=_REPO, org=_ORG, output_path=str(output_file)
        )
    assert result is False
    assert f"{OUTPUT_KEY_SCOPE_OVERRIDE}=false" in output_file.read_text()


@pytest.mark.unit
def test_resolve_label_absent_writes_false(tmp_path) -> None:
    """PR present without the override label -> scope_override=false."""
    output_file = tmp_path / "output.txt"
    with patch(
        "scripts.check_merged_pr_override.fetch_merged_pr",
        return_value=_pr(labels=["bug", "needs-review"]),
    ):
        result = resolve_push_scope_override(
            commit_sha=_SHA, repo=_REPO, org=_ORG, output_path=str(output_file)
        )
    assert result is False
    assert f"{OUTPUT_KEY_SCOPE_OVERRIDE}=false" in output_file.read_text()


@pytest.mark.unit
def test_resolve_label_present_admin_writes_true(tmp_path) -> None:
    """Override label + admin author -> scope_override=true (override honoured on push)."""
    output_file = tmp_path / "output.txt"
    with (
        patch(
            "scripts.check_merged_pr_override.fetch_merged_pr",
            return_value=_pr(labels=[SCOPE_OVERRIDE_LABEL], author="admin-user"),
        ),
        patch("scripts.check_merged_pr_override.is_admin_member", return_value=True) as mock_admin,
    ):
        result = resolve_push_scope_override(
            commit_sha=_SHA, repo=_REPO, org=_ORG, output_path=str(output_file)
        )
    assert result is True
    assert f"{OUTPUT_KEY_SCOPE_OVERRIDE}=true" in output_file.read_text()
    mock_admin.assert_called_once_with(org=_ORG, author="admin-user")


@pytest.mark.unit
def test_resolve_label_present_non_admin_raises(tmp_path) -> None:
    """Override label + non-admin author -> AuthorizationError (fail-closed, no widening)."""
    output_file = tmp_path / "output.txt"
    with (
        patch(
            "scripts.check_merged_pr_override.fetch_merged_pr",
            return_value=_pr(labels=[SCOPE_OVERRIDE_LABEL], author="random-user"),
        ),
        patch("scripts.check_merged_pr_override.is_admin_member", return_value=False),
        pytest.raises(AuthorizationError),
    ):
        resolve_push_scope_override(
            commit_sha=_SHA, repo=_REPO, org=_ORG, output_path=str(output_file)
        )


@pytest.mark.unit
def test_resolve_label_present_missing_author_raises(tmp_path) -> None:
    """Override label but no resolvable author -> MergedPrLookupError (cannot fail-closed-auth)."""
    output_file = tmp_path / "output.txt"
    with (
        patch(
            "scripts.check_merged_pr_override.fetch_merged_pr",
            return_value=_pr(labels=[SCOPE_OVERRIDE_LABEL], author=None),
        ),
        pytest.raises(MergedPrLookupError),
    ):
        resolve_push_scope_override(
            commit_sha=_SHA, repo=_REPO, org=_ORG, output_path=str(output_file)
        )


# ---------------------------------------------------------------------------
# parse_pr_number_from_merge_group_ref -- merge-queue head-ref parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "ref,expected",
    [
        pytest.param("gh-readonly-queue/main/pr-161-1edfc05ab", 161, id="bare-queue-ref"),
        pytest.param(
            "refs/heads/gh-readonly-queue/main/pr-42-deadbeef0123",
            42,
            id="refs-heads-prefixed",
        ),
        pytest.param(
            "gh-readonly-queue/release-1.x/pr-7-abcdef0",
            7,
            id="hyphenated-base-branch",
        ),
        pytest.param("  gh-readonly-queue/main/pr-205-1234abc  ", 205, id="surrounding-whitespace"),
    ],
)
def test_parse_pr_number_from_merge_group_ref_valid(ref: str, expected: int) -> None:
    """The queued PR number is extracted from the merge_group head ref's embedded pr-<n>-<sha>."""
    assert parse_pr_number_from_merge_group_ref(ref) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("refs/heads/main", id="plain-branch-no-pr"),
        pytest.param("gh-readonly-queue/main/pr-161", id="missing-sha-suffix"),
        pytest.param("gh-readonly-queue/main/pr-abc-1234abc", id="non-numeric-pr"),
        pytest.param("", id="empty-ref"),
    ],
)
def test_parse_pr_number_from_merge_group_ref_malformed_raises(ref: str) -> None:
    """A ref with no parseable PR number fails fast (fail-closed), never a silent no-override."""
    with pytest.raises(MergedPrLookupError):
        parse_pr_number_from_merge_group_ref(ref)


# ---------------------------------------------------------------------------
# fetch_pr_by_number -- single-PR API shape + failure handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fetch_pr_by_number_returns_object() -> None:
    """fetch_pr_by_number returns the single PR object from the pulls/{number} endpoint."""
    mock_result = MagicMock()
    mock_result.stdout = '{"number": 161, "labels": [{"name": "detect-scope-override"}]}'
    mock_result.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch("subprocess.run", return_value=mock_result),
    ):
        pr = fetch_pr_by_number(repo=_REPO, pr_number=161)

    assert pr is not None
    assert pr["number"] == 161


@pytest.mark.unit
def test_fetch_pr_by_number_returns_none_on_empty() -> None:
    """An empty API response yields None (handled fail-closed by the caller)."""
    mock_result = MagicMock()
    mock_result.stdout = ""
    mock_result.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch("subprocess.run", return_value=mock_result),
    ):
        assert fetch_pr_by_number(repo=_REPO, pr_number=161) is None


@pytest.mark.unit
def test_fetch_pr_by_number_raises_when_gh_missing() -> None:
    """A missing gh binary fails fast (fail-closed)."""
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(MergedPrLookupError),
    ):
        fetch_pr_by_number(repo=_REPO, pr_number=161)


@pytest.mark.unit
def test_fetch_pr_by_number_raises_on_api_failure() -> None:
    """A failing gh api call fails fast (fail-closed)."""
    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh", stderr="boom"),
        ),
        pytest.raises(MergedPrLookupError),
    ):
        fetch_pr_by_number(repo=_REPO, pr_number=161)


@pytest.mark.unit
def test_fetch_pr_by_number_raises_on_non_dict() -> None:
    """A non-object JSON response is a shape error and fails fast (fail-closed)."""
    mock_result = MagicMock()
    mock_result.stdout = "[]"
    mock_result.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch("subprocess.run", return_value=mock_result),
        pytest.raises(MergedPrLookupError),
    ):
        fetch_pr_by_number(repo=_REPO, pr_number=161)


# ---------------------------------------------------------------------------
# resolve_merge_group_scope_override -- end-to-end branch coverage
# ---------------------------------------------------------------------------

_MQ_REF = "gh-readonly-queue/main/pr-161-1edfc05ab"


@pytest.mark.unit
def test_resolve_mq_label_present_admin_writes_true(tmp_path) -> None:
    """Queued PR with override label + admin author -> scope_override=true."""
    output_file = tmp_path / "output.txt"
    with (
        patch(
            "scripts.check_merged_pr_override.fetch_pr_by_number",
            return_value=_pr(labels=[SCOPE_OVERRIDE_LABEL], author="admin-user"),
        ) as mock_fetch,
        patch("scripts.check_merged_pr_override.is_admin_member", return_value=True) as mock_admin,
    ):
        result = resolve_merge_group_scope_override(
            merge_group_ref=_MQ_REF, repo=_REPO, org=_ORG, output_path=str(output_file)
        )
    assert result is True
    assert f"{OUTPUT_KEY_SCOPE_OVERRIDE}=true" in output_file.read_text()
    mock_fetch.assert_called_once_with(repo=_REPO, pr_number=161)
    mock_admin.assert_called_once_with(org=_ORG, author="admin-user")


@pytest.mark.unit
def test_resolve_mq_label_absent_writes_false(tmp_path) -> None:
    """Queued PR without the override label -> scope_override=false (normal single-scope)."""
    output_file = tmp_path / "output.txt"
    with patch(
        "scripts.check_merged_pr_override.fetch_pr_by_number",
        return_value=_pr(labels=["bug"], author="anyone"),
    ):
        result = resolve_merge_group_scope_override(
            merge_group_ref=_MQ_REF, repo=_REPO, org=_ORG, output_path=str(output_file)
        )
    assert result is False
    assert f"{OUTPUT_KEY_SCOPE_OVERRIDE}=false" in output_file.read_text()


@pytest.mark.unit
def test_resolve_mq_label_present_non_admin_raises(tmp_path) -> None:
    """Queued PR with override label + non-admin author -> AuthorizationError (no widening)."""
    output_file = tmp_path / "output.txt"
    with (
        patch(
            "scripts.check_merged_pr_override.fetch_pr_by_number",
            return_value=_pr(labels=[SCOPE_OVERRIDE_LABEL], author="random-user"),
        ),
        patch("scripts.check_merged_pr_override.is_admin_member", return_value=False),
        pytest.raises(AuthorizationError),
    ):
        resolve_merge_group_scope_override(
            merge_group_ref=_MQ_REF, repo=_REPO, org=_ORG, output_path=str(output_file)
        )


@pytest.mark.unit
def test_resolve_mq_no_pr_object_raises(tmp_path) -> None:
    """The PR number came from the queue ref, so an empty PR response is fail-closed."""
    output_file = tmp_path / "output.txt"
    with (
        patch("scripts.check_merged_pr_override.fetch_pr_by_number", return_value=None),
        pytest.raises(MergedPrLookupError),
    ):
        resolve_merge_group_scope_override(
            merge_group_ref=_MQ_REF, repo=_REPO, org=_ORG, output_path=str(output_file)
        )


@pytest.mark.unit
def test_resolve_mq_malformed_ref_raises(tmp_path) -> None:
    """A malformed merge_group ref fails fast before any API call (fail-closed)."""
    output_file = tmp_path / "output.txt"
    with (
        patch("scripts.check_merged_pr_override.fetch_pr_by_number") as mock_fetch,
        pytest.raises(MergedPrLookupError),
    ):
        resolve_merge_group_scope_override(
            merge_group_ref="refs/heads/main", repo=_REPO, org=_ORG, output_path=str(output_file)
        )
    mock_fetch.assert_not_called()
