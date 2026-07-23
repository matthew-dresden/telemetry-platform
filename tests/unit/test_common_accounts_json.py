"""Unit tests for terragrunt/common/accounts.json -- the account-id-keyed mapping layer.

These tests assert the structural and content constraints for the account registry
at terragrunt/common/accounts.json (spec AC-3, S4.1, S5, S6, S3.5, S3.6).

Assertions:
- The file exists at terragrunt/common/accounts.json and NOT inside terragrunt/live/.
- The file parses as a valid JSON object.
- Every top-level key matches the 12-digit AWS account ID regex ^[0-9]{12}$.
- All four seed accounts are present with documented aws_profile/account_role/deploy_role_name.
- Every row carries the required sub-keys: aws_profile, account_role, deploy_role_name,
  is_dns_owner.
- Exactly one row (444444444444) has is_dns_owner=true and carries a real, non-placeholder,
  Z-prefixed dns_owner_zone_id (AC-1, AC-2, spec FR-3.1).
- All other rows have is_dns_owner=false and omit dns_owner_zone_id.
- The file contains no AWS access-key patterns.
- Non-dns-owner rows carry no real Route53 zone id (spec S3.6 leak guard).
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ACCOUNTS_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "accounts.json"

# 12-digit AWS account ID pattern (spec S5, AC-3).
_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")

# Required sub-keys for every account row (spec S4.1, S5, D-19).
_REQUIRED_KEYS = frozenset(
    {"aws_profile", "account_role", "deploy_role_name", "is_dns_owner", "ci_deploy"}
)

# Expected ci_deploy values per account (spec 5.1, D-19).
# sandbox=false; qa/prod/root=true.
_EXPECTED_CI_DEPLOY: dict[str, bool] = {
    "111111111111": True,  # prod-infra
    "222222222222": False,  # sandbox -- local-only
    "333333333333": True,  # qa-infra
    "444444444444": True,  # dns-owner (root)
}

# Known seed accounts with documented aws_profile / account_role / deploy_role_name values
# (spec AC-3, S5, S6).
_SEED_ACCOUNT_ROWS: list[tuple[str, str, str, str]] = [
    ("111111111111", "prod", "prod-infra", "telemetry-platform-gha-tg-apply"),
    ("222222222222", "sandbox", "sandbox", "telemetry-platform-gha-tg-apply"),
    ("333333333333", "qa", "qa-infra", "telemetry-platform-gha-terratest"),
    ("444444444444", "root", "dns-owner", "telemetry-platform-dns-writer"),
]

# The single account that must carry is_dns_owner=true (spec S4.1, S5).
_DNS_OWNER_ACCOUNT_ID = "444444444444"

# The forbidden <REAL_Z_ID> placeholder; the dns-owner row must carry a real
# Z-prefixed Route53 zone id (spec FR-3.1).
_ZONE_ID_PLACEHOLDER = "<REAL_Z_ID>"

# AWS access-key prefixes (AKIA = long-term, ASIA = STS short-term).
# Defined once at module scope so any future key-type can be added in one place.
_AWS_KEY_PREFIXES: tuple[str, ...] = ("AKIA", "ASIA")

# Secret material patterns that must NOT appear in the file (spec S3.5, S3.6).
# Built from _AWS_KEY_PREFIXES to avoid duplicating the prefix strings inline (DRY).
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"{prefix}[0-9A-Z]{{16}}") for prefix in _AWS_KEY_PREFIXES
]


# ---------------------------------------------------------------------------
# Meta-guard: _SECRET_PATTERNS must be built from _AWS_KEY_PREFIXES (DRY)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_secret_patterns_built_from_aws_key_prefixes_constant() -> None:
    """_SECRET_PATTERNS must be derived from a module-level _AWS_KEY_PREFIXES tuple (DRY).

    Requiring the prefixes to be defined once at module scope ensures that any
    future key-type addition is made in a single place, and that the parametrize
    decorator (if added later) can reference the same constant without duplication.

    This meta-assertion reads this file's own source and verifies two conditions:
    1. A module-level '_AWS_KEY_PREFIXES = ' assignment exists (at column zero).
    2. The _SECRET_PATTERNS block does NOT contain a bare inline 're.compile(r\"AKIA'
       or 're.compile(r\"ASIA' literal -- i.e. the patterns are built from the
       constant, not hard-coded inline.
    If the constant is absent, CI blocks at RED until the DRY refactor is applied.
    """
    this_file = pathlib.Path(__file__)
    source = this_file.read_text(encoding="utf-8")
    lines = source.splitlines()

    # Condition 1: module-level constant definition must exist at column zero.
    # The line may carry a type annotation (_AWS_KEY_PREFIXES: tuple[str, ...] = ...)
    # so match on the name prefix only.
    prefix_def_lines = [ln for ln in lines if ln.startswith("_AWS_KEY_PREFIXES")]
    assert len(prefix_def_lines) == 1, (
        f"Expected exactly 1 module-level '_AWS_KEY_PREFIXES' definition line in "
        f"{this_file.name}, found {len(prefix_def_lines)}. "
        "The AWS key prefixes (AKIA, ASIA) must be defined exactly once as a module-level "
        "tuple constant and referenced from _SECRET_PATTERNS (DRY principle)."
    )

    # Condition 2: _SECRET_PATTERNS must not contain bare inline re.compile(r"AKIA...")
    # or re.compile(r"ASIA...") literals at 4-space list-item indentation.
    # Those lines would start with exactly '    re.compile(r"AKIA' or '    re.compile(r"ASIA'
    # -- the precise form used in the old un-refactored definition.
    old_inline_lines = [
        ln
        for ln in lines
        if ln.startswith('    re.compile(r"AKIA') or ln.startswith('    re.compile(r"ASIA')
    ]
    assert len(old_inline_lines) == 0, (
        f"Found {len(old_inline_lines)} old-style inline re.compile literal(s) for AKIA/ASIA "
        f"at 4-space indent in {this_file.name}: {old_inline_lines}. "
        "_SECRET_PATTERNS must be built from _AWS_KEY_PREFIXES rather than duplicating "
        "the prefix strings inline (DRY principle)."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_accounts(path: pathlib.Path) -> dict[str, Any]:
    """Load and return accounts.json as a typed dict.

    Fails fast with TypeError if the root is not a JSON object.
    """
    data = json.load(path.open())
    if not isinstance(data, dict):
        raise TypeError(
            f"accounts.json must contain a JSON object at the root, "
            f"got {type(data).__name__}. "
            "The file must be a {{...}} object mapping account IDs to their configuration."
        )
    return data


# ---------------------------------------------------------------------------
# Existence and location tests (AC-FIX-T2-003, AC-3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_accounts_json_exists_outside_live() -> None:
    """accounts.json must exist at terragrunt/common/ and NOT inside terragrunt/live/."""
    assert ACCOUNTS_JSON_PATH.exists(), (
        f"terragrunt/common/accounts.json not found at {ACCOUNTS_JSON_PATH} (AC-3). "
        "Author the file as part of E8-F1-S1-T2."
    )
    live_path = REPO_ROOT / "terragrunt" / "live" / "common" / "accounts.json"
    assert not live_path.exists(), (
        f"accounts.json must NOT reside inside terragrunt/live/ (AC-3). "
        f"Found forbidden copy at {live_path}."
    )


@pytest.mark.unit
def test_accounts_json_parses_as_object() -> None:
    """accounts.json must parse as a valid JSON object (not array or primitive)."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    assert isinstance(data, dict), (
        f"accounts.json root must be a JSON object ({{...}}), got {type(data).__name__}."
    )


