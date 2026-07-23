"""FR-10: scripts/bootstrap_oidc_provider.py -- GitHub OIDC provider bootstrapper.

Invoked as:
    uv run python -m scripts.bootstrap_oidc_provider --env <qa|prod>
    make bootstrap-oidc-provider ENV=<qa|prod>

This tool ensures the GitHub Actions OIDC identity provider
token.actions.githubusercontent.com with audience sts.amazonaws.com
exists in the target account (qa or prod only). It is verify-first and
idempotent: an existing correct provider is a no-op. A pre-existing
provider with an unexpected audience is reported and causes a non-zero
exit -- the tool never modifies a shared account-level provider.

Accounts sandbox and root are explicitly refused:
  - sandbox refuses per D33 (no OIDC provider required in sandbox)
  - root refuses per D-11 (role-chaining used; no provider required)

ENV-to-profile resolution:
    Reads the aws_profile key from terragrunt/common/accounts.json. The --env
    argument is authoritative for the target account. The resolved aws_profile
    is always passed as boto3.Session(profile_name=...) so an ambient
    AWS_PROFILE in the caller's environment cannot silently redirect to a
    different account.

Output:
    Human-readable lines to stdout (ALREADY_PRESENT / CREATED / ERROR).
    Remediation lines to stderr on error.

Exit codes:
    0 -- provider already present or created successfully
    1 -- error (wrong-audience fail-safe, forbidden target, AWS error)
    2 -- usage error (unknown ENV, missing/invalid accounts.json)
"""

from __future__ import annotations

import argparse
import enum
import json
import pathlib
import sys
from typing import Any, cast

import boto3
import botocore.exceptions

# ---------------------------------------------------------------------------
# Public protocol constants (not configuration -- these are the well-known
# GitHub Actions OIDC endpoint identifiers per the GitHub OIDC spec).
# ---------------------------------------------------------------------------

# Well-known GitHub Actions OIDC provider URL (not a configurable endpoint).
GITHUB_OIDC_URL: str = "https://token.actions.githubusercontent.com"

# Required audience for AWS OIDC federation with GitHub Actions.
GITHUB_OIDC_AUDIENCE: str = "sts.amazonaws.com"

# Well-known GitHub Actions OIDC CA root thumbprint (hex SHA-1 of the root cert).
# This is a public cryptographic fingerprint, not a secret. It is the stable
# root CA thumbprint documented by GitHub and AWS for GitHub Actions OIDC.
# Source: https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc_verify-thumbprint.html
_GITHUB_OIDC_THUMBPRINT: str = "6938fd4d98bab03faadb97b34396831e3780aea1"

# Account roles that must be refused. Sandbox refuses per D33; root refuses per D-11.
_FORBIDDEN_ACCOUNT_ROLES: frozenset[str] = frozenset({"sandbox", "dns-owner"})

# The aws_profile values that map to forbidden targets in accounts.json.
_FORBIDDEN_PROFILES: frozenset[str] = frozenset({"sandbox", "root"})

# Ledger references for refusal messages -- keyed by env name (aws_profile value).
_REFUSAL_LEDGER_BY_ENV: dict[str, str] = {
    "sandbox": "D33",
    "root": "D-11",
}

# Ledger references keyed by account_role -- fallback when env alias is not in
# _REFUSAL_LEDGER_BY_ENV (e.g. an aliased env whose underlying account_role is forbidden).
_REFUSAL_LEDGER_BY_ROLE: dict[str, str] = {
    "sandbox": "D33",
    "dns-owner": "D-11",
}

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_DEFAULT_ACCOUNTS_JSON = _REPO_ROOT / "terragrunt" / "common" / "accounts.json"


# ---------------------------------------------------------------------------
# Result enum
# ---------------------------------------------------------------------------


class ProviderResult(enum.Enum):
    """Outcome of a bootstrap_oidc_provider call."""

    CREATED = "created"
    ALREADY_PRESENT = "already_present"


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class UsageError(Exception):
    """Raised for configuration or input errors -- exit 2."""


class ForbiddenTargetError(Exception):
    """Raised when the target account must not receive an OIDC provider.

    sandbox refuses per D33; root refuses per D-11 (role-chaining).
    """


class OidcProviderWrongAudienceError(Exception):
    """Raised when a pre-existing provider has an unexpected audience.

    The tool never modifies a shared account-level provider -- it fails
    safe and reports instead.
    """


class CredentialError(Exception):
    """Raised when an AWS API call fails due to missing credentials or permissions."""


