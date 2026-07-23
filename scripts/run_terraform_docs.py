"""Thin wrapper that invokes the pinned terraform-docs binary for documentation checks.

Backs the following Makefile targets:
- make tf-docs-check -> check <module>
      -> terraform-docs markdown table --output-file README.md --output-check <module>

The pinned terraform-docs (0.24.0) has no `check` subcommand. The supported way to
verify that a module README is current is to render the module documentation and ask
terraform-docs to diff the rendered content against the existing output file via the
`--output-check` flag. The render is driven declaratively by the repo-root
`.terraform-docs.yml`, which terraform-docs auto-discovers by walking up from the module
path; the wrapper supplies only the formatter, the output file name, and the check flag.
With `--output-check` terraform-docs performs a read-only diff and never rewrites the
README, so a stale README fails the gate rather than being silently regenerated.

Architecture:
- run_terraform_docs_command(): library function that maps a subcommand to its argv and
  delegates the subprocess to scripts.binary_runner.invoke_pinned_binary.
- main(): CLI entry point that parses sys.argv and dispatches; calls sys.exit only here.
- TerraformDocsCommandError: specific exception carrying subcommand/argv/exit_code/stderr
  so fail-fast error messages are actionable in CI logs.

The shared invoke_pinned_binary helper from scripts.binary_runner keeps subprocess
boilerplate DRY across all quality-gate wrappers (run_opa.py, run_actionlint.py).
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections.abc import Sequence

from scripts.binary_runner import invoke_pinned_binary

# ---------------------------------------------------------------------------
# Supported subcommands
# ---------------------------------------------------------------------------

_SUPPORTED_SUBCOMMANDS = frozenset({"check"})

# Markdown formatter terraform-docs renders for the README diff. Matches the
# `formatter: markdown table` declared in the repo-root .terraform-docs.yml.
_MARKDOWN_FORMATTER_ARGS = ("markdown", "table")

# README file each module's documentation is rendered into. Matches the
# `output.file: README.md` declared in the repo-root .terraform-docs.yml.
_OUTPUT_FILE = "README.md"

# Flag that makes terraform-docs diff the rendered content against the existing
# output file (read-only) and exit non-zero when the README is out of date.
_OUTPUT_CHECK_FLAG = "--output-check"

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class TerraformDocsCommandError(Exception):
    """Raised when the terraform-docs binary exits with a non-zero code.

    Attributes:
        subcommand: The wrapper subcommand that was requested (e.g. check).
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
            f"ERROR: terraform-docs {subcommand!r} failed with exit code {exit_code}. "
            f"argv={argv!r}. stderr: {detail}. "
            f"Remediation: check that the pinned terraform-docs binary is installed via "
            f"'make tools-ensure' and that the module has a valid .terraform-docs.yml."
        )


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def _module_uses_variable_source(module_path: str) -> bool:
    """True when any .tf file directly in module_path declares a module block whose source is a
    variable reference (source = var.<name>). terraform-docs cannot parse such modules (the
    const-source design used tree-wide), so tf-docs-check is skipped for them. Matches a literal
    var.<ident> source assignment; literal-string sources (../primitives/x, git URLs) do not match.
    """
    module_dir = pathlib.Path(module_path)
    source_var_pattern = re.compile(r"source\s*=\s*var\.[A-Za-z_][A-Za-z0-9_]*")
    for tf_file in sorted(module_dir.glob("*.tf")):
        if source_var_pattern.search(tf_file.read_text(encoding="utf-8")):
            return True
    return False