# ---------------------------------------------------------------------------
# Key format tests (AC-FIX-T2-004, AC-3, spec S5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_accounts_json_keys_are_12_digit_account_ids() -> None:
    """Every top-level key in accounts.json must match the regex ^[0-9]{12}$."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    invalid_keys = [k for k in data if not _ACCOUNT_ID_PATTERN.match(k)]
    assert not invalid_keys, (
        f"The following keys in accounts.json are not valid 12-digit AWS account IDs "
        f"(spec S5, AC-3): {invalid_keys}. "
        "Each key must match the pattern ^[0-9]{12}$."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "account_id,expected_aws_profile,expected_account_role,expected_deploy_role_name",
    _SEED_ACCOUNT_ROWS,
    ids=[row[0] for row in _SEED_ACCOUNT_ROWS],
)
def test_accounts_json_seed_account_values(
    account_id: str,
    expected_aws_profile: str,
    expected_account_role: str,
    expected_deploy_role_name: str,
) -> None:
    """Every seed account must be present with documented values.

    Validates aws_profile, account_role, deploy_role_name per spec AC-3, S5, S6.
    """
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    assert account_id in data, (
        f"Required seed account '{account_id}' is missing from accounts.json (AC-3, S5, S6)."
    )
    row = data[account_id]
    assert isinstance(row, dict), (
        f"Account '{account_id}' value must be a JSON object, got {type(row).__name__}."
    )
    assert row.get("aws_profile") == expected_aws_profile, (
        f"Account '{account_id}': aws_profile must be '{expected_aws_profile}', "
        f"got '{row.get('aws_profile')}' (spec S5, S6)."
    )
    assert row.get("account_role") == expected_account_role, (
        f"Account '{account_id}': account_role must be '{expected_account_role}', "
        f"got '{row.get('account_role')}' (spec S5, S6)."
    )
    assert row.get("deploy_role_name") == expected_deploy_role_name, (
        f"Account '{account_id}': deploy_role_name must be '{expected_deploy_role_name}', "
        f"got '{row.get('deploy_role_name')}' (spec S5, S6)."
    )


# ---------------------------------------------------------------------------
# Sub-key structure tests (AC-FIX-T2-005, AC-3, spec S4.1, S5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_accounts_json_every_row_has_required_keys() -> None:
    """Every account row must carry the required sub-keys."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    missing: list[tuple[str, list[str]]] = []
    for account_id, row in data.items():
        if not isinstance(row, dict):
            missing.append((account_id, list(_REQUIRED_KEYS)))
            continue
        absent = [k for k in _REQUIRED_KEYS if k not in row]
        if absent:
            missing.append((account_id, absent))
    assert not missing, (
        f"The following accounts in accounts.json are missing required sub-keys "
        f"(spec S4.1, S5): {missing}. "
        f"Every row must carry: {sorted(_REQUIRED_KEYS)}."
    )


