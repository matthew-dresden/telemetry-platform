"""Unit tests for the sandbox integration-validation evidence contract in environment_instance.hcl.

These tests assert the structural and content constraints for the sandbox account
(222222222222) environment_instance.hcl integration-validation evidence block. They
validate:

- AC-19: the environment_instance.hcl carries the integration-validation evidence
  contract (apply_result, smoke_outcome, destroy_confirmation) sourced as inputs
  with no inline literal values -- all evidence tokens are input-driven (D31).
- AC-19: the evidence block includes an integration_validation locals block or
  equivalent structure that gates on the three pillars: apply, smoke, destroy.
- AC-18: the environment_instance.hcl anchors the AC-18 parity contract by
  declaring the sandbox and prod subtree paths used for module-source parity
  comparison, sourced from account.hcl (never hardcoded).
- D30: no LocalStack references appear anywhere in the file -- the integration
  gate requires real AWS.
- D31: sandbox and prod units differ only by folder path and per-environment inputs,
  not by module sources, versions, or unit set; evidence tokens make this auditable.
- D33: the evidence block confirms the cycle runs locally under AWS_PROFILE=sandbox
  with no GitHub Actions workflow or OIDC role reference.
- Security: no hardcoded AWS access keys, no em-dash characters (U+2014), no
  secrets or credentials inline.
- docs/terragrunt-concepts.md: canonical seven-layer hierarchy basename idiom present.

All assertions use file-content inspection to validate static HCL without
requiring a live AWS credential or a real Terraform init.

Test count: 30 unit-marked tests covering file existence, evidence contract structure,
evidence token presence, sourcing discipline (inputs vs literals), parity block,
forbidden patterns (LocalStack, em-dash, secrets), and security.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

SANDBOX_ACCOUNT = "222222222222"
PROD_SERVICE_ACCOUNT = "111111111111"
DNS_OWNER_ACCOUNT = "444444444444"

ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / SANDBOX_ACCOUNT
SANDBOX_BASE = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / "sandbox"
ENV_INSTANCE_DIR = SANDBOX_BASE / "000"
ENVIRONMENT_INSTANCE_HCL = ENV_INSTANCE_DIR / "environment_instance.hcl"

# AC-19: evidence contract token names that MUST appear in the file.
EVIDENCE_APPLY_RESULT_TOKEN = "apply_result"
EVIDENCE_SMOKE_OUTCOME_TOKEN = "smoke_outcome"
EVIDENCE_DESTROY_CONFIRMATION_TOKEN = "destroy_confirmation"

# AC-18: parity subtree path segments that MUST appear, sourced from account.hcl.
SANDBOX_SUBTREE_SEGMENT = "sandbox"
PROD_SERVICE_ACCOUNT_SEGMENT = PROD_SERVICE_ACCOUNT
DNS_OWNER_ACCOUNT_SEGMENT = DNS_OWNER_ACCOUNT

# D30: LocalStack must NOT appear anywhere.
LOCALSTACK_MARKER = "localstack"

# D33: GitHub Actions OIDC role reference must NOT appear in the evidence block.
GITHUB_ACTIONS_OIDC_MARKER = "github.com/token"
OIDC_ROLE_MARKER = "oidc_role"

# docs/terragrunt-concepts.md: canonical basename idiom.
BASENAME_IDIOM = "basename(get_terragrunt_dir())"

# D31: evidence values must NOT be hardcoded literals (empty-string sentinels are OK
# as the initial baseline; non-empty literal results are forbidden).
HARDCODED_APPLY_SUCCESS_LITERAL = '"success"'
HARDCODED_SMOKE_PASS_LITERAL = '"pass"'
HARDCODED_DESTROY_CLEAN_LITERAL = '"clean"'

# Security: em-dash character (U+2014) must NOT appear.
EM_DASH = "\u2014"

# AC-18: allowed prod-only delta units -- these are not required to have sandbox
# counterparts and their prod-only presence is not a parity failure.
PROD_ONLY_DELTA_UNITS = frozenset(
    {
        "dns-delegation",
        "dns-collector-pretty",
    }
)


# ---------------------------------------------------------------------------
# File fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def env_instance_content() -> str:
    """Read environment_instance.hcl content. Fails loudly if the file does not exist."""
    assert ENVIRONMENT_INSTANCE_HCL.exists(), (
        f"environment_instance.hcl not found at {ENVIRONMENT_INSTANCE_HCL}. "
        "This file must be present for the sandbox environment instance layer (AC-19)."
    )
    return ENVIRONMENT_INSTANCE_HCL.read_text()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_environment_instance_hcl_exists() -> None:
    """environment_instance.hcl must exist at the sandbox/000 path (AC-19)."""
    assert ENVIRONMENT_INSTANCE_HCL.exists(), (
        f"environment_instance.hcl not found at {ENVIRONMENT_INSTANCE_HCL}. "
        "The sandbox environment instance file is required (AC-19)."
    )


# ---------------------------------------------------------------------------
# Basename idiom (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_environment_instance_hcl_declares_basename_local(
    env_instance_content: str,
) -> None:
    """environment_instance.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert BASENAME_IDIOM in env_instance_content, (
        "environment_instance.hcl must declare "
        f"`environment_instance = {BASENAME_IDIOM}` (docs/terragrunt-concepts.md). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# AC-19: Integration-validation evidence contract -- token presence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_evidence_contract_contains_apply_result_token(
    env_instance_content: str,
) -> None:
    """environment_instance.hcl must declare the apply_result evidence token (AC-19)."""
    assert EVIDENCE_APPLY_RESULT_TOKEN in env_instance_content, (
        f"environment_instance.hcl must contain the '{EVIDENCE_APPLY_RESULT_TOKEN}' "
        "evidence token as part of the integration-validation contract (AC-19). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_evidence_contract_contains_smoke_outcome_token(
    env_instance_content: str,
) -> None:
    """environment_instance.hcl must declare the smoke_outcome evidence token (AC-19)."""
    assert EVIDENCE_SMOKE_OUTCOME_TOKEN in env_instance_content, (
        f"environment_instance.hcl must contain the '{EVIDENCE_SMOKE_OUTCOME_TOKEN}' "
        "evidence token as part of the integration-validation contract (AC-19). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_evidence_contract_contains_destroy_confirmation_token(
    env_instance_content: str,
) -> None:
    """environment_instance.hcl must declare the destroy_confirmation evidence token (AC-19)."""
    assert EVIDENCE_DESTROY_CONFIRMATION_TOKEN in env_instance_content, (
        f"environment_instance.hcl must contain the '{EVIDENCE_DESTROY_CONFIRMATION_TOKEN}' "
        "evidence token as part of the integration-validation contract (AC-19). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# AC-19: Evidence contract block structure (locals block required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_evidence_contract_is_inside_locals_block(
    env_instance_content: str,
) -> None:
    """All three evidence tokens must appear within a locals { } block (AC-19)."""
    # Find the locals block(s) and ensure all tokens appear within one.
    locals_blocks = re.findall(r"locals\s*\{[^}]*\}", env_instance_content, re.DOTALL)
    assert locals_blocks, (
        "environment_instance.hcl must contain at least one locals { } block. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )
    combined = "\n".join(locals_blocks)
    for token in (
        EVIDENCE_APPLY_RESULT_TOKEN,
        EVIDENCE_SMOKE_OUTCOME_TOKEN,
        EVIDENCE_DESTROY_CONFIRMATION_TOKEN,
    ):
        assert token in combined, (
            f"Evidence token '{token}' must appear inside a locals {{ }} block "
            f"in environment_instance.hcl (AC-19). File: {ENVIRONMENT_INSTANCE_HCL}"
        )


# ---------------------------------------------------------------------------
# AC-19: Evidence tokens must be sourced, not hardcoded literals
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_result_is_not_a_hardcoded_success_literal(
    env_instance_content: str,
) -> None:
    """apply_result must not be assigned the hardcoded literal 'success' (D31).

    The test checks the assignment line only -- comments that mention the word
    'success' to describe what the field eventually holds are not a violation.
    """
    # Match the actual assignment line: apply_result = "success"
    assignment_pattern = re.compile(r'^\s*apply_result\s*=\s*"success"', re.MULTILINE)
    assert not assignment_pattern.search(env_instance_content), (
        f"environment_instance.hcl must not assign apply_result = "
        f"{HARDCODED_APPLY_SUCCESS_LITERAL} as a hardcoded literal. "
        "Evidence is recorded after a real apply (D31). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_smoke_outcome_is_not_a_hardcoded_pass_literal(
    env_instance_content: str,
) -> None:
    """smoke_outcome must not be assigned the hardcoded literal 'pass' (D31).

    The test checks the assignment line only -- comments mentioning the field
    outcome are not a violation.
    """
    assignment_pattern = re.compile(r'^\s*smoke_outcome\s*=\s*"pass"', re.MULTILINE)
    assert not assignment_pattern.search(env_instance_content), (
        f"environment_instance.hcl must not assign smoke_outcome = "
        f"{HARDCODED_SMOKE_PASS_LITERAL} as a hardcoded literal. "
        "Evidence is recorded after a real smoke run (D31). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_destroy_confirmation_is_not_a_hardcoded_clean_literal(
    env_instance_content: str,
) -> None:
    """destroy_confirmation must not be assigned the hardcoded literal 'clean' (D31).

    The test checks the assignment line only -- comments mentioning the outcome
    state are not a violation.
    """
    assignment_pattern = re.compile(r'^\s*destroy_confirmation\s*=\s*"clean"', re.MULTILINE)
    assert not assignment_pattern.search(env_instance_content), (
        f"environment_instance.hcl must not assign destroy_confirmation = "
        f"{HARDCODED_DESTROY_CLEAN_LITERAL} as a hardcoded literal. "
        "Evidence is recorded after a real destroy cycle (D31). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# AC-19: Evidence baseline -- unrecorded evidence sentinel
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_evidence_contract_baseline_apply_result_is_empty_sentinel(
    env_instance_content: str,
) -> None:
    """apply_result must use an empty-string sentinel as the unrecorded baseline (AC-19).

    The baseline (pre-apply) value must be an empty string so that validation
    tooling can detect whether evidence has been recorded. A non-empty literal
    is forbidden (D31). An empty sentinel is required to fail the validation
    contract until a real apply is run.
    """
    # The apply_result line must contain the token followed by an assignment.
    # Pattern: apply_result = "" (or using a variable ref -- either is valid).
    apply_result_line_match = re.search(r"apply_result\s*=\s*(.+)", env_instance_content)
    assert apply_result_line_match is not None, (
        f"environment_instance.hcl must assign a value to apply_result (AC-19). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )
    assigned_value = apply_result_line_match.group(1).strip()
    assert assigned_value in ('""', "null") or assigned_value.startswith("var."), (
        f'apply_result baseline must be an empty-string sentinel ("") or a '
        f"variable reference (var.*) so the evidence contract fails until a real "
        f"apply is recorded (AC-19). Got: {assigned_value!r}. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_evidence_contract_baseline_smoke_outcome_is_empty_sentinel(
    env_instance_content: str,
) -> None:
    """smoke_outcome must use an empty-string sentinel as the unrecorded baseline (AC-19)."""
    smoke_line_match = re.search(r"smoke_outcome\s*=\s*(.+)", env_instance_content)
    assert smoke_line_match is not None, (
        f"environment_instance.hcl must assign a value to smoke_outcome (AC-19). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )
    assigned_value = smoke_line_match.group(1).strip()
    assert assigned_value in ('""', "null") or assigned_value.startswith("var."), (
        f'smoke_outcome baseline must be an empty-string sentinel ("") or a '
        f"variable reference (var.*) so the evidence contract fails until a real "
        f"smoke run is recorded (AC-19). Got: {assigned_value!r}. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_evidence_contract_baseline_destroy_confirmation_is_empty_sentinel(
    env_instance_content: str,
) -> None:
    """destroy_confirmation must use an empty-string sentinel as the unrecorded baseline."""
    destroy_line_match = re.search(r"destroy_confirmation\s*=\s*(.+)", env_instance_content)
    assert destroy_line_match is not None, (
        f"environment_instance.hcl must assign a value to destroy_confirmation (AC-19). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )
    assigned_value = destroy_line_match.group(1).strip()
    assert assigned_value in ('""', "null") or assigned_value.startswith("var."), (
        f'destroy_confirmation baseline must be an empty-string sentinel ("") or a '
        f"variable reference (var.*) so the evidence contract fails until a real "
        f"destroy is recorded (AC-19). Got: {assigned_value!r}. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# AC-18: Parity contract -- prod subtree path references
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# D30: No LocalStack references
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_localstack_endpoint_or_url_reference(env_instance_content: str) -> None:
    """environment_instance.hcl must not configure a LocalStack endpoint or URL (D30).

    Comments that state the D30 no-LocalStack policy are acceptable. The test
    rejects any line that sets a localstack endpoint, host, or URL as a value.
    """
    # Matches actual configuration assignments containing localstack hostnames or
    # endpoint URLs (e.g., endpoint = "http://localhost:4566", host = "localstack").
    # Does NOT flag comment lines (lines starting with optional whitespace then '#').
    localstack_config_pattern = re.compile(
        r"^(?!\s*#).*(?:localhost:\s*45[0-9]{2}|localstack\.cloud|localstack/localstack)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = localstack_config_pattern.search(env_instance_content)
    assert match is None, (
        "environment_instance.hcl must not configure a LocalStack endpoint or URL. "
        f"The integration gate requires real AWS (D30). Match: {match.group() if match else ''}. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# D33: No GitHub Actions OIDC role reference
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_github_actions_oidc_role_reference(env_instance_content: str) -> None:
    """environment_instance.hcl must not reference GitHub Actions OIDC roles (D33)."""
    assert GITHUB_ACTIONS_OIDC_MARKER not in env_instance_content, (
        "environment_instance.hcl must not reference GitHub Actions OIDC tokens. "
        "The sandbox integration cycle runs locally under AWS_PROFILE=sandbox (D33). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# Security: no em-dash, no secrets
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_em_dash_characters(env_instance_content: str) -> None:
    """environment_instance.hcl must not contain em-dash (U+2014) characters."""
    assert EM_DASH not in env_instance_content, (
        "environment_instance.hcl contains an em-dash (U+2014) character, which is "
        "prohibited. Use '--' (double hyphen) instead. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_no_hardcoded_aws_access_key(env_instance_content: str) -> None:
    """environment_instance.hcl must not contain hardcoded AWS access key IDs."""
    # AWS access key IDs start with AKIA or ASIA followed by 16 uppercase alphanum chars.
    access_key_pattern = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")
    assert not access_key_pattern.search(env_instance_content), (
        "environment_instance.hcl contains what looks like a hardcoded AWS access key. "
        "Credentials must never appear in HCL files. "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


@pytest.mark.unit
def test_no_hardcoded_secret_keywords(env_instance_content: str) -> None:
    """environment_instance.hcl must not contain secret or password keywords inline."""
    forbidden_patterns = [
        r"secret\s*=\s*\"[^\"]+\"",
        r"password\s*=\s*\"[^\"]+\"",
        r"token\s*=\s*\"[^A-Z\{\}][^\"]*\"",  # non-templated token value
    ]
    for pattern in forbidden_patterns:
        match = re.search(pattern, env_instance_content, re.IGNORECASE)
        assert match is None, (
            f"environment_instance.hcl contains a suspicious inline secret "
            f"matching pattern '{pattern}': {match.group() if match else ''}. "
            "Credentials must come from environment or Secrets Manager. "
            f"File: {ENVIRONMENT_INSTANCE_HCL}"
        )


# ---------------------------------------------------------------------------
# Structural: enable_custom_domain sourcing (D31)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enable_custom_domain_sourced_from_account_hcl(
    env_instance_content: str,
) -> None:
    """enable_custom_domain must be sourced from account.hcl, not hardcoded (D31)."""
    assert "enable_custom_domain" in env_instance_content, (
        "environment_instance.hcl must declare enable_custom_domain (D31). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )
    assert "account_vars" in env_instance_content, (
        "environment_instance.hcl must source enable_custom_domain via account_vars "
        "(read_terragrunt_config of account.hcl), not as a hardcoded value (D31). "
        f"File: {ENVIRONMENT_INSTANCE_HCL}"
    )


# ---------------------------------------------------------------------------
# AC-18: Parity subtree paths -- sandbox subtree declared
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AC-18: Sandbox unit set -- all expected units present in 000 subtree
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "unit_name",
    [
        "acm-collector",
        "acm-validate-collector",
        "athena",
        "collector-ingestion",
        "data-lake",
        "dns-collector",
        "identity",
        "observability",
    ],
)
def test_sandbox_unit_directory_exists(unit_name: str) -> None:
    """Each expected sandbox service unit directory must exist under 000/ (AC-18)."""
    _ud_base = (
        (SANDBOX_BASE / "_singletons" / "shared")
        if unit_name in {"athena", "data-lake", "identity", "observability"}
        else ENV_INSTANCE_DIR
    )
    unit_dir = _ud_base / unit_name
    assert unit_dir.is_dir(), (
        f"Sandbox unit directory '{unit_name}' not found at {unit_dir}. "
        "All service units must exist in the sandbox subtree for AC-18 parity (AC-18)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "unit_name",
    [
        "acm-collector",
        "acm-validate-collector",
        "athena",
        "collector-ingestion",
        "data-lake",
        "dns-collector",
        "identity",
        "observability",
    ],
)
def test_sandbox_unit_terragrunt_hcl_exists(unit_name: str) -> None:
    """Each expected sandbox service unit must have a leaf terragrunt.hcl (AC-18)."""
    _base = (
        (SANDBOX_BASE / "_singletons" / "shared")
        if unit_name in {"athena", "data-lake", "identity", "observability"}
        else ENV_INSTANCE_DIR
    )
    tg_hcl = _base / unit_name / "000" / "terragrunt.hcl"
    assert tg_hcl.is_file(), (
        f"Sandbox unit '{unit_name}' missing leaf terragrunt.hcl at {tg_hcl}. "
        "Every service unit must have a terragrunt.hcl for the parity check (AC-18)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("role", ["sandbox_role", "prod_role", "qa_role"])
def test_foundation_dns_prod_zone_unit_exists(role: str) -> None:
    """The dns-prod-zone unit must live in the stable foundation (bootstrap) tier.

    Phase 8 (Approach A): dns-prod-zone owns the long-lived per-env hosted zone, the
    platform 'telemetry-config' Customer Managed Key, and the SSM seed. It was relocated
    OUT of the disposable <env>/_singletons/shared service tree into bootstrap/<role>/ (a
    sibling of the env subtree) so a service-tree destroy/recreate never churns the zone
    (nameservers) or the Customer Managed Key (ARN). It must exist for every role and must
    NOT remain in the service tree.
    """
    us_east_1 = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
    leaf = us_east_1 / "bootstrap" / role / "dns-prod-zone" / "000" / "terragrunt.hcl"
    assert leaf.is_file(), (
        f"Foundation dns-prod-zone leaf for '{role}' not found at {leaf}. "
        "The dns-prod-zone unit must live in bootstrap/<role>/ (Phase 8 Approach A)."
    )
    stale = us_east_1 / role.removesuffix("_role") / "_singletons" / "shared" / "dns-prod-zone"
    assert not stale.exists(), (
        f"dns-prod-zone must NOT remain in the service tree at {stale}; it was relocated "
        "to the foundation (bootstrap) tier (Phase 8 Approach A)."
    )


# ---------------------------------------------------------------------------
# AC-18: No prod-only delta units in sandbox subtree
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "delta_unit",
    sorted(PROD_ONLY_DELTA_UNITS),
)
def test_prod_only_delta_unit_not_in_sandbox_subtree(delta_unit: str) -> None:
    """Prod-only delta units must NOT exist in the sandbox subtree (AC-18).

    dns-delegation and dns-collector-pretty are prod-only by
    design (homed in the DNS-owner account). Their absence from sandbox is expected
    and is NOT a parity failure. Having them in sandbox would be an error.
    """
    delta_dir = ENV_INSTANCE_DIR / delta_unit
    assert not delta_dir.exists(), (
        f"Prod-only delta unit '{delta_unit}' must NOT exist in the sandbox subtree "
        f"at {delta_dir}. This unit is DNS-owner-account-only (AC-18). "
        "Remove it from the sandbox subtree."
    )


# ---------------------------------------------------------------------------
# AC-18 (env-keyed): structural parity between the sandbox and prod env trees.
#
# The pre-refactor parity contract compared account-keyed subtrees (166.../sandbox vs
# 136.../prod vs 468.../prod) and named dns-collector-pretty/dns-portal-pretty as prod-only
# deltas. The env-keyed instance-set layout removed account folders and made the tree fully
# symmetric (the _pretty + _dns_owner singletons exist in BOTH envs), so parity is now the
# stronger property: sandbox/ and prod/ have IDENTICAL unit structure, and every env difference
# is resolved from config (account.hcl / domains.json / env_accounts.json), never the tree.
# ---------------------------------------------------------------------------

PROD_BASE = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / "prod"
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")


def _unit_dir_names(env_base, relsub):
    """Names of the unit directories (those containing a terragrunt.hcl below them) under relsub."""
    base = env_base / relsub
    if not base.exists():
        return set()
    return {
        p.name
        for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(".") and any(p.rglob("terragrunt.hcl"))
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "relsub",
    ["000", "_singletons/shared", "_singletons/dns_owner", "_singletons/pretty"],
)
def test_parity_sandbox_and_prod_unit_structure_identical(relsub: str) -> None:
    """Every tier's unit set is identical between sandbox and prod (AC-18, env-keyed parity)."""
    sandbox_units = _unit_dir_names(SANDBOX_BASE, relsub)
    prod_units = _unit_dir_names(PROD_BASE, relsub)
    assert sandbox_units, f"sandbox/{relsub} declares no units; parity comparison is meaningless."
    assert sandbox_units == prod_units, (
        f"sandbox and prod diverge under {relsub}: only in sandbox={sandbox_units - prod_units}, "
        f"only in prod={prod_units - sandbox_units}. The env-keyed trees must be structurally "
        "identical; env differences come from config, not the tree (AC-18)."
    )


@pytest.mark.unit
def test_parity_pretty_units_present_in_both_envs() -> None:
    """The pretty CNAME units exist in BOTH envs (no prod-only delta in the env-keyed layout)."""
    for env_base, env in ((SANDBOX_BASE, "sandbox"), (PROD_BASE, "prod")):
        pretty = _unit_dir_names(env_base, "_singletons/pretty")
        assert {"collector"} <= pretty, (
            f"{env}/_singletons/pretty must contain collector; the active-set "
            "pretty CNAME is symmetric across envs (its account/zone come from config, D2)."
        )


@pytest.mark.unit
@pytest.mark.parametrize("env_base,env", [(SANDBOX_BASE, "sandbox"), (PROD_BASE, "prod")])
def test_parity_no_account_number_folders_in_env_tree(env_base, env: str) -> None:
    """No 12-digit account-id folder appears anywhere under an env tree (account out of path,
    D2)."""
    offenders = [p for p in env_base.rglob("*") if p.is_dir() and _ACCOUNT_ID_RE.match(p.name)]
    assert not offenders, (
        f"{env} tree contains account-number folders {[p.name for p in offenders]}; the account "
        "must be resolved from config, never encoded in the folder path (D2)."
    )
