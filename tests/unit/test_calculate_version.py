"""Unit tests for scripts.calculate_version and scripts.ci_calculate_version.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases cover the tag-prefix scheme, initial version, bump derivation,
glob/strip correctness, existing-tag fail-fast, and D17 forgery detection.

AC-15:
- calculate_version globs <prefix>[0-9]* tags and derives <module_path>/v<x.y.z>
- D17: bump comes from API PR title; mismatch with squash subject fails
"""

from __future__ import annotations

import pytest

from scripts.calculate_version import (
    calculate_next_version,
    find_latest_version,
)
from scripts.validate_pr_title import BumpType

# ---------------------------------------------------------------------------
# Tag prefix scheme: module vs config
# ---------------------------------------------------------------------------

_TAG_PREFIX_CASES = [
    pytest.param(
        "providers/aws/primitives/kms-key",
        "module",
        "providers/aws/primitives/kms-key/v",
        id="module-primitive-prefix",
    ),
    pytest.param(
        "providers/aws/references/vpc-network",
        "module",
        "providers/aws/references/vpc-network/v",
        id="module-reference-prefix",
    ),
    pytest.param(
        "",
        "config",
        "monorepo-config/v",
        id="config-prefix",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("module_path,scope,expected_prefix", _TAG_PREFIX_CASES)
def test_calculate_version_tag_prefix(module_path: str, scope: str, expected_prefix: str) -> None:
    """calculate_next_version uses <module_path>/v for module scope and <config_tag_prefix>/v
    for config scope.

    AC-15: tag prefix must match the terraform-modules <module_path>/v<x.y.z> scheme.
    """
    from scripts.calculate_version import build_tag_prefix

    prefix = build_tag_prefix(
        scope=scope, module_path=module_path, config_tag_prefix="monorepo-config"
    )
    assert prefix == expected_prefix, (
        f"Expected tag_prefix={expected_prefix!r} for scope={scope!r}, "
        f"module_path={module_path!r}, got {prefix!r}. "
        "build_tag_prefix must derive the correct prefix per the <module_path>/v scheme."
    )


# ---------------------------------------------------------------------------
# Initial 0.0.0 -> first bump
# ---------------------------------------------------------------------------

_INITIAL_BUMP_CASES = [
    pytest.param([], BumpType.MINOR, "0.1.0", id="initial-minor-no-tags"),
    pytest.param([], BumpType.PATCH, "0.0.1", id="initial-patch-no-tags"),
    pytest.param([], BumpType.MAJOR, "1.0.0", id="initial-major-no-tags"),
]


@pytest.mark.unit
@pytest.mark.parametrize("existing_tags,bump,expected_version", _INITIAL_BUMP_CASES)
def test_calculate_version_initial_bump(
    existing_tags: list[str],
    bump: BumpType,
    expected_version: str,
) -> None:
    """calculate_next_version starts from 0.0.0 when no existing tags are found.

    AC-15: initial version is 0.0.0; first bump produces 0.1.0 (minor), 0.0.1 (patch),
    or 1.0.0 (major).
    """
    tag_prefix = "providers/aws/primitives/kms-key/v"
    result = calculate_next_version(
        existing_tags=existing_tags,
        tag_prefix=tag_prefix,
        bump=bump,
    )
    assert result.next_version == expected_version, (
        f"Expected next_version={expected_version!r} for initial bump={bump.value!r} "
        f"with no existing tags, got {result.next_version!r}. "
        "Initial version must start from 0.0.0."
    )
    assert result.is_initial is True, "is_initial must be True when no existing tags exist."


# ---------------------------------------------------------------------------
# Glob finds latest among mixed tags
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calculate_version_finds_latest_among_mixed_tags() -> None:
    """find_latest_version picks the highest semver among tags matching the prefix.

    AC-15: glob f"{tag_prefix}[0-9]*" filters correctly; the latest is the max semver.
    """
    tag_prefix = "providers/aws/primitives/kms-key/v"
    # Mix: some tags with the right prefix, some unrelated, some with another module's prefix
    all_tags = [
        "providers/aws/primitives/kms-key/v0.1.0",
        "providers/aws/primitives/kms-key/v0.2.0",
        "providers/aws/primitives/kms-key/v0.1.5",
        "providers/aws/primitives/s3-bucket/v1.0.0",  # different module
        "monorepo-config/v1.0.0",  # config scope tag
        "providers/aws/primitives/kms-key/v0.2.1",
    ]

    latest = find_latest_version(tags=all_tags, tag_prefix=tag_prefix)

    assert latest == "0.2.1", (
        f"find_latest_version must return '0.2.1' as the latest version "
        f"from the mixed tag set, got {latest!r}. "
        "Tags from other modules must be ignored."
    )


@pytest.mark.unit
def test_calculate_version_strips_prefix_correctly() -> None:
    """find_latest_version strips exactly the tag_prefix (including /v) to get semver.

    AC-15: strip exactly tag_prefix from each matching tag; no extra slash.
    The tag_prefix ends with '/v', so stripping it yields the bare semver string.
    """
    tag_prefix = "providers/aws/primitives/kms-key/v"
    tags = [
        "providers/aws/primitives/kms-key/v1.2.3",
        "providers/aws/primitives/kms-key/v1.3.0",
    ]

    latest = find_latest_version(tags=tags, tag_prefix=tag_prefix)

    assert latest == "1.3.0", (
        f"find_latest_version must strip '{tag_prefix}' and return '1.3.0', got {latest!r}."
    )


# ---------------------------------------------------------------------------
# Major / minor / patch bump derivation
# ---------------------------------------------------------------------------

_BUMP_CASES = [
    pytest.param(
        ["providers/aws/primitives/kms-key/v1.2.3"],
        BumpType.MAJOR,
        "2.0.0",
        id="major-bump",
    ),
    pytest.param(
        ["providers/aws/primitives/kms-key/v1.2.3"],
        BumpType.MINOR,
        "1.3.0",
        id="minor-bump",
    ),
    pytest.param(
        ["providers/aws/primitives/kms-key/v1.2.3"],
        BumpType.PATCH,
        "1.2.4",
        id="patch-bump",
    ),
    pytest.param(
        ["providers/aws/primitives/kms-key/v0.9.9"],
        BumpType.PATCH,
        "0.9.10",
        id="patch-bump-two-digit",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("existing_tags,bump,expected_version", _BUMP_CASES)
def test_calculate_version_bump_derivation(
    existing_tags: list[str],
    bump: BumpType,
    expected_version: str,
) -> None:
    """calculate_next_version derives the correct next version for each bump type.

    AC-15: major resets MINOR+PATCH to 0, minor resets PATCH to 0, patch increments PATCH.
    """
    tag_prefix = "providers/aws/primitives/kms-key/v"
    result = calculate_next_version(
        existing_tags=existing_tags,
        tag_prefix=tag_prefix,
        bump=bump,
    )
    assert result.next_version == expected_version, (
        f"Expected next_version={expected_version!r} for bump={bump.value!r} "
        f"from existing_tags={existing_tags!r}, got {result.next_version!r}."
    )
    assert result.is_initial is False, "is_initial must be False when existing tags are present."


# ---------------------------------------------------------------------------
# Existing full_tag fail-fast
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calculate_version_existing_full_tag_fails() -> None:
    """calculate_next_version must raise ValueError when the computed full_tag already exists.

    Fail-fast: simulates a race condition where the computed next tag was already
    published (e.g. concurrent release). We patch find_latest_version to return
    a known version so the computed next_tag is predictable, then inject that
    next_tag into existing_tags.
    """
    from unittest.mock import patch

    tag_prefix = "providers/aws/primitives/kms-key/v"
    # find_latest_version returns "1.2.3", so next MINOR = "1.3.0"
    # We include prefix/v1.3.0 in existing_tags to trigger the fail-fast.
    existing_tags = [
        "providers/aws/primitives/kms-key/v1.2.3",
        "providers/aws/primitives/kms-key/v1.3.0",  # already tagged (simulated race)
    ]

    # Patch find_latest_version to return "1.2.3" despite v1.3.0 existing,
    # simulating the race: find_latest ran before v1.3.0 was published,
    # but by the time we check, v1.3.0 exists.
    with (
        patch("scripts.calculate_version.find_latest_version", return_value="1.2.3"),
        pytest.raises(ValueError) as exc_info,
    ):
        calculate_next_version(
            existing_tags=existing_tags,
            tag_prefix=tag_prefix,
            bump=BumpType.MINOR,
        )

    msg = str(exc_info.value)
    assert "1.3.0" in msg or "exists" in msg.lower(), (
        f"ValueError must mention the colliding tag (1.3.0) or 'exists'. Got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# D17 forgery detection cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calculate_version_d17_api_and_squash_agree_passes() -> None:
    """D17: when API PR title bump == squash subject bump, version derivation succeeds.

    This is the valid case where no forgery occurred.
    """
    from scripts.calculate_version import validate_bump_agreement

    api_bump = BumpType.MAJOR
    squash_bump = BumpType.MAJOR

    # Must not raise
    validate_bump_agreement(api_bump=api_bump, squash_bump=squash_bump)


@pytest.mark.unit
def test_calculate_version_d17_forgery_detected_when_bumps_differ() -> None:
    """D17: when API PR title bump != squash subject bump, forgery is detected and raised.

    AC-15: PR-title API returns 'feat:' (MINOR) but squash subject is 'feat!:' (MAJOR).
    This mismatch is a forgery attempt and must raise ValueError.
    """
    from scripts.calculate_version import validate_bump_agreement
    from scripts.validate_pr_title import BumpType

    api_bump = BumpType.MINOR  # 'feat:' from PR title API
    squash_bump = BumpType.MAJOR  # 'feat!:' from hand-edited squash subject

    with pytest.raises(ValueError) as exc_info:
        validate_bump_agreement(api_bump=api_bump, squash_bump=squash_bump)

    msg = str(exc_info.value)
    assert len(msg) > 0, "ValueError must have an explanatory message."
    # Message should indicate the discrepancy
    has_bump_mention = (
        "minor" in msg.lower()
        or "major" in msg.lower()
        or "mismatch" in msg.lower()
        or "disagree" in msg.lower()
    )
    assert has_bump_mention, (
        f"ValueError must mention the bump type discrepancy (D17 forgery). Got: {msg!r}"
    )


@pytest.mark.unit
def test_calculate_version_d17_no_associated_pr_fails() -> None:
    """D17: when the GitHub API call finds no PR associated with the commit, fail fast.

    AC-15 / docs/release-pipeline.md: a commit with no associated PR cannot have its
    bump type validated from the API, so it must fail closed.
    """
    from scripts.calculate_version import derive_bump_from_api_result

    # Simulate the GitHub API returning no PR (empty list)
    with pytest.raises(ValueError) as exc_info:
        derive_bump_from_api_result(api_pr_title=None)

    msg = str(exc_info.value)
    assert len(msg) > 0, "ValueError for no associated PR must have an explanatory message."
    assert "no" in msg.lower() or "pr" in msg.lower() or "pull request" in msg.lower(), (
        f"ValueError must mention the missing PR. Got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# VersionResult full_tag derivation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calculate_version_full_tag_format() -> None:
    """The full_tag must be <tag_prefix><MAJOR>.<MINOR>.<PATCH> (no extra slash).

    AC-15: full_tag format matches <module_path>/v<x.y.z>.
    """
    tag_prefix = "providers/aws/primitives/kms-key/v"
    existing_tags = ["providers/aws/primitives/kms-key/v0.1.0"]
    result = calculate_next_version(
        existing_tags=existing_tags,
        tag_prefix=tag_prefix,
        bump=BumpType.MINOR,
    )
    expected_full_tag = "providers/aws/primitives/kms-key/v0.2.0"
    assert result.full_tag == expected_full_tag, (
        f"Expected full_tag={expected_full_tag!r}, got {result.full_tag!r}. "
        "full_tag must be tag_prefix + semver with no extra slash."
    )


# ---------------------------------------------------------------------------
# ci_calculate_version helper function coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ci_calculate_version_require_env_raises_on_missing(monkeypatch) -> None:
    """_require_env must call sys.exit(1) when the env var is absent."""
    import io
    from unittest.mock import patch

    from scripts.ci_calculate_version import _require_env

    monkeypatch.delenv("CI_TEST_MISSING_VAR", raising=False)

    with (
        patch("sys.exit") as mock_exit,
        patch("sys.stderr", io.StringIO()),
    ):
        _require_env("CI_TEST_MISSING_VAR")

    mock_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_ci_calculate_version_require_env_returns_value(monkeypatch) -> None:
    """_require_env returns the value when the env var is set."""
    from scripts.ci_calculate_version import _require_env

    monkeypatch.setenv("CI_TEST_PRESENT_VAR", "my-value")

    result = _require_env("CI_TEST_PRESENT_VAR")
    assert result == "my-value", f"_require_env must return the env var value. Got: {result!r}"


@pytest.mark.unit
def test_ci_calculate_version_fetch_pr_title_raises_on_gh_failure() -> None:
    """_fetch_pr_title_from_api raises RuntimeError when gh api fails."""
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import _fetch_pr_title_from_api

    mock_result = MagicMock(returncode=1, stdout="", stderr="gh: api error")

    with (
        patch("scripts.ci_calculate_version.subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError) as exc_info,
    ):
        _fetch_pr_title_from_api(repo="owner/repo", commit_sha="abc123")

    assert "gh api" in str(exc_info.value).lower() or "failed" in str(exc_info.value).lower(), (
        f"RuntimeError must mention the gh api failure. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_fetch_pr_title_returns_none_for_null() -> None:
    """_fetch_pr_title_from_api returns None when the API response is 'null'."""
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import _fetch_pr_title_from_api

    mock_result = MagicMock(returncode=0, stdout="null", stderr="")

    with patch("scripts.ci_calculate_version.subprocess.run", return_value=mock_result):
        result = _fetch_pr_title_from_api(repo="owner/repo", commit_sha="abc123")

    assert result is None, (
        f"_fetch_pr_title_from_api must return None when API returns 'null'. Got: {result!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_fetch_pr_title_returns_title() -> None:
    """_fetch_pr_title_from_api returns the PR title when the API succeeds."""
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import _fetch_pr_title_from_api

    mock_result = MagicMock(returncode=0, stdout="feat: add telemetry exporter\n", stderr="")

    with patch("scripts.ci_calculate_version.subprocess.run", return_value=mock_result):
        result = _fetch_pr_title_from_api(repo="owner/repo", commit_sha="abc123")

    assert result == "feat: add telemetry exporter", (
        f"_fetch_pr_title_from_api must return the PR title. Got: {result!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_fetch_git_tags_returns_list() -> None:
    """_fetch_git_tags returns a list of tag strings."""
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import _fetch_git_tags

    mock_result = MagicMock(
        returncode=0,
        stdout="providers/aws/primitives/kms-key/v0.1.0\nmonorepo-config/v1.0.0\n",
        stderr="",
    )

    with patch("scripts.ci_calculate_version.subprocess.run", return_value=mock_result):
        tags = _fetch_git_tags()

    assert "providers/aws/primitives/kms-key/v0.1.0" in tags, (
        f"_fetch_git_tags must include the first tag. Got: {tags!r}"
    )
    assert "monorepo-config/v1.0.0" in tags, (
        f"_fetch_git_tags must include the second tag. Got: {tags!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_fetch_git_tags_raises_on_failure() -> None:
    """_fetch_git_tags raises RuntimeError when git tag fails."""
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import _fetch_git_tags

    mock_result = MagicMock(returncode=1, stdout="", stderr="fatal: not a git repo")

    with (
        patch("scripts.ci_calculate_version.subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError) as exc_info,
    ):
        _fetch_git_tags()

    assert "git" in str(exc_info.value).lower(), (
        f"RuntimeError must mention git. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_main_terragrunt_scope_exits_zero(tmp_path, monkeypatch) -> None:
    """main() exits 0 immediately for scope=terragrunt (no version outputs)."""
    import io
    from unittest.mock import patch

    from scripts.ci_calculate_version import main

    output_file = tmp_path / "github_output"
    output_file.write_text("")

    monkeypatch.setenv("SCOPE", "terragrunt")
    monkeypatch.setenv("OUTPUT", str(output_file))

    with (
        patch("sys.stdout", io.StringIO()),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()

    # Should exit 0 for terragrunt scope
    assert exc_info.value.code == 0, (
        f"main() must exit with code 0 for terragrunt scope. Got: {exc_info.value.code!r}"
    )
    # No version outputs should be written
    content = output_file.read_text()
    assert "next_version=" not in content, (
        "main() must not write version outputs for terragrunt scope."
    )


@pytest.mark.unit
def test_ci_calculate_version_load_config_tag_prefix(tmp_path) -> None:
    """_load_config_tag_prefix reads config_tag_prefix from monorepo-config.json."""
    import json
    import os
    from unittest.mock import patch

    from scripts.ci_calculate_version import _load_config_tag_prefix

    config = {"config_tag_prefix": "monorepo-config"}
    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps(config))

    with patch("pathlib.Path.cwd", return_value=tmp_path):
        # Change the working directory context
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = _load_config_tag_prefix()
        finally:
            os.chdir(original_cwd)

    assert result == "monorepo-config", (
        f"_load_config_tag_prefix must return 'monorepo-config'. Got: {result!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_build_tag_prefix_invalid_scope_raises() -> None:
    """build_tag_prefix raises ValueError for unsupported scope."""
    from scripts.calculate_version import build_tag_prefix

    with pytest.raises(ValueError) as exc_info:
        build_tag_prefix(
            scope="terragrunt",
            module_path="",
            config_tag_prefix="monorepo-config",
        )

    assert "scope" in str(exc_info.value).lower() or "terragrunt" in str(exc_info.value), (
        f"ValueError must mention the unsupported scope. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_build_tag_prefix_module_empty_path_raises() -> None:
    """build_tag_prefix raises ValueError when module_path is empty for module scope."""
    from scripts.calculate_version import build_tag_prefix

    with pytest.raises(ValueError) as exc_info:
        build_tag_prefix(
            scope="module",
            module_path="",
            config_tag_prefix="monorepo-config",
        )

    assert "module_path" in str(exc_info.value) or "empty" in str(exc_info.value).lower(), (
        f"ValueError must mention empty module_path. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_main_module_scope_success(tmp_path, monkeypatch) -> None:
    """main() successfully derives version for module scope with mocked gh and git."""
    import io
    import json
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import main

    # Set up env vars
    monkeypatch.setenv("SCOPE", "module")
    monkeypatch.setenv("OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("COMMIT_SHA", "abc123def456")
    monkeypatch.setenv("COMMIT_MSG", "feat: add exporter")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("MODULE_PATH", "providers/aws/primitives/kms-key")

    # Write a monorepo-config.json in the cwd
    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    output_file = tmp_path / "output"
    output_file.write_text("")

    def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
        result = MagicMock()
        if cmd[0] == "gh":
            result.returncode = 0
            result.stdout = "feat: add exporter\n"
            result.stderr = ""
        elif cmd == ["git", "tag", "-l"]:
            result.returncode = 0
            result.stdout = ""  # no existing tags -> initial release
            result.stderr = ""
        return result

    captured = io.StringIO()

    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with (
            patch("scripts.ci_calculate_version.subprocess.run", side_effect=mock_subprocess_run),
            patch("sys.stdout", captured),
        ):
            main()
    finally:
        os.chdir(original_cwd)

    content = output_file.read_text()
    assert "next_version=0.1.0" in content, (
        f"main() must write next_version=0.1.0 for initial minor bump. Got: {content!r}"
    )
    assert "bump_type=minor" in content, f"main() must write bump_type=minor. Got: {content!r}"
    assert "tag_prefix=providers/aws/primitives/kms-key/v" in content, (
        f"main() must write the correct tag_prefix. Got: {content!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_main_api_failure_exits_nonzero(tmp_path, monkeypatch) -> None:
    """main() exits non-zero when the GitHub API call fails."""
    import io
    import json
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import main

    monkeypatch.setenv("SCOPE", "module")
    monkeypatch.setenv("OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("COMMIT_SHA", "abc123def456")
    monkeypatch.setenv("COMMIT_MSG", "feat: add exporter")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("MODULE_PATH", "providers/aws/primitives/kms-key")

    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    output_file = tmp_path / "output"
    output_file.write_text("")

    def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
        result = MagicMock()
        if cmd[0] == "gh":
            result.returncode = 1
            result.stdout = ""
            result.stderr = "gh: not found"
        return result

    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with (
            patch("scripts.ci_calculate_version.subprocess.run", side_effect=mock_subprocess_run),
            patch("sys.stderr", io.StringIO()),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
    finally:
        os.chdir(original_cwd)

    assert exc_info.value.code == 1, (
        f"main() must exit with code 1 when gh api fails. Got: {exc_info.value.code!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_main_no_pr_exits_nonzero(tmp_path, monkeypatch) -> None:
    """main() exits non-zero when no PR is associated with the commit (D17)."""
    import io
    import json
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import main

    monkeypatch.setenv("SCOPE", "module")
    monkeypatch.setenv("OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("COMMIT_SHA", "abc123def456")
    monkeypatch.setenv("COMMIT_MSG", "feat: add exporter")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("MODULE_PATH", "providers/aws/primitives/kms-key")

    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    output_file = tmp_path / "output"
    output_file.write_text("")

    def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
        result = MagicMock()
        if cmd[0] == "gh":
            result.returncode = 0
            result.stdout = "null\n"  # no PR found
            result.stderr = ""
        return result

    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with (
            patch("scripts.ci_calculate_version.subprocess.run", side_effect=mock_subprocess_run),
            patch("sys.stderr", io.StringIO()),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
    finally:
        os.chdir(original_cwd)

    assert exc_info.value.code == 1, (
        f"main() must exit with code 1 when no PR is associated (D17). Got: {exc_info.value.code!r}"
    )


@pytest.mark.unit
def test_ci_calculate_version_main_forgery_exits_nonzero(tmp_path, monkeypatch) -> None:
    """main() exits non-zero when API PR title bump disagrees with squash subject (D17)."""
    import io
    import json
    from unittest.mock import MagicMock, patch

    from scripts.ci_calculate_version import main

    monkeypatch.setenv("SCOPE", "module")
    monkeypatch.setenv("OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("COMMIT_SHA", "abc123def456")
    monkeypatch.setenv("COMMIT_MSG", "feat!: BREAKING redesign")  # major bump
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("MODULE_PATH", "providers/aws/primitives/kms-key")

    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    output_file = tmp_path / "output"
    output_file.write_text("")

    def mock_subprocess_run(cmd: list[str], **kwargs: object) -> MagicMock:
        result = MagicMock()
        if cmd[0] == "gh":
            result.returncode = 0
            result.stdout = "feat: add exporter\n"  # minor bump (API says feat:)
            result.stderr = ""
        return result

    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with (
            patch("scripts.ci_calculate_version.subprocess.run", side_effect=mock_subprocess_run),
            patch("sys.stderr", io.StringIO()),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
    finally:
        os.chdir(original_cwd)

    assert exc_info.value.code == 1, (
        f"main() must exit with code 1 when bump types disagree (D17 forgery). "
        f"Got: {exc_info.value.code!r}"
    )