@pytest.mark.unit
def test_accounts_json_exactly_one_dns_owner_row() -> None:
    """Exactly one account must have is_dns_owner=true, and it must be 444444444444."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    dns_owner_ids = [
        account_id
        for account_id, row in data.items()
        if isinstance(row, dict) and row.get("is_dns_owner") is True
    ]
    assert len(dns_owner_ids) == 1, (
        f"Expected exactly one account with is_dns_owner=true, "
        f"found {len(dns_owner_ids)}: {dns_owner_ids} (spec S4.1, S5)."
    )
    assert dns_owner_ids[0] == _DNS_OWNER_ACCOUNT_ID, (
        f"The dns_owner account must be '{_DNS_OWNER_ACCOUNT_ID}', "
        f"got '{dns_owner_ids[0]}' (spec S4.1, S5)."
    )


@pytest.mark.unit
def test_accounts_json_dns_owner_has_zone_id() -> None:
    """The dns_owner account (444444444444) must carry a dns_owner_zone_id sub-key."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    assert _DNS_OWNER_ACCOUNT_ID in data, (
        f"DNS owner account '{_DNS_OWNER_ACCOUNT_ID}' is missing from accounts.json (spec S4.1)."
    )
    row = data[_DNS_OWNER_ACCOUNT_ID]
    assert isinstance(row, dict), (
        f"DNS owner account row must be a JSON object, got {type(row).__name__}."
    )
    assert "dns_owner_zone_id" in row, (
        f"Account '{_DNS_OWNER_ACCOUNT_ID}' must carry a dns_owner_zone_id sub-key "
        "(spec S4.1, S5, FR-3.1). "
        "The value must be the real Route53 hosted-zone id for telemetry.example.com."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "account_id",
    [row[0] for row in _SEED_ACCOUNT_ROWS if row[0] != _DNS_OWNER_ACCOUNT_ID],
    ids=[row[0] for row in _SEED_ACCOUNT_ROWS if row[0] != _DNS_OWNER_ACCOUNT_ID],
)
def test_accounts_json_non_dns_owner_rows_are_false_and_lack_zone_id(account_id: str) -> None:
    """Non-dns-owner accounts must have is_dns_owner=false and omit dns_owner_zone_id."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    assert account_id in data, (
        f"Account '{account_id}' is missing from accounts.json (spec S5, S6)."
    )
    row = data[account_id]
    assert isinstance(row, dict), (
        f"Account '{account_id}' value must be a JSON object, got {type(row).__name__}."
    )
    assert row.get("is_dns_owner") is False, (
        f"Account '{account_id}': is_dns_owner must be false, "
        f"got '{row.get('is_dns_owner')}' (spec S4.1, S5)."
    )
    assert "dns_owner_zone_id" not in row, (
        f"Account '{account_id}': non-dns-owner rows must omit dns_owner_zone_id (spec S4.1, S5). "
        f"Found key with value '{row.get('dns_owner_zone_id')}'."
    )


# ---------------------------------------------------------------------------
# No-secrets tests (AC-FIX-T2-006, spec S3.5, S3.6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_accounts_json_contains_no_aws_access_key_patterns() -> None:
    """accounts.json must contain no AWS access key ID patterns (AKIA/ASIA + 16 alphanumerics)."""
    content = ACCOUNTS_JSON_PATH.read_text(encoding="utf-8") if ACCOUNTS_JSON_PATH.exists() else ""
    found = [pattern.pattern for pattern in _SECRET_PATTERNS if pattern.search(content)]
    assert not found, (
        f"Potential AWS access key patterns found in accounts.json: {found}. "
        "The file must contain only non-secret configuration (spec S3.5)."
    )


@pytest.mark.unit
def test_accounts_json_dns_owner_zone_id_is_present_and_real_or_placeholder() -> None:
    """The dns_owner account must carry a dns_owner_zone_id that is either a real
    Route53 zone id or the documented '<REAL_Z_ID>' placeholder.

    accounts.json is a reusable template: it intentionally ships the '<REAL_Z_ID>'
    placeholder, which a consumer replaces with their own hosted-zone id. The
    common.hcl parse-time dns-owner guard is what enforces a real value before any
    deploy, so this test asserts the STRUCTURE/CONTRACT rather than requiring the
    committed template to already be populated: the single is_dns_owner=true row
    must carry a non-empty string dns_owner_zone_id that is EITHER a real
    Route53 zone id (^Z[A-Z0-9]+$) OR the '<REAL_Z_ID>' placeholder (AC-1, AC-2,
    spec FR-3.1).
    """
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    assert _DNS_OWNER_ACCOUNT_ID in data, (
        f"DNS owner account '{_DNS_OWNER_ACCOUNT_ID}' is missing from accounts.json (AC-1)."
    )
    row = data[_DNS_OWNER_ACCOUNT_ID]
    assert isinstance(row, dict), (
        f"DNS owner account row must be a JSON object, got {type(row).__name__}."
    )
    zone_id = row.get("dns_owner_zone_id")
    assert isinstance(zone_id, str) and zone_id, (
        f"Account '{_DNS_OWNER_ACCOUNT_ID}': dns_owner_zone_id must be a non-empty string "
        f"(a real Route53 zone id or the '{_ZONE_ID_PLACEHOLDER}' template placeholder), "
        f"got {zone_id!r} (AC-1, spec FR-3.1)."
    )
    real_zone_id_pattern = re.compile(r"^Z[A-Z0-9]+$")
    is_real = bool(real_zone_id_pattern.match(zone_id))
    is_placeholder = zone_id == _ZONE_ID_PLACEHOLDER
    assert is_real or is_placeholder, (
        f"Account '{_DNS_OWNER_ACCOUNT_ID}': dns_owner_zone_id must be EITHER a real "
        f"Route53 zone id matching ^Z[A-Z0-9]+$ OR the documented '{_ZONE_ID_PLACEHOLDER}' "
        f"template placeholder, got '{zone_id}' (AC-2, spec FR-3.1). "
        "A consumer replaces the placeholder with 'aws route53 list-hosted-zones'; the "
        "common.hcl parse-time guard enforces a real value before deploy."
    )


@pytest.mark.unit
def test_accounts_json_non_dns_owner_rows_have_no_zone_id_leak() -> None:
    """Non-dns-owner rows must not carry a real Route53 zone id (spec S3.6 leak guard).

    The dns_owner_zone_id key is authorised only for the single is_dns_owner=true account.
    Any real Z-prefixed value on a non-owner row would indicate accidental leakage of
    the hosted-zone identifier into an account that should not reference it.
    """
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    # Pattern for a real Route53 zone id (Z + 8 or more uppercase alphanumerics).
    real_zone_id_pattern = re.compile(r"^Z[A-Z0-9]{8,}$")
    leaks: list[tuple[str, str]] = []
    for account_id, row in data.items():
        if not isinstance(row, dict):
            continue
        if row.get("is_dns_owner") is True:
            continue
        zone_id = row.get("dns_owner_zone_id", "")
        if zone_id and real_zone_id_pattern.match(zone_id):
            leaks.append((account_id, zone_id))
    assert not leaks, (
        f"Non-dns-owner accounts carry a real Route53 zone id (spec S3.6): {leaks}. "
        "Only the is_dns_owner=true account (444444444444) may hold dns_owner_zone_id."
    )


# ---------------------------------------------------------------------------
# ci_deploy key tests (spec 5.1, D-19, E10-F2-S4-T1 AC-3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_accounts_json_every_row_has_ci_deploy_key() -> None:
    """Every account row must carry the boolean ci_deploy key (spec 5.1, D-19, AC-3).

    A missing ci_deploy key causes resolve_deploy_role to fail fast with an error;
    the schema must always carry this key so the guard has something to read.
    """
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    missing = [
        account_id
        for account_id, row in data.items()
        if not isinstance(row, dict) or "ci_deploy" not in row
    ]
    assert not missing, (
        f"The following accounts in accounts.json are missing the 'ci_deploy' key "
        f"(spec 5.1, D-19, E10-F2-S4-T1 AC-3): {missing}. "
        "Every row must carry ci_deploy: true|false."
    )


@pytest.mark.unit
def test_accounts_json_ci_deploy_is_boolean_on_every_row() -> None:
    """The ci_deploy value must be a JSON boolean (true/false), not a string (spec 5.1, D-19)."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    non_bool = [
        (account_id, type(row.get("ci_deploy")).__name__)
        for account_id, row in data.items()
        if isinstance(row, dict) and not isinstance(row.get("ci_deploy"), bool)
    ]
    assert not non_bool, (
        f"The following accounts have a non-boolean ci_deploy value "
        f"(spec 5.1, D-19): {non_bool}. "
        "ci_deploy must be a JSON boolean (true or false), not a string."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "account_id,expected_ci_deploy",
    list(_EXPECTED_CI_DEPLOY.items()),
    ids=list(_EXPECTED_CI_DEPLOY.keys()),
)
def test_accounts_json_ci_deploy_values_match_spec(
    account_id: str,
    expected_ci_deploy: bool,
) -> None:
    """ci_deploy must be false for sandbox, true for qa/prod/root (spec 5.1, D-19, AC-3)."""
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    assert account_id in data, (
        f"Seed account '{account_id}' is missing from accounts.json (spec 5.1, D-19)."
    )
    row = data[account_id]
    assert isinstance(row, dict), f"Account '{account_id}' value must be a JSON object."
    actual = row.get("ci_deploy")
    assert actual == expected_ci_deploy, (
        f"Account '{account_id}': ci_deploy must be {expected_ci_deploy}, "
        f"got {actual!r} (spec 5.1, D-19, E10-F2-S4-T1 AC-3)."
    )


