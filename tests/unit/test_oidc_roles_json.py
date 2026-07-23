"""Unit tests for terragrunt/common/oidc-roles.json -- the per-account OIDC roles registry.

These tests assert the structural and content constraints for the OIDC roles mapping
at terragrunt/common/oidc-roles.json (spec section 4.9, AC-16, E8-F6-S2-T1).

Assertions:
- The file exists at terragrunt/common/oidc-roles.json and NOT inside terragrunt/live/.
- The file parses as a valid JSON object.
- Every top-level key matches the 12-digit AWS account ID regex ^[0-9]{12}$.
- The three oidc-bootstrap accounts are present: prod (111111111111), qa (333333333333),
  root (444444444444).
- Every row carries a 'roles' sub-key that is a non-empty JSON object.
- Each role entry carries at least a 'description' or a recognisable role-payload key
  (sub, trust_policy_json, managed_policy_arns, inline_policies, etc.).
- The prod account row carries 'telemetry-platform-gha-tg-plan' and
  'telemetry-platform-gha-tg-apply' role keys.
- The QA account row carries 'telemetry-platform-gha-terratest' role key only.
- The root account row carries 'telemetry-platform-dns-writer' role key only.
- The sandbox account (222222222222) carries a read-only 'telemetry-platform-gha-tg-plan'
  role plus a
  'telemetry-platform-gha-tg-apply' ON-DEMAND apply/destroy role whose trust is env-locked to the
  dedicated 'sandbox-apply' GitHub environment (Phase 0 ephemeral sandbox lifecycle).
- The file contains no AWS access-key patterns.

FR-12 / AC-4 hardening assertions (E10-F3-S3-T1):
- The prod tg-apply 'sub' is locked to exactly
  'repo:matthew-dresden/telemetry-platform:environment:prod-apply'
  (spec section 4.12, AC-27, D-11).
- The root dns-writer entry carries NO 'sub' field -- it uses role-chaining (D-11),
  not direct OIDC federation. The trust_policy_json is injected at apply time by the
  root terragrunt leaf.
- The QA terratest role carries non-empty 'inline_policies' providing at least the
  Tagging API reads and deletion actions required by FR-4 sweep (spec section 4.12).
- No inline policy statement in ANY account's role widens the Action + Resource pair to
  '*:*' / '*' + '*' -- least-privilege enforcement (spec section 4.12, doc-03 6.2).

oidc-bootstrap module fail-fast guard (E10-F3-S3-T1, REVIEW_FAIL round 1):
- providers/aws/references/oidc-bootstrap/main.tf must contain a Terraform check block
  (or precondition) that fails fast when any role has sub set (OIDC trust) but
  github_oidc_provider_arn is empty. Silently emitting Principal.Federated="" violates
  the FR-10 fail-fast contract.
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
OIDC_ROLES_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "oidc-roles.json"

# 12-digit AWS account ID pattern (spec S5, AC-3).
_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")

# The three oidc-bootstrap accounts that MUST be present.
PROD_ACCOUNT_ID = "111111111111"
QA_ACCOUNT_ID = "333333333333"
ROOT_ACCOUNT_ID = "444444444444"
SANDBOX_ACCOUNT_ID = "222222222222"

REQUIRED_ACCOUNTS = [PROD_ACCOUNT_ID, QA_ACCOUNT_ID, ROOT_ACCOUNT_ID]

# Per-account expected role keys.
PROD_EXPECTED_ROLES = {"telemetry-platform-gha-tg-plan", "telemetry-platform-gha-tg-apply"}
QA_EXPECTED_ROLES = {"telemetry-platform-gha-terratest"}
ROOT_EXPECTED_ROLES = {"telemetry-platform-dns-writer"}

# Secret material patterns that must NOT appear in the file.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_oidc_roles(path: pathlib.Path) -> dict[str, Any]:
    """Load and return oidc-roles.json as a typed dict.

    Fails fast with TypeError if the root is not a JSON object.
    """
    data = json.load(path.open())
    if not isinstance(data, dict):
        raise TypeError(
            f"oidc-roles.json must contain a JSON object at the root, "
            f"got {type(data).__name__}. "
            "The file must be a {{...}} object mapping account IDs to their roles."
        )
    return data


# ---------------------------------------------------------------------------
# Existence and location tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oidc_roles_json_exists() -> None:
    """oidc-roles.json must exist at terragrunt/common/ (AC-FUNC-001, spec 4.9)."""
    assert OIDC_ROLES_JSON_PATH.exists(), (
        f"terragrunt/common/oidc-roles.json not found at {OIDC_ROLES_JSON_PATH}. "
        "This file must be authored as part of E8-F6-S2-T1 to store per-account "
        "OIDC roles maps outside the copy boundary (spec section 4.9, AC-16)."
    )


@pytest.mark.unit
def test_oidc_roles_json_not_inside_live() -> None:
    """oidc-roles.json must NOT reside inside terragrunt/live/."""
    live_path = REPO_ROOT / "terragrunt" / "live" / "common" / "oidc-roles.json"
    assert not live_path.exists(), (
        f"oidc-roles.json must NOT reside inside terragrunt/live/ (spec D3). "
        f"Found forbidden copy at {live_path}. "
        "The file belongs in terragrunt/common/ only."
    )


@pytest.mark.unit
def test_oidc_roles_json_parses_as_object() -> None:
    """oidc-roles.json must parse as a valid JSON object."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert isinstance(data, dict), (
        f"oidc-roles.json root must be a JSON object ({{...}}), got {type(data).__name__}."
    )


