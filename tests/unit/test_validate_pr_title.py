"""Unit tests for scripts.validate_pr_title.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases cover conventional-commit type-to-bump mapping and D17 API-binding guard.

AC-15:
- valid conventional types map to the correct semver bump
- invalid type raises ValueError
- bump is derived from the API PR title, not the squash subject (D17)
"""

from __future__ import annotations

import pytest

from scripts.validate_pr_title import BumpType, parse_pr_title

# ---------------------------------------------------------------------------
# Type-to-bump mapping parametrize cases
# ---------------------------------------------------------------------------

_VALID_TITLE_CASES = [
    # MINOR_TYPES
    pytest.param("feat: add new telemetry exporter", BumpType.MINOR, id="feat-minor"),
    pytest.param("perf: improve collector throughput", BumpType.MINOR, id="perf-minor"),
    pytest.param("build: update dependencies", BumpType.MINOR, id="build-minor"),
    pytest.param("ci: improve workflow caching", BumpType.MINOR, id="ci-minor"),
    pytest.param("revert: revert bad commit", BumpType.MINOR, id="revert-minor"),
    pytest.param("release: v1.2.3 module", BumpType.MINOR, id="release-minor"),
    pytest.param("meta: update monorepo config", BumpType.MINOR, id="meta-minor"),
    pytest.param("module: add kms-key primitive", BumpType.MINOR, id="module-minor"),
    # PATCH_TYPES
    pytest.param("fix: correct ARN construction", BumpType.PATCH, id="fix-patch"),
    pytest.param("chore: update tool versions", BumpType.PATCH, id="chore-patch"),
    pytest.param("docs: update README", BumpType.PATCH, id="docs-patch"),
    pytest.param("style: fix formatting", BumpType.PATCH, id="style-patch"),
    pytest.param("refactor: extract shared helper", BumpType.PATCH, id="refactor-patch"),
    pytest.param("test: add unit tests", BumpType.PATCH, id="test-patch"),
    # BREAKING CHANGE via !
    pytest.param("feat!: redesign telemetry API", BumpType.MAJOR, id="feat-bang-major"),
    pytest.param("fix!: change output format", BumpType.MAJOR, id="fix-bang-major"),
    pytest.param("chore!: drop python 3.11 support", BumpType.MAJOR, id="chore-bang-major"),
    # With optional scope
    pytest.param("feat(kms-key): add key rotation", BumpType.MINOR, id="feat-scope-minor"),
    pytest.param("fix(s3-bucket): correct bucket policy", BumpType.PATCH, id="fix-scope-patch"),
    pytest.param("feat(vpc-network)!: rename outputs", BumpType.MAJOR, id="feat-scope-bang-major"),
]


@pytest.mark.unit
@pytest.mark.parametrize("title,expected_bump", _VALID_TITLE_CASES)
def test_validate_pr_title_mapping(title: str, expected_bump: BumpType) -> None:
    """parse_pr_title maps each conventional-commit type to the correct semver bump.

    AC-15: validate_pr_title must return the correct BumpType for every type.
    """
    result = parse_pr_title(title)
    assert result.bump == expected_bump, (
        f"Expected bump={expected_bump.value!r} for title={title!r}, "
        f"got bump={result.bump.value!r}. "
        "parse_pr_title must map commit types per the MINOR_TYPES/PATCH_TYPES/! rules."
    )
    assert result.commit_type is not None, (
        f"parse_pr_title must set commit_type for title={title!r}."
    )


# ---------------------------------------------------------------------------
# Invalid title cases
# ---------------------------------------------------------------------------

_INVALID_TITLE_CASES = [
    pytest.param("add new feature", id="no-type-prefix"),
    pytest.param("WIP: work in progress", id="unknown-type-wip"),
    pytest.param("update: some update", id="unknown-type-update"),
    pytest.param("", id="empty-title"),
    pytest.param("feat", id="type-only-no-colon"),
    pytest.param(": no type", id="empty-type"),
    pytest.param("FEAT: uppercase type", id="uppercase-type"),
]


@pytest.mark.unit
@pytest.mark.parametrize("title", _INVALID_TITLE_CASES)
def test_validate_pr_title_invalid_raises(title: str) -> None:
    """parse_pr_title must raise ValueError for invalid conventional-commit titles.

    Fail-fast: an invalid PR title must be caught immediately, not silently accepted.
    """
    with pytest.raises(ValueError) as exc_info:
        parse_pr_title(title)

    assert len(str(exc_info.value)) > 0, (
        f"ValueError for invalid title={title!r} must have a non-empty message."
    )


