"""filter_ci_deployable_units -- split a changed-unit apply scope into the CI-deployable units
(ci_deploy=true accounts) and the local-only units (ci_deploy=false accounts) so the
terragrunt-apply workflow deploys only the CI-deployable units and validly SKIPS the local-only
ones, instead of failing fast on a scope that happens to span a local-only account.

Run via: uv run python -m scripts.filter_ci_deployable_units

Why this exists:
  A single push to main can change a file (e.g. a shared _envcommon/*.hcl or common/*.json) whose
  dependent-unit scope spans BOTH a ci_deploy=true account (prod / dns-owner) AND the
  ci_deploy=false sandbox account (D33: sandbox is applied LOCALLY, never by CI). The apply path
  resolves ONE primary deploy role for the whole scope (scripts.resolve_deploy_role); a scope that
  includes the local-only sandbox account aborts (the scope "spans service accounts
  <prod>,<sandbox>" / ci_deploy=false), so terragrunt-apply can NEVER be green for such a change --
  and it jams the never-cancel tg-apply-prod concurrency lock behind the prod-apply environment
  gate until a human rejects the pending deployment.

  This filter removes the local-only (ci_deploy=false) units from the apply scope UP FRONT, leaving
  only the CI-deployable units for resolve_deploy_role + tg-apply. The skipped local-only units are
  listed on a clear, visible log line (never silently dropped). When the ENTIRE scope is local-only,
  the filter emits has_units=false so the apply is a clean CI no-op (SUCCESS), not a failure.

Account resolution + the ci_deploy flag come from the EXACT same source of truth as the deploy-role
path: resolve_account_id over common/env_accounts.json + common/domains.json, then
common/accounts.json[<resolved id>].ci_deploy (scripts.resolve_deploy_role). The filter therefore
never drifts from the runtime account resolution (DRY) -- "ci_deploy" is keyed on the RESOLVED
account, not on the env folder name, so e.g. a sandbox-folder _singletons/dns_owner unit that runs
in the shared (ci_deploy=true) dns-owner account is correctly KEPT, while a sandbox service unit
(ci_deploy=false sandbox account) is skipped.

Fail-fast (no fallback, CLAUDE.md): a unit whose resolved account is not registered in
accounts.json, an account row missing the ci_deploy key, or an unparseable unit path aborts
non-zero with an actionable message -- a genuine configuration error is NEVER silently skipped.
Only an account that EXPLICITLY declares ci_deploy=false is skipped.

Outputs (written to $GITHUB_OUTPUT, reusing the scripts.detect_terragrunt_units output contract so
the workflow can chain this step in front of the apply with no downstream key renaming):
    include_dir_flags   the --queue-include-dir flags for the CI-deployable units (may be empty)
    has_units           "true" when >=1 CI-deployable unit remains, else "false" (clean no-op)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

from scripts.constants import write_output
from scripts.detect_terragrunt_units import (
    OUTPUT_KEY_HAS_UNITS,
    OUTPUT_KEY_INCLUDE_DIR_FLAGS,
    QUEUE_INCLUDE_DIR_TOKEN,
)
from scripts.resolve_deploy_role import (
    DOMAINS_FILE,
    ENV_ACCOUNTS_FILE,
    AccountNotFoundError,
    AccountResolutionError,
    PathParseError,
    extract_all_unit_paths,
    is_account_ci_deployable,
    on_demand_opt_in,
    resolve_account_id,
)

# Visible marker for the skipped local-only units (so the skip is transparent, never silent).
SKIP_LOG_PREFIX = "[skipped: ci_deploy=false / local-only]"


def partition_by_ci_deploy(
    include_dir_flags: str,
    accounts_json_path: pathlib.Path,
    on_demand: bool = False,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split the unit scope into (deployable_paths, skipped) by resolved-account ci_deploy.

    Args:
        include_dir_flags: the --queue-include-dir flags string for the apply scope.
        accounts_json_path: path to common/accounts.json (env_accounts.json + domains.json are
            resolved as siblings, matching scripts.resolve_deploy_role).
        on_demand: when True the on-demand ephemeral-apply lane is active -- a unit whose resolved
            account is ci_deploy=false but declares ci_deploy_on_demand=true is KEPT (deployable)
            instead of skipped. Defaults to False, so the normal push lane is unchanged.

    Returns:
        A tuple ``(deployable, skipped)`` where ``deployable`` is the list of unit paths whose
        resolved account is CI-applyable (order preserved) and ``skipped`` is the list of
        ``(unit_path, account_id)`` pairs whose resolved account is local-only.

    Raises:
        PathParseError: an unparseable unit path (or an empty flags string).
        AccountResolutionError: an env/role that cannot be mapped to an account.
        AccountNotFoundError: a resolved account missing from accounts.json or missing ci_deploy.
    """
    common_dir = accounts_json_path.parent
    env_accounts: dict[str, Any] = json.loads(
        (common_dir / ENV_ACCOUNTS_FILE).read_text(encoding="utf-8")
    )
    domains: dict[str, Any] = json.loads((common_dir / DOMAINS_FILE).read_text(encoding="utf-8"))
    accounts: dict[str, Any] = json.loads(accounts_json_path.read_text(encoding="utf-8"))

    deployable: list[str] = []
    skipped: list[tuple[str, str]] = []
    for unit_path in extract_all_unit_paths(include_dir_flags):
        _region, account_id = resolve_account_id(unit_path, env_accounts, domains)
        if is_account_ci_deployable(account_id, accounts, accounts_json_path, on_demand):
            deployable.append(unit_path)
        else:
            skipped.append((unit_path, account_id))
    return deployable, skipped