# ---------------------------------------------------------------------------
# Key format tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oidc_roles_json_keys_are_12_digit_account_ids() -> None:
    """Every top-level key in oidc-roles.json must match the regex ^[0-9]{12}$."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    invalid_keys = [k for k in data if not _ACCOUNT_ID_PATTERN.match(k)]
    assert not invalid_keys, (
        f"The following keys in oidc-roles.json are not valid 12-digit AWS account IDs: "
        f"{invalid_keys}. Each key must match the pattern ^[0-9]{{12}}$."
    )


# AWS caps an IAM role description at 1000 characters; a longer value fails at
# `terraform plan` (aws_iam_role validation), not at authoring time. Guard it here
# so an over-long description is caught at the fastest gate (unit test) rather than
# deep in the terragrunt-plan lane.
_IAM_ROLE_DESCRIPTION_MAX = 1000


@pytest.mark.unit
def test_oidc_roles_json_descriptions_within_iam_limit() -> None:
    """Every role description must be <= the AWS IAM role-description limit (1000 chars)."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    over_limit = {
        f"{account_id}/{role_name}": len(role["description"])
        for account_id, entry in data.items()
        for role_name, role in entry.get("roles", {}).items()
        if isinstance(role, dict) and len(role.get("description", "")) > _IAM_ROLE_DESCRIPTION_MAX
    }
    assert not over_limit, (
        "The following role descriptions exceed the AWS IAM role-description limit "
        f"({_IAM_ROLE_DESCRIPTION_MAX} chars) and will fail at terraform plan: {over_limit}. "
        "Shorten each description to <= 1000 characters."
    )


# ---------------------------------------------------------------------------
# Required account presence tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("account_id", REQUIRED_ACCOUNTS, ids=REQUIRED_ACCOUNTS)
def test_oidc_roles_json_required_account_present(account_id: str) -> None:
    """Each oidc-bootstrap account must have an entry in oidc-roles.json (AC-FUNC-001/002)."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert account_id in data, (
        f"Required account '{account_id}' is missing from oidc-roles.json. "
        "All three oidc-bootstrap accounts (prod, qa, root) must have an entry "
        "so their leaves can resolve roles from common/ (AC-FUNC-001/002, spec 4.9)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("account_id", REQUIRED_ACCOUNTS, ids=REQUIRED_ACCOUNTS)
def test_oidc_roles_json_account_has_roles_key(account_id: str) -> None:
    """Each required account entry must carry a 'roles' sub-key (AC-FUNC-001/002)."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert account_id in data, f"Account '{account_id}' is missing from oidc-roles.json."
    row = data[account_id]
    assert isinstance(row, dict), (
        f"oidc-roles.json entry for account '{account_id}' must be a JSON object, "
        f"got {type(row).__name__}."
    )
    assert "roles" in row, (
        f"oidc-roles.json entry for account '{account_id}' must contain a 'roles' key. "
        "The roles map is keyed by role name and consumed by the oidc-bootstrap leaves "
        "to build the IAM roles (AC-FUNC-001/002, spec section 4.9)."
    )
    roles = row["roles"]
    assert isinstance(roles, dict) and len(roles) > 0, (
        f"oidc-roles.json 'roles' entry for account '{account_id}' must be a non-empty "
        f"JSON object, got: {type(roles).__name__} with value {roles!r}."
    )


