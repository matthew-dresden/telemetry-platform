"""Unit tests for scripts.check_staleness.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases:
- a same-module touch since the merge SHA -> stale
- a shared_paths_affecting_modules touch -> stale
- an unrelated touch -> not stale

AC-15:
- check_staleness detects a stale base before the release proceeds
- same-module files changed since merge SHA are stale
- shared_paths_affecting_modules files changed since merge SHA are stale
- unrelated files changed -> not stale (within scope)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.check_staleness import (
    StalenessResult,
    check_staleness,
    is_path_in_scope,
)

# ---------------------------------------------------------------------------
# is_path_in_scope parametrized cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "changed_path,module_path,shared_paths,expected",
    [
        pytest.param(
            "providers/aws/primitives/kms-key/main.tf",
            "providers/aws/primitives/kms-key",
            ["scripts/", "Makefile"],
            True,
            id="same-module-touch-in-scope",
        ),
        pytest.param(
            "scripts/calculate_version.py",
            "providers/aws/primitives/kms-key",
            ["scripts/", "Makefile"],
            True,
            id="shared-paths-touch-in-scope",
        ),
        pytest.param(
            "Makefile",
            "providers/aws/primitives/kms-key",
            ["scripts/", "Makefile"],
            True,
            id="makefile-shared-path-in-scope",
        ),
        pytest.param(
            "providers/aws/primitives/other-module/main.tf",
            "providers/aws/primitives/kms-key",
            ["scripts/", "Makefile"],
            False,
            id="other-module-touch-not-in-scope",
        ),
        pytest.param(
            "docs/README.md",
            "providers/aws/primitives/kms-key",
            ["scripts/", "Makefile"],
            False,
            id="unrelated-file-not-in-scope",
        ),
    ],
)
def test_check_staleness_is_path_in_scope(
    changed_path: str,
    module_path: str,
    shared_paths: list[str],
    expected: bool,
) -> None:
    """is_path_in_scope must correctly identify files affecting the module.

    AC-15: same-module or shared_paths_affecting_modules files are in scope.
    """
    result = is_path_in_scope(
        changed_path=changed_path,
        module_path=module_path,
        shared_paths=shared_paths,
    )
    assert result == expected, (
        f"Expected is_path_in_scope={expected} for path={changed_path!r}, "
        f"module={module_path!r}, shared={shared_paths!r}. Got {result!r}."
    )


# ---------------------------------------------------------------------------
# check_staleness -- main stale-base detection
# ---------------------------------------------------------------------------

_STALENESS_CASES = [
    pytest.param(
        ["providers/aws/primitives/kms-key/main.tf"],  # files changed since merge SHA
        "providers/aws/primitives/kms-key",
        ["scripts/", "Makefile"],
        StalenessResult.STALE,
        id="same-module-touch-is-stale",
    ),
    pytest.param(
        ["scripts/calculate_version.py"],  # shared paths touch
        "providers/aws/primitives/kms-key",
        ["scripts/", "Makefile"],
        StalenessResult.STALE,
        id="shared-path-touch-is-stale",
    ),
    pytest.param(
        ["providers/aws/primitives/other-module/main.tf"],  # unrelated touch
        "providers/aws/primitives/kms-key",
        ["scripts/", "Makefile"],
        StalenessResult.NOT_STALE,
        id="unrelated-touch-is-not-stale",
    ),
    pytest.param(
        [],  # no changes since merge SHA
        "providers/aws/primitives/kms-key",
        ["scripts/", "Makefile"],
        StalenessResult.NOT_STALE,
        id="no-changes-is-not-stale",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "changed_files,module_path,shared_paths,expected_result",
    _STALENESS_CASES,
)
def test_check_staleness_detection(
    changed_files: list[str],
    module_path: str,
    shared_paths: list[str],
    expected_result: StalenessResult,
) -> None:
    """check_staleness must return the correct StalenessResult.

    AC-15: stale base detection before the release proceeds.
    """
    result = check_staleness(
        changed_files=changed_files,
        module_path=module_path,
        shared_paths=shared_paths,
    )
    assert result == expected_result, (
        f"Expected {expected_result!r} for changed_files={changed_files!r}, "
        f"module_path={module_path!r}. Got {result!r}."
    )


# ---------------------------------------------------------------------------
# CLI main -- calls git correctly and exits non-zero when stale
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_staleness_main_exits_nonzero_when_stale() -> None:
    """main() must exit non-zero when the base is stale.

    AC-15: a stale base must fail the release pipeline step.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "git" in cmd and "diff" in cmd:
            # Return a changed file in the module path.
            return MagicMock(
                returncode=0,
                stdout="providers/aws/primitives/kms-key/main.tf\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.check_staleness.subprocess.run", side_effect=fake_run):
        from scripts.check_staleness import main

        with (
            patch(
                "sys.argv",
                [
                    "check_staleness",
                    "--merge-sha",
                    "abc1234",
                    "--module-path",
                    "providers/aws/primitives/kms-key",
                    "--shared-paths",
                    "scripts/,Makefile",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero when stale. Got code {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_check_staleness_main_exits_zero_when_not_stale() -> None:
    """main() must exit 0 when the base is not stale.

    AC-15: a fresh base must pass the check.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "git" in cmd and "diff" in cmd:
            # Return an unrelated changed file.
            return MagicMock(
                returncode=0,
                stdout="docs/README.md\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.check_staleness.subprocess.run", side_effect=fake_run):
        from scripts.check_staleness import main

        with (
            patch(
                "sys.argv",
                [
                    "check_staleness",
                    "--merge-sha",
                    "abc1234",
                    "--module-path",
                    "providers/aws/primitives/kms-key",
                    "--shared-paths",
                    "scripts/,Makefile",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code == 0, (
        f"main() must exit 0 when not stale. Got code {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_check_staleness_main_raises_on_git_error() -> None:
    """main() must exit non-zero when git diff fails.

    AC-15: no silent failures -- git errors must propagate loudly.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        return MagicMock(returncode=1, stdout="", stderr="fatal: bad revision")

    with patch("scripts.check_staleness.subprocess.run", side_effect=fake_run):
        from scripts.check_staleness import main

        with (
            patch(
                "sys.argv",
                [
                    "check_staleness",
                    "--merge-sha",
                    "abc1234",
                    "--module-path",
                    "providers/aws/primitives/kms-key",
                    "--shared-paths",
                    "scripts/",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero on git error. Got code {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# CLI missing --merge-sha and --module-path (lines 169-173, 176-180)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv,expected_fragment",
    [
        pytest.param(
            [
                "check_staleness",
                "--module-path",
                "providers/aws/primitives/kms-key",
            ],
            "merge-sha",
            id="missing-merge-sha",
        ),
    ],
)
def test_check_staleness_main_exits_nonzero_when_required_arg_missing(
    argv: list[str], expected_fragment: str
) -> None:
    """main() must exit non-zero when required arguments are not provided.

    AC-15: fail fast on missing required inputs.
    """
    from scripts.check_staleness import main

    with patch("sys.argv", argv), pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code != 0, (
        f"main() must exit non-zero when {expected_fragment!r} is missing. "
        f"Got: {exc_info.value.code!r}."
    )


# ---------------------------------------------------------------------------
# Config-scope staleness: no --module-path -> repo-wide check (not an error).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_staleness_main_config_scope_exits_zero_when_no_changes() -> None:
    """main() with no --module-path (config scope) exits 0 when nothing changed.

    AC-15: a config-scope release has no module path; an empty diff since the
    merge commit is a fresh base, not a missing-argument error.
    """

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "git" in cmd and "diff" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.check_staleness.subprocess.run", side_effect=fake_run):
        from scripts.check_staleness import main

        with (
            patch("sys.argv", ["check_staleness", "--merge-sha", "abc1234"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code == 0, (
        f"config-scope check_staleness must exit 0 when no changes. Got {exc_info.value.code!r}."
    )


@pytest.mark.unit
def test_check_staleness_main_config_scope_exits_nonzero_when_changed() -> None:
    """main() with no --module-path (config scope) exits non-zero when the repo
    advanced since the merge commit (repo-wide stale base)."""

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        if "git" in cmd and "diff" in cmd:
            return MagicMock(
                returncode=0, stdout="providers/aws/primitives/kms-key/main.tf\n", stderr=""
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("scripts.check_staleness.subprocess.run", side_effect=fake_run):
        from scripts.check_staleness import main

        with (
            patch("sys.argv", ["check_staleness", "--merge-sha", "abc1234"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

    assert exc_info.value.code != 0, (
        f"config-scope check_staleness must exit non-zero when changed. "
        f"Got {exc_info.value.code!r}."
    )
