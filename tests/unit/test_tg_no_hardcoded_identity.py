"""Unit tests for scripts/tg_no_hardcoded_identity.py.

Implements the spec section 4.8 per-guard pytest matrix:

Positive case (clean tree):
  - A fixture tree of pure-basename layer files + common/ JSON + terraform.tfvars
    produces zero findings and exits 0.

Negative cases (one per forbidden literal class):
  - 12-digit account id literal -> findings, non-zero exit
  - Region string "us-east-1" -> findings, non-zero exit
  - Region string "useast1" -> findings, non-zero exit
  - Domain apex substring -> findings, non-zero exit
  - CIDR literal (10.N.0.0/16) -> findings, non-zero exit
  - Digit-only config_path index (/000) -> findings, non-zero exit
  - declared_* identity local -> findings, non-zero exit

Allowlist cases:
  - The same forbidden literals placed under common/** produce zero findings.
  - The same forbidden literals placed in a terraform.tfvars produce zero findings.
"""

from __future__ import annotations

import pathlib

import pytest

from scripts.tg_no_hardcoded_identity import (
    Finding,
    HardcodedIdentityError,
    ScannerConfig,
    run_scanner,
    scan_tree,
)

# ---------------------------------------------------------------------------
# Helpers: build minimal fixture trees in tmp_path
# ---------------------------------------------------------------------------

_CLEAN_PRODUCT_HCL = """\
locals {
  product = basename(get_terragrunt_dir())
}
"""

_CLEAN_REGION_HCL = """\
locals {
  aws_region = basename(get_terragrunt_dir())
}
"""

_CLEAN_ACCOUNT_HCL = """\
locals {
  aws_account_id = basename(get_terragrunt_dir())
}
"""

_CLEAN_ENVIRONMENT_HCL = """\
locals {
  environment = basename(get_terragrunt_dir())
}
"""

_CLEAN_ENV_INSTANCE_HCL = """\
locals {
  environment_instance = basename(get_terragrunt_dir())
}
"""

_CLEAN_SERVICE_HCL = """\
locals {
  service = basename(get_terragrunt_dir())
}
"""

_CLEAN_SERVICE_INSTANCE_HCL = """\
locals {
  svc_instance = basename(get_terragrunt_dir())
}
"""

_CLEAN_LEAF_TERRAGRUNT = """\
include "root" {
  path           = find_in_parent_folders("root.hcl")
  expose         = true
  merge_strategy = "deep"
}

terraform {
  source = "${get_repo_root()}//providers/aws/primitives/s3-bucket"
}

inputs = {
  tags = include.root.locals.common_tags
}
"""

_CLEAN_TFVARS = """\
transition_days = 30
expiration_days = 365
"""

# accounts.json content for common/ (legitimate home for account IDs)
_COMMON_ACCOUNTS_JSON = """\
{
  "111111111111": {
    "account_role": "prod-infra",
    "is_dns_owner": false
  }
}
"""

# domains.json content for common/ (legitimate home for domain apex)
_COMMON_DOMAINS_JSON = """\
{
  "prod": {
    "dns_pretty_apex": "telemetry.example.com",
    "dns_service_apex": "prod.telemetry.example.com",
    "enable_custom_domain": true
  }
}
"""

# networks.json content for common/ (legitimate home for CIDRs)
_COMMON_NETWORKS_JSON = """\
{
  "telemetry-useast1-prod-000-collector-000": {
    "vpc_cidr_block": "10.0.0.0/16"
  }
}
"""


