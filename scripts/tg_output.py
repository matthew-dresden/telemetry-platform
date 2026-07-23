"""tg_output -- wraps 'terragrunt output -raw <name>' for a named unit dir.

Run via: uv run python -m scripts.tg_output
Exposed as: make tg-output UNIT=<dir> NAME=<output_name> OUTPUT=<output_file>

Runs 'terragrunt output -raw <name>' for the given unit directory, writes the
'<name>=<value>' line to the OUTPUT file (GitHub Actions output format), and
fails closed with a non-zero exit when the named output has no value.

CLI arguments (all required):
    --unit      path to the Terragrunt unit directory
    --name      name of the Terragrunt output to fetch
    --output    path to the GitHub Actions output file ($GITHUB_OUTPUT)
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class EmptyOutputValueError(RuntimeError):
    """Raised when terragrunt output -raw returns an empty value for the named output."""


# ---------------------------------------------------------------------------
# Core logic functions (pure -- dependency-injected for testability)
# ---------------------------------------------------------------------------


def parse_raw_output(name: str, raw_value: str) -> str:
    """Validate and return the raw output value.

    Args:
        name: The output variable name (for error messages).
        raw_value: The raw string returned by terragrunt output -raw.

    Returns:
        The stripped value string.

    Raises:
        EmptyOutputValueError: If the value is empty or whitespace-only.
    """
    stripped = raw_value.strip()
    if not stripped:
        raise EmptyOutputValueError(
            f"ERROR: Terragrunt output '{name}' returned an empty value.\n"
            f"  The output must be present and non-empty before writing to the OUTPUT file.\n"
            f"  Remedy: verify that the Terragrunt unit has been applied and the output\n"
            f"  '{name}' is defined and populated in the unit's outputs.tf."
        )
    return stripped


def write_output_pair(output_path: str, name: str, value: str) -> None:
    """Append '<name>=<value>' to the GitHub Actions output file.

    Args:
        output_path: Path to the GitHub Actions output file.
        name: Output variable name.
        value: Output variable value.
    """
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


# ---------------------------------------------------------------------------
# Subprocess wrapper
# ---------------------------------------------------------------------------


def fetch_terragrunt_output(unit_dir: str, output_name: str) -> str:
    """Run 'terragrunt output -raw <name>' in the given unit directory.

    Args:
        unit_dir: Path to the Terragrunt unit directory.
        output_name: Name of the output to fetch.

    Returns:
        The raw output value string.

    Raises:
        RuntimeError: If the terragrunt command fails.
    """
    result = subprocess.run(
        ["terragrunt", "output", "-raw", output_name],
        cwd=unit_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ERROR: terragrunt output -raw {output_name} failed (exit {result.returncode}).\n"
            f"  Unit: {unit_dir}\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  Remedy: verify the unit is initialized and the output '{output_name}' exists."
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a named Terragrunt output and write it to the GitHub Actions output file."
        )
    )
    parser.add_argument("--unit", required=True, help="Path to the Terragrunt unit directory.")
    parser.add_argument("--name", required=True, help="Name of the Terragrunt output to fetch.")
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for `uv run python -m scripts.tg_output`."""
    args = _parse_args(sys.argv[1:])

    try:
        raw_value = fetch_terragrunt_output(unit_dir=args.unit, output_name=args.name)
        value = parse_raw_output(name=args.name, raw_value=raw_value)
        write_output_pair(output_path=args.output, name=args.name, value=value)
    except EmptyOutputValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"tg-output: {args.name}={value} written to {args.output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
