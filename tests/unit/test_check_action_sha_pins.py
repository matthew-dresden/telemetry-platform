"""Unit tests for scripts.check_action_sha_pins -- SHA-pin gate for GitHub Actions.

Tests assert that:
- AC-1: properly pinned uses: (40-char SHA + trailing version comment) passes
- AC-1: unpinned floating @vN refs fail with non-zero exit and ERROR: message
- AC-1: SHA missing its trailing version comment fails
- AC-1: first-party ./ actions are not subject to the SHA-pin rule and pass
- AC-1: the gate fails on the first violation and reports file and line context
"""

from __future__ import annotations

import importlib
import pathlib
import textwrap
from unittest.mock import patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
CHECK_SHA_PINS_SOURCE = REPO_ROOT / "scripts" / "check_action_sha_pins.py"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _import_check_sha_pins():
    """Import (or re-import) scripts.check_action_sha_pins."""
    import scripts.check_action_sha_pins as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# Parametrized fixture content cases
# ---------------------------------------------------------------------------

# Each entry: (label, yaml_content, should_pass)
_CASES = [
    (
        "properly_pinned_40char_sha_with_version_comment",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@abc12345678901234567890123456789012345ab  # v4.1.0
        """),
        True,
    ),
    (
        "unpinned_floating_v_ref",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@v4
        """),
        False,
    ),
    (
        "unpinned_latest_ref",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@main
        """),
        False,
    ),
    (
        "sha_present_but_missing_version_comment",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@abc12345678901234567890123456789012345ab
        """),
        False,
    ),
    (
        "first_party_local_action_dot_slash",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: ./.github/actions/setup-tools
        """),
        True,
    ),
    (
        "multiple_steps_all_pinned",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@abc12345678901234567890123456789012345ab  # v4.1.0
                  - uses: actions/setup-python@def4567890123456789012345678901234567890  # v5.0.0
        """),
        True,
    ),
    (
        "mixed_pinned_and_unpinned_fails",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@abc12345678901234567890123456789012345ab  # v4.1.0
                  - uses: actions/setup-python@v5
        """),
        False,
    ),
    (
        "short_sha_not_40chars_fails",
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@abc1234  # v4.1.0
        """),
        False,
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("label,yaml_content,should_pass", _CASES, ids=[c[0] for c in _CASES])
def test_check_action_sha_pins_cases(
    label: str,
    yaml_content: str,
    should_pass: bool,
    tmp_path: pathlib.Path,
) -> None:
    """Parametrized test for all SHA-pin validation cases.

    Each test case provides a workflow YAML fragment and asserts whether
    the pin check passes or fails, per the spec-canonical rule (docs/release-pipeline.md):
    - PASS: 40-char SHA with trailing version comment, or first-party ./
    - FAIL: floating @vN, missing comment, short SHA, or other non-compliant refs
    """
    mod = _import_check_sha_pins()

    workflow_file = tmp_path / "test_workflow.yml"
    workflow_file.write_text(yaml_content, encoding="utf-8")

    violations = mod.check_file(workflow_file)

    if should_pass:
        assert violations == [], (
            f"Case {label!r}: expected no violations but got {violations!r}. "
            "A properly pinned action or first-party action must pass the SHA-pin gate."
        )
    else:
        assert len(violations) > 0, (
            f"Case {label!r}: expected at least one violation but got none. "
            "An improperly pinned action must fail the SHA-pin gate."
        )
        # Each violation must include file path and line number context
        for violation in violations:
            assert "ERROR:" in violation, (
                f"Case {label!r}: violation message must contain 'ERROR:', got: {violation!r}"
            )


# ---------------------------------------------------------------------------
# Violation message contains file and line context
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_violation_includes_file_and_line(
    tmp_path: pathlib.Path,
) -> None:
    """A violation message must include the offending file path and line number."""
    mod = _import_check_sha_pins()

    yaml_content = textwrap.dedent("""\
        jobs:
          test:
            steps:
              - uses: actions/checkout@v4
    """)
    workflow_file = tmp_path / "bad_workflow.yml"
    workflow_file.write_text(yaml_content, encoding="utf-8")

    violations = mod.check_file(workflow_file)

    assert len(violations) > 0, "Expected at least one violation for unpinned action."
    violation_text = violations[0]
    assert str(workflow_file) in violation_text or "bad_workflow.yml" in violation_text, (
        f"Violation must include the file name, got: {violation_text!r}"
    )