# ---------------------------------------------------------------------------
# D17: bump derived from GitHub API PR title, not squash subject
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_pr_title_d17_api_title_is_source_of_truth() -> None:
    """parse_pr_title derives the bump from the title argument, not a squash subject.

    D17: the bump type is bound to the API-fetched PR title. When the PR title
    says 'feat:' (minor) and the squash subject says 'feat!:' (major), the
    bump must be derived from the PR title (minor), proving the caller must
    supply the API-fetched title -- not the squash subject -- to get the
    authoritative bump.

    This test verifies the function is pure over its title argument, which
    is the D17 contract: the caller (ci_calculate_version) is responsible
    for fetching the title from the GitHub API.
    """
    pr_title = "feat: add new telemetry dashboard"
    squash_subject = "feat!: add new telemetry dashboard"

    result_from_api_title = parse_pr_title(pr_title)
    result_from_squash = parse_pr_title(squash_subject)

    # PR title -> minor bump
    assert result_from_api_title.bump == BumpType.MINOR, (
        f"API PR title {pr_title!r} must produce MINOR bump, "
        f"got {result_from_api_title.bump.value!r}."
    )
    # Squash subject -> major bump
    assert result_from_squash.bump == BumpType.MAJOR, (
        f"Squash subject {squash_subject!r} must produce MAJOR bump (to contrast), "
        f"got {result_from_squash.bump.value!r}."
    )
    # Prove they differ -- passing the squash subject would yield wrong bump
    assert result_from_api_title.bump != result_from_squash.bump, (
        "API-fetched PR title and hand-edited squash subject must yield different bumps, "
        "demonstrating that the bump source matters (D17)."
    )


@pytest.mark.unit
def test_validate_pr_title_d17_matching_titles_agree() -> None:
    """When API PR title and squash subject agree on type, bumps match.

    D17: if a merger did NOT edit the squash subject, both parses agree.
    This is the passing case that ci_calculate_version validates.
    """
    matching_title = "feat!: redesign collector API"

    result_api = parse_pr_title(matching_title)
    result_squash = parse_pr_title(matching_title)

    assert result_api.bump == BumpType.MAJOR, (
        f"'feat!' title must produce MAJOR bump, got {result_api.bump.value!r}."
    )
    assert result_api.bump == result_squash.bump, (
        "When PR title and squash subject are identical, bumps must agree."
    )


# ---------------------------------------------------------------------------
# main() direct call coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_pr_title_main_valid_title_prints_bump() -> None:
    """main() prints the bump type for a valid title."""
    import io
    from unittest.mock import patch

    from scripts.validate_pr_title import main

    captured = io.StringIO()

    with (
        patch("sys.argv", ["validate_pr_title", "--title", "feat: add exporter"]),
        patch("sys.stdout", captured),
    ):
        main()

    output = captured.getvalue()
    assert "bump=minor" in output, (
        f"main() must print 'bump=minor' for 'feat:' title. Got: {output!r}"
    )


@pytest.mark.unit
def test_validate_pr_title_main_invalid_title_exits_nonzero() -> None:
    """main() exits non-zero for an invalid title."""
    import io
    from unittest.mock import patch

    from scripts.validate_pr_title import main

    with (
        patch("sys.argv", ["validate_pr_title", "--title", "invalid title"]),
        patch("sys.stderr", io.StringIO()),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    assert exc_info.value.code == 1, (
        f"main() must exit with code 1 for invalid title. Got: {exc_info.value.code!r}"
    )


@pytest.mark.unit
def test_validate_pr_title_main_breaking_title_prints_major() -> None:
    """main() prints bump=major for a breaking change title."""
    import io
    from unittest.mock import patch

    from scripts.validate_pr_title import main

    captured = io.StringIO()

    with (
        patch("sys.argv", ["validate_pr_title", "--title", "feat!: redesign API"]),
        patch("sys.stdout", captured),
    ):
        main()

    output = captured.getvalue()
    assert "bump=major" in output, (
        f"main() must print 'bump=major' for 'feat!' title. Got: {output!r}"
    )


@pytest.mark.unit
def test_validate_pr_title_main_scope_title_prints_scope() -> None:
    """main() prints the scope when present in the title."""
    import io
    from unittest.mock import patch

    from scripts.validate_pr_title import main

    captured = io.StringIO()

    with (
        patch(
            "sys.argv",
            ["validate_pr_title", "--title", "fix(kms-key): correct ARN"],
        ),
        patch("sys.stdout", captured),
    ):
        main()

    output = captured.getvalue()
    assert "scope=kms-key" in output, (
        f"main() must print 'scope=kms-key' for scoped title. Got: {output!r}"
    )


# ---------------------------------------------------------------------------
# CLI handler (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_pr_title_cli_exits_zero_on_valid_title(tmp_path) -> None:
    """The CLI handler must exit 0 when the PR title is valid."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[2])

    result = subprocess.run(
        [sys.executable, "-m", "scripts.validate_pr_title", "--title", "feat: add exporter"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0, (
        f"validate_pr_title CLI must exit 0 for a valid title. "
        f"Got returncode={result.returncode}, stderr={result.stderr!r}."
    )


@pytest.mark.unit
def test_validate_pr_title_cli_exits_nonzero_on_invalid_title(tmp_path) -> None:
    """The CLI handler must exit non-zero when the PR title is invalid."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parents[2])

    result = subprocess.run(
        [sys.executable, "-m", "scripts.validate_pr_title", "--title", "add some thing"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode != 0, (
        f"validate_pr_title CLI must exit non-zero for an invalid title. "
        f"Got returncode={result.returncode}."
    )