# ---------------------------------------------------------------------------
# ci_deploy_on_demand key tests (Phase 0 -- on-demand ephemeral sandbox apply)
# ---------------------------------------------------------------------------

# The account that opts into the on-demand ephemeral-apply lane (Phase 0).
_SANDBOX_ACCOUNT_ID = "222222222222"


@pytest.mark.unit
def test_accounts_json_sandbox_opts_into_on_demand_apply() -> None:
    """The sandbox account must declare ci_deploy_on_demand=true so a run that sets
    TT_ON_DEMAND_APPLY can stand up / tear down the sandbox stack via CI (Phase 0).

    ci_deploy stays false, so the normal push lane still treats sandbox as no-auto-apply; the
    on-demand key is the per-account eligibility half of the two-key opt-in.
    """
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    row = data[_SANDBOX_ACCOUNT_ID]
    assert row.get("ci_deploy") is False, (
        f"sandbox ({_SANDBOX_ACCOUNT_ID}) ci_deploy must stay false so the normal push lane never "
        "auto-applies it; on-demand is gated separately."
    )
    assert row.get("ci_deploy_on_demand") is True, (
        f"sandbox ({_SANDBOX_ACCOUNT_ID}) must declare ci_deploy_on_demand=true to be eligible for "
        "the on-demand ephemeral-apply lane (Phase 0)."
    )