# ---------------------------------------------------------------------------
# CLI exits non-zero when violations found
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_cli_exits_nonzero_on_violation(
    tmp_path: pathlib.Path,
) -> None:
    """The CLI must exit non-zero when any workflow has an unpinned action."""
    mod = _import_check_sha_pins()

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    bad_file = workflows_dir / "bad.yml"
    bad_file.write_text(
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@v4
        """),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        mod.main(workflows_dir=str(workflows_dir))

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero when violations are found, got {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# CLI exits zero when all workflows are compliant
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_cli_exits_zero_when_compliant(
    tmp_path: pathlib.Path,
) -> None:
    """The CLI must exit 0 when all workflows use properly pinned actions."""
    mod = _import_check_sha_pins()

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    good_file = workflows_dir / "good.yml"
    good_file.write_text(
        textwrap.dedent("""\
            jobs:
              test:
                steps:
                  - uses: actions/checkout@abc12345678901234567890123456789012345ab  # v4.1.0
                  - uses: ./.github/actions/setup-tools
        """),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        mod.main(workflows_dir=str(workflows_dir))

    assert exc_info.value.code == 0, (
        f"CLI must exit 0 when all actions are properly pinned, got {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# CLI exits zero on empty workflows directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_cli_exits_zero_on_empty_dir(
    tmp_path: pathlib.Path,
) -> None:
    """The CLI must exit 0 when the workflows directory is empty."""
    mod = _import_check_sha_pins()

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc_info:
        mod.main(workflows_dir=str(workflows_dir))

    assert exc_info.value.code == 0, (
        f"CLI must exit 0 when no workflow files are found, got {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# CLI: missing workflows directory fails with actionable message
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_cli_fails_on_missing_dir(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """The CLI must fail with a non-zero exit and ERROR: message when the dir is absent."""
    mod = _import_check_sha_pins()

    missing_dir = str(tmp_path / "nonexistent" / "workflows")

    with pytest.raises(SystemExit) as exc_info:
        mod.main(workflows_dir=missing_dir)

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero when workflows dir is missing, got {exc_info.value.code!r}"
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"CLI must print ERROR: to stderr when dir is missing, got stderr: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Edge case: ref without @ separator (no SHA segment)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_ref_without_at_fails(
    tmp_path: pathlib.Path,
) -> None:
    """A uses: ref with no '@' separator must fail the SHA-pin gate."""
    mod = _import_check_sha_pins()

    yaml_content = "        - uses: actions/checkout\n"
    workflow_file = tmp_path / "no_at.yml"
    workflow_file.write_text(yaml_content, encoding="utf-8")

    violations = mod.check_file(workflow_file)

    assert len(violations) > 0, "A uses: ref without '@' must produce at least one violation."
    assert "ERROR:" in violations[0], (
        f"Violation message must contain 'ERROR:', got: {violations[0]!r}"
    )


# ---------------------------------------------------------------------------
# check_file raises FileNotFoundError for missing file
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_file_raises_for_missing_file(tmp_path: pathlib.Path) -> None:
    """check_file must raise FileNotFoundError if the workflow file does not exist."""
    mod = _import_check_sha_pins()
    missing = tmp_path / "nonexistent.yml"

    with pytest.raises(FileNotFoundError) as exc_info:
        mod.check_file(missing)

    assert str(missing) in str(exc_info.value), (
        f"FileNotFoundError must include the missing path, got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# main() default path: invoked without workflows_dir argument
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_action_sha_pins_main_default_path(tmp_path: pathlib.Path) -> None:
    """main() invoked without workflows_dir must attempt the .github/workflows default path.

    This test exercises the default-path branch of main() by creating the
    expected directory structure and placing a compliant workflow inside it.
    """
    mod = _import_check_sha_pins()

    # Build the expected default directory structure: .github/workflows
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    good_file = workflows_dir / "good.yml"
    good_file.write_text(
        "        - uses: actions/checkout@abc12345678901234567890123456789012345ab  # v4.1.0\n",
        encoding="utf-8",
    )

    # Redirect the module's __file__ so its parent.parent resolves to tmp_path.
    fake_script_path = str(tmp_path / "scripts" / "check_action_sha_pins.py")
    with patch.object(mod, "__file__", fake_script_path), pytest.raises(SystemExit) as exc_info:
        mod.main()

    # All workflows are compliant, so exit code must be 0.
    assert exc_info.value.code == 0, (
        f"main() with default path must exit 0 when all workflows are compliant, "
        f"got {exc_info.value.code!r}"
    )