# ---------------------------------------------------------------------------
# Accounts.json loader
# ---------------------------------------------------------------------------


def load_accounts_data(accounts_json_path: pathlib.Path) -> dict[str, Any]:
    """Load and parse accounts.json, raising UsageError on failure.

    Args:
        accounts_json_path: Path to the accounts.json file.

    Returns:
        Parsed accounts.json dict.

    Raises:
        UsageError: When the file is not found or contains invalid JSON.
    """
    if not accounts_json_path.exists():
        raise UsageError(
            f"ERROR: accounts.json not found at {accounts_json_path}\n"
            f"Remediation: ensure terragrunt/common/accounts.json is present."
        )
    try:
        return cast(dict[str, Any], json.loads(accounts_json_path.read_text()))
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"ERROR: invalid JSON in {accounts_json_path}: {exc}\n"
            f"Remediation: fix the JSON syntax in terragrunt/common/accounts.json."
        ) from exc


def _load_accounts_from_default_path() -> dict[str, Any]:
    """Load accounts.json from the default repo-relative path."""
    return load_accounts_data(_DEFAULT_ACCOUNTS_JSON)


# ---------------------------------------------------------------------------
# Account resolution helpers
# ---------------------------------------------------------------------------


def _resolve_account_info(env: str, accounts_data: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve the aws_profile, account ID, and account_role for the given ENV.

    Args:
        env: The environment name matching an aws_profile value in accounts.json.
        accounts_data: Parsed accounts.json content.

    Returns:
        Tuple of (aws_profile, account_id, account_role).

    Raises:
        UsageError: When no account row has an aws_profile matching env.
    """
    for account_id, account_info in accounts_data.items():
        if account_info.get("aws_profile") == env:
            profile: str = account_info["aws_profile"]
            role: str = account_info.get("account_role", "")
            return profile, str(account_id), role
    raise UsageError(
        f"ERROR: unknown ENV {env!r}: no account in accounts.json has aws_profile={env!r}\n"
        f"Remediation: check terragrunt/common/accounts.json for valid ENV values."
    )


def _check_forbidden_target(env: str, account_role: str) -> None:
    """Raise ForbiddenTargetError when the target must not receive an OIDC provider.

    Checks both the ENV name and the account_role to cover all cases.
    sandbox refuses per D33; root (dns-owner role) refuses per D-11.

    Args:
        env: The environment name (aws_profile value).
        account_role: The account_role from accounts.json.

    Raises:
        ForbiddenTargetError: When the target is sandbox (D33) or root (D-11).
    """
    if env in _FORBIDDEN_PROFILES or account_role in _FORBIDDEN_ACCOUNT_ROLES:
        ledger = _REFUSAL_LEDGER_BY_ENV.get(env) or _REFUSAL_LEDGER_BY_ROLE.get(
            account_role, "see bootstrap-ordering.md"
        )
        raise ForbiddenTargetError(
            f"ERROR: ENV={env!r} (account_role={account_role!r}) must not receive "
            f"a GitHub OIDC provider ({ledger}).\n"
            f"  sandbox accounts need no OIDC provider per D33.\n"
            f"  The root account uses role-chaining per D-11; no OIDC provider required.\n"
            f"Remediation: run with ENV=qa or ENV=prod only."
        )


# ---------------------------------------------------------------------------
# Provider inspection
# ---------------------------------------------------------------------------


def _find_github_provider_arn(
    iam_client: Any,
) -> str | None:
    """List OIDC providers and return the GitHub provider ARN if present.

    Args:
        iam_client: A boto3 IAM client.

    Returns:
        The ARN of the token.actions.githubusercontent.com provider, or None.
    """
    providers = iam_client.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])
    for p in providers:
        arn: str = p.get("Arn", "")
        if "token.actions.githubusercontent.com" in arn:
            return arn
    return None


def _verify_provider_audience(
    iam_client: Any,
    provider_arn: str,
) -> list[str]:
    """Retrieve the ClientIDList for the given OIDC provider ARN.

    Args:
        iam_client: A boto3 IAM client.
        provider_arn: The OIDC provider ARN to inspect.

    Returns:
        The ClientIDList for the provider.
    """
    detail = iam_client.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)
    return list(detail.get("ClientIDList", []))


# ---------------------------------------------------------------------------
# Core bootstrap function
# ---------------------------------------------------------------------------


def bootstrap_oidc_provider(
    env: str,
    accounts_data: dict[str, Any],
) -> ProviderResult:
    """Bootstrap the GitHub OIDC provider in the target account.

    This function is verify-first and idempotent:
      1. Refuse sandbox/root targets (ForbiddenTargetError).
      2. List existing OIDC providers.
      3. If token.actions.githubusercontent.com is present, verify its audience.
         - Correct audience -> return ALREADY_PRESENT (no-op).
         - Wrong audience -> raise OidcProviderWrongAudienceError (fail-safe, no mutation).
      4. If absent -> create the provider and return CREATED.

    Args:
        env: The environment name (qa or prod).
        accounts_data: Parsed accounts.json content.

    Returns:
        ProviderResult.ALREADY_PRESENT or ProviderResult.CREATED.

    Raises:
        ForbiddenTargetError: For sandbox or root targets.
        OidcProviderWrongAudienceError: When a pre-existing provider has the wrong audience.
        CredentialError: When AWS API calls fail due to credentials or permissions.
        UsageError: For unknown ENV values.
    """
    aws_profile, account_id, account_role = _resolve_account_info(env, accounts_data)
    _check_forbidden_target(env, account_role)

    session = boto3.Session(profile_name=aws_profile)
    iam = session.client("iam")

    try:
        existing_arn = _find_github_provider_arn(iam)
    except botocore.exceptions.ClientError as exc:
        raise CredentialError(
            f"ERROR: AWS API call failed for profile {aws_profile!r} "
            f"(account {account_id}):\n"
            f"  {exc}\n"
            f"Remediation: ensure AWS profile {aws_profile!r} is configured with "
            f"iam:ListOpenIDConnectProviders permission."
        ) from exc

    if existing_arn is not None:
        # Verify the audience -- never modify a pre-existing provider
        client_ids = _verify_provider_audience(iam, existing_arn)
        if GITHUB_OIDC_AUDIENCE not in client_ids:
            raise OidcProviderWrongAudienceError(
                f"ERROR: provider {existing_arn!r} exists in account {account_id} "
                f"but has unexpected audience(s): {client_ids!r}.\n"
                f"  Expected audience: {GITHUB_OIDC_AUDIENCE!r}\n"
                f"  This provider may be shared by another project. The tool "
                f"never modifies a pre-existing provider without operator confirmation.\n"
                f"Remediation: manually verify the provider in the AWS console and "
                f"add {GITHUB_OIDC_AUDIENCE!r} to its ClientIDList if appropriate."
            )

        print(
            f"ALREADY_PRESENT: provider {existing_arn} in account {account_id} "
            f"already has audience {GITHUB_OIDC_AUDIENCE!r}. No action taken.",
            flush=True,
        )
        return ProviderResult.ALREADY_PRESENT

    # Provider absent -- create it
    try:
        response = iam.create_open_id_connect_provider(
            Url=GITHUB_OIDC_URL,
            ClientIDList=[GITHUB_OIDC_AUDIENCE],
            ThumbprintList=[_GITHUB_OIDC_THUMBPRINT],
        )
    except botocore.exceptions.ClientError as exc:
        raise CredentialError(
            f"ERROR: failed to create OIDC provider for profile {aws_profile!r} "
            f"(account {account_id}):\n"
            f"  {exc}\n"
            f"Remediation: ensure AWS profile {aws_profile!r} is configured with "
            f"iam:CreateOpenIDConnectProvider permission."
        ) from exc

    created_arn: str = response["OpenIDConnectProviderArn"]
    print(
        f"CREATED: OIDC provider {created_arn} in account {account_id} "
        f"with audience {GITHUB_OIDC_AUDIENCE!r}.",
        flush=True,
    )
    return ProviderResult.CREATED


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for bootstrap_oidc_provider.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Raises:
        SystemExit: With exit code 0 on success, 1 on error, 2 on usage error.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap the GitHub OIDC identity provider "
            "(token.actions.githubusercontent.com, audience sts.amazonaws.com) "
            "in the target AWS account. "
            "ENV must be qa or prod; sandbox (D33) and root (D-11) are refused."
        )
    )
    parser.add_argument(
        "--env",
        required=True,
        metavar="ENV",
        help="Target environment: qa or prod (sandbox and root are refused).",
    )
    args = parser.parse_args(argv)

    try:
        accounts_data = _load_accounts_from_default_path()
    except UsageError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(2)

    try:
        bootstrap_oidc_provider(env=args.env, accounts_data=accounts_data)
    except (ForbiddenTargetError, OidcProviderWrongAudienceError, CredentialError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
    except UsageError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