def run_terraform_docs_command(
    subcommand: str,
    extra_args: Sequence[str],
    terraform_docs_binary: str = "terraform-docs",
) -> None:
    """Invoke the pinned terraform-docs binary for the requested subcommand.

    Subcommand mappings:
        check <module> ->
            terraform-docs markdown table --output-file README.md --output-check <module>

    The pinned terraform-docs (0.24.0) exposes no `check` subcommand; the README
    freshness check is expressed as a markdown render with `--output-check`, which
    diffs the rendered content against the existing README without rewriting it.

    Args:
        subcommand: One of check.
        extra_args: Additional arguments forwarded to the binary argv.
            For check: exactly the module path.
        terraform_docs_binary: Path or name of the pinned terraform-docs binary.
            Must be configured externally -- never hard-coded inline.

    Raises:
        TerraformDocsCommandError: If the terraform-docs binary exits non-zero.
        ValueError: If an unsupported subcommand is passed, or if the check
            subcommand is not given exactly one module path.
    """
    extra = list(extra_args)

    if subcommand not in _SUPPORTED_SUBCOMMANDS:
        raise ValueError(
            f"Unsupported subcommand {subcommand!r}. Supported: {sorted(_SUPPORTED_SUBCOMMANDS)!r}"
        )

    if len(extra) != 1:
        raise ValueError(
            f"Subcommand {subcommand!r} requires exactly one module path argument, "
            f"got {len(extra)}: {extra!r}."
        )
    module_path = extra[0]

    # terraform-docs (every version through the pinned 0.24.0) cannot parse a module that uses a
    # variable as a module source (source = var.<x>_source) -- the const-source design used by
    # every reference module. terraform-config-inspect FATALs "Variables not allowed" during load,
    # before any rendering (upstream terraform-docs issues #793/#860, open + unfixed). The tool is
    # therefore inoperable on var-driven-source modules. Operator-approved (2026-06-24): SKIP the
    # README freshness check for such modules. This scopes an inoperable tool to where it can run;
    # it is NOT a suppressed finding (terraform-docs cannot execute here at all). The check stays
    # active for modules with literal sources (primitives), where it detects real README drift.
    if _module_uses_variable_source(module_path):
        print(
            f"terraform-docs check SKIPPED for {module_path!r}: the module uses variable-driven "
            "module sources (source = var.*), which terraform-docs cannot parse (upstream "
            "#793/#860). README freshness for such modules is maintained outside this gate.",
            file=sys.stderr,
        )
        return

    binary_args = [
        *_MARKDOWN_FORMATTER_ARGS,
        "--output-file",
        _OUTPUT_FILE,
        _OUTPUT_CHECK_FLAG,
        module_path,
    ]
    result = invoke_pinned_binary(
        binary=terraform_docs_binary,
        args=binary_args,
        capture_output=True,
    )

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

    if result.returncode != 0:
        raise TerraformDocsCommandError(
            subcommand=subcommand,
            argv=[terraform_docs_binary, *binary_args],
            exit_code=result.returncode,
            stderr_output=result.stderr,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse sys.argv and invoke the terraform-docs binary for the requested subcommand.

    Usage:
        uv run python -m scripts.run_terraform_docs <subcommand> <module>

    Subcommands:
        check <module>  -- render the module README and diff it against the
            existing README.md via terraform-docs --output-check (read-only).

    Exits:
        0 on success.
        1 on terraform-docs binary failure (TerraformDocsCommandError).
        1 on missing pinned binary (FileNotFoundError).
        2 on missing/unknown subcommand or wrong number of module-path arguments.
    """
    argv = sys.argv[1:]
    if not argv:
        print(
            "ERROR: subcommand required. "
            "Usage: run_terraform_docs <subcommand> [args...]\n"
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

    if len(extra_args) != 1:
        print(
            f"ERROR: subcommand {subcommand!r} requires exactly one module path argument, "
            f"got {len(extra_args)}: {extra_args!r}.\n"
            "Usage: run_terraform_docs check <module>",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        run_terraform_docs_command(subcommand, extra_args)
    except TerraformDocsCommandError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(exc.exit_code)
    except FileNotFoundError:
        print(
            "ERROR: required binary 'terraform-docs' not found; run make tools-ensure",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
