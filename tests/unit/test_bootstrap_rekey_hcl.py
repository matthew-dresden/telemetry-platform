"""Unit tests for the re-keyed bootstrap tree (terragrunt/live/.../bootstrap/<role>/).

Supersedes the pre-refactor per-account bootstrap tests (test_account_hcl, test_prod_bootstrap_hcl,
test_qa_oidc_bootstrap_hcl, test_root_oidc_bootstrap_hcl, test_sandbox_state_bootstrap_hcl,
test_state_bootstrap_backend_toggle) which asserted the OLD account-keyed model
(<account-id>/bootstrap/000/<unit> + account.hcl = basename(get_terragrunt_dir())).

The env-keyed layout abstracts the AWS account OUT of the folder path: the per-account bootstraps
live under bootstrap/<role>/ (role dir = sandbox_role/prod_role/qa_role/dns_owner_role), the
account is resolved from common/env_accounts.json by the role basename with the "_role" namespace
suffix stripped, and there are ZERO 12-digit account-number folders.
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
US_EAST_1 = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
BOOTSTRAP = US_EAST_1 / "bootstrap"

# role dir (4th-field "*_role" sub-class) -> the bootstrap units that role hosts
ROLE_UNITS = {
    "sandbox_role": ["state-bootstrap"],
    "prod_role": ["oidc-bootstrap", "state-bootstrap"],
    "qa_role": ["oidc-bootstrap"],
    "dns_owner_role": ["oidc-bootstrap", "state-bootstrap"],
}
ROLES = sorted(ROLE_UNITS)
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")


def _read(p: pathlib.Path) -> str:
    assert p.exists(), f"expected bootstrap file not found: {p}"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Layout (account abstracted out of the path)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_account_number_folders_anywhere_under_live() -> None:
    """The env-keyed + re-keyed-bootstrap layout has ZERO 12-digit account-number folders (D2)."""
    offenders = [
        p.name
        for p in US_EAST_1.rglob("*")
        if p.is_dir() and _ACCOUNT_ID_RE.match(p.name) and ".terragrunt-cache" not in str(p)
    ]
    assert not offenders, (
        f"Found account-number folders {offenders} under terragrunt/live; the AWS account must be "
        "resolved from config (env_accounts.json), never encoded in the folder path (D2)."
    )


@pytest.mark.unit
def test_shared_bootstrap_environment_hcl() -> None:
    """bootstrap/environment.hcl is shared and basename-derives environment = 'bootstrap'."""
    content = _read(BOOTSTRAP / "environment.hcl")
    assert "environment = basename(get_terragrunt_dir())" in content, (
        "bootstrap/environment.hcl must derive environment from the basename (-> 'bootstrap')."
    )


@pytest.mark.unit
@pytest.mark.parametrize("role", ROLES)
def test_role_scaffolding_present(role: str) -> None:
    """Each bootstrap/<role> carries account.hcl + environment_instance.hcl."""
    base = BOOTSTRAP / role
    for fname in ("account.hcl", "environment_instance.hcl"):
        assert (base / fname).exists(), f"bootstrap/{role}/{fname} is missing."


@pytest.mark.unit
@pytest.mark.parametrize("role", ROLES)
def test_role_hosts_expected_units(role: str) -> None:
    """Each role hosts exactly its expected bootstrap units (state-bootstrap and/or
    oidc-bootstrap)."""
    for unit in ROLE_UNITS[role]:
        leaf = BOOTSTRAP / role / unit / "000" / "terragrunt.hcl"
        assert leaf.exists(), f"bootstrap/{role}/{unit}/000/terragrunt.hcl is missing."


@pytest.mark.unit
@pytest.mark.parametrize("role", ROLES)
def test_environment_instance_is_basename_derived(role: str) -> None:
    """environment_instance is the role, derived from the dir basename (folder mirrors
    namespace)."""
    content = _read(BOOTSTRAP / role / "environment_instance.hcl")
    assert "environment_instance = basename(get_terragrunt_dir())" in content, (
        f"bootstrap/{role}/environment_instance.hcl must basename-derive environment_instance."
    )


# ---------------------------------------------------------------------------
# Role-keyed account.hcl (account resolved from config, NOT the folder basename)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("role", ROLES)
def test_account_hcl_resolves_account_from_config_by_role(role: str) -> None:
    """account.hcl resolves the account from env_accounts.json by the role basename - NOT from a
    12-digit folder basename (the old model) and NOT hardcoded."""
    content = _read(BOOTSTRAP / role / "account.hcl")
    assert "common/env_accounts.json" in content, (
        f"bootstrap/{role}/account.hcl must read common/env_accounts.json to resolve the account."
    )
    assert "basename(get_terragrunt_dir())" in content, (
        f"bootstrap/{role}/account.hcl must use the role basename as the lookup key."
    )
    # the old account-keyed model derived the account id straight from the basename - must be gone
    assert (
        "aws_account_id            = basename(get_terragrunt_dir())" not in content
        and "aws_account_id = basename(get_terragrunt_dir())" not in content
    ), (
        f"bootstrap/{role}/account.hcl must NOT derive aws_account_id directly from the folder "
        "basename (the obsolete account-keyed model); it resolves from env_accounts.json by role."
    )
    assert (
        "aws_account_id" in content
        and "aws_profile" in content
        and "use_pinned_module_sources" in content
    ), (
        f"bootstrap/{role}/account.hcl must expose aws_account_id + aws_profile + "
        "use_pinned_module_sources."
    )


@pytest.mark.unit
def test_dns_owner_account_hcl_uses_dns_owner_block_no_zone_id() -> None:
    """The dns_owner_role bootstrap resolves the shared dns_owner account and carries no
    dns_owner_zone_id."""
    content = _read(BOOTSTRAP / "dns_owner_role" / "account.hcl")
    assert '"dns_owner"' in content, (
        "dns_owner_role account.hcl must select the env_accounts.json dns_owner block."
    )
    assert "dns_owner_zone_id" not in content, (
        "account.hcl must not carry dns_owner_zone_id (it is resolved from common/accounts.json "
        "by the units that need it, not the bootstrap account layer)."
    )


# ---------------------------------------------------------------------------
# state-bootstrap conditional local-backend toggle (D-15) - preserved coverage
# ---------------------------------------------------------------------------

STATE_BOOTSTRAP_ROLES = [r for r in ROLES if "state-bootstrap" in ROLE_UNITS[r]]


@pytest.mark.unit
@pytest.mark.parametrize("role", STATE_BOOTSTRAP_ROLES)
def test_state_bootstrap_conditional_local_backend_toggle(role: str) -> None:
    """state-bootstrap keeps the D-15 conditional local-backend override (bootstrap_local_backend ?
    backend.tf : backend_disabled.tf.disabled) so the first apply bootstraps the S3 backend."""
    content = _read(BOOTSTRAP / role / "state-bootstrap" / "000" / "terragrunt.hcl")
    assert "bootstrap_local_backend" in content, (
        f"bootstrap/{role}/state-bootstrap must declare bootstrap_local_backend (D-15 toggle)."
    )
    assert '"backend.tf"' in content and '"backend_disabled.tf.disabled"' in content, (
        f'bootstrap/{role}/state-bootstrap generate "backend" must use the conditional path '
        "(backend.tf when true, backend_disabled.tf.disabled when false)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("role", STATE_BOOTSTRAP_ROLES)
def test_state_bootstrap_names_managed_resources_by_account(role: str) -> None:
    """state-bootstrap names the state CMK/buckets by the resolved account id (<account>-tfstate) so
    the re-key (folder only) does not rename the live state foundation."""
    content = _read(BOOTSTRAP / role / "state-bootstrap" / "000" / "terragrunt.hcl")
    assert "tfstate" in content, (
        f"bootstrap/{role}/state-bootstrap must name the state bucket/CMK with the -tfstate suffix."
    )
