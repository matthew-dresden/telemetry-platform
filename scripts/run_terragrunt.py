"""Thin wrapper that invokes the pinned terragrunt binary for infrastructure quality gates.

Backs the following Makefile targets:
- make tg-format-check  -> hcl-format-check -> terragrunt hcl format --check
- make tg-validate      (Makefile:195) -> validate          -> terragrunt hcl validate
- make tg-plan          (Makefile:201) -> plan [flags]      -> terragrunt run --all plan [flags]
- make tg-apply         (Makefile:204) -> apply [flags]     -> terragrunt run --all apply [flags]
- make tg-destroy       -> destroy [flags]   -> terragrunt run --all destroy [flags]
  (ephemeral teardown lane only -- e.g. the sandbox perf-test workflow's stand-up/tear-down)

Architecture:
- run_terragrunt_command(): library function that maps a subcommand to its argv and
  delegates the subprocess to scripts.binary_runner.invoke_pinned_binary.
- main(): CLI entry point that parses sys.argv and dispatches; calls sys.exit only here.
- TerragruntCommandError: specific exception carrying subcommand/argv/exit_code/stderr
  so fail-fast error messages are actionable in CI logs.

The shared invoke_pinned_binary helper from scripts.binary_runner keeps subprocess
boilerplate DRY across all quality-gate wrappers (run_opa.py, run_actionlint.py).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from scripts.binary_runner import invoke_pinned_binary

# ---------------------------------------------------------------------------
# Supported subcommands
# ---------------------------------------------------------------------------

_SUPPORTED_SUBCOMMANDS = frozenset({"validate", "hcl-format-check", "plan", "apply", "destroy"})

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class TerragruntCommandError(Exception):
    """Raised when the terragrunt binary exits with a non-zero code.

    Attributes:
        subcommand: The wrapper subcommand that was requested (e.g. validate, plan).
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
            f"ERROR: terragrunt {subcommand!r} failed with exit code {exit_code}. "
            f"argv={argv!r}. stderr: {detail}. "
            f"Remediation: check that the pinned terragrunt binary is installed, "
            f"AWS credentials are configured, and the HCL configuration is valid."
        )


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def run_terragrunt_command(
    subcommand: str,
    extra_args: Sequence[str],
    terragrunt_binary: str = "terragrunt",
) -> None:
    """Invoke the pinned terragrunt binary for the requested subcommand.

    Maps wrapper subcommand names to the terragrunt argv expected by the binary,
    then delegates the subprocess call to scripts.binary_runner.invoke_pinned_binary
    so no subprocess boilerplate is duplicated here (DRY with run_opa.py).

    Subcommand mappings:
        validate        -> terragrunt hcl validate [extra_args]
        hcl-format-check -> terragrunt hcl format --check
        plan            -> terragrunt run --all plan [extra_args]
        apply           -> terragrunt run --all apply [extra_args]
        destroy         -> terragrunt run --all destroy [extra_args]

    Args:
        subcommand: One of validate, hcl-format-check, plan, apply.
        extra_args: Additional arguments forwarded to the terragrunt argv.
            Used for --include-dir flags on plan/apply targets.
        terragrunt_binary: Path or name of the pinned terragrunt binary. Must be
            configured externally -- never hard-coded inline.

    Raises:
        TerragruntCommandError: If the terragrunt binary exits with a non-zero
            exit code. The error carries subcommand, full argv, exit code, and
            captured stderr for actionable CI log output.
        ValueError: If an unsupported subcommand is passed. Callers should
            validate subcommands before calling this function; main() does this.
    """
    extra = list(extra_args)

    if subcommand == "validate":
        # `terragrunt hcl validate` recursively discovers + validates the HCL configs.
        # The bare `terragrunt validate` (an OpenTofu/Terraform passthrough) requires a
        # `terragrunt.hcl` in the working directory; this repo's root config is `root.hcl`
        # (the renamed root), so the passthrough fails at the repo root with
        # "folder that does not contain a terragrunt.hcl file". Callers scope validation to
        # the changed units via --queue-include-dir flags (passed through extra_args), exactly
        # like the change-scoped plan, so unrelated units' deploy-time get_env() locals are
        # never evaluated.
        binary_args = ["hcl", "validate"] + extra
    elif subcommand == "hcl-format-check":
        binary_args = ["hcl", "format", "--check"] + extra
    elif subcommand == "plan":
        binary_args = ["run", "--all", "plan"] + extra
    elif subcommand == "apply":
        # --non-interactive: a CI runner has no TTY, so `terragrunt run --all apply` blocks on
        # the "Are you sure you want to run 'terragrunt apply' ...? (y/n)" prompt and aborts with
        # "ERROR EOF". --non-interactive auto-answers the run-queue confirmation (terragrunt then
        # passes -auto-approve to each unit's terraform apply). This is apply-only -- plan and
        # validate never prompt -- so it is not added to the other subcommands.
        binary_args = ["run", "--all", "apply", "--non-interactive"] + extra
    elif subcommand == "destroy":
        # Mirrors apply's non-interactive contract: `terragrunt run --all destroy` on a CI runner
        # (no TTY) would otherwise block on the destroy confirmation prompt and abort with "ERROR
        # EOF". --non-interactive auto-answers the run-queue confirmation (terragrunt then passes
        # -auto-approve to each unit's terraform destroy). Used ONLY by the ephemeral teardown lane
        # (the sandbox perf-test workflow), always change-scoped via --queue-include-dir extra_args
        # so it tears down exactly the stand-up scope and nothing else.
        binary_args = ["run", "--all", "destroy", "--non-interactive"] + extra
    else:
        raise ValueError(
            f"Unsupported subcommand {subcommand!r}. Supported: {sorted(_SUPPORTED_SUBCOMMANDS)!r}"
        )

    result = invoke_pinned_binary(
        binary=terragrunt_binary,
        args=binary_args,
        capture_output=True,
    )

    if result.returncode != 0:
        raise TerragruntCommandError(
            subcommand=subcommand,
            argv=[terragrunt_binary, *binary_args],
            exit_code=result.returncode,
            stderr_output=result.stderr,
        )

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse sys.argv and invoke the pinned terragrunt binary for the requested subcommand.

    Usage:
        uv run python -m scripts.run_terragrunt <subcommand> [args...]

    Subcommands:
        validate          -- run 'terragrunt hcl validate [args]'
        hcl-format-check  -- run 'terragrunt hcl format --check'
        plan [flags]      -- run 'terragrunt run --all plan [flags]'
        apply [flags]     -- run 'terragrunt run --all apply [flags]'
        destroy [flags]   -- run 'terragrunt run --all destroy [flags]'

    Exits:
        0 on success.
        1 on terragrunt binary failure (TerragruntCommandError).
        2 on missing or unknown subcommand argument.
    """
    argv = sys.argv[1:]
    if not argv:
        print(
            "ERROR: subcommand required. "
            "Usage: run_terragrunt <subcommand> [args...]\n"
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
        run_terragrunt_command(subcommand, extra_args)
    except TerragruntCommandError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(
            "ERROR: required binary 'terragrunt' not found; run make tools-ensure",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
