"""Thin wrapper that shells out to the pinned opa binary for OPA quality gates.

Backs the following Makefile targets (D11):
- make rego-format    -> run_opa fmt --list policies
- make rego-lint      -> run_opa check policies
- make rego-unit-test-coverage -> run_opa test policies --coverage --threshold <N>
- make module-validate -> run_opa module-validate --module-path <p> --module-type <t>

This module explicitly does NOT invoke any Go file-collector binary (D11 constraint).
The only subprocess invocation is to the pinned opa binary.

Architecture:
- run_opa_command(): library function that builds and invokes the opa subprocess.
- run_module_validate(): evaluates the module-type source policy against a module's
  terraform files via `opa eval` over the policies bundle.
- main(): CLI entry point that parses sys.argv and delegates; calls sys.exit only here.
- OpaCommandError: specific exception carrying file/argv context for fail-fast.
- ModuleValidateError: specific exception for module-validate setup failures.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from collections.abc import Sequence

from scripts.binary_runner import invoke_pinned_binary

# ---------------------------------------------------------------------------
# Fixed repository locations (not business config: these are the canonical
# in-repo paths of the OPA policy bundle, resolved relative to this file).
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_POLICIES_BUNDLE = _REPO_ROOT / "policies" / "opa" / "terraform"
_MODULE_TYPES_DIR = _POLICIES_BUNDLE / "provider" / "aws" / "module_types"

# Directories whose .tf files are out of scope for the source policy. The
# library policy (tf_files_for_module) excludes these same paths.
_EXCLUDED_SUBDIRS = ("examples", "tests")

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class OpaCommandError(Exception):
    """Raised when the opa binary exits with a non-zero code.

    Attributes:
        subcommand: The OPA subcommand that failed (fmt, check, test).
        argv: The full argv list passed to the binary.
        exit_code: The non-zero exit code returned by the binary.
        stderr_output: stderr text captured from the binary.
    """

    def __init__(
        self,
        subcommand: str,
        argv: list[str],
        exit_code: int,
        stderr_output: str,
    ) -> None:
        self.subcommand = subcommand
        self.argv = argv
        self.exit_code = exit_code
        self.stderr_output = stderr_output
        detail = stderr_output.strip() if stderr_output.strip() else "(no stderr output)"
        super().__init__(
            f"ERROR: opa {subcommand!r} failed with exit code {exit_code}. "
            f"argv={argv!r}. stderr: {detail}. "
            f"Remediation: check that the pinned opa binary is installed and "
            f"that the policies directory is valid."
        )


class ModuleValidateError(Exception):
    """Raised when module-validate cannot evaluate the source policy.

    Covers fail-fast setup conditions (missing module directory, no .tf files)
    and propagates a clear, actionable message so CI logs identify the cause.
    """


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def run_opa_command(
    subcommand: str,
    args: Sequence[str],
    opa_binary: str = "opa",
) -> None:
    """Invoke the pinned opa binary for a specific subcommand over the policies tree.

    Supports fmt, check, and test subcommands. Never invokes any Go file-collector
    binary (D11). Fails fast with an OpaCommandError if the binary exits non-zero.

    Args:
        subcommand: OPA subcommand to run (fmt, check, test).
        args: Additional arguments passed to the opa subcommand.
        opa_binary: Path or name of the pinned opa binary. Must be configured
            externally -- never hard-coded inline.

    Raises:
        OpaCommandError: If the opa binary exits with a non-zero exit code.
            The error message includes the argv, exit code, and stderr output
            so the caller or CI log immediately identifies the failure.
    """
    full_args = [subcommand, *list(args)]
    result = invoke_pinned_binary(
        binary=opa_binary,
        args=full_args,
        capture_output=True,
    )

    if result.returncode != 0:
        raise OpaCommandError(
            subcommand=subcommand,
            argv=[opa_binary, *full_args],
            exit_code=result.returncode,
            stderr_output=result.stderr,
        )

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


# ---------------------------------------------------------------------------
# module-validate: evaluate the module-type source policy
# ---------------------------------------------------------------------------


def _module_type_has_policy(module_type: str) -> bool:
    """Return True when the module type declares a source policy package.

    Only module types with a source_policy.rego under
    policies/opa/terraform/provider/aws/module_types/<module_type>/ are
    evaluated. Leaf module types (e.g. primitive) declare no source policy and
    therefore have nothing to validate.

    Args:
        module_type: The resolved module type (e.g. reference, collection, primitive).

    Returns:
        True if a source_policy.rego exists for the module type, else False.
    """
    return (_MODULE_TYPES_DIR / module_type / "source_policy.rego").is_file()


def _collect_module_tf_content(module_dir: pathlib.Path) -> str:
    """Collect and concatenate every in-scope *.tf file's content for a module.

    Recurses through the module directory, including every *.tf file but
    excluding any file under an examples/ or tests/ subdirectory (the source
    policy scope excludes those, matching tf_files_for_module in the library
    policy).

    The contents are concatenated into a single string because the source
    policy evaluates the const-source contract within a single file's text:
    a child module's `source = var.<name>_source` reference and the matching
    `variable "<name>_source" { const = true ... }` declaration normally live
    in separate files (main.tf vs variables.tf). Presenting the module's whole
    .tf content as one unit lets the policy correlate them, exactly as the
    policy's own unit tests co-locate both constructs in one file.

    Args:
        module_dir: The resolved module directory.

    Returns:
        The concatenated content of every in-scope *.tf file, ordered by path
        for deterministic evaluation.

    Raises:
        ModuleValidateError: If the directory is missing or has no in-scope .tf files.
    """
    if not module_dir.is_dir():
        raise ModuleValidateError(
            f"ERROR: module directory not found: {module_dir}. "
            "Remediation: pass --module-path pointing to an existing module directory."
        )

    tf_files = sorted(
        tf
        for tf in module_dir.rglob("*.tf")
        if not any(part in _EXCLUDED_SUBDIRS for part in tf.relative_to(module_dir).parts[:-1])
    )

    if not tf_files:
        raise ModuleValidateError(
            f"ERROR: no .tf files found in module directory: {module_dir} "
            f"(excluding {', '.join(_EXCLUDED_SUBDIRS)} subdirectories). "
            "Remediation: ensure the module path points to a Terraform module."
        )

    return "\n".join(tf.read_text(encoding="utf-8") for tf in tf_files)


def _build_module_input(module_path: str, combined_content: str) -> dict[str, object]:
    """Build the policy input JSON for a module.

    The files map is keyed by a single combined-content path under module_path
    so it satisfies the library policy's tf_files_for_module prefix gate
    (startswith "<module_path>/") and .tf suffix gate. See
    _collect_module_tf_content for why the content is concatenated.

    Args:
        module_path: The module path string (used as both module_path and repo_path).
        combined_content: The concatenated *.tf content for the module.

    Returns:
        The input object matching the policy contract.
    """
    combined_key = f"{module_path}/__module_validate_combined__.tf"
    return {
        "module_path": module_path,
        "repo_path": module_path,
        "files": {combined_key: combined_content},
    }


def _extract_violations(stdout: str) -> list[object]:
    """Extract the violation list from an `opa eval --format json` stdout.

    The violations live at result[0].expressions[0].value. They are absent (the
    rule is undefined) or empty when there are no violations.

    Args:
        stdout: The JSON text emitted by `opa eval --format json`.

    Returns:
        The list of violation objects (empty when there are no violations).

    Raises:
        ModuleValidateError: If the stdout is not valid JSON.
    """
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ModuleValidateError(
            f"ERROR: could not parse opa eval JSON output: {exc}. Raw output: {stdout!r}"
        ) from exc

    result = parsed.get("result")
    if not result:
        return []
    expressions = result[0].get("expressions")
    if not expressions:
        return []
    value = expressions[0].get("value")
    if not value:
        return []
    return list(value)


def run_module_validate(
    module_path: str,
    module_type: str,
    opa_binary: str = "opa",
) -> None:
    """Evaluate the module-type source policy against a module's terraform files.

    Module types that declare no source policy package (e.g. primitive, a leaf
    module with no child module blocks) have nothing to validate and pass.

    For module types that do declare a source policy, this collects the module's
    in-scope *.tf content, writes the policy input to a temp file, and runs the
    pinned opa binary:

        opa eval --bundle <policies/opa/terraform> --input <tmp.json> --format json
            'data.terraform.provider.aws.module_types.<module_type>.source.violation'

    Args:
        module_path: Repo-relative module directory path to validate.
        module_type: The module type (selects the source policy package).
        opa_binary: Path or name of the pinned opa binary.

    Raises:
        ModuleValidateError: On setup failure (missing dir, no .tf files, bad JSON)
            or when the policy reports one or more violations.
        OpaCommandError: If the opa binary itself exits non-zero (e.g. bundle load
            failure), surfacing argv, exit code, and stderr for fail-fast.
    """
    if not _module_type_has_policy(module_type):
        print(
            f"module-validate: module type {module_type!r} declares no source "
            f"policy; nothing to validate.",
            file=sys.stderr,
        )
        return

    module_dir = (_REPO_ROOT / module_path).resolve()
    combined_content = _collect_module_tf_content(module_dir)
    policy_input = _build_module_input(module_path, combined_content)

    query = f"data.terraform.provider.aws.module_types.{module_type}.source.violation"

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        encoding="utf-8",
        delete=True,
    ) as input_file:
        json.dump(policy_input, input_file)
        input_file.flush()

        eval_args = [
            "eval",
            "--bundle",
            str(_POLICIES_BUNDLE),
            "--input",
            input_file.name,
            "--format",
            "json",
            query,
        ]
        result = invoke_pinned_binary(
            binary=opa_binary,
            args=eval_args,
            capture_output=True,
        )

    if result.returncode != 0:
        raise OpaCommandError(
            subcommand="eval",
            argv=[opa_binary, *eval_args],
            exit_code=result.returncode,
            stderr_output=result.stderr,
        )

    violations = _extract_violations(result.stdout)
    if violations:
        print(
            f"ERROR: module-validate found {len(violations)} source policy "
            f"violation(s) for module {module_path!r} (type {module_type!r}):",
            file=sys.stderr,
        )
        for item in violations:
            if isinstance(item, dict):
                message = item.get("message", "(no message)")
                details = item.get("details", "")
                resolution = item.get("resolution", "")
                print(f"  - {message}: {details} | {resolution}", file=sys.stderr)
            else:
                print(f"  - {item}", file=sys.stderr)
        raise ModuleValidateError(
            f"ERROR: module-validate failed with {len(violations)} violation(s) "
            f"for module {module_path!r}."
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _run_module_validate_cli(extra_args: Sequence[str]) -> None:
    """Parse module-validate arguments and run the validation, exiting on result.

    Args:
        extra_args: The argv after the 'module-validate' subcommand.
    """
    parser = argparse.ArgumentParser(
        prog="run_opa module-validate",
        description="Evaluate the module-type source policy against a module's terraform files.",
    )
    parser.add_argument(
        "--module-path",
        required=True,
        help="Repo-relative module directory path to validate.",
    )
    parser.add_argument(
        "--module-type",
        required=True,
        help="Module type selecting the source policy package (e.g. reference, collection).",
    )
    args = parser.parse_args(list(extra_args))

    try:
        run_module_validate(module_path=args.module_path, module_type=args.module_type)
    except (ModuleValidateError, OpaCommandError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def main() -> None:
    """Parse sys.argv and invoke the opa binary for the requested subcommand.

    Usage:
        uv run python -m scripts.run_opa <subcommand> [args...]

    Subcommands:
        fmt --list policies       -- format check
        check policies            -- lint/check
        test policies --coverage  -- test with coverage
        module-validate --module-path <p> --module-type <t>
            -- evaluate the module-type source policy against a module

    Exits:
        0 on success.
        1 on opa binary failure (OpaCommandError) or module-validate violation.
        2 on missing subcommand argument.
    """
    argv = sys.argv[1:]
    if not argv:
        print(
            "ERROR: subcommand required. Usage: run_opa <subcommand> [args...]\n"
            "Subcommands: fmt, check, test, module-validate",
            file=sys.stderr,
        )
        sys.exit(2)

    subcommand = argv[0]
    extra_args = argv[1:]

    if subcommand == "module-validate":
        _run_module_validate_cli(extra_args)
        return

    try:
        run_opa_command(subcommand, extra_args)
    except OpaCommandError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