def _reassemble_flags(unit_paths: list[str]) -> str:
    """Re-build the --queue-include-dir flags string for the deployable unit paths."""
    parts: list[str] = []
    for p in unit_paths:
        parts.extend([QUEUE_INCLUDE_DIR_TOKEN, p])
    return " ".join(parts)


def run(
    include_dir_flags: str,
    accounts_json_path: pathlib.Path,
    output_path: str,
    on_demand: bool = False,
) -> None:
    """Filter the apply scope to CI-deployable units and write the GitHub Actions outputs."""
    deployable, skipped = partition_by_ci_deploy(include_dir_flags, accounts_json_path, on_demand)

    if skipped:
        rendered = ", ".join(f"{path} (account {account_id})" for path, account_id in skipped)
        print(
            f"filter-ci-deployable-units: {SKIP_LOG_PREFIX} {len(skipped)} local-only unit(s) "
            f"will NOT be deployed by CI (applied locally, D33): {rendered}"
        )

    if not deployable:
        write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, "")
        write_output(output_path, OUTPUT_KEY_HAS_UNITS, "false")
        print(
            "filter-ci-deployable-units: 0 CI-deployable unit(s) remain after the local-only "
            "filter -- CI apply is a no-op for this change (has_units=false)."
        )
        return

    flags_str = _reassemble_flags(deployable)
    write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, flags_str)
    write_output(output_path, OUTPUT_KEY_HAS_UNITS, "true")
    print(
        f"filter-ci-deployable-units: {len(deployable)} CI-deployable unit(s) remain "
        f"({len(skipped)} local-only skipped). include_dir_flags written to {output_path}."
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Filter a changed Terragrunt apply scope to the CI-deployable units (ci_deploy=true "
            "accounts), skipping local-only (ci_deploy=false) units transparently so the apply "
            "workflow stays green for a scope that spans a local-only account."
        )
    )
    parser.add_argument(
        "--include-dir-flags",
        required=True,
        help="The --queue-include-dir flags string for the apply scope.",
    )
    parser.add_argument("--accounts-json", required=True, help="Path to common/accounts.json.")
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for `uv run python -m scripts.filter_ci_deployable_units`."""
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
            output_path=args.output,
            on_demand=on_demand_opt_in(os.environ),
        )
    except (
        PathParseError,
        AccountNotFoundError,
        AccountResolutionError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
