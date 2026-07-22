"""Thin wrapper that invokes Terraform and tflint for Terraform quality gates.

Backs the following Makefile targets:
- make tf-format       -> fmt [args]     -> terraform fmt [args]
- make tf-format-check -> fmt --check .  -> terraform fmt --check --recursive .
- make tf-lint         -> lint --chdir X -> tflint --chdir X
- make tf-validate     -> validate X     -> terraform -chdir=<ex> init -backend=false + validate
- make tf-plan         -> plan X         -> terraform -chdir=<ex> init -backend=false + plan

Architecture:
- run_terraform_command(): library function that maps a subcommand to its argv and
  delegates the subprocess to scripts.binary_runner.invoke_pinned_binary.
- main(): CLI entry point that parses sys.argv and dispatches; calls sys.exit only here.
- TerraformCommandError: specific exception carrying subcommand/argv/exit_code/stderr
  so fail-fast error messages are actionable in CI logs.

The shared invoke_pinned_binary helper from scripts.binary_runner keeps subprocess
boilerplate DRY across all quality-gate wrappers (run_opa.py, run_actionlint.py).
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Sequence

from scripts.binary_runner import invoke_pinned_binary

# ---------------------------------------------------------------------------
# Supported subcommands
# ---------------------------------------------------------------------------

_SUPPORTED_SUBCOMMANDS = frozenset({"fmt", "lint", "validate", "plan"})

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class TerraformCommandError(Exception):
    """Raised when the terraform or tflint binary exits with a non-zero code.

    Attributes:
        subcommand: The wrapper subcommand that was requested (e.g. fmt, lint).
        argv: The full argv list passed to the pinned binary.
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
            f"ERROR: terraform {subcommand!r} failed with exit code {exit_code}. "
            f"argv={argv!r}. stderr: {detail}. "
            f"Remediation: check that the pinned terraform/tflint binary is installed "
            f"via 'make tools-ensure' and that the Terraform configuration is valid."
        )


# ---------------------------------------------------------------------------
# Helper: discover examples under a module directory
# ---------------------------------------------------------------------------


def _discover_examples(module_path: str) -> list[str]:
    """Discover all example directories under <module_path>/examples/.

    Args:
        module_path: Path to the Terraform module root.

    Returns:
        A sorted list of example directory paths. May be empty if no examples exist.
    """
    examples_dir = pathlib.Path(module_path) / "examples"
    if not examples_dir.is_dir():
        return []
    return sorted(str(d) for d in examples_dir.iterdir() if d.is_dir())


# ---------------------------------------------------------------------------
# Helper: run one binary invocation and stream stdout/stderr
# ---------------------------------------------------------------------------


def _invoke_and_stream(
    binary: str,
    args: list[str],
    subcommand: str,
) -> None:
    """Invoke binary with args via invoke_pinned_binary and stream output.

    Args:
        binary: Path or name of the binary to invoke.
        args: Argument list to pass after the binary name.
        subcommand: Wrapper subcommand name (for error context).

    Raises:
        TerraformCommandError: If the binary exits with a non-zero exit code.
    """
    result = invoke_pinned_binary(
        binary=binary,
        args=args,
        capture_output=True,
    )

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    if result.returncode != 0:
        raise TerraformCommandError(
            subcommand=subcommand,
            argv=[binary, *args],
            exit_code=result.returncode,
            stderr_output=result.stderr,
        )


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def run_terraform_command(
    subcommand: str,
    extra_args: Sequence[str],
    terraform_binary: str = "terraform",
    tflint_binary: str = "tflint",
) -> None:
    """Invoke the pinned terraform/tflint binary for the requested subcommand.

    Subcommand mappings:
        fmt      -> terraform fmt [extra_args]
        lint     -> tflint [extra_args]  (--chdir <path> expected in extra_args)
        validate -> per examples/<ex>: terraform -chdir=<ex> init -backend=false + validate
        plan     -> per examples/<ex>: terraform -chdir=<ex> init -backend=false + plan

    Args:
        subcommand: One of fmt, lint, validate, plan.
        extra_args: Additional arguments forwarded to the binary argv.
            For fmt: passed directly to 'terraform fmt'.
            For lint: passed directly to 'tflint' (--chdir path required).
            For validate/plan: first element must be the module path.
        terraform_binary: Path or name of the pinned terraform binary.
        tflint_binary: Path or name of the pinned tflint binary.

    Raises:
        TerraformCommandError: If any binary invocation exits with a non-zero code.
        ValueError: If an unsupported subcommand is passed.
    """
    extra = list(extra_args)

    if subcommand == "fmt":
        _invoke_and_stream(terraform_binary, ["fmt", *extra], subcommand)

    elif subcommand == "lint":
        _invoke_and_stream(tflint_binary, extra, subcommand)

    elif subcommand in ("validate", "plan"):
        module_path = extra[0] if extra else "."
        examples = _discover_examples(module_path)
        targets = examples if examples else [module_path]

        for example_dir in targets:
            # Terraform requires the working directory be selected with the global
            # -chdir flag placed *before* the subcommand; passing the directory as a
            # positional argument fails with "Too many command line arguments".
            # Init offline
            _invoke_and_stream(
                terraform_binary,
                [f"-chdir={example_dir}", "init", "-backend=false"],
                subcommand,
            )
            # Validate or plan
            _invoke_and_stream(
                terraform_binary,
                [f"-chdir={example_dir}", subcommand],
                subcommand,
            )

    else:
        raise ValueError(
            f"Unsupported subcommand {subcommand!r}. Supported: {sorted(_SUPPORTED_SUBCOMMANDS)!r}"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse sys.argv and invoke the pinned terraform/tflint binary.

    Usage:
        uv run python -m scripts.run_terraform <subcommand> [args...]

    Subcommands:
        fmt [args]         -- run 'terraform fmt [args]'
        lint [args]        -- run 'tflint [args]' (pass --chdir <module>)
        validate <path>    -- run offline -chdir init + validate per examples/<ex>
        plan <path>        -- run offline -chdir init + plan per examples/<ex>

    Exits:
        0 on success.
        1 on terraform/tflint binary failure (TerraformCommandError).
        1 on missing pinned binary (FileNotFoundError).
        2 on missing or unknown subcommand argument.
    """
    argv = sys.argv[1:]
    if not argv:
        print(
            "ERROR: subcommand required. "
            "Usage: run_terraform <subcommand> [args...]\n"
            f"Subcommands: {', '.join(sorted(_SUPPORTED_SUBCOMMANDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    subcommand = argv[0]
    extra_args = argv[1:]

    if subcommand not in _SUPPORTED_SUBCOMMANDS:
        print(
            f"ERROR: unknown subcommand {subcommand!r}. "
            f"Supported subcommands: {', '.join(sorted(_SUPPORTED_SUBCOMMANDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        run_terraform_command(subcommand, extra_args)
    except TerraformCommandError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(exc.exit_code)
    except FileNotFoundError as exc:
        binary_name = "terraform"
        if "tflint" in str(exc):
            binary_name = "tflint"
        print(
            f"ERROR: required binary '{binary_name}' not found; run make tools-ensure",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
