"""Thin wrapper that invokes the pinned trivy binary for security scanning.

Backs the following Makefile targets:
- make tf-security  -> config --exit-code 1 --ignorefile .trivyignore <path>
- make tg-security  -> config --exit-code 1 --ignorefile .trivyignore terragrunt/

The pinned trivy (0.71.0) `config` command IS the misconfiguration scanner and
does NOT accept the `--scanners` flag (that flag belongs to `trivy fs`/`image`),
so the canonical invocation omits it.

Architecture:
- run_trivy_command(): library function that invokes the trivy binary via
  scripts.binary_runner.invoke_pinned_binary.
- main(): CLI entry point that parses sys.argv and dispatches; calls sys.exit only here.
- TrivyCommandError: specific exception carrying argv/exit_code/stderr
  so fail-fast error messages are actionable in CI logs.

The shared invoke_pinned_binary helper from scripts.binary_runner keeps subprocess
boilerplate DRY across all quality-gate wrappers (run_opa.py, run_actionlint.py).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

import scripts.constants as _constants
from scripts.binary_runner import invoke_pinned_binary

# Env var name trivy's terraform scanner reads to resolve a variable's value
# (trivy honours TF_VAR_* the same way terraform does).
_TF_VAR_TERRATEST_RUN_ID: str = "TF_VAR_terratest_run_id"

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class TrivyCommandError(Exception):
    """Raised when the trivy binary exits with a non-zero code.

    Attributes:
        argv: The full argv list passed to the binary.
        exit_code: The non-zero exit code returned by the binary.
        stderr_output: stderr text captured from the binary.
    """

    def __init__(
        self,
        argv: list[str],
        exit_code: int,
        stderr_output: str,
    ) -> None:
        self.argv = argv
        self.exit_code = exit_code
        self.stderr_output = stderr_output
        detail = stderr_output.strip() if stderr_output.strip() else "(no stderr output)"
        super().__init__(
            f"ERROR: trivy failed with exit code {exit_code}. "
            f"argv={argv!r}. stderr: {detail}. "
            f"Remediation: check that the pinned trivy binary is installed via "
            f"'make tools-ensure' and review the reported security findings."
        )


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def run_trivy_command(
    args: Sequence[str],
    trivy_binary: str = "trivy",
) -> None:
    """Invoke the pinned trivy binary with the given arguments.

    Passes all args directly to the trivy binary via invoke_pinned_binary.
    The canonical invocation is:
        trivy config --exit-code 1 --ignorefile .trivyignore <path>

    Args:
        args: Arguments passed to trivy after the binary name.
        trivy_binary: Path or name of the pinned trivy binary. Must be configured
            externally -- never hard-coded inline.

    Raises:
        TrivyCommandError: If the trivy binary exits with a non-zero exit code.
    """
    full_args = list(args)
    # Make the run-id-scoped fixture names statically resolvable for the scanner.
    # Terratest example fixtures derive a per-run uniqueness suffix from
    # var.terratest_run_id; that variable has no Terraform default (it must
    # fail-fast at apply time if unset), so an offline trivy scan would leave it
    # unknown and falsely flag CloudFront/S3 access logging as disabled
    # (AWS-0010/AWS-0089) because the access-log bucket name cannot be resolved.
    # Exporting a static scan-time placeholder (never used by a real apply) lets
    # trivy resolve the name. setdefault preserves any value a caller already set.
    scan_env = dict(os.environ)
    scan_env.setdefault(_TF_VAR_TERRATEST_RUN_ID, _constants.TERRATEST_OFFLINE_SCAN_RUN_ID)
    result = invoke_pinned_binary(
        binary=trivy_binary,
        args=full_args,
        capture_output=True,
        env=scan_env,
    )

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    if result.returncode != 0:
        raise TrivyCommandError(
            argv=[trivy_binary, *full_args],
            exit_code=result.returncode,
            stderr_output=result.stderr,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse sys.argv and invoke the trivy binary with all provided arguments.

    Usage:
        uv run python -m scripts.run_trivy config
            --exit-code 1 --ignorefile .trivyignore <path>

    Exits:
        0 on success.
        1 on trivy binary failure (TrivyCommandError).
        1 on missing pinned binary (FileNotFoundError).
        2 on missing arguments.
    """
    args = sys.argv[1:]
    if not args:
        print(
            "ERROR: arguments required. "
            "Usage: run_trivy config "
            "--exit-code 1 --ignorefile .trivyignore <path>",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        run_trivy_command(args)
    except TrivyCommandError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(exc.exit_code)
    except FileNotFoundError:
        print(
            "ERROR: required binary 'trivy' not found; run make tools-ensure",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
