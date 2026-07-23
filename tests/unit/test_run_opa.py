"""Unit tests for scripts.run_opa and scripts.run_actionlint -- pinned binary wrappers.

Tests assert that:
- AC-1: run_opa.py builds the correct argv for fmt, check, and test --coverage subcommands
- AC-1: run_opa.py invokes only the pinned opa binary (no Go file-collector)
- AC-1: a non-zero binary exit is surfaced as a non-zero process exit with ERROR: message
- AC-1: the wrapper never invokes any Go-based file collector binary
- AC-1: run_opa.py passes stdout/stderr from the binary to sys.stdout/stderr on success
- AC-1: run_opa.py exits with code 2 when no subcommand is given
- AC-1: run_actionlint.py invokes the pinned actionlint binary and fails closed on findings
"""

from __future__ import annotations

import importlib
import pathlib
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
RUN_OPA_SOURCE = REPO_ROOT / "scripts" / "run_opa.py"

# module-validate evaluates the real OPA policy bundle via the pinned opa binary,
# so these tests require opa on PATH. Skip (rather than silently pass) when absent.
_OPA_AVAILABLE = shutil.which("opa") is not None
_REQUIRES_OPA = pytest.mark.skipif(
    not _OPA_AVAILABLE,
    reason="opa binary not available on PATH; module-validate tests need the pinned opa binary.",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _import_run_opa():
    """Import (or re-import) scripts.run_opa."""
    import scripts.run_opa as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-1: Subcommand argv construction
# ---------------------------------------------------------------------------

_SUBCOMMAND_CASES = [
    # (subcommand, extra_args, expected_argv_contains)
    (
        "fmt",
        ["--list", "policies"],
        ["fmt", "--list", "policies"],
    ),
    (
        "check",
        ["policies"],
        ["check", "policies"],
    ),
    (
        "test",
        ["policies", "--coverage", "--threshold", "90"],
        ["test", "policies", "--coverage", "--threshold", "90"],
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("subcommand,extra_args,expected_contains", _SUBCOMMAND_CASES)
def test_run_opa_builds_correct_argv(
    subcommand: str,
    extra_args: list[str],
    expected_contains: list[str],
) -> None:
    """run_opa must build argv containing the expected subcommand and arguments.

    The binary used must be the pinned opa binary resolved from tool config,
    not any Go-based collector or hard-coded path.
    """
    mod = _import_run_opa()

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_opa_command(subcommand, extra_args, opa_binary="opa")

    assert len(captured_calls) == 1, (
        f"Expected exactly one subprocess.run call for subcommand={subcommand!r}, "
        f"got {len(captured_calls)}."
    )
    actual_argv = captured_calls[0]
    # First element is the binary
    assert actual_argv[0] == "opa", (
        f"Expected binary 'opa', got {actual_argv[0]!r}. "
        "The wrapper must invoke the pinned opa binary."
    )
    for expected_part in expected_contains:
        assert expected_part in actual_argv, (
            f"Expected {expected_part!r} in argv {actual_argv!r} for subcommand={subcommand!r}."
        )


# ---------------------------------------------------------------------------
# AC-1: Non-zero binary exit is surfaced as non-zero process exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_run_opa_raises_on_nonzero_exit(exit_code: int) -> None:
    """A non-zero binary exit must raise OpaCommandError with file/argv context."""
    mod = _import_run_opa()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            argv, exit_code, stdout="", stderr=f"opa error rc={exit_code}"
        )

    with (
        patch("subprocess.run", side_effect=fake_run),
        pytest.raises(mod.OpaCommandError) as exc_info,
    ):
        mod.run_opa_command("check", ["policies"], opa_binary="opa")

    err_msg = str(exc_info.value)
    assert "ERROR:" in err_msg, (
        f"OpaCommandError message must contain 'ERROR:' prefix, got: {err_msg!r}"
    )
    assert str(exit_code) in err_msg, (
        f"OpaCommandError message must include exit code {exit_code}, got: {err_msg!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: Zero binary exit does not raise
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_does_not_raise_on_zero_exit() -> None:
    """A zero binary exit must not raise any exception."""
    mod = _import_run_opa()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_opa_command("fmt", ["--list", "policies"], opa_binary="opa")


# ---------------------------------------------------------------------------
# AC-1: No Go file-collector invoked
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_never_invokes_go_file_collector() -> None:
    """The OPA wrapper must never shell out to a Go file-collector binary.

    D11 forbids any Go file-collector behind the OPA gate. The wrapper must
    only invoke the pinned opa/conftest binary, never 'terraform-file-collector'
    or any other Go binary.
    """
    mod = _import_run_opa()

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_opa_command("fmt", ["--list", "policies"], opa_binary="opa")
        mod.run_opa_command("check", ["policies"], opa_binary="opa")
        mod.run_opa_command("test", ["policies", "--coverage"], opa_binary="opa")

    go_collector_names = {"terraform-file-collector", "go-unit-test", "rego-unit-test"}
    for call_argv in captured_calls:
        binary_invoked = call_argv[0]
        assert binary_invoked not in go_collector_names, (
            f"run_opa shelled out to Go collector binary {binary_invoked!r}. "
            "D11 forbids any Go file-collector behind the OPA gate. "
            "The wrapper must only invoke the pinned opa/conftest binary."
        )


# ---------------------------------------------------------------------------
# AC-1: Source file contains no Go collector references
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_source_has_no_go_collector_reference() -> None:
    """scripts/run_opa.py must not reference any Go file-collector binary.

    Static check -- ensures no import or subprocess call to Go collector
    binaries exists in the source, per D11.
    """
    assert RUN_OPA_SOURCE.exists(), (
        f"scripts/run_opa.py not found at {RUN_OPA_SOURCE}. "
        "The module must exist before this test can pass."
    )
    source_text = RUN_OPA_SOURCE.read_text(encoding="utf-8")

    # These are the Go binary names that must NOT appear as subprocess invocation targets.
    # We use word-boundary matching to avoid false positives (e.g. "rego-unit-test"
    # containing "go-unit-test" as a substring).
    import re

    forbidden_patterns = [
        r"\bterraform-file-collector\b",
        r"\bgo-unit-test\b",
        r'subprocess\.run\(\["go"\b',
        r'\bsubprocess\.run\(\["go\s',
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, source_text), (
            f"Found forbidden Go-collector reference matching {pattern!r} in scripts/run_opa.py. "
            "D11 forbids any Go file-collector behind the OPA gate."
        )


# ---------------------------------------------------------------------------
# AC-1: CLI main exits non-zero on OpaCommandError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_cli_exits_nonzero_on_failure() -> None:
    """The CLI entry point must call sys.exit with non-zero on OpaCommandError."""
    mod = _import_run_opa()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="opa failed")

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch.object(sys, "argv", ["run_opa", "check", "policies"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()
    assert exc_info.value.code != 0, (
        f"CLI main must exit non-zero on failure, got exit code {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI main exits zero on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_cli_exits_zero_on_success() -> None:
    """The CLI entry point must exit 0 on success."""
    mod = _import_run_opa()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch.object(sys, "argv", ["run_opa", "fmt", "--list", "policies"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()
    assert exc_info.value.code == 0, f"CLI main must exit 0 on success, got {exc_info.value.code!r}"


# ---------------------------------------------------------------------------
# AC-1: Binary path is configurable, not hard-coded
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_accepts_custom_binary_path() -> None:
    """The opa binary path must be configurable, not hard-coded inline."""
    mod = _import_run_opa()
    custom_binary = "/usr/local/bin/opa-1.17.0"

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_opa_command("check", ["policies"], opa_binary=custom_binary)

    assert captured_calls[0][0] == custom_binary, (
        f"Expected binary {custom_binary!r}, got {captured_calls[0][0]!r}. "
        "The binary path must be configurable, not hard-coded."
    )


# ---------------------------------------------------------------------------
# AC-1: stdout/stderr passthrough on success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_opa_passes_stdout_on_success(capsys) -> None:
    """On success, run_opa must pass the binary stdout to sys.stdout."""
    mod = _import_run_opa()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="policy ok\n", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_opa_command("check", ["policies"], opa_binary="opa")

    captured = capsys.readouterr()
    assert "policy ok" in captured.out, f"Expected stdout passthrough, got: {captured.out!r}"


@pytest.mark.unit
def test_run_opa_passes_stderr_on_success(capsys) -> None:
    """On success, run_opa must pass the binary stderr to sys.stderr."""
    mod = _import_run_opa()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="warning: something\n")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_opa_command("fmt", ["--list", "policies"], opa_binary="opa")

    captured = capsys.readouterr()
    assert "warning" in captured.err, f"Expected stderr passthrough, got: {captured.err!r}"


@pytest.mark.unit
def test_run_opa_cli_exits_2_when_no_subcommand() -> None:
    """CLI must exit with code 2 when invoked without a subcommand."""
    mod = _import_run_opa()

    with patch.object(sys, "argv", ["run_opa"]), pytest.raises(SystemExit) as exc_info:
        mod.main()

    assert exc_info.value.code == 2, (
        f"CLI must exit 2 when no subcommand given, got {exc_info.value.code!r}"
    )


# ---------------------------------------------------------------------------
# AC-1: run_actionlint -- invokes pinned actionlint binary
# ---------------------------------------------------------------------------


def _import_run_actionlint():
    """Import (or re-import) scripts.run_actionlint."""
    import scripts.run_actionlint as m

    return importlib.reload(m)


@pytest.mark.unit
def test_run_actionlint_invokes_actionlint_binary(tmp_path: pathlib.Path) -> None:
    """run_actionlint must invoke the pinned actionlint binary with individual yml file paths."""
    mod = _import_run_actionlint()

    # Create a test workflow file so the glob yields at least one path.
    workflow_file = tmp_path / "test.yml"
    workflow_file.write_text("name: Test\n")

    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_actionlint(
            workflows_dir=str(tmp_path),
            actionlint_binary="actionlint",
        )

    assert len(captured_calls) == 1, (
        f"Expected exactly one subprocess.run call, got {len(captured_calls)}."
    )
    assert captured_calls[0][0] == "actionlint", (
        f"Expected binary 'actionlint', got {captured_calls[0][0]!r}."
    )
    assert str(workflow_file) in captured_calls[0], (
        f"Expected workflow file path {str(workflow_file)!r} in argv {captured_calls[0]!r}."
    )


@pytest.mark.unit
def test_run_actionlint_raises_on_nonzero_exit(tmp_path: pathlib.Path) -> None:
    """run_actionlint must raise ActionlintError when actionlint exits non-zero."""
    mod = _import_run_actionlint()

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 1, stdout="finding: bad step\n", stderr="")

    with (
        patch("subprocess.run", side_effect=fake_run),
        pytest.raises(mod.ActionlintError) as exc_info,
    ):
        mod.run_actionlint(
            workflows_dir=str(tmp_path),
            actionlint_binary="actionlint",
        )

    err_msg = str(exc_info.value)
    assert "ERROR:" in err_msg, f"ActionlintError must contain 'ERROR:', got: {err_msg!r}"
    assert "1" in err_msg, f"ActionlintError must include exit code 1, got: {err_msg!r}"


@pytest.mark.unit
def test_run_actionlint_error_argv_reflects_file_paths(tmp_path: pathlib.Path) -> None:
    """ActionlintError.argv must list the individual file paths actually passed to the binary.

    Regression guard: before the fix, argv contained [binary, str(resolved_dir)] which made
    the error message misleading (the directory, not the actual files, appeared in the log).
    """
    mod = _import_run_actionlint()

    workflow_file = tmp_path / "my-workflow.yml"
    workflow_file.write_text("name: Test\n")

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, 1, stdout="error: bad step\n", stderr="")

    with (
        patch("subprocess.run", side_effect=fake_run),
        pytest.raises(mod.ActionlintError) as exc_info,
    ):
        mod.run_actionlint(
            workflows_dir=str(tmp_path),
            actionlint_binary="actionlint",
        )

    error = exc_info.value
    assert str(workflow_file) in error.argv, (
        f"ActionlintError.argv must contain the actual workflow file path "
        f"{str(workflow_file)!r}, not the directory. Got: {error.argv!r}"
    )
    assert str(tmp_path) not in error.argv, (
        f"ActionlintError.argv must NOT contain the directory path {str(tmp_path)!r} "
        f"(only file paths should appear). Got: {error.argv!r}"
    )


@pytest.mark.unit
def test_run_actionlint_cli_exits_nonzero_on_finding(tmp_path: pathlib.Path) -> None:
    """CLI main must exit non-zero when actionlint reports a finding."""
    mod = _import_run_actionlint()

    with (
        patch("scripts.run_actionlint.run_actionlint") as mock_run,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_run.side_effect = mod.ActionlintError(
            argv=["actionlint", ".github/workflows"],
            exit_code=1,
            output="error: bad workflow",
        )
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero on actionlint finding, got {exc_info.value.code!r}"
    )


@pytest.mark.unit
def test_run_actionlint_cli_exits_zero_on_success(tmp_path: pathlib.Path) -> None:
    """CLI main must exit 0 when actionlint reports no findings."""
    mod = _import_run_actionlint()

    with patch("scripts.run_actionlint.run_actionlint"), pytest.raises(SystemExit) as exc_info:
        mod.main()

    assert exc_info.value.code == 0, f"CLI must exit 0 on success, got {exc_info.value.code!r}"


@pytest.mark.unit
def test_run_actionlint_accepts_custom_binary(tmp_path: pathlib.Path) -> None:
    """The actionlint binary path must be configurable, not hard-coded."""
    mod = _import_run_actionlint()

    custom_binary = "/usr/local/bin/actionlint-v1.7.0"
    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        mod.run_actionlint(
            workflows_dir=str(tmp_path),
            actionlint_binary=custom_binary,
        )

    assert captured_calls[0][0] == custom_binary, (
        f"Expected binary {custom_binary!r}, got {captured_calls[0][0]!r}."
    )


# ---------------------------------------------------------------------------
# module-validate -- evaluate the module-type source policy against a module
# ---------------------------------------------------------------------------


def _write_module(module_root: pathlib.Path, files: dict[str, str]) -> None:
    """Write a mock terraform module's .tf files under module_root.

    Args:
        module_root: Directory representing the module (created if absent).
        files: Mapping of module-relative path -> file content.
    """
    for rel_path, content in files.items():
        target = module_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# A reference module whose child source is var-driven with const = true and an
# in-repo relative default -- the source-policy-compliant shape (zero violations).
_VALID_REFERENCE_MAIN_TF = "\n".join(
    [
        'module "kms" {',
        "  source = var.kms_source",
        "}",
    ]
)
_VALID_REFERENCE_VARIABLES_TF = "\n".join(
    [
        'variable "kms_source" {',
        "  type    = string",
        "  const   = true",
        '  default = "../../primitives/kms-key"',
        "}",
    ]
)

# A reference module with a bad literal local source (no var indirection) --
# triggers the "Local module source detected" violation.
_BAD_LOCAL_SOURCE_MAIN_TF = "\n".join(
    [
        'module "x" {',
        '  source = "../../primitives/x"',
        "}",
    ]
)


@_REQUIRES_OPA
@pytest.mark.unit
def test_module_validate_valid_reference_passes(tmp_path: pathlib.Path) -> None:
    """A reference module with valid var-driven const sources yields zero violations.

    The source reference (main.tf) and its const = true variable declaration
    (variables.tf) live in separate files; the validator must correlate them and
    report no violations, exiting cleanly.
    """
    mod = _import_run_opa()

    module_rel = "providers/aws/references/valid-ref"
    module_root = tmp_path / module_rel
    _write_module(
        module_root,
        {
            "main.tf": _VALID_REFERENCE_MAIN_TF,
            "variables.tf": _VALID_REFERENCE_VARIABLES_TF,
        },
    )

    # Resolve module_path relative to the temp repo root; keep the real policy bundle.
    with patch.object(mod, "_REPO_ROOT", tmp_path):
        # Must not raise -- zero violations.
        mod.run_module_validate(module_path=module_rel, module_type="reference")


@_REQUIRES_OPA
@pytest.mark.unit
def test_module_validate_bad_local_source_fails(tmp_path: pathlib.Path) -> None:
    """A reference module with a bad literal local source raises ModuleValidateError.

    A literal `source = "../../primitives/x"` (no var indirection) is a local
    module source the policy rejects, so the validator must fail closed.
    """
    mod = _import_run_opa()

    module_rel = "providers/aws/references/bad-ref"
    module_root = tmp_path / module_rel
    _write_module(module_root, {"main.tf": _BAD_LOCAL_SOURCE_MAIN_TF})

    with (
        patch.object(mod, "_REPO_ROOT", tmp_path),
        pytest.raises(mod.ModuleValidateError) as exc_info,
    ):
        mod.run_module_validate(module_path=module_rel, module_type="reference")

    err_msg = str(exc_info.value)
    assert "ERROR:" in err_msg, f"ModuleValidateError must contain 'ERROR:', got: {err_msg!r}"
    assert "violation" in err_msg.lower(), (
        f"ModuleValidateError must mention the violation(s), got: {err_msg!r}"
    )


@_REQUIRES_OPA
@pytest.mark.unit
def test_module_validate_bad_local_source_cli_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """The CLI must exit non-zero when the source policy reports a violation."""
    mod = _import_run_opa()

    module_rel = "providers/aws/references/bad-ref"
    module_root = tmp_path / module_rel
    _write_module(module_root, {"main.tf": _BAD_LOCAL_SOURCE_MAIN_TF})

    cli_argv = [
        "run_opa",
        "module-validate",
        "--module-path",
        module_rel,
        "--module-type",
        "reference",
    ]
    with (
        patch.object(mod, "_REPO_ROOT", tmp_path),
        patch.object(sys, "argv", cli_argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        mod.main()

    assert exc_info.value.code != 0, (
        f"CLI must exit non-zero on a source policy violation, got {exc_info.value.code!r}"
    )


@pytest.mark.unit
def test_module_validate_primitive_no_policy_passes(tmp_path: pathlib.Path) -> None:
    """A primitive module type has no source policy package -> passes without eval.

    Detection is by the absence of a source_policy.rego for the module type, so
    no opa invocation occurs (the subprocess must never be called).
    """
    mod = _import_run_opa()

    module_rel = "providers/aws/primitives/example"
    module_root = tmp_path / module_rel
    _write_module(module_root, {"main.tf": 'resource "aws_s3_bucket" "b" {}\n'})

    def fail_if_called(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        raise AssertionError(
            f"opa must not be invoked for a module type with no source policy; got {argv!r}"
        )

    with (
        patch.object(mod, "_REPO_ROOT", tmp_path),
        patch("subprocess.run", side_effect=fail_if_called),
    ):
        # primitive declares no source_policy.rego -> returns without raising.
        mod.run_module_validate(module_path=module_rel, module_type="primitive")


@pytest.mark.unit
def test_module_validate_missing_module_dir_fails(tmp_path: pathlib.Path) -> None:
    """A missing module directory must fail fast with ModuleValidateError."""
    mod = _import_run_opa()

    with (
        patch.object(mod, "_REPO_ROOT", tmp_path),
        pytest.raises(mod.ModuleValidateError) as exc_info,
    ):
        mod.run_module_validate(
            module_path="providers/aws/references/does-not-exist",
            module_type="reference",
        )

    assert "not found" in str(exc_info.value), (
        f"Expected a missing-directory error, got: {str(exc_info.value)!r}"
    )


@pytest.mark.unit
def test_module_validate_no_tf_files_fails(tmp_path: pathlib.Path) -> None:
    """A module directory with no in-scope .tf files must fail fast."""
    mod = _import_run_opa()

    module_rel = "providers/aws/references/empty-ref"
    module_root = tmp_path / module_rel
    # Only an excluded examples/ .tf file -- no in-scope .tf at the module root.
    _write_module(
        module_root,
        {"examples/default/main.tf": 'module "x" {\n  source = "../.."\n}\n'},
    )

    with (
        patch.object(mod, "_REPO_ROOT", tmp_path),
        pytest.raises(mod.ModuleValidateError) as exc_info,
    ):
        mod.run_module_validate(module_path=module_rel, module_type="reference")

    assert "no .tf files" in str(exc_info.value), (
        f"Expected a no-.tf-files error, got: {str(exc_info.value)!r}"
    )


@_REQUIRES_OPA
@pytest.mark.unit
def test_module_validate_excludes_examples_and_tests(tmp_path: pathlib.Path) -> None:
    """Bad sources under examples/ and tests/ must not produce violations.

    The validator must exclude examples/ and tests/ from the input it sends to
    the policy, matching the library policy's tf_files_for_module scope.
    """
    mod = _import_run_opa()

    module_rel = "providers/aws/references/scoped-ref"
    module_root = tmp_path / module_rel
    _write_module(
        module_root,
        {
            # In-scope, valid.
            "main.tf": _VALID_REFERENCE_MAIN_TF,
            "variables.tf": _VALID_REFERENCE_VARIABLES_TF,
            # Out-of-scope bad sources that must be ignored.
            "examples/default/main.tf": _BAD_LOCAL_SOURCE_MAIN_TF,
            "tests/unit/main.tf": _BAD_LOCAL_SOURCE_MAIN_TF,
        },
    )

    with patch.object(mod, "_REPO_ROOT", tmp_path):
        # Must not raise -- the bad examples/ and tests/ sources are out of scope.
        mod.run_module_validate(module_path=module_rel, module_type="reference")