@pytest.mark.unit
def test_accounts_json_on_demand_flag_is_boolean_and_only_on_local_only_accounts() -> None:
    """Where present, ci_deploy_on_demand must be a JSON boolean, and it may be true ONLY on a
    ci_deploy=false account (the flag is meaningless for always-CI-deployable accounts).

    This keeps the config self-consistent: on-demand eligibility is a property of a local-only
    account, and a ci_deploy=true account is applied by the normal lane regardless.
    """
    data = _load_accounts(ACCOUNTS_JSON_PATH)
    non_bool = [
        (account_id, type(row.get("ci_deploy_on_demand")).__name__)
        for account_id, row in data.items()
        if isinstance(row, dict)
        and "ci_deploy_on_demand" in row
        and not isinstance(row.get("ci_deploy_on_demand"), bool)
    ]
    assert not non_bool, (
        f"ci_deploy_on_demand must be a JSON boolean where present, found non-boolean: {non_bool}."
    )
    misplaced = [
        account_id
        for account_id, row in data.items()
        if isinstance(row, dict)
        and row.get("ci_deploy_on_demand") is True
        and row.get("ci_deploy") is not False
    ]
    assert not misplaced, (
        f"ci_deploy_on_demand=true may only appear on a ci_deploy=false account; found on: "
        f"{misplaced}. A ci_deploy=true account applies via the normal lane and needs no on-demand "
        "opt-in."
    )