def _build_clean_tree(root: pathlib.Path) -> pathlib.Path:
    """Build a minimal clean fixture tree under root/live/.

    Returns the live root path (root/live/).
    """
    live = root / "live"
    leaf = live / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    leaf.mkdir(parents=True)

    # Structural layer files (all basename-derived, no literals)
    (live / "acme").mkdir(exist_ok=True)
    (live / "acme" / "product.hcl").write_text(_CLEAN_PRODUCT_HCL)
    region_dir = live / "acme" / "us-east-1"
    region_dir.mkdir(exist_ok=True)
    (region_dir / "region.hcl").write_text(_CLEAN_REGION_HCL)
    acct_dir = region_dir / "111122223333"
    acct_dir.mkdir(exist_ok=True)
    (acct_dir / "account.hcl").write_text(_CLEAN_ACCOUNT_HCL)
    env_dir = acct_dir / "prod"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "environment.hcl").write_text(_CLEAN_ENVIRONMENT_HCL)
    env_inst_dir = env_dir / "000"
    env_inst_dir.mkdir(exist_ok=True)
    (env_inst_dir / "environment_instance.hcl").write_text(_CLEAN_ENV_INSTANCE_HCL)
    svc_dir = env_inst_dir / "my-svc"
    svc_dir.mkdir(exist_ok=True)
    (svc_dir / "service.hcl").write_text(_CLEAN_SERVICE_HCL)

    # Service instance leaf
    (leaf / "service_instance.hcl").write_text(_CLEAN_SERVICE_INSTANCE_HCL)
    (leaf / "terragrunt.hcl").write_text(_CLEAN_LEAF_TERRAGRUNT)
    (leaf / "terraform.tfvars").write_text(_CLEAN_TFVARS)

    return live


def _default_config(live_root: pathlib.Path) -> ScannerConfig:
    """Return a default ScannerConfig for the given live root."""
    return ScannerConfig(
        live_root=live_root,
        allowlist_patterns=[
            "common/**",
            "**/terraform.tfvars",
        ],
        exempt_account_id_var_names=frozenset(["sandbox_account_id", "prod_account_id"]),
        exempt_config_path_patterns=[
            r"/\d{12}/",
            r"/bootstrap/",
        ],
    )


# ---------------------------------------------------------------------------
# Positive case: clean fixture tree passes with 0 findings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clean_tree_produces_no_findings(tmp_path: pathlib.Path) -> None:
    """A fixture tree of pure-basename derivation produces zero findings."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)
    findings = scan_tree(config)
    assert findings == [], f"Expected zero findings on a clean tree, got: {findings}"


@pytest.mark.unit
def test_run_scanner_exits_zero_on_clean_tree(tmp_path: pathlib.Path) -> None:
    """run_scanner returns 0 (exit code) on a clean tree."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)
    exit_code = run_scanner(config)
    assert exit_code == 0, f"Expected exit 0 on clean tree, got {exit_code}"


# ---------------------------------------------------------------------------
# Negative cases: one per forbidden literal class
# ---------------------------------------------------------------------------

