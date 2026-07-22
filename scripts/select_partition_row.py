"""select_partition_row -- pick ONE account's row out of a partition_units_by_account matrix.

Run via: uv run python -m scripts.select_partition_row

The ephemeral perf-test workflow stands up / tears down ONLY the sandbox SERVICE-account units
(the sandbox apply role has no cross-account dns-writer grant, so the shared dns-owner account's
units -- e.g. ``_singletons/dns_owner/dns-delegation`` -- must be excluded). ``partition_units_by_
account --mode apply`` already groups the full sandbox scope by resolved account into a matrix;
this helper selects the single row whose ``account_id`` matches the target (the sandbox service
account, read from ``env_accounts.json`` -- never hardcoded) and writes that row's deploy
``role_arn``, ``aws_region`` and ``include_dir_flags`` as GitHub Actions step outputs. The excluded
accounts' rows (dns-owner) are simply not selected -- the exclusion is account-derived, not a
hardcoded path list.

Fail-fast (CLAUDE.md, no fallback): a missing ``matrix=`` line, unparseable JSON, or no row for the
requested account aborts non-zero with an actionable message.

Outputs written (to --output / $GITHUB_OUTPUT):
    role_arn           the deploy role ARN for the selected account
    aws_region         the AWS region for the selected account
    include_dir_flags  the --queue-include-dir flags for that account's units (the apply scope)
    unit_count         the number of units in the selected scope

Exit codes::

    0 -- a row was selected and written
    1 -- usage / parse error, or no row for the requested account
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from scripts.constants import write_output

MATRIX_PREFIX = "matrix="


class SelectRowError(ValueError):
    """Raised when the matrix cannot be parsed or holds no row for the account."""


def extract_matrix(matrix_text: str) -> list[dict[str, Any]]:
    """Return the ``include`` rows from a partition ``matrix=<json>`` output blob."""
    line = next(
        (ln for ln in matrix_text.splitlines() if ln.startswith(MATRIX_PREFIX)),
        None,
    )
    if line is None:
        raise SelectRowError(
            f"ERROR: no '{MATRIX_PREFIX}' line found in the partition output.\n"
            "  Remedy: pass the file that `make tg-partition-units` wrote to its OUTPUT."
        )
    payload = line[len(MATRIX_PREFIX) :]
    try:
        matrix = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SelectRowError(f"ERROR: cannot parse the partition matrix JSON: {exc}") from exc
    rows = matrix.get("include", [])
    if not isinstance(rows, list):
        raise SelectRowError("ERROR: partition matrix 'include' is not a list.")
    return rows


def select_row(rows: list[dict[str, Any]], account_id: str) -> dict[str, Any]:
    """Return the single matrix row whose account_id == account_id (fail-fast when absent)."""
    matches = [r for r in rows if str(r.get("account_id")) == account_id]
    if not matches:
        available = ", ".join(sorted(str(r.get("account_id")) for r in rows)) or "(none)"
        raise SelectRowError(
            f"ERROR: no partition row for account '{account_id}'. "
            f"Available accounts: {available}.\n"
            "  Remedy: verify the scope includes at least one unit in that account."
        )
    if len(matches) > 1:
        raise SelectRowError(
            f"ERROR: {len(matches)} partition rows for account '{account_id}' (expected exactly 1)."
        )
    return matches[0]


def run(matrix_file: pathlib.Path, account_id: str, output_path: str) -> None:
    """Parse the matrix file, select the account's row, and write the scalar step outputs."""
    rows = extract_matrix(matrix_file.read_text(encoding="utf-8"))
    row = select_row(rows, account_id)
    flags = str(row.get("include_dir_flags", ""))
    unit_count = flags.count("--queue-include-dir")

    write_output(output_path, "role_arn", str(row["role_arn"]))
    write_output(output_path, "aws_region", str(row["aws_region"]))
    write_output(output_path, "include_dir_flags", flags)
    write_output(output_path, "unit_count", str(unit_count))
    print(
        f"select-partition-row: account={account_id} role_arn={row['role_arn']} "
        f"region={row['aws_region']} units={unit_count}"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select one account's row from a partition_units_by_account matrix and write its "
            "role_arn / aws_region / include_dir_flags as GitHub Actions step outputs."
        )
    )
    parser.add_argument(
        "--matrix-file",
        required=True,
        help="Path to the file `make tg-partition-units` wrote (contains a matrix=<json> line).",
    )
    parser.add_argument(
        "--account-id",
        required=True,
        help="The 12-digit account id whose row to select (e.g. the sandbox service account).",
    )
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for `uv run python -m scripts.select_partition_row`."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        run(pathlib.Path(args.matrix_file), args.account_id, args.output)
    except (SelectRowError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
