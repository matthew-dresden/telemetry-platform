"""Thin wrapper that invokes the pinned actionlint binary over .github/workflows.

Backs the Makefile target:
    make actionlint -> uv run python -m scripts.run_actionlint

Architecture:
- run_actionlint(): library function that builds and invokes the actionlint subprocess.
- main(): CLI entry point; calls sys.exit only here.
- ActionlintError: specific exception carrying argv/exit-code context for fail-fast.

The shared invoke_pinned_binary helper from scripts.binary_runner keeps the subprocess
boilerplate DRY across all quality-gate wrappers (Approach step 11).
"""

from __future__ import annotations

import pathlib
import sys

from scripts.binary_runner import invoke_pinned_binary

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class ActionlintError(Exception):
    """Raised when the actionlint binary exits with a non-zero code.

    Attributes:
        argv: The full argv list passed to the binary.
        exit_code: The non-zero exit code returned by the binary.
        output: Combined stdout/stderr captured from the binary.
    """

    def __init__(
        self,
        argv: list[str],
        exit_code: int,
        output: str,
    ) -> None:
        self.argv = argv
        self.exit_code = exit_code
        self.output = output
        detail = output.strip() if output.strip() else "(no output)"
        super().__init__(
            f"ERROR: actionlint failed with exit code {exit_code}. "
            f"argv={argv!r}. output: {detail}. "
            f"Remediation: fix the reported workflow violations and re-run 'make actionlint'."
        )


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def run_actionlint(
    workflows_dir: str | pathlib.Path | None = None,
    actionlint_binary: str = "actionlint",
) -> None:
    """Invoke the pinned actionlint binary over the .github/workflows directory.

    Fails fast with an ActionlintError if the binary exits non-zero (indicating
    one or more workflow violations were found).

    Args:
        workflows_dir: Path to the workflows directory to scan. Defaults to
            '.github/workflows' relative to the repository root.
        actionlint_binary: Path or name of the pinned actionlint binary.
            Must be configured externally -- never hard-coded inline.

    Raises:
        ActionlintError: If actionlint exits with a non-zero exit code.
    """
    if workflows_dir is None:
        repo_root = pathlib.Path(__file__).parent.parent
        resolved_dir = repo_root / ".github" / "workflows"
    else:
        resolved_dir = pathlib.Path(workflows_dir)

    workflow_files = sorted(resolved_dir.glob("*.yml")) + sorted(resolved_dir.glob("*.yaml"))
    args = [str(f) for f in workflow_files]
    result = invoke_pinned_binary(binary=actionlint_binary, args=args, capture_output=True)

    if result.returncode != 0:
        raise ActionlintError(
            argv=[actionlint_binary, *args],
            exit_code=result.returncode,
            output=result.stdout + result.stderr,
        )

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Invoke actionlint over .github/workflows and exit non-zero on any finding.

    Exits:
        0 if actionlint reports no violations.
        1 if actionlint reports one or more violations.
    """
    try:
        run_actionlint()
    except ActionlintError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
