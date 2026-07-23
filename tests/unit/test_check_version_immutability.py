"""Unit tests for scripts.check_version_immutability.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases:
- hand-edited VERSION pin against an already-published tag -> rejected (B13)
- untouched VERSION -> accepted
- missing VERSION file -> raise

AC-14 / B13:
- check_version_immutability detects a hand-edited VERSION pin to an
  already-published version and rejects it
- an unchanged VERSION file passes without error
- a missing VERSION file raises loudly
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.check_version_immutability import (
    check_version_immutability,
    is_version_already_published,
)

# ---------------------------------------------------------------------------
# is_version_already_published parametrized cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "existing_tags,tag_prefix,version,expected",
    [
        pytest.param(
            ["providers/aws/primitives/kms-key/v0.1.0"],
            "providers/aws/primitives/kms-key/v",
            "0.1.0",
            True,
            id="already-published",
        ),
        pytest.param(
            ["providers/aws/primitives/kms-key/v0.1.0"],
            "providers/aws/primitives/kms-key/v",
            "0.2.0",
            False,
            id="not-yet-published",
        ),
        pytest.param(
            [],
            "providers/aws/primitives/kms-key/v",
            "0.1.0",
            False,
            id="no-tags-at-all",
        ),
    ],
)
def test_check_version_immutability_is_version_already_published(
    existing_tags: list[str],
    tag_prefix: str,
    version: str,
    expected: bool,
) -> None:
    """is_version_already_published must correctly identify published versions.

    AC-14 / B13: a version that matches an existing tag is already published.
    """
    result = is_version_already_published(
        existing_tags=existing_tags,
        tag_prefix=tag_prefix,
        version=version,
    )
    assert result == expected, (
        f"Expected is_version_already_published={expected} for "
        f"version={version!r} against tags={existing_tags!r}. Got {result!r}."
    )


# ---------------------------------------------------------------------------
# check_version_immutability -- main detection logic
# ---------------------------------------------------------------------------

_IMMUTABILITY_CASES = [
    pytest.param(
        "0.1.0",  # version in the VERSION file after the edit
        "0.0.9",  # version in the VERSION file before the edit (at base_ref)
        ["providers/aws/primitives/kms-key/v0.1.0"],  # existing published tags
        "providers/aws/primitives/kms-key/v",  # tag prefix for this module
        True,  # should_reject = True (hand-edited to an already-published version)
        id="hand-edited-to-published-version-rejected",
    ),
    pytest.param(
        "0.2.0",  # version after edit
        "0.2.0",  # version before edit (unchanged)
        ["providers/aws/primitives/kms-key/v0.1.0"],  # existing published tags
        "providers/aws/primitives/kms-key/v",
        False,  # should_reject = False (VERSION unchanged)
        id="unchanged-version-accepted",
    ),
    pytest.param(
        "0.2.0",  # version after edit
        "0.1.0",  # version before edit (changed, but not yet published)
        [],  # no existing published tags
        "providers/aws/primitives/kms-key/v",
        False,  # should_reject = False (version changed but not published)
        id="changed-but-not-published-accepted",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "current_version,base_version,existing_tags,tag_prefix,should_reject",
    _IMMUTABILITY_CASES,
)
def test_check_version_immutability_detection(
    current_version: str,
    base_version: str,
    existing_tags: list[str],
    tag_prefix: str,
    should_reject: bool,
) -> None:
    """check_version_immutability must reject hand-edited pins to published versions.

    AC-14 / B13: if the current VERSION differs from the base VERSION AND the
    current version is already published as a git tag, raise ValueError.
    """
    if should_reject:
        with pytest.raises(ValueError) as exc_info:
            check_version_immutability(
                current_version=current_version,
                base_version=base_version,
                existing_tags=existing_tags,
                tag_prefix=tag_prefix,
            )
        assert (
            "immutab" in str(exc_info.value).lower()
            or "already" in str(exc_info.value).lower()
            or "published" in str(exc_info.value).lower()
            or "B13" in str(exc_info.value)
        ), f"ValueError must mention immutability/published version. Got: {exc_info.value!r}"
    else:
        # Should not raise
        check_version_immutability(
            current_version=current_version,
            base_version=base_version,
            existing_tags=existing_tags,
            tag_prefix=tag_prefix,
        )


# ---------------------------------------------------------------------------
# CLI main -- missing VERSION file -> raise
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_version_immutability_missing_version_file_raises(tmp_path) -> None:
    """The CLI must raise when the VERSION file is missing.

    AC-14: missing input files are a hard error -- fail fast.
    """
    from scripts.check_version_immutability import main_check

    with pytest.raises((FileNotFoundError, RuntimeError, SystemExit)) as exc_info:
        main_check(
            version_file=str(tmp_path / "NONEXISTENT_VERSION"),
            base_ref="HEAD~1",
            module_path="providers/aws/primitives/kms-key",
        )

    # Must have exited with a non-zero code or raised an informative error
    if isinstance(exc_info.value, SystemExit):
        assert exc_info.value.code != 0, (
            "SystemExit code must be non-zero when VERSION file is missing."
        )


# ---------------------------------------------------------------------------
# CLI main -- calls git and gh correctly
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_version_immutability_main_cli_exits_nonzero_on_violation(tmp_path) -> None:
    """The CLI must exit non-zero when a B13 immutability violation is detected.

    AC-14 / B13: CLI must propagate the rejection as a non-zero exit code.
    """
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.1.0\n")

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "git" in cmd and "show" in cmd:
            # Base version was 0.0.9, now hand-edited to 0.1.0
            return MagicMock(returncode=0, stdout="0.0.9\n", stderr="")
        if "git" in cmd and "tag" in cmd:
            # 0.1.0 already published
            return MagicMock(
                returncode=0,
                stdout="providers/aws/primitives/kms-key/v0.1.0\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.check_version_immutability.subprocess.run", side_effect=fake_run):
        from scripts.check_version_immutability import main

        with (
            patch(
                "sys.argv",
                [
                    "check_version_immutability",
                    "--base-ref",
                    "HEAD~1",
                    "--scope",
                    "module",
                    "--module-path",
                    "providers/aws/primitives/kms-key",
                    "--version-file",
                    str(version_file),
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero on B13 violation. Got: {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_check_version_immutability_main_cli_exits_zero_on_clean(tmp_path) -> None:
    """The CLI must exit 0 when no immutability violation is detected.

    AC-14: a clean VERSION check must pass without error.
    """
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.2.0\n")

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "git" in cmd and "show" in cmd:
            # Unchanged version
            return MagicMock(returncode=0, stdout="0.2.0\n", stderr="")
        if "git" in cmd and "tag" in cmd:
            return MagicMock(
                returncode=0,
                stdout="providers/aws/primitives/kms-key/v0.1.0\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.check_version_immutability.subprocess.run", side_effect=fake_run):
        from scripts.check_version_immutability import main

        with patch(
            "sys.argv",
            [
                "check_version_immutability",
                "--base-ref",
                "HEAD~1",
                "--scope",
                "module",
                "--module-path",
                "providers/aws/primitives/kms-key",
                "--version-file",
                str(version_file),
            ],
        ):
            # main() succeeds without raising on a clean version
            try:
                main()
            except SystemExit as exc:
                assert exc.code == 0, f"CLI must exit 0 on clean version. Got: {exc.code!r}."


@pytest.mark.unit
def test_check_version_immutability_cli_fails_fast_on_missing_module_path_for_module_scope(
    tmp_path,
) -> None:
    """The CLI must exit non-zero when --scope=module but --module-path is missing.

    AC-14: fail fast on missing required input. Module scope derives its tag prefix
    from the module path, so an absent --module-path is a hard error. (Config scope
    -- the default -- needs no module path; it derives the prefix from
    monorepo-config.json's config_tag_prefix.)
    """
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.1.0\n")

    from scripts.check_version_immutability import main

    # --scope module but no --module-path in argv.
    argv = [
        "check_version_immutability",
        "--base-ref",
        "HEAD~1",
        "--scope",
        "module",
        "--version-file",
        str(version_file),
    ]
    with patch("sys.argv", argv), pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0, (
        "CLI must exit non-zero when --scope=module and --module-path is missing. "
        f"Got: {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_check_version_immutability_config_scope_derives_prefix_from_config(tmp_path) -> None:
    """Config scope must derive the tag prefix '<config_tag_prefix>/v' from config.

    AC-14 / B13: the repo-wide root VERSION uses scope='config'; its tag prefix is
    '<config_tag_prefix>/v', read from monorepo-config.json -- never a module path.
    """
    import json

    from scripts.check_version_immutability import _resolve_tag_prefix

    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    prefix = _resolve_tag_prefix(
        scope="config",
        module_path="",
        config_path=str(config_file),
    )

    assert prefix == "monorepo-config/v", (
        "config-scope check must derive the tag prefix from config_tag_prefix as "
        f"'monorepo-config/v'. Got: {prefix!r}."
    )


@pytest.mark.unit
def test_check_version_immutability_config_scope_passes_when_unchanged(tmp_path) -> None:
    """Config-scope main_check must pass (no raise) for an unchanged root VERSION.

    AC-14 / B13: a config-scope check needs no --module-path and passes when the
    current VERSION equals the base-ref VERSION.
    """
    import json

    version_file = tmp_path / "VERSION"
    version_file.write_text("0.1.0\n")
    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "cat-file" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "git" in cmd and "show" in cmd:
            # Base version equals current version -> unchanged -> pass.
            return MagicMock(returncode=0, stdout="0.1.0\n", stderr="")
        if "git" in cmd and "tag" in cmd:
            return MagicMock(returncode=0, stdout="monorepo-config/v0.0.9\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    from scripts.check_version_immutability import main_check

    with patch("scripts.check_version_immutability.subprocess.run", side_effect=fake_run):
        main_check(
            version_file=str(version_file),
            base_ref="origin/main",
            module_path="",
            scope="config",
            config_path=str(config_file),
        )


@pytest.mark.unit
def test_check_version_immutability_passes_when_version_absent_at_base_ref(tmp_path) -> None:
    """The check must PASS when the VERSION file is absent at the base ref (introduction).

    AC-14 / B13: the very first PR that introduces VERSION has a base ref (the
    initial commit) that predates the file. `git cat-file -e <ref>:VERSION` reports
    the file absent, so base_version is None (introduced), and the published-tag
    guard runs against the current version. With no matching published tag, the
    check passes -- it must not hard-error on the absent base file.
    """
    import json

    version_file = tmp_path / "VERSION"
    version_file.write_text("0.1.0\n")
    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        # cat-file -e probe -> file absent at base ref (non-zero exit).
        if "cat-file" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        if "git" in cmd and "show" in cmd:
            # Should not be reached when the file is absent; guard with exit 128.
            return MagicMock(
                returncode=128,
                stdout="",
                stderr="fatal: path 'VERSION' exists on disk, but not in 'origin/main'",
            )
        if "git" in cmd and "tag" in cmd:
            # No matching published tag for the new version.
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    from scripts.check_version_immutability import main_check

    with patch("scripts.check_version_immutability.subprocess.run", side_effect=fake_run):
        # Must not raise: the introduced version is not an already-published tag.
        main_check(
            version_file=str(version_file),
            base_ref="origin/main",
            module_path="",
            scope="config",
            config_path=str(config_file),
        )


@pytest.mark.unit
def test_check_version_immutability_rejects_introduced_version_matching_published_tag(
    tmp_path,
) -> None:
    """B13 must still reject an introduced version that already exists as a tag.

    AC-14 / B13: handling the absent-base case must NOT weaken the published-tag
    guard. If VERSION is introduced at a value that is already a published tag, the
    check must reject it.
    """
    import json

    version_file = tmp_path / "VERSION"
    version_file.write_text("0.1.0\n")
    config_file = tmp_path / "monorepo-config.json"
    config_file.write_text(json.dumps({"config_tag_prefix": "monorepo-config"}))

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "cat-file" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="")
        if "git" in cmd and "tag" in cmd:
            # 0.1.0 already published under the config tag prefix.
            return MagicMock(returncode=0, stdout="monorepo-config/v0.1.0\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    from scripts.check_version_immutability import main_check

    with (
        patch("scripts.check_version_immutability.subprocess.run", side_effect=fake_run),
        pytest.raises(ValueError) as exc_info,
    ):
        main_check(
            version_file=str(version_file),
            base_ref="origin/main",
            module_path="",
            scope="config",
            config_path=str(config_file),
        )

    assert "B13" in str(exc_info.value) or "already" in str(exc_info.value).lower(), (
        f"Introduced version matching a published tag must be rejected. Got: {exc_info.value!r}"
    )


@pytest.mark.unit
def test_check_version_immutability_main_check_calls_git(tmp_path) -> None:
    """main_check must read the VERSION file and use git to get the base version.

    AC-14: the check must compare current vs base-ref VERSION content.
    """
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.2.0\n")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        calls.append(cmd)
        result = MagicMock(returncode=0, stdout="", stderr="")
        if "git" in cmd and "show" in cmd:
            result.stdout = "0.2.0\n"
        elif "git" in cmd and "tag" in cmd:
            result.stdout = "providers/aws/primitives/kms-key/v0.1.0\n"
        return result

    with patch("scripts.check_version_immutability.subprocess.run", side_effect=fake_run):
        # Should not raise (0.2.0 changed but not yet published)
        from scripts.check_version_immutability import main_check

        main_check(
            version_file=str(version_file),
            base_ref="HEAD~1",
            module_path="providers/aws/primitives/kms-key",
            scope="module",
        )

    git_cmds = [cmd for cmd in calls if cmd and cmd[0] == "git"]
    assert len(git_cmds) >= 1, f"main_check must invoke git. Commands: {calls!r}."
