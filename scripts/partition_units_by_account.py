"""partition_units_by_account -- group a changed-unit scope by resolved AWS account so a
cross-account Terragrunt plan/apply can run each account's units under that account's own
OIDC role.

Run via: uv run python -m scripts.partition_units_by_account

Why this exists (the two multi-account-CI walls):
  WALL 1 (plan): a single PR can touch leaves in more than one account (e.g. a
    providers/aws/** repin referenced by BOTH prod and sandbox leaves, plus the
    prod _pretty/_dns_owner singletons that live in the dns-owner account). The root
    terragrunt.hcl generates ``allowed_account_ids = ["<unit account>"]`` per unit, so a
    plan run under a single account's OIDC role fails every unit in another account with
    "AWS account ID not allowed". The fix is one plan job per account, each assuming that
    account's plan role.
  WALL 2 (apply): the prod apply scope spans the prod service account AND the dns-owner
    account (prod _dns_owner/_pretty units). A single ``run --all apply`` under one ambient
    credential cannot satisfy both accounts' allowed_account_ids guards, and the dns-writer
    role-chain (configured last) clobbered the prod role so the prod state preflight ran as
    dns-writer. The fix is one apply job per account, each under that account's deploy role
    (the dns-owner account's units chain to the dns-writer role).

This module reuses the EXACT account-resolution rules from scripts.resolve_deploy_role
(``resolve_account_id``) so the partitioning never drifts from the runtime account.hcl tier
rules. It does NOT duplicate that logic (DRY).

Modes:
  --mode plan   -> per-account role = accounts.json[<id>].plan_role_name
  --mode apply  -> per-account role = accounts.json[<id>].deploy_role_name
                   (the dns-owner account additionally sets needs_dns_writer=true so the
                    workflow chains to the dns-writer role for that account's units)

Output (written to $GITHUB_OUTPUT as a single ``matrix`` key, a JSON object suitable for
``fromJSON`` in a GitHub Actions ``strategy.matrix``):

  {"include": [
     {"account_id": "...", "role_arn": "...", "needs_dns_writer": "false",
      "aws_region": "us-east-1",
      "include_dir_flags": "--queue-include-dir <p> --queue-include-dir <p> ..."},
     ...
  ]}

Empty scope (deletion-only no-op): when the detect step emits an EMPTY include_dir_flags (a
deletion-only changeset whose removed units are already destroyed, signalled has_units=false),
partition emits an EMPTY matrix ({"include": []}, exit 0) instead of failing. An empty matrix
expands to zero downstream plan jobs, so the plan lane is a clean green no-op. This mirrors the
detect step's empty-scope model; all non-empty behaviour is unchanged.

Fail-fast (no fallback, CLAUDE.md): an account row missing the required role-name key, an
account with ci_deploy=false in apply mode, or an unparseable (non-empty but tokenless) unit
scope aborts non-zero with an actionable message. Every account in a non-empty scope MUST
resolve to a usable role; a partial matrix is never emitted.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

from scripts.constants import write_output
from scripts.resolve_deploy_role import (
    CI_DEPLOY_KEY,
    CI_DEPLOY_ON_DEMAND_KEY,
    DEPLOY_ROLE_NAME_KEY,
    ENV_ACCOUNTS_FILE,
    ON_DEMAND_APPLY_ENV,
    AccountNotFoundError,
    AccountResolutionError,
    CIDeployForbiddenError,
    PathParseError,
    account_allows_ci_apply,
    build_role_arn,
    extract_all_unit_paths,
    on_demand_opt_in,
    resolve_account_id,
)

# accounts.json key holding the read-only plan role name for an account (WALL 1).
PLAN_ROLE_NAME_KEY = "plan_role_name"

# domains.json sibling (resolved next to accounts.json), needed by resolve_account_id for
# the _pretty tier's apex-asymmetry account resolution.
DOMAINS_FILE = "domains.json"

MODE_PLAN = "plan"
MODE_APPLY = "apply"

OUTPUT_KEY_MATRIX = "matrix"


class PartitionError(ValueError):
    """Raised when a scope cannot be partitioned into a usable per-account matrix."""


def _load_role_name(
    account_id: str,
    accounts: dict[str, Any],
    mode: str,
    path: pathlib.Path,
    on_demand: bool = False,
) -> str:
    """Return the role name to assume for account_id in the given mode (fail-fast).

    In apply mode, when ``on_demand`` is True an account that is ci_deploy=false but declares
    ci_deploy_on_demand=true resolves its deploy role (the on-demand ephemeral-apply lane) instead
    of failing fast. ``on_demand`` defaults to False, so the normal push lane is unchanged. Plan
    mode is read-only and never gated on ci_deploy.
    """
    if account_id not in accounts:
        raise AccountNotFoundError(
            f"ERROR: Account id '{account_id}' is not registered in {path}.\n"
            f"  Remedy: add a '{account_id}' entry with the required role-name keys."
        )
    entry = accounts[account_id]
    if CI_DEPLOY_KEY not in entry:
        raise AccountNotFoundError(
            f"ERROR: Account entry '{account_id}' in {path} is missing the required "
            f"'{CI_DEPLOY_KEY}' key."
        )
    if mode == MODE_APPLY:
        if not account_allows_ci_apply(entry, on_demand):
            account_role = entry.get("account_role", "unknown")
            raise CIDeployForbiddenError(
                f"ERROR: account {account_id} ({account_role}) is local-only (ci_deploy=false); "
                "CI cannot apply these units.\n  Remedy: apply those units locally, or run the "
                f"on-demand lane ({ON_DEMAND_APPLY_ENV} set AND {CI_DEPLOY_ON_DEMAND_KEY}=true for "
                "the account)."
            )
        key = DEPLOY_ROLE_NAME_KEY
    else:
        key = PLAN_ROLE_NAME_KEY
    if key not in entry:
        raise AccountNotFoundError(
            f"ERROR: Account entry '{account_id}' in {path} is missing the required "
            f"'{key}' key needed for mode '{mode}'.\n"
            f"  Remedy: add '{key}' to the '{account_id}' row."
        )
    return str(entry[key])


def _reassemble_flags(unit_paths: list[str]) -> str:
    """Re-build the --queue-include-dir flags string for a subset of unit paths."""
    parts: list[str] = []
    for p in unit_paths:
        parts.extend(["--queue-include-dir", p])
    return " ".join(parts)


def partition(
    include_dir_flags: str,
    accounts_json_path: pathlib.Path,
    mode: str,
    on_demand: bool = False,
) -> list[dict[str, str]]:
    """Partition the unit scope by resolved account and build the per-account matrix rows.

    When ``on_demand`` is True (apply mode), an account that is ci_deploy=false but declares
    ci_deploy_on_demand=true is included in the matrix under its deploy role (the on-demand
    ephemeral-apply lane). ``on_demand`` defaults to False, so the normal push lane is unchanged.
    """
    if mode not in (MODE_PLAN, MODE_APPLY):
        raise PartitionError(
            f"ERROR: unknown mode '{mode}' (expected '{MODE_PLAN}' or '{MODE_APPLY}')."
        )

    # An EMPTY scope (no --queue-include-dir tokens) is a clean no-op, not a parse error. The
    # detect step (scripts.detect_terragrunt_units) already models a deletion-only changeset --
    # whose removed units' live resources are already destroyed, so there is nothing to plan --
    # as empty include_dir_flags + has_units=false. Partition mirrors that: an empty scope yields
    # NO account rows (an empty matrix), so the downstream per-account plan matrix runs zero jobs
    # and the lane is green. A non-empty-but-tokenless string is genuinely malformed and still
    # fails fast via extract_all_unit_paths below (PathParseError, D3 fail-closed unchanged).
    if not include_dir_flags.strip():
        return []

    common_dir = accounts_json_path.parent
    env_accounts = json.loads((common_dir / ENV_ACCOUNTS_FILE).read_text(encoding="utf-8"))
    domains = json.loads((common_dir / DOMAINS_FILE).read_text(encoding="utf-8"))
    accounts = json.loads(accounts_json_path.read_text(encoding="utf-8"))

    dns_owner_account_id = str(env_accounts.get("dns_owner", {}).get("account_id", ""))

    all_paths = extract_all_unit_paths(include_dir_flags)

    # Group unit paths by resolved account id; preserve a stable region (first seen).
    by_account: dict[str, list[str]] = {}
    region_by_account: dict[str, str] = {}
    for p in all_paths:
        region, account_id = resolve_account_id(p, env_accounts, domains)
        by_account.setdefault(account_id, []).append(p)
        region_by_account.setdefault(account_id, region)

    rows: list[dict[str, str]] = []
    for account_id in sorted(by_account):
        role_name = _load_role_name(account_id, accounts, mode, accounts_json_path, on_demand)
        # In apply mode the dns-owner account's units run under the dns-writer role, which is
        # reached by chaining FROM the prod apply role (the dns-writer trust only permits the
        # prod apply role). needs_dns_writer tells the workflow to add the role-chain step for
        # this account's job; the chained role IS the deploy role for that account.
        is_dns_owner = account_id == dns_owner_account_id
        needs_dns_writer = mode == MODE_APPLY and is_dns_owner
        rows.append(
            {
                "account_id": account_id,
                "role_arn": build_role_arn(account_id, role_name),
                "needs_dns_writer": "true" if needs_dns_writer else "false",
                "aws_region": region_by_account[account_id],
                "include_dir_flags": _reassemble_flags(by_account[account_id]),
            }
        )
    return rows


def run(
    include_dir_flags: str,
    accounts_json_path: pathlib.Path,
    mode: str,
    output_path: str,
    on_demand: bool = False,
) -> None:
    """Partition the scope and write the GitHub Actions matrix output."""
    rows = partition(include_dir_flags, accounts_json_path, mode, on_demand)
    matrix = {"include": rows}
    write_output(output_path, OUTPUT_KEY_MATRIX, json.dumps(matrix))
    print(f"partition-units-by-account ({mode}): {len(rows)} account job(s):")
    for r in rows:
        count = r["include_dir_flags"].count("--queue-include-dir")
        print(
            f"  account_id={r['account_id']} role_arn={r['role_arn']} "
            f"needs_dns_writer={r['needs_dns_writer']} units={count}"
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Partition a changed Terragrunt unit scope by resolved AWS account into a "
            "GitHub Actions matrix so each account's units run under that account's OIDC role."
        )
    )
    parser.add_argument(
        "--include-dir-flags",
        required=True,
        help="The --queue-include-dir flags string from tg-detect-units.",
    )
    parser.add_argument("--accounts-json", required=True, help="Path to common/accounts.json.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=[MODE_PLAN, MODE_APPLY],
        help=(
            "plan -> use plan_role_name; apply -> use deploy_role_name "
            "(dns-owner chains to dns-writer)."
        ),
    )
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for `uv run python -m scripts.partition_units_by_account`."""
    args = _parse_args(sys.argv[1:])
    accounts_json_path = pathlib.Path(args.accounts_json)

    for required in (
        accounts_json_path,
        accounts_json_path.parent / ENV_ACCOUNTS_FILE,
        accounts_json_path.parent / DOMAINS_FILE,
    ):
        if not required.exists():
            print(
                f"ERROR: required config not found at {required}\n"
                "  Remedy: pass a valid --accounts-json path (env_accounts.json + domains.json "
                "are resolved as siblings).",
                file=sys.stderr,
            )
            return 1

    try:
        run(
            include_dir_flags=args.include_dir_flags,
            accounts_json_path=accounts_json_path,
            mode=args.mode,
            output_path=args.output,
            on_demand=on_demand_opt_in(os.environ),
        )
    except (
        PathParseError,
        AccountNotFoundError,
        AccountResolutionError,
        CIDeployForbiddenError,
        PartitionError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