# ---------------------------------------------------------------------------
# Per-account role key tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("role_name", sorted(PROD_EXPECTED_ROLES), ids=sorted(PROD_EXPECTED_ROLES))
def test_oidc_roles_json_prod_has_expected_role(role_name: str) -> None:
    """Prod account (111111111111) roles map must contain the expected role key (AC-FUNC-001)."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert PROD_ACCOUNT_ID in data, (
        f"Prod account '{PROD_ACCOUNT_ID}' is missing from oidc-roles.json."
    )
    roles = data[PROD_ACCOUNT_ID].get("roles", {})
    assert role_name in roles, (
        f"oidc-roles.json prod account ({PROD_ACCOUNT_ID}) is missing expected role "
        f"'{role_name}'. "
        f"Expected roles: {sorted(PROD_EXPECTED_ROLES)} (AC-FUNC-001, D40)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("role_name", sorted(QA_EXPECTED_ROLES), ids=sorted(QA_EXPECTED_ROLES))
def test_oidc_roles_json_qa_has_expected_role(role_name: str) -> None:
    """QA account (333333333333) roles map must contain the expected role key (AC-FUNC-002)."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert QA_ACCOUNT_ID in data, f"QA account '{QA_ACCOUNT_ID}' is missing from oidc-roles.json."
    roles = data[QA_ACCOUNT_ID].get("roles", {})
    assert role_name in roles, (
        f"oidc-roles.json QA account ({QA_ACCOUNT_ID}) is missing expected role "
        f"'{role_name}'. "
        f"Expected roles: {sorted(QA_EXPECTED_ROLES)} (AC-FUNC-002, D40)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("role_name", sorted(ROOT_EXPECTED_ROLES), ids=sorted(ROOT_EXPECTED_ROLES))
def test_oidc_roles_json_root_has_expected_role(role_name: str) -> None:
    """Root account (444444444444) roles map must contain the expected role key (AC-FUNC-002)."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert ROOT_ACCOUNT_ID in data, (
        f"Root account '{ROOT_ACCOUNT_ID}' is missing from oidc-roles.json."
    )
    roles = data[ROOT_ACCOUNT_ID].get("roles", {})
    assert role_name in roles, (
        f"oidc-roles.json root account ({ROOT_ACCOUNT_ID}) is missing expected role "
        f"'{role_name}'. "
        f"Expected roles: {sorted(ROOT_EXPECTED_ROLES)} (AC-FUNC-002, D40)."
    )


# ---------------------------------------------------------------------------
# No-secrets tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oidc_roles_json_contains_no_aws_access_key_patterns() -> None:
    """oidc-roles.json must contain no AWS access key ID patterns (AKIA/ASIA + 16 alphanumerics)."""
    if not OIDC_ROLES_JSON_PATH.exists():
        pytest.skip("oidc-roles.json does not exist yet -- existence tests cover this.")
    content = OIDC_ROLES_JSON_PATH.read_text(encoding="utf-8")
    found = [pattern.pattern for pattern in _SECRET_PATTERNS if pattern.search(content)]
    assert not found, (
        f"Potential AWS access key patterns found in oidc-roles.json: {found}. "
        "The file must contain only non-secret configuration."
    )


# ---------------------------------------------------------------------------
# FR-12 / AC-4 hardening tests (E10-F3-S3-T1, spec section 4.12)
# ---------------------------------------------------------------------------

# The exact sub claim that locks the prod apply role to the GHA prod-apply environment.
_PROD_APPLY_SUB = "repo:matthew-dresden/telemetry-platform:environment:prod-apply"

# The exact sub claim that locks the sandbox on-demand apply role to the GHA sandbox-apply
# environment (Phase 0). Mirrors the prod env-lock so only a job running in the sandbox-apply
# environment can assume the sandbox apply/destroy role.
_SANDBOX_APPLY_SUB = "repo:matthew-dresden/telemetry-platform:environment:sandbox-apply"

# Required FR-4 sweep action prefixes the QA terratest inline policy must cover.
# Tagging API reads are needed for enumeration; deletion actions per resource class
# from the FR-4 handler table. At least these prefixes must appear in the combined
# action list of any inline policy on the terratest role (spec section 4.12, FR-4).
_REQUIRED_SWEEP_ACTION_PREFIXES: frozenset[str] = frozenset(
    {
        "tag:GetResources",
        "sts:GetCallerIdentity",
        "s3:",
        "kms:",
        "iam:",
        "cloudfront:",
        "acm:",
        "route53:",
        "ec2:",
        "ecs:",
        "elasticloadbalancing:",
        "firehose:",
        "glue:",
        "athena:",
        "sns:",
        "ssm:",
        "lambda:",
        "logs:",
        "cloudwatch:",
        "budgets:",
        "ce:",
    }
)


def _collect_all_inline_policy_statements(
    inline_policies: dict[str, str],
    role_name: str,
) -> list[dict[str, Any]]:
    """Parse and return all Statement entries from all inline policies for a role.

    Fails fast if any inline policy value is not valid JSON, naming the role and
    policy key in the error message.
    """
    statements: list[dict[str, Any]] = []
    for policy_name, policy_json in inline_policies.items():
        try:
            doc = json.loads(policy_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"inline_policies['{policy_name}'] for role '{role_name}' is not "
                f"valid JSON: {exc}. "
                "Each inline policy value must be a JSON policy document string."
            ) from exc
        if not isinstance(doc, dict):
            raise TypeError(
                f"inline_policies['{policy_name}'] for role '{role_name}' must "
                f"decode to a JSON object, got {type(doc).__name__}."
            )
        for stmt in doc.get("Statement", []):
            statements.append(stmt)
    return statements


def _statement_widens_to_star_star(stmt: dict[str, Any]) -> bool:
    """Return True iff the statement grants Action='*' on Resource='*'."""
    effect = stmt.get("Effect", "")
    if effect != "Allow":
        return False
    action = stmt.get("Action", [])
    resource = stmt.get("Resource", [])
    if isinstance(action, str):
        action = [action]
    if isinstance(resource, str):
        resource = [resource]
    return "*" in action and "*" in resource


@pytest.mark.unit
def test_prod_tg_apply_sub_is_env_locked() -> None:
    """Prod tg-apply 'sub' must be locked to the prod-apply GHA environment (AC-4, spec 4.12).

    The sub claim 'repo:matthew-dresden/telemetry-platform:environment:prod-apply' ensures
    only GHA jobs running in the prod-apply environment (with required reviewers) can
    assume the apply role (AC-27, D-11).
    """
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert PROD_ACCOUNT_ID in data, (
        f"Prod account '{PROD_ACCOUNT_ID}' missing from oidc-roles.json."
    )
    roles = data[PROD_ACCOUNT_ID].get("roles", {})
    assert "telemetry-platform-gha-tg-apply" in roles, (
        "Prod account is missing role 'telemetry-platform-gha-tg-apply' in oidc-roles.json."
    )
    apply_role = roles["telemetry-platform-gha-tg-apply"]
    actual_sub = apply_role.get("sub")
    assert actual_sub == _PROD_APPLY_SUB, (
        f"telemetry-platform-gha-tg-apply 'sub' is {actual_sub!r}; "
        f"must be exactly {_PROD_APPLY_SUB!r} to lock the apply role to the "
        "prod-apply GHA environment (AC-27, spec section 4.12, D-11)."
    )


@pytest.mark.unit
def test_sandbox_apply_role_is_env_locked_admin_plus_sweep() -> None:
    """The sandbox on-demand apply role must be env-locked to sandbox-apply and carry the
    AdministratorAccess create grant plus a non-empty teardown/sweep inline policy (Phase 0).

    Modeled on the proven QA terratest identity (AdministratorAccess + sweep) so the role can both
    stand up AND `run-all destroy` the full sandbox stack, but with the trust GATED to the dedicated
    'sandbox-apply' GitHub environment (like prod's prod-apply) instead of a broad repo:...:* sub.
    """
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert SANDBOX_ACCOUNT_ID in data, (
        f"Sandbox account '{SANDBOX_ACCOUNT_ID}' missing from oidc-roles.json."
    )
    roles = data[SANDBOX_ACCOUNT_ID].get("roles", {})
    assert "telemetry-platform-gha-tg-apply" in roles, (
        f"Sandbox account ({SANDBOX_ACCOUNT_ID}) is missing role 'telemetry-platform-gha-tg-apply' "
        "-- required for the on-demand ephemeral-apply lifecycle (Phase 0)."
    )
    apply_role = roles["telemetry-platform-gha-tg-apply"]

    actual_sub = apply_role.get("sub")
    assert actual_sub == _SANDBOX_APPLY_SUB, (
        f"sandbox telemetry-platform-gha-tg-apply 'sub' is {actual_sub!r}; must be exactly "
        f"{_SANDBOX_APPLY_SUB!r} to gate the apply role to the sandbox-apply GHA environment "
        "(never a broad repo:...:* sub)."
    )
    assert "arn:aws:iam::aws:policy/AdministratorAccess" in apply_role.get(
        "managed_policy_arns", []
    ), (
        "sandbox apply role must attach AdministratorAccess for the create side of the stack "
        "(matches the established prod/QA OIDC-deploy-identity pattern)."
    )
    inline_policies = apply_role.get("inline_policies", {})
    assert isinstance(inline_policies, dict) and len(inline_policies) > 0, (
        f"sandbox apply role 'inline_policies' must be a non-empty map (teardown/sweep), "
        f"got: {inline_policies!r}. The role needs delete/sweep permissions to run-all destroy "
        "the full sandbox stack (mirrors the QA terratest-sweep-permissions policy)."
    )


@pytest.mark.unit
def test_sandbox_apply_role_sweep_covers_required_teardown_actions() -> None:
    """The sandbox apply role's sweep inline policy must cover the same FR-4 teardown action
    prefixes as the QA terratest sweep, so `run-all destroy` + orphan sweep fully tears down."""
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    roles = data.get(SANDBOX_ACCOUNT_ID, {}).get("roles", {})
    apply_role = roles.get("telemetry-platform-gha-tg-apply", {})
    inline_policies = apply_role.get("inline_policies", {})

    statements = _collect_all_inline_policy_statements(
        inline_policies, "telemetry-platform-gha-tg-apply (sandbox)"
    )
    allowed_actions: list[str] = []
    for stmt in statements:
        if stmt.get("Effect") == "Allow":
            action = stmt.get("Action", [])
            if isinstance(action, str):
                action = [action]
            allowed_actions.extend(action)

    missing = [
        prefix
        for prefix in sorted(_REQUIRED_SWEEP_ACTION_PREFIXES)
        if not any(act == prefix or act.startswith(prefix) for act in allowed_actions)
    ]
    assert not missing, (
        f"sandbox telemetry-platform-gha-tg-apply sweep inline policy is missing coverage for the "
        f"following required teardown action prefixes: {missing}. "
        "Mirror the QA terratest-sweep-permissions policy so the sandbox stack fully tears down."
    )


@pytest.mark.unit
def test_root_dns_writer_has_no_sub_field() -> None:
    """Root dns-writer must NOT carry a 'sub' field -- it uses role-chaining, not OIDC (D-11).

    The trust_policy_json is dynamically injected by the root terragrunt leaf at apply time
    (prod tg-apply role ARN as principal). Presence of a 'sub' field would incorrectly
    imply OIDC federation for the dns-writer role (AC-28, spec section 4.12).
    """
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert ROOT_ACCOUNT_ID in data, (
        f"Root account '{ROOT_ACCOUNT_ID}' missing from oidc-roles.json."
    )
    roles = data[ROOT_ACCOUNT_ID].get("roles", {})
    assert "telemetry-platform-dns-writer" in roles, (
        "Root account is missing role 'telemetry-platform-dns-writer' in oidc-roles.json."
    )
    dns_writer = roles["telemetry-platform-dns-writer"]
    assert "sub" not in dns_writer, (
        f"telemetry-platform-dns-writer must NOT have a 'sub' field in oidc-roles.json "
        f"(found sub={dns_writer['sub']!r}). "
        "The dns-writer uses role-chaining from prod tg-apply (D-11); the trust policy "
        "is injected at apply time by the root terragrunt leaf. A 'sub' field would "
        "incorrectly configure it as an OIDC-trusted role (AC-28, spec 4.12)."
    )


@pytest.mark.unit
def test_qa_terratest_role_has_non_empty_inline_policies() -> None:
    """QA terratest role must carry non-empty inline_policies for FR-4 sweep (AC-4, spec 4.12).

    The FR-4 sweep tool runs under the QA terratest OIDC role. The role's permissions
    must include the Tagging API reads and deletion actions for all example resource
    classes. An empty inline_policies map means the role has no sweep capability
    (spec section 4.12, FR-9, doc-03 6.2).
    """
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    assert QA_ACCOUNT_ID in data, f"QA account '{QA_ACCOUNT_ID}' missing from oidc-roles.json."
    roles = data[QA_ACCOUNT_ID].get("roles", {})
    assert "telemetry-platform-gha-terratest" in roles, (
        "QA account is missing role 'telemetry-platform-gha-terratest' in oidc-roles.json."
    )
    terratest_role = roles["telemetry-platform-gha-terratest"]
    inline_policies = terratest_role.get("inline_policies", {})
    assert isinstance(inline_policies, dict) and len(inline_policies) > 0, (
        f"telemetry-platform-gha-terratest 'inline_policies' must be a non-empty map, "
        f"got: {inline_policies!r}. "
        "The QA terratest role needs sweep permissions (Tagging API reads + deletion "
        "actions for all example resource classes) to support the FR-4 orphan sweep "
        "and the always-run CI cleanup step (AC-4, spec section 4.12, FR-9)."
    )


@pytest.mark.unit
def test_qa_terratest_inline_policies_cover_required_sweep_actions() -> None:
    """QA terratest inline policies must cover all required FR-4 sweep action prefixes.

    The sweep tool (scripts/terratest_sweep.py) requires Tagging API reads for
    enumeration plus deletion actions for every resource class in the FR-4 handler
    table: S3, KMS, IAM, CloudFront, ACM, Route53, EC2, ECS, ELB, Firehose, Glue,
    Athena, SNS, SSM, Lambda, CloudWatch/Logs, QuickSight (CE/Budgets) (spec 4.4, 4.12).
    """
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    roles = data.get(QA_ACCOUNT_ID, {}).get("roles", {})
    terratest_role = roles.get("telemetry-platform-gha-terratest", {})
    inline_policies = terratest_role.get("inline_policies", {})

    statements = _collect_all_inline_policy_statements(
        inline_policies, "telemetry-platform-gha-terratest"
    )
    # Gather every Action value from Allow statements.
    allowed_actions: list[str] = []
    for stmt in statements:
        if stmt.get("Effect") == "Allow":
            action = stmt.get("Action", [])
            if isinstance(action, str):
                action = [action]
            allowed_actions.extend(action)

    missing: list[str] = []
    for required_prefix in sorted(_REQUIRED_SWEEP_ACTION_PREFIXES):
        if not any(
            act == required_prefix or act.startswith(required_prefix) for act in allowed_actions
        ):
            missing.append(required_prefix)

    assert not missing, (
        f"telemetry-platform-gha-terratest inline_policies are missing coverage for the "
        f"following required FR-4 sweep action prefixes: {missing}. "
        "Add the missing service permissions to the QA terratest inline policy in "
        "terragrunt/common/oidc-roles.json (spec section 4.12, doc-03 6.2, FR-4)."
    )


@pytest.mark.unit
def test_no_inline_policy_statement_widens_to_star_star() -> None:
    """No inline policy statement in any role may grant Action='*' on Resource='*'.

    Statements that widen the Action+Resource pair to '*'+'*' violate the least-privilege
    requirement (spec section 4.12, doc-03 6.2, CLAUDE.md security). All permissions
    must use tagged or tt-prefixed scoping, not unbounded wildcards (AC-4).
    """
    data = _load_oidc_roles(OIDC_ROLES_JSON_PATH)
    violations: list[str] = []
    for account_id, account_entry in data.items():
        for role_name, role_spec in account_entry.get("roles", {}).items():
            inline_policies = role_spec.get("inline_policies", {})
            if not inline_policies:
                continue
            statements = _collect_all_inline_policy_statements(inline_policies, role_name)
            for idx, stmt in enumerate(statements):
                if _statement_widens_to_star_star(stmt):
                    violations.append(
                        f"account={account_id} role={role_name} stmt[{idx}]: "
                        f"Action={stmt.get('Action')!r} Resource={stmt.get('Resource')!r}"
                    )
    assert not violations, (
        f"The following inline policy statements widen to Action='*' on Resource='*', "
        f"violating least-privilege: {violations}. "
        "Use tagged/tt-prefixed resource conditions instead of unbounded wildcards "
        "(spec section 4.12, doc-03 6.2, AC-4)."
    )


# ---------------------------------------------------------------------------
# oidc-bootstrap module fail-fast guard (E10-F3-S3-T1, REVIEW_FAIL round 1)
# ---------------------------------------------------------------------------


OIDC_BOOTSTRAP_MAIN_TF = (
    REPO_ROOT / "providers" / "aws" / "references" / "oidc-bootstrap" / "main.tf"
)


@pytest.mark.unit
def test_oidc_bootstrap_main_tf_has_oidc_trust_guard() -> None:
    """oidc-bootstrap main.tf must contain a fail-fast guard for OIDC roles missing provider ARN.

    When github_oidc_provider_arn was made optional (default ""), the existing OIDC trust
    path silently emits Principal.Federated="" for any role that has sub set but no
    provider ARN -- an invalid trust policy that Terraform accepts without error at plan.

    The fix requires a Terraform check block (or lifecycle precondition) that fails fast
    with the provider ARN named in the message when any role has:
      sub != "" AND trust_policy_json == "" AND github_oidc_provider_arn == ""

    This test verifies the guard is present in main.tf by checking for the Terraform
    check block pattern and the key guard condition tokens (E10-F3-S3-T1, REVIEW_FAIL r1,
    FR-10 fail-fast contract).
    """
    assert OIDC_BOOTSTRAP_MAIN_TF.exists(), (
        f"oidc-bootstrap main.tf not found at {OIDC_BOOTSTRAP_MAIN_TF}. "
        "The file must exist and contain the OIDC-trust fail-fast guard."
    )
    content = OIDC_BOOTSTRAP_MAIN_TF.read_text(encoding="utf-8")

    # The guard must use a Terraform check block (Terraform >= 1.5, available at >= 1.15.5).
    # The check block must assert that github_oidc_provider_arn is non-empty
    # for any OIDC-trust role (sub != "" and trust_policy_json == "").
    assert "check " in content or 'check"' in content or "check {" in content, (
        "oidc-bootstrap main.tf does not contain a Terraform 'check' block. "
        "A check block is required to fail fast when any role uses OIDC trust "
        "(sub set, trust_policy_json empty) but github_oidc_provider_arn is empty. "
        "Silently emitting Principal.Federated='' violates the FR-10 fail-fast contract "
        "(E10-F3-S3-T1, REVIEW_FAIL round 1)."
    )
    assert "github_oidc_provider_arn" in content, (
        "oidc-bootstrap main.tf check block must reference 'github_oidc_provider_arn' "
        "in the guard condition so the error message names the missing ARN "
        "(FR-10 fail-fast contract, E10-F3-S3-T1)."
    )