_NEG_PARAMS = [
    pytest.param(
        "account_id_literal",
        'hardcoded_id = "111111111111"\n',
        "terragrunt.hcl",
        id="account_id_12_digits",
    ),
    pytest.param(
        "region_dashed",
        'aws_region = "us-east-1"\n',
        "terragrunt.hcl",
        id="region_dashed",
    ),
    pytest.param(
        "region_collapsed",
        'region_code = "useast1"\n',
        "terragrunt.hcl",
        id="region_collapsed",
    ),
    pytest.param(
        "domain_apex",
        'domain = "telemetry.example.com"\n',
        "terragrunt.hcl",
        id="domain_apex",
    ),
    pytest.param(
        "cidr_literal",
        'vpc_cidr = "10.1.0.0/16"\n',
        "terragrunt.hcl",
        id="cidr_literal",
    ),
    pytest.param(
        "digit_config_path",
        'config_path = "../../other-svc/000"\n',
        "service_instance.hcl",
        id="digit_config_path_index",
    ),
    pytest.param(
        "declared_local",
        'declared_account_id = "111122223333"\n',
        "account.hcl",
        id="declared_identity_local",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("_name,injection,filename", _NEG_PARAMS)
def test_forbidden_literal_produces_findings(
    tmp_path: pathlib.Path,
    _name: str,
    injection: str,
    filename: str,
) -> None:
    """Each forbidden literal class produces at least one finding."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    # Inject forbidden literal into a leaf file
    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    target = leaf / filename
    if target.exists():
        original = target.read_text()
        target.write_text(original + "\n# Injected for test\n" + injection)
    else:
        target.write_text(injection)

    findings = scan_tree(config)
    assert len(findings) > 0, (
        f"Expected at least one finding for injection '{injection.strip()}' "
        f"in {filename}, got zero findings."
    )


@pytest.mark.unit
@pytest.mark.parametrize("_name,injection,filename", _NEG_PARAMS)
def test_forbidden_literal_exits_nonzero(
    tmp_path: pathlib.Path,
    _name: str,
    injection: str,
    filename: str,
) -> None:
    """run_scanner returns non-zero exit code when forbidden literal is present."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    target = leaf / filename
    if target.exists():
        original = target.read_text()
        target.write_text(original + "\n# Injected for test\n" + injection)
    else:
        target.write_text(injection)

    exit_code = run_scanner(config)
    assert exit_code != 0, f"Expected non-zero exit for injection '{injection.strip()}', got 0."


@pytest.mark.unit
@pytest.mark.parametrize("_name,injection,filename", _NEG_PARAMS)
def test_forbidden_literal_finding_has_file_and_line(
    tmp_path: pathlib.Path,
    _name: str,
    injection: str,
    filename: str,
) -> None:
    """Each finding reports the file path and line number."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    target = leaf / filename
    if target.exists():
        original = target.read_text()
        target.write_text(original + "\n# Injected for test\n" + injection)
    else:
        target.write_text(injection)

    findings = scan_tree(config)
    assert len(findings) > 0, "Expected findings"
    finding = findings[0]
    assert isinstance(finding, Finding), f"Finding must be a Finding instance, got {type(finding)}"
    assert finding.file_path is not None, "Finding must have a file_path"
    assert finding.line_number > 0, "Finding must have a positive line_number"
    assert finding.literal is not None and finding.literal != "", (
        "Finding must name the offending literal"
    )
    assert finding.remediation is not None and finding.remediation != "", (
        "Finding must have a remediation"
    )


@pytest.mark.unit
@pytest.mark.parametrize("_name,injection,filename", _NEG_PARAMS)
def test_forbidden_literal_raises_hardcoded_identity_error(
    tmp_path: pathlib.Path,
    _name: str,
    injection: str,
    filename: str,
) -> None:
    """scan_tree raises HardcodedIdentityError when findings exist and raise_on_findings is True."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    target = leaf / filename
    if target.exists():
        original = target.read_text()
        target.write_text(original + "\n# Injected for test\n" + injection)
    else:
        target.write_text(injection)

    with pytest.raises(HardcodedIdentityError):
        scan_tree(config, raise_on_findings=True)


# ---------------------------------------------------------------------------
# Allowlist cases: same literals under common/** and terraform.tfvars pass
# ---------------------------------------------------------------------------

_ALLOWLIST_PARAMS = [
    pytest.param("111111111111", id="account_id_in_common"),
    pytest.param("telemetry.example.com", id="domain_in_common"),
    pytest.param("10.0.0.0/16", id="cidr_in_common"),
]


@pytest.mark.unit
@pytest.mark.parametrize("literal_value", _ALLOWLIST_PARAMS)
def test_forbidden_literal_in_common_is_allowed(
    tmp_path: pathlib.Path,
    literal_value: str,
) -> None:
    """Forbidden literals placed under common/ produce zero findings (allowlisted)."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    # Create a common/ directory OUTSIDE live/ (sibling to live/)
    common_dir = live_root.parent / "common"
    common_dir.mkdir(exist_ok=True)
    (common_dir / "accounts.json").write_text(f'{{"key": "{literal_value}"}}\n')

    # The scanner walks live/**, so common/ is NOT scanned.
    # Verify no findings for the clean tree (common/ is outside live/).
    findings = scan_tree(config)
    assert findings == [], (
        f"Expected zero findings: literal '{literal_value}' in common/ is allowlisted, "
        f"but scanner reported: {findings}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("literal_value", _ALLOWLIST_PARAMS)
def test_forbidden_literal_in_tfvars_is_allowed(
    tmp_path: pathlib.Path,
    literal_value: str,
) -> None:
    """Forbidden literals inside terraform.tfvars produce zero findings (allowlisted)."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    # Inject the literal into the existing terraform.tfvars in the leaf
    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    tfvars = leaf / "terraform.tfvars"
    tfvars.write_text(f'{_CLEAN_TFVARS}\nsome_value = "{literal_value}"\n')

    findings = scan_tree(config)
    assert findings == [], (
        f"Expected zero findings: literal '{literal_value}' in terraform.tfvars is allowlisted, "
        f"but scanner reported: {findings}"
    )


# ---------------------------------------------------------------------------
# Additional allowlist cases: account ID in cross-account config_path is exempt
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_account_id_in_cross_account_config_path_is_exempt(
    tmp_path: pathlib.Path,
) -> None:
    """A 12-digit account ID embedded in a cross-account config_path is exempt."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    si = leaf / "service_instance.hcl"
    # Cross-account path: traverses into another account directory
    cross_account_dep = (
        '\ndependency "remote" {\n'
        '  config_path = "../../../../../999988887777/prod/000/other-svc/000"\n'
        "}\n"
    )
    si.write_text(_CLEAN_SERVICE_INSTANCE_HCL + cross_account_dep)

    findings = scan_tree(config)
    assert findings == [], f"Expected zero findings for cross-account config_path, got: {findings}"


@pytest.mark.unit
def test_account_id_in_bootstrap_config_path_is_exempt(
    tmp_path: pathlib.Path,
) -> None:
    """A config_path into a bootstrap subtree is exempt from the digit-index rule."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    tg = leaf / "terragrunt.hcl"
    original = tg.read_text()
    bootstrap_dep = (
        '\ndependency "state_bootstrap" {\n'
        '  config_path = "../../../../../bootstrap/000/state-bootstrap/000"\n'
        "}\n"
    )
    tg.write_text(original + bootstrap_dep)

    findings = scan_tree(config)
    assert findings == [], f"Expected zero findings for bootstrap config_path, got: {findings}"


# ---------------------------------------------------------------------------
# Mock-outputs block exclusion: domain/account literals inside mock_outputs pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_literals_in_mock_outputs_are_not_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """Forbidden literals inside a mock_outputs block are not flagged."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    si = leaf / "service_instance.hcl"
    si.write_text(
        _CLEAN_SERVICE_INSTANCE_HCL
        + """
dependency "acm" {
  config_path = "../../acm-svc/${local.svc_instance}"

  mock_outputs_allowed_terraform_commands = ["validate", "plan"]
  mock_outputs = {
    certificate_arn = "arn:aws:acm:us-east-1:111111111111:certificate/mock-cert"
    domain_name     = "telemetry.example.com"
    cidr_block      = "10.5.0.0/16"
  }
}
"""
    )

    findings = scan_tree(config)
    assert findings == [], (
        f"Expected zero findings for literals inside mock_outputs, got: {findings}"
    )


# ---------------------------------------------------------------------------
# Error path: live_root does not exist
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_tree_raises_for_nonexistent_root(tmp_path: pathlib.Path) -> None:
    """scan_tree raises ValueError when the live_root does not exist."""
    nonexistent = tmp_path / "does_not_exist"
    config = ScannerConfig(
        live_root=nonexistent,
        allowlist_patterns=["common/**", "**/terraform.tfvars"],
        exempt_account_id_var_names=frozenset(),
        exempt_config_path_patterns=[],
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        scan_tree(config)


# ---------------------------------------------------------------------------
# Error path: live_root is a file, not a directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_tree_raises_for_file_as_root(tmp_path: pathlib.Path) -> None:
    """scan_tree raises ValueError when the live_root is a file, not a directory."""
    a_file = tmp_path / "somefile.hcl"
    a_file.write_text("locals {}\n")
    config = ScannerConfig(
        live_root=a_file,
        allowlist_patterns=["common/**", "**/terraform.tfvars"],
        exempt_account_id_var_names=frozenset(),
        exempt_config_path_patterns=[],
    )
    with pytest.raises(ValueError, match="not a directory"):
        scan_tree(config)


# ---------------------------------------------------------------------------
# Allowlist: full-path match and common/** prefix match
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_file_in_common_subdir_is_allowlisted(tmp_path: pathlib.Path) -> None:
    """Files under common/ (sibling of live/) are never scanned by the walker."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    # Place a dirty HCL under a 'common' dir at the same level as 'live'
    common = live_root.parent / "common"
    common.mkdir(exist_ok=True)
    (common / "accounts.json").write_text('{"key": "111111111111"}\n')

    # Scanner walks live/ only; common/ is outside live/ so it is never scanned.
    findings = scan_tree(config)
    assert findings == [], f"Expected zero findings: common/ is outside live root, got: {findings}"


@pytest.mark.unit
def test_fnmatch_allowlist_full_path(tmp_path: pathlib.Path) -> None:
    """Allowlist pattern matched on full path (not just basename) is respected."""
    live_root = _build_clean_tree(tmp_path)
    # Add a forbidden literal inside a file whose name matches "**/terraform.tfvars"
    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    (leaf / "terraform.tfvars").write_text('account_id = "111111111111"\n')

    config = _default_config(live_root)
    findings = scan_tree(config)
    assert findings == [], (
        f"Expected zero findings: terraform.tfvars is allowlisted, got: {findings}"
    )


# ---------------------------------------------------------------------------
# run_scanner error path: OSError from scan_tree
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_scanner_returns_1_on_invalid_root(tmp_path: pathlib.Path) -> None:
    """run_scanner returns 1 when the live_root does not exist."""
    nonexistent = tmp_path / "nope"
    config = ScannerConfig(
        live_root=nonexistent,
        allowlist_patterns=["common/**", "**/terraform.tfvars"],
        exempt_account_id_var_names=frozenset(),
        exempt_config_path_patterns=[],
    )
    exit_code = run_scanner(config)
    assert exit_code == 1, f"Expected exit 1 for nonexistent root, got {exit_code}"


# ---------------------------------------------------------------------------
# Exempt account_id variable names: sandbox_account_id / prod_account_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "var_name",
    [
        pytest.param("sandbox_account_id", id="sandbox_account_id"),
        pytest.param("prod_account_id", id="prod_account_id"),
    ],
)
def test_exempt_account_id_var_names_are_not_flagged(
    tmp_path: pathlib.Path,
    var_name: str,
) -> None:
    """State-bootstrap account-id variable names are exempt from the account-id rule."""
    live_root = _build_clean_tree(tmp_path)
    config = _default_config(live_root)

    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    (leaf / "terragrunt.hcl").write_text(
        _CLEAN_LEAF_TERRAGRUNT + f'\nlocals {{\n  {var_name} = "111122223333"\n}}\n'
    )

    findings = scan_tree(config)
    assert findings == [], (
        f"Expected zero findings for exempt var name '{var_name}', got: {findings}"
    )


# ---------------------------------------------------------------------------
# CLI: _parse_args and _resolve_live_root
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_args_uses_root_argument(tmp_path: pathlib.Path) -> None:
    """_parse_args correctly parses --root argument."""
    from scripts.tg_no_hardcoded_identity import _parse_args

    args = _parse_args(["--root", str(tmp_path)])
    assert args.root == str(tmp_path)


@pytest.mark.unit
def test_parse_args_root_defaults_to_none() -> None:
    """_parse_args defaults root to None when --root is not given."""
    from scripts.tg_no_hardcoded_identity import _parse_args

    args = _parse_args([])
    assert args.root is None


@pytest.mark.unit
def test_resolve_live_root_uses_explicit_arg(tmp_path: pathlib.Path) -> None:
    """_resolve_live_root returns Path from explicit --root argument."""
    from scripts.tg_no_hardcoded_identity import _resolve_live_root

    result = _resolve_live_root(str(tmp_path))
    assert result == tmp_path


@pytest.mark.unit
def test_resolve_live_root_uses_env_variable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_live_root uses TG_LIVE_ROOT environment variable when --root is None."""
    from scripts.tg_no_hardcoded_identity import _resolve_live_root

    monkeypatch.setenv("TG_LIVE_ROOT", str(tmp_path))
    result = _resolve_live_root(None)
    assert result == tmp_path


@pytest.mark.unit
def test_resolve_live_root_falls_back_to_repo_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_live_root falls back to repo-root/terragrunt/live when no arg or env."""
    import os

    from scripts.tg_no_hardcoded_identity import _resolve_live_root

    old_env = os.environ.pop("TG_LIVE_ROOT", None)
    try:
        result = _resolve_live_root(None)
        # The real live tree exists in this repo
        assert result.name == "live"
    finally:
        if old_env is not None:
            os.environ["TG_LIVE_ROOT"] = old_env


@pytest.mark.unit
def test_resolve_live_root_raises_when_no_root_determinable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_live_root raises ValueError when root cannot be determined."""
    import scripts.tg_no_hardcoded_identity as mod
    from scripts.tg_no_hardcoded_identity import _resolve_live_root

    monkeypatch.delenv("TG_LIVE_ROOT", raising=False)
    # Patch __file__ so the default candidate path points to a tmp dir without live/
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "fake_script.py"))
    with pytest.raises(ValueError, match="Cannot determine"):
        _resolve_live_root(None)


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_zero_for_clean_tree(tmp_path: pathlib.Path) -> None:
    """main() returns 0 when --root points to a clean tree."""
    from scripts.tg_no_hardcoded_identity import main

    live_root = _build_clean_tree(tmp_path)
    exit_code = main(["--root", str(live_root)])
    assert exit_code == 0, f"Expected exit 0 for clean tree, got {exit_code}"


@pytest.mark.unit
def test_main_returns_one_for_dirty_tree(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when --root points to a tree with violations."""
    from scripts.tg_no_hardcoded_identity import main

    live_root = _build_clean_tree(tmp_path)
    leaf = live_root / "acme" / "us-east-1" / "111122223333" / "prod" / "000" / "my-svc" / "000"
    (leaf / "terragrunt.hcl").write_text(
        _CLEAN_LEAF_TERRAGRUNT + '\nlocals {\n  bad = "111111111111"\n}\n'
    )
    exit_code = main(["--root", str(live_root)])
    assert exit_code == 1, f"Expected exit 1 for dirty tree, got {exit_code}"


@pytest.mark.unit
def test_main_returns_one_for_nonexistent_root(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when --root does not exist."""
    from scripts.tg_no_hardcoded_identity import main

    exit_code = main(["--root", str(tmp_path / "nope")])
    assert exit_code == 1, f"Expected exit 1 for nonexistent root, got {exit_code}"


@pytest.mark.unit
def test_main_returns_one_when_live_root_undeterminable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when --root is omitted and no live root can be resolved."""
    import scripts.tg_no_hardcoded_identity as mod
    from scripts.tg_no_hardcoded_identity import main

    monkeypatch.delenv("TG_LIVE_ROOT", raising=False)
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "fake_script.py"))
    exit_code = main([])
    assert exit_code == 1, f"Expected exit 1 when live root undeterminable, got {exit_code}"


# ---------------------------------------------------------------------------
# build_default_config produces a config that passes on the real live tree
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_default_config_returns_scanner_config(tmp_path: pathlib.Path) -> None:
    """build_default_config returns a valid ScannerConfig."""
    from scripts.tg_no_hardcoded_identity import build_default_config

    config = build_default_config(tmp_path)
    assert isinstance(config, ScannerConfig)
    assert config.live_root == tmp_path
    assert "common/**" in config.allowlist_patterns
    assert "**/terraform.tfvars" in config.allowlist_patterns
    assert "sandbox_account_id" in config.exempt_account_id_var_names
    assert "prod_account_id" in config.exempt_account_id_var_names


# ---------------------------------------------------------------------------
# Live-tree integration: make tg-no-hardcoded-identity passes on current tree
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_real_live_tree_is_clean() -> None:
    """The committed live tree produces zero findings (AC-FUNC-002)."""
    import os

    repo_root = pathlib.Path(__file__).parent.parent.parent
    live_root = repo_root / "terragrunt" / "live"

    if not live_root.exists():
        pytest.skip(f"Live tree not found at {live_root}")

    env_root = os.environ.get("TG_LIVE_ROOT", "")
    if env_root:
        live_root = pathlib.Path(env_root)

    config = ScannerConfig(
        live_root=live_root,
        allowlist_patterns=[
            "common/**",
            "**/terraform.tfvars",
        ],
        exempt_account_id_var_names=frozenset(["sandbox_account_id", "prod_account_id"]),
        exempt_config_path_patterns=[
            r"/\d{12}/",
            r"/bootstrap/",
        ],
    )
    findings = scan_tree(config)
    assert findings == [], f"Live tree has {len(findings)} finding(s):\n" + "\n".join(
        f"  {f.file_path}:{f.line_number}: {f.literal}" for f in findings
    )
