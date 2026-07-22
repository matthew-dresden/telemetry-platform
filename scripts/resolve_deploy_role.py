"""resolve_deploy_role -- resolves the OIDC deploy-role ARN + region for a changed Terragrunt
unit in the env-keyed live tree (the AWS account is abstracted OUT of the folder path).

Run via: uv run python -m scripts.resolve_deploy_role

The live-tree path grammar is env-keyed (D2 - no 12-digit account folders). The 4th namespace
field (env_instance) has three sub-classes: numeric set (NNN), bare tier word
(shared|dns_owner|pretty, under _singletons/), and "*_role" (under bootstrap/):
    .../live/telemetry/<region>/<env>/...                  (env service tree: sandbox/prod/qa)
    .../live/telemetry/<region>/<env>/.../_singletons/dns_owner/...   (runs in the shared
        dns_owner account)
    .../live/telemetry/<region>/<env>/.../_singletons/pretty/...      (config-derived account
        by apex asymmetry)
    .../live/telemetry/<region>/bootstrap/<role>_role/...  (per-account bootstrap:
        sandbox_role/prod_role/qa_role/dns_owner_role)

This resolver mirrors the runtime account.hcl resolution EXACTLY (the documented tier rules):
  - service tier         -> common/env_accounts.json envs[<env>].account_id
  - dns_owner tier       -> common/env_accounts.json dns_owner.account_id
  - pretty tier          -> dns_owner UNLESS domains[<env>].dns_pretty_apex == dns_service_apex
                            (pretty served from the env's own zone), then the env service account
  - bootstrap/<role>_role -> strip "_role"; dns_owner_role -> dns_owner.account_id,
                             else envs[<role>].account_id

The resolved account id is then looked up in common/accounts.json (still account-id-keyed) for
deploy_role_name + ci_deploy. A scope spanning more than one account fails fast (apply per account);
ci_deploy=false fails fast (local-only accounts cannot be applied via CI).

Output variables written:
    role_arn          The deploy role ARN to assume (arn:aws:iam::<account_id>:role/<name>)
    aws_region        The AWS region derived from the unit path
    target_account_id The resolved 12-digit account id
    needs_dns_writer  "true" when the scope runs in the dns_owner account (the workflow chains
                      to the dns-writer role), else "false"
    dns_writer_arn    The dns_owner deploy-role ARN (resolved from config, never hardcoded)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Region + first path segment (env or "bootstrap") + the remaining path.
_UNIT_PATH_RE = re.compile(
    r"live/telemetry/(?P<region>[a-z][a-z0-9-]+-[0-9]+)/(?P<rest>[^/].*?)(?:/|$)"
)
_QUEUE_INCLUDE_DIR_RE = re.compile(r"--queue-include-dir\s+(\S+)")

_ROLE_ARN_TEMPLATE = "arn:aws:iam::{account_id}:role/{role_name}"

OUTPUT_KEY_ROLE_ARN = "role_arn"
OUTPUT_KEY_AWS_REGION = "aws_region"
OUTPUT_KEY_TARGET_ACCOUNT_ID = "target_account_id"
OUTPUT_KEY_NEEDS_DNS_WRITER = "needs_dns_writer"
OUTPUT_KEY_DNS_WRITER_ARN = "dns_writer_arn"

DEPLOY_ROLE_NAME_KEY = "deploy_role_name"
CI_DEPLOY_KEY = "ci_deploy"

# On-demand ephemeral-apply opt-in (Phase 0: sandbox stand-up/tear-down lifecycle).
# Two independent keys must BOTH hold for a ci_deploy=false account to be CI-applyable:
#   - TT_ON_DEMAND_APPLY (env var): the per-RUN intent -- an explicit workflow_dispatch opt-in.
#   - ci_deploy_on_demand (accounts.json key): the per-ACCOUNT eligibility declaration.
# Absent the env var the behavior is UNCHANGED (a ci_deploy=false account still fails fast in the
# normal push lane), so sandbox stays NOT always-on. No hardcoded account id: which account is
# on-demand-eligible is declared in accounts.json, never in code.
ON_DEMAND_APPLY_ENV = "TT_ON_DEMAND_APPLY"
CI_DEPLOY_ON_DEMAND_KEY = "ci_deploy_on_demand"

# Truthy values accepted for the TT_ON_DEMAND_APPLY env var (case-insensitive, whitespace-trimmed).
_ON_DEMAND_TRUTHY = frozenset({"1", "true", "yes", "on"})

# config filenames (all siblings under common/)
ENV_ACCOUNTS_FILE = "env_accounts.json"
DOMAINS_FILE = "domains.json"

# path-tier sentinels.
# The 4th namespace field (env_instance) has three sub-classes, told apart by FORM:
#   - numeric set ("000", ...)                  -> a per-set serving instance
#   - bare tier word (shared|dns_owner|pretty)  -> a once-per-env singleton tier (_singletons/)
#   - "*_role" (sandbox_role|...|dns_owner_role) -> a per-account bootstrap role (bootstrap/)
# The singleton-tier dirs dropped their leading underscore (_dns_owner -> dns_owner, _pretty ->
# pretty) in the -/_ field-rule refactor, so these segments match the dir names verbatim.
_DNS_OWNER_SEGMENT = "dns_owner"
_PRETTY_SEGMENT = "pretty"
_BOOTSTRAP_SEGMENT = "bootstrap"
# Bootstrap role dirs carry the "_role" namespace suffix (sandbox_role/prod_role/qa_role/
# dns_owner_role). The suffix is stripped before the env_accounts lookup; the dns_owner role
# then matches the dns_owner block (not an envs entry).
_BOOTSTRAP_ROLE_SUFFIX = "_role"
_DNS_OWNER_ROLE = "dns_owner"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class PathParseError(ValueError):
    """Raised when region/env/role cannot be parsed from the unit path."""


class AccountNotFoundError(KeyError):
    """Raised when the resolved account_id is not in accounts.json, or the
    deploy_role_name or ci_deploy key is absent from the account entry."""


class AccountResolutionError(ValueError):
    """Raised when the env/role cannot be mapped to an account via
    env_accounts.json/domains.json."""


class MixedAccountScopeError(ValueError):
    """Raised when the scope spans more than one resolved account id."""


class CIDeployForbiddenError(ValueError):
    """Raised when the resolved account has ci_deploy=false (local-only, D33) and the run has
    NOT opted into the on-demand ephemeral-apply lane."""


# ---------------------------------------------------------------------------
# On-demand ephemeral-apply opt-in (Phase 0)
# ---------------------------------------------------------------------------


def on_demand_opt_in(environ: Mapping[str, str]) -> bool:
    """Return True when the run explicitly opts into the on-demand ephemeral-apply lane.

    The opt-in is the TT_ON_DEMAND_APPLY env var set to a truthy value (1/true/yes/on,
    case-insensitive, whitespace-trimmed). Passing the environment mapping in (rather than reading
    os.environ inside the pure resolver functions) keeps those functions input-driven and unit
    testable; only the CLI main() boundary reads the process environment.
    """
    return environ.get(ON_DEMAND_APPLY_ENV, "").strip().lower() in _ON_DEMAND_TRUTHY


def account_allows_ci_apply(entry: dict[str, Any], on_demand: bool) -> bool:
    """Return whether an account entry may be applied by CI.

    An account is CI-applyable iff it is always CI-deployable (ci_deploy=true) OR the run opted into
    the on-demand lane (on_demand=True) AND the account declares itself on-demand-eligible
    (ci_deploy_on_demand=true). The ci_deploy_on_demand key is optional and defaults to False when
    absent (a non-eligible account), so accounts that do not declare it behave exactly as before.

    The caller MUST have already validated that CI_DEPLOY_KEY is present in ``entry`` (fail-fast, no
    default); this predicate only interprets an already-validated entry.
    """
    if bool(entry[CI_DEPLOY_KEY]):
        return True
    return on_demand and bool(entry.get(CI_DEPLOY_ON_DEMAND_KEY, False))


# ---------------------------------------------------------------------------
# Path -> (region, account_id) resolution (pure given the loaded config dicts)
# ---------------------------------------------------------------------------


def extract_all_unit_paths(include_dir_flags: str) -> list[str]:
    """Extract all --queue-include-dir unit paths from the flags string emitted by
    tg-detect-units."""
    paths = _QUEUE_INCLUDE_DIR_RE.findall(include_dir_flags)
    if not paths:
        raise PathParseError(
            "ERROR: No --queue-include-dir token found in include_dir_flags: "
            f"'{include_dir_flags}'\n"
            "  Remedy: ensure tg-detect-units emitted at least one unit path."
        )
    return paths


def parse_unit_path(unit_path: str) -> tuple[str, str, str]:
    """Parse (region, first_segment, rest) from a Terragrunt live-tree unit path.

    first_segment is the env (sandbox/prod/qa) or the literal "bootstrap".
    rest is the remainder of the path after the first segment (may be empty).
    """
    match = _UNIT_PATH_RE.search(unit_path)
    if not match:
        raise PathParseError(
            f"ERROR: Cannot parse region/env from unit path: '{unit_path}'\n"
            "  Expected grammar: .../live/telemetry/<region>/<env|bootstrap>/...\n"
            "  Remedy: ensure the unit path is inside the env-keyed live/telemetry tree."
        )
    region = match.group("region")
    rest_full = unit_path[match.end("rest") :].lstrip("/")
    return region, match.group("rest"), rest_full


def resolve_account_id(
    unit_path: str,
    env_accounts: dict[str, Any],
    domains: dict[str, Any],
) -> tuple[str, str]:
    """Resolve (region, account_id) for a unit path, mirroring the account.hcl tier rules."""
    region, first, rest = parse_unit_path(unit_path)
    envs = env_accounts.get("envs", {})
    dns_owner = env_accounts.get("dns_owner", {})

    def _envs_account(env: str) -> str:
        if env not in envs:
            raise AccountResolutionError(
                f"ERROR: env/role '{env}' (from '{unit_path}') is not in "
                f"{ENV_ACCOUNTS_FILE} envs.\n"
                f"  Remedy: add an envs.{env} entry (account_id + aws_profile) "
                f"to common/{ENV_ACCOUNTS_FILE}."
            )
        return str(envs[env]["account_id"])

    def _dns_owner_account() -> str:
        if "account_id" not in dns_owner:
            raise AccountResolutionError(
                f"ERROR: common/{ENV_ACCOUNTS_FILE} is missing the dns_owner.account_id needed to "
                f"resolve '{unit_path}'."
            )
        return str(dns_owner["account_id"])

    # bootstrap/<role> -- the <role> dir is the "*_role" 4th-field sub-class
    # (sandbox_role/prod_role/qa_role/dns_owner_role). Strip the "_role" suffix to recover the
    # env_accounts lookup key: dns_owner_role -> dns_owner (the dns_owner block), else the env.
    if first == _BOOTSTRAP_SEGMENT:
        role_dir = rest.split("/", 1)[0] if rest else ""
        if not role_dir:
            raise PathParseError(
                f"ERROR: bootstrap unit path '{unit_path}' has no <role> segment "
                "(expected bootstrap/<sandbox_role|prod_role|qa_role|dns_owner_role>/...)."
            )
        role = (
            role_dir[: -len(_BOOTSTRAP_ROLE_SUFFIX)]
            if role_dir.endswith(_BOOTSTRAP_ROLE_SUFFIX)
            else role_dir
        )
        account_id = _dns_owner_account() if role == _DNS_OWNER_ROLE else _envs_account(role)
        return region, account_id

    # env service tree (sandbox/prod/qa) - tier from the remaining path
    env = first
    rest_segments = rest.split("/")
    if _DNS_OWNER_SEGMENT in rest_segments:
        return region, _dns_owner_account()
    if _PRETTY_SEGMENT in rest_segments:
        dom = domains.get(env)
        if dom is None:
            raise AccountResolutionError(
                f"ERROR: env '{env}' (from _pretty unit '{unit_path}') is not in "
                f"common/{DOMAINS_FILE}; "
                "cannot resolve the config-derived _pretty account."
            )
        pretty_in_env_zone = dom.get("dns_pretty_apex") == dom.get("dns_service_apex")
        return region, (_envs_account(env) if pretty_in_env_zone else _dns_owner_account())
    # default: service tier -> the env's own account
    return region, _envs_account(env)


def build_role_arn(account_id: str, role_name: str) -> str:
    """Construct the full IAM role ARN."""
    return _ROLE_ARN_TEMPLATE.format(account_id=account_id, role_name=role_name)


def is_account_ci_deployable(
    account_id: str, accounts: dict[str, Any], source: pathlib.Path, on_demand: bool = False
) -> bool:
    """Return whether account_id may be applied by CI, from an already-loaded accounts.json dict.

    Unlike ``load_account_entry`` this does NOT raise on a non-applyable account: it returns the
    boolean so a caller (e.g. scripts.filter_ci_deployable_units) can SKIP local-only units (D33:
    the sandbox account is applied locally, never by the normal push lane) instead of aborting the
    whole apply scope.

    When ``on_demand`` is True the on-demand ephemeral-apply lane is active: an account that is
    ci_deploy=false but declares ci_deploy_on_demand=true is reported as applyable (so the filter
    KEEPS its units). ``on_demand`` defaults to False, so the normal push lane is unchanged.

    Fail-fast (no default): the account MUST be registered in accounts.json AND MUST declare the
    ci_deploy key explicitly -- a missing account or missing key is a genuine configuration error
    and raises AccountNotFoundError (it is never silently treated as deployable or skippable).
    """
    if account_id not in accounts:
        raise AccountNotFoundError(
            f"ERROR: Account id '{account_id}' is not registered in {source}.\n"
            f"  Remedy: add a '{account_id}' entry with '{CI_DEPLOY_KEY}'."
        )
    entry = accounts[account_id]
    if CI_DEPLOY_KEY not in entry:
        raise AccountNotFoundError(
            f"ERROR: Account entry '{account_id}' in {source} is missing the required "
            f"'{CI_DEPLOY_KEY}' key. No default is applied -- every row must declare this "
            "explicitly."
        )
    return account_allows_ci_apply(entry, on_demand)


def load_account_entry(
    account_id: str, accounts_json_path: pathlib.Path, on_demand: bool = False
) -> dict[str, Any]:
    """Load + validate the accounts.json entry for account_id (deploy_role_name + ci_deploy
    present).

    When ``on_demand`` is True an account that is ci_deploy=false but declares
    ci_deploy_on_demand=true is permitted (the on-demand ephemeral-apply lane); otherwise a
    ci_deploy=false account fails fast (CIDeployForbiddenError, D33). ``on_demand`` defaults to
    False, so the normal push lane is unchanged.
    """
    accounts: dict[str, Any] = json.loads(accounts_json_path.read_text(encoding="utf-8"))
    if account_id not in accounts:
        raise AccountNotFoundError(
            f"ERROR: Account id '{account_id}' is not registered in {accounts_json_path}.\n"
            f"  Remedy: add a '{account_id}' entry with '{DEPLOY_ROLE_NAME_KEY}' "
            f"+ '{CI_DEPLOY_KEY}'."
        )
    entry: dict[str, Any] = accounts[account_id]
    if DEPLOY_ROLE_NAME_KEY not in entry:
        raise AccountNotFoundError(
            f"ERROR: Account entry '{account_id}' in {accounts_json_path} is missing the required "
            f"'{DEPLOY_ROLE_NAME_KEY}' key."
        )
    if CI_DEPLOY_KEY not in entry:
        raise AccountNotFoundError(
            f"ERROR: Account entry '{account_id}' in {accounts_json_path} is missing the required "
            f"'{CI_DEPLOY_KEY}' key. No default is applied -- every row must declare this "
            "explicitly."
        )
    if not account_allows_ci_apply(entry, on_demand):
        account_role = entry.get("account_role", "unknown")
        raise CIDeployForbiddenError(
            f"ERROR: account {account_id} ({account_role}) is local-only (D33); CI cannot apply "
            "these units.\n  Remedy: apply those units locally. Only ci_deploy=true accounts apply "
            f"via the normal lane; a ci_deploy=false account applies via CI ONLY when the run sets "
            f"{ON_DEMAND_APPLY_ENV} AND the account declares {CI_DEPLOY_ON_DEMAND_KEY}=true "
            "(on-demand ephemeral lane)."
        )
    return entry


def load_deploy_role_name(
    account_id: str, accounts_json_path: pathlib.Path, on_demand: bool = False
) -> str:
    """Return the deploy_role_name for account_id from accounts.json (validated)."""
    return str(load_account_entry(account_id, accounts_json_path, on_demand)[DEPLOY_ROLE_NAME_KEY])


# ---------------------------------------------------------------------------
# Main resolution
# ---------------------------------------------------------------------------


def resolve_deploy_role(
    unit_path: str | None,
    accounts_json_path: pathlib.Path,
    output_path: str,
    include_dir_flags: str | None = None,
    on_demand: bool = False,
) -> None:
    """Resolve the deploy role + region for a unit (or a multi-unit scope) and write GH outputs.

    When ``on_demand`` is True the on-demand ephemeral-apply lane is active: a resolved account that
    is ci_deploy=false but declares ci_deploy_on_demand=true resolves its deploy role instead of
    failing fast (used by the workflow_dispatch sandbox stand-up/tear-down lifecycle). ``on_demand``
    defaults to False, so the normal push lane is unchanged.
    """
    common_dir = accounts_json_path.parent
    env_accounts = json.loads((common_dir / ENV_ACCOUNTS_FILE).read_text(encoding="utf-8"))
    domains = json.loads((common_dir / DOMAINS_FILE).read_text(encoding="utf-8"))

    dns_owner_account_id = str(env_accounts.get("dns_owner", {}).get("account_id", ""))

    if include_dir_flags is not None:
        all_paths = extract_all_unit_paths(include_dir_flags)
        resolved = [resolve_account_id(p, env_accounts, domains) for p in all_paths]
    else:
        if unit_path is None:
            raise PathParseError("ERROR: Either unit_path or include_dir_flags must be provided.")
        resolved = [resolve_account_id(unit_path, env_accounts, domains)]

    # In the env-keyed model a single `run --all` over an env legitimately spans the env's own
    # service account AND the shared dns_owner account (the _dns_owner units), each written to its
    # account via its named profile. So the scope may contain at most ONE service (non-dns_owner)
    # account; a second distinct service account is a genuinely mixed scope and fails fast.
    region = resolved[0][0]
    account_ids = {acct for _region, acct in resolved}
    service_accounts = {a for a in account_ids if a != dns_owner_account_id}
    if len(service_accounts) > 1:
        raise MixedAccountScopeError(
            f"ERROR: apply scope spans service accounts {','.join(sorted(service_accounts))}; "
            "split the change per env. Each CI apply run targets one service account (plus the "
            "shared "
            "dns_owner account for its _dns_owner units)."
        )

    # primary role = the single service account when present, else the dns_owner account (a scope
    # of only dns_owner units, e.g. bootstrap/dns-owner). dns-writer chaining is needed only when
    # dns_owner units co-exist UNDER a service-account primary (cross-account DNS writes).
    if service_accounts:
        account_id = next(iter(service_accounts))
        needs_dns_writer = dns_owner_account_id in account_ids
    else:
        account_id = dns_owner_account_id
        needs_dns_writer = False

    role_name = load_deploy_role_name(account_id, accounts_json_path, on_demand)
    role_arn = build_role_arn(account_id, role_name)

    # dns-writer ARN (resolved from config, never hardcoded) for the workflow's role-chaining step.
    dns_writer_arn = (
        build_role_arn(
            dns_owner_account_id,
            load_deploy_role_name(dns_owner_account_id, accounts_json_path, on_demand),
        )
        if needs_dns_writer
        else ""
    )

    from scripts.constants import write_output

    write_output(output_path, OUTPUT_KEY_ROLE_ARN, role_arn)
    write_output(output_path, OUTPUT_KEY_AWS_REGION, region)
    write_output(output_path, OUTPUT_KEY_TARGET_ACCOUNT_ID, account_id)
    write_output(output_path, OUTPUT_KEY_NEEDS_DNS_WRITER, "true" if needs_dns_writer else "false")
    write_output(output_path, OUTPUT_KEY_DNS_WRITER_ARN, dns_writer_arn)

    print(
        f"resolve-deploy-role: account_id={account_id} region={region} role_arn={role_arn} "
        f"needs_dns_writer={needs_dns_writer}"
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve the OIDC deploy role ARN + region for an env-keyed Terragrunt unit "
            "(account resolved from common/env_accounts.json + domains.json + accounts.json)."
        )
    )
    path_group = parser.add_mutually_exclusive_group(required=True)
    path_group.add_argument("--unit-path", help="Path to the Terragrunt unit directory.")
    path_group.add_argument(
        "--include-dir-flags",
        help=(
            "The --queue-include-dir flags string from tg-detect-units "
            "(mixed-account scopes fail fast)."
        ),
    )
    parser.add_argument("--accounts-json", required=True, help="Path to common/accounts.json.")
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for `uv run python -m scripts.resolve_deploy_role`."""
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
        resolve_deploy_role(
            unit_path=args.unit_path,
            accounts_json_path=accounts_json_path,
            output_path=args.output,
            include_dir_flags=args.include_dir_flags,
            on_demand=on_demand_opt_in(os.environ),
        )
    except (
        PathParseError,
        AccountNotFoundError,
        AccountResolutionError,
        MixedAccountScopeError,
        CIDeployForbiddenError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
