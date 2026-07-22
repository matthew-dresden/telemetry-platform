"""Unit regression tests for the terragrunt/_envcommon shared input templates.

These tests assert the structural and content constraints for the _envcommon
templates that centralize DRY inputs for every sandbox and prod service unit.
They validate:

- AC-3 / AC-18: all required templates exist (no missing files).
- D37: canonical input names are used (prod_hosted_zone_id, collector_service_fqdn,
  collector_pretty_fqdn); no synonyms.
- D44: collector-ingestion.hcl pins concrete abuse-limit defaults with no TODO placeholder:
  max_request_body_size = 4194304 (bytes) and rate_limit_per_ip = 2000 (req/5min).
- D46: no state-bootstrap.hcl template is authored (state-bootstrap leaves pass
  lock-table inputs inline, with no include "envcommon").
- D47: no hardcoded prod domain literals in any template (domain values composed
  from per-env account.hcl apexes).
- D4: no assume_role in any _envcommon template.
- AC-18: oidc-bootstrap.hcl passes github_oidc_provider_arn and roles inputs.
- Security: no hardcoded AWS access keys, no em-dash characters.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from scripts.tg_regression import (
    ENVCOMMON_DIR,
    REPO_ROOT,
    REQUIRED_TEMPLATES,
    assert_abuse_limit_defaults,
    assert_canonical_input_names,
    assert_no_assume_role,
    assert_no_forbidden_templates,
    assert_no_hardcoded_prod_domains,
    assert_no_synonym_inputs,
    assert_no_todo_placeholders,
    assert_required_templates_exist,
    run_assertions,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

ENVCOMMON_PATH = REPO_ROOT / "terragrunt" / "_envcommon"

TEMPLATE_NAMES = [
    "collector-ingestion.hcl",
    "data-lake.hcl",
    "observability.hcl",
    "dns-prod-zone.hcl",
    "acm-collector.hcl",
    "identity.hcl",
    "oidc-bootstrap.hcl",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _template_content(template_name: str) -> str:
    """Read a template file and return its content. Fails if not found."""
    path = ENVCOMMON_PATH / template_name
    assert path.exists(), (
        f"Required _envcommon template not found: {path.relative_to(REPO_ROOT)}. "
        f"Author it as part of E5-F1-S2-T2."
    )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# File existence tests (AC-3, AC-18)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_envcommon_template_exists(template_name: str) -> None:
    """Each required _envcommon template must exist (E5-F1-S2-T2 manifest)."""
    path = ENVCOMMON_PATH / template_name
    assert path.exists(), (
        f"Required _envcommon template missing: terragrunt/_envcommon/{template_name}. "
        f"These templates are required by the E5-F1-S2-T2 Changes Manifest."
    )


@pytest.mark.unit
def test_no_state_bootstrap_template() -> None:
    """No state-bootstrap.hcl template may exist (D46).

    State-bootstrap leaves are bootstrap-only with no include 'envcommon'.
    They pass hash_key='LockID' and attributes=[{name='LockID',type='S'}] inline.
    A shared state-bootstrap.hcl would be an orphaned, never-included file.
    """
    forbidden = ENVCOMMON_PATH / "state-bootstrap.hcl"
    assert not forbidden.exists(), (
        "terragrunt/_envcommon/state-bootstrap.hcl must NOT be authored. "
        "D46: state-bootstrap leaves pass lock-table inputs (hash_key=LockID, "
        "attributes=[{name=LockID,type=S}]) inline with no include 'envcommon'. "
        "Remove this file."
    )


# ---------------------------------------------------------------------------
# D37: canonical input names in template-specific files
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_prod_zone_uses_prod_hosted_zone_id() -> None:
    """dns-prod-zone.hcl must pass prod_hosted_zone_id (canonical name, D37)."""
    content = _template_content("dns-prod-zone.hcl")
    assert "prod_hosted_zone_id" in content, (
        "terragrunt/_envcommon/dns-prod-zone.hcl does not contain 'prod_hosted_zone_id'. "
        "D37: Use the canonical input name 'prod_hosted_zone_id', not a synonym."
    )


@pytest.mark.unit
def test_acm_collector_uses_collector_service_fqdn() -> None:
    """acm-collector.hcl must pass collector_service_fqdn (canonical name, D37)."""
    content = _template_content("acm-collector.hcl")
    assert "collector_service_fqdn" in content, (
        "terragrunt/_envcommon/acm-collector.hcl does not contain 'collector_service_fqdn'. "
        "D37: Use the canonical input name 'collector_service_fqdn', not a synonym."
    )


@pytest.mark.unit
def test_acm_collector_uses_collector_pretty_fqdn() -> None:
    """acm-collector.hcl must pass collector_pretty_fqdn (canonical name, D37)."""
    content = _template_content("acm-collector.hcl")
    assert "collector_pretty_fqdn" in content, (
        "terragrunt/_envcommon/acm-collector.hcl does not contain 'collector_pretty_fqdn'. "
        "D37: Use the canonical input name 'collector_pretty_fqdn', not a synonym."
    )


# ---------------------------------------------------------------------------
# D44: collector-ingestion.hcl concrete abuse-limit defaults (no TODO)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collector_ingestion_has_max_request_body_size_default() -> None:
    """collector-ingestion.hcl must pin max_request_body_size = 4194304 (D44)."""
    content = _template_content("collector-ingestion.hcl")
    assert re.search(r"max_request_body_size\s*=\s*4194304", content), (
        "terragrunt/_envcommon/collector-ingestion.hcl does not pin "
        "max_request_body_size = 4194304. "
        "D44: The ADOT OTLP receiver max_request_body_size must default to 4194304 bytes "
        "as a concrete, input-driven value with no TODO placeholder."
    )


@pytest.mark.unit
def test_collector_ingestion_has_rate_limit_per_ip_default() -> None:
    """collector-ingestion.hcl must pin rate_limit_per_ip = 2000 (D44)."""
    content = _template_content("collector-ingestion.hcl")
    assert re.search(r"rate_limit_per_ip\s*=\s*2000", content), (
        "terragrunt/_envcommon/collector-ingestion.hcl does not pin "
        "rate_limit_per_ip = 2000. "
        "D44: The WAF rate_limit_per_ip must default to 2000 requests per 5 min "
        "as a concrete, input-driven value with no TODO placeholder."
    )


@pytest.mark.unit
def test_collector_ingestion_has_no_todo_placeholder() -> None:
    """collector-ingestion.hcl must contain no TODO/placeholder tokens (D44)."""
    content = _template_content("collector-ingestion.hcl")
    todo_pattern = re.compile(r"\b(TODO|PLACEHOLDER|FIXME|XXX|TBD)\b", re.IGNORECASE)
    match = todo_pattern.search(content)
    assert not match, (
        "terragrunt/_envcommon/collector-ingestion.hcl contains a TODO/placeholder token: "
        f"'{match.group()}'. "
        "D44: All abuse-limit defaults must be concrete, input-driven values. "
        "Remove all TODO/PLACEHOLDER/FIXME/XXX/TBD tokens."
    )


# ---------------------------------------------------------------------------
# D47: no hardcoded prod domain literals in any template
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_no_hardcoded_prod_domain_in_template(template_name: str) -> None:
    """No template may contain a hardcoded prod domain literal (D47).

    Domain values must be composed from per-env account.hcl apexes
    (dns_service_apex, dns_pretty_apex), not hardcoded as fixed prod strings.
    Comment lines (starting with #) are exempted.
    """
    path = ENVCOMMON_PATH / template_name
    if not path.exists():
        pytest.skip(f"{template_name} does not exist yet -- existence tests cover this.")
    content = path.read_text(encoding="utf-8")
    prod_domain_patterns = [
        re.compile(r"prod\.telemetry\.example\.com"),
        re.compile(r"telemetry\.example\.com"),
    ]
    for i, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # comment lines documenting the resolved prod value are acceptable
        for pattern in prod_domain_patterns:
            assert not pattern.search(stripped), (
                f"terragrunt/_envcommon/{template_name} line {i} contains a hardcoded "
                f"prod domain literal: '{stripped}'. "
                "D47: Compose domain values from account.hcl apexes (dns_service_apex, "
                "dns_pretty_apex) via local.account_vars.locals, not prod literals."
            )


# ---------------------------------------------------------------------------
# D4: no assume_role in any _envcommon template
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_no_assume_role_in_template(template_name: str) -> None:
    """No _envcommon template may contain assume_role (D4).

    The OIDC-assumed role IS the deploy identity. No second role assumption is needed
    in any shared input template.
    """
    path = ENVCOMMON_PATH / template_name
    if not path.exists():
        pytest.skip(f"{template_name} does not exist yet -- existence tests cover this.")
    content = path.read_text(encoding="utf-8")
    assert "assume_role" not in content, (
        f"terragrunt/_envcommon/{template_name} contains 'assume_role'. "
        "D4: No second assume_role is allowed in any Terragrunt HCL file. "
        "The OIDC-assumed role is the deploy identity."
    )


# ---------------------------------------------------------------------------
# AC-18: oidc-bootstrap.hcl passes github_oidc_provider_arn and roles inputs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oidc_bootstrap_passes_github_oidc_provider_arn() -> None:
    """oidc-bootstrap.hcl must pass github_oidc_provider_arn as an input (D40)."""
    content = _template_content("oidc-bootstrap.hcl")
    assert "github_oidc_provider_arn" in content, (
        "terragrunt/_envcommon/oidc-bootstrap.hcl does not contain 'github_oidc_provider_arn'. "
        "D40: The shared oidc-bootstrap template must pass github_oidc_provider_arn so the "
        "prod oidc-bootstrap leaf can include 'envcommon' (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_oidc_bootstrap_passes_roles_map() -> None:
    """oidc-bootstrap.hcl must pass the roles map input (D40)."""
    content = _template_content("oidc-bootstrap.hcl")
    assert "roles" in content, (
        "terragrunt/_envcommon/oidc-bootstrap.hcl does not contain 'roles'. "
        "D40: The shared oidc-bootstrap template must pass the per-account roles map input "
        "so the prod oidc-bootstrap leaf can include 'envcommon' (docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# D47: templates read per-env domain values from account.hcl
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collector_ingestion_reads_account_vars() -> None:
    """collector-ingestion.hcl must read account.hcl for per-env values (D45/D47).

    acm-collector.hcl migrated away from account_vars for apex reads
    in E7-F3-S1-T2: that template now resolves dns_service_apex / dns_pretty_apex via
    include.root.locals (root terragrunt.hcl lines 99-102, expose=true) because
    account.hcl no longer declares those locals (removed in E8-F2-S1-T1). Only
    collector-ingestion.hcl still reads account.hcl (for vpc_cidr_block and other
    account-level values that remain in account.hcl).
    """
    content = _template_content("collector-ingestion.hcl")
    assert "account_vars" in content, (
        "terragrunt/_envcommon/collector-ingestion.hcl does not read account_vars. "
        "D45/D47: account-level values (e.g., vpc_cidr_block) must be read from account.hcl "
        "via: account_vars = read_terragrunt_config(find_in_parent_folders('account.hcl'))"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "template_name",
    ["acm-collector.hcl"],
)
def test_acm_template_reads_apexes_via_common_domains_json(template_name: str) -> None:
    """acm-collector.hcl must resolve apexes from common/domains.json
    via local.domain_cfg["dns_*_apex"], NOT from a standalone root.hcl decode or
    include.root.locals.* (D45/D47).

    _envcommon shared templates have no include block in scope, so include.root.locals.* is
    invalid (terragrunt render: "There is no variable named include"). Reading the root
    config standalone via read_terragrunt_config(find_in_parent_folders("root.hcl")) ALSO
    aborts parse: terragrunt 1.0.7 evaluates root.hcl's own find_in_parent_folders(
    "product.hcl"|...) hierarchy reads relative to terragrunt/ (root.hcl's directory),
    raising ParentFileNotFoundError on every leaf that includes an _envcommon
    (proven, commit 1e37f6e).

    The correct, copy-any-level pattern resolves the per-env apex from common/domains.json
    keyed by the leaf's env-class (the same source root.hcl itself uses, root.hcl:54,100),
    anchored on get_repo_root() (D3):
      environment_vars = read_terragrunt_config(find_in_parent_folders("environment.hcl"))
      domains          = jsondecode(file("${get_repo_root()}/terragrunt/common/domains.json"))
      domain_cfg       = local.domains[local.environment_name]   # fail-fast guarded
      service_apex     = local.domain_cfg["dns_service_apex"]
      pretty_apex      = local.domain_cfg["dns_pretty_apex"]

    account.hcl no longer declares these apex locals (removed in E8-F2-S1-T1).
    """
    content = _template_content(template_name)
    assert "read_terragrunt_config(find_in_parent_folders(" in content, (
        f"terragrunt/_envcommon/{template_name} does not call read_terragrunt_config. "
        "D45/D47: _envcommon templates resolve the leaf env-class via "
        'read_terragrunt_config(find_in_parent_folders("environment.hcl")) '
        "and look the apexes up in common/domains.json. The standalone root.hcl decode is "
        "forbidden (it aborts parse with ParentFileNotFoundError)."
    )
    assert "${get_repo_root()}/terragrunt/common/domains.json" in content, (
        f"terragrunt/_envcommon/{template_name} does not read common/domains.json "
        "(anchored on get_repo_root(), D3). "
        "D45/D47: the per-env apex must come from common/domains.json keyed by env-class, "
        "the same source root.hcl uses, not from a standalone root.hcl decode."
    )
    assert 'local.domain_cfg["dns_service_apex"]' in content, (
        f"terragrunt/_envcommon/{template_name} does not read dns_service_apex via "
        'local.domain_cfg["dns_service_apex"]. '
        "D45/D47: resolve service_apex from the common/domains.json env-class config."
    )
    assert 'local.domain_cfg["dns_pretty_apex"]' in content, (
        f"terragrunt/_envcommon/{template_name} does not read dns_pretty_apex via "
        'local.domain_cfg["dns_pretty_apex"]. '
        "D45/D47: resolve pretty_apex from the common/domains.json env-class config."
    )
    assert "local.root.locals.dns_service_apex" not in content, (
        f"terragrunt/_envcommon/{template_name} contains local.root.locals.dns_service_apex. "
        "FATAL: that requires decoding root.hcl standalone, which aborts terragrunt 1.0.7 "
        "parse with ParentFileNotFoundError (commit 1e37f6e). "
        'Replace with local.domain_cfg["dns_service_apex"] (common/domains.json).'
    )
    assert "local.root.locals.dns_pretty_apex" not in content, (
        f"terragrunt/_envcommon/{template_name} contains local.root.locals.dns_pretty_apex. "
        "FATAL: standalone root.hcl decode aborts parse (ParentFileNotFoundError). "
        'Replace with local.domain_cfg["dns_pretty_apex"] (common/domains.json).'
    )
    assert "include.root.locals.dns_service_apex" not in content, (
        f"terragrunt/_envcommon/{template_name} contains include.root.locals.dns_service_apex. "
        "FATAL: include.root.locals.* is invalid inside _envcommon (no include block in scope). "
        "terragrunt render fails with 'There is no variable named include'. "
        'Replace with local.domain_cfg["dns_service_apex"] (common/domains.json).'
    )
    assert "include.root.locals.dns_pretty_apex" not in content, (
        f"terragrunt/_envcommon/{template_name} contains include.root.locals.dns_pretty_apex. "
        "FATAL: include.root.locals.* is invalid inside _envcommon (no include block in scope). "
        "terragrunt render fails with 'There is no variable named include'. "
        'Replace with local.domain_cfg["dns_pretty_apex"] (common/domains.json).'
    )
    assert "account_vars.locals.dns_service_apex" not in content, (
        f"terragrunt/_envcommon/{template_name} still reads dns_service_apex from account_vars. "
        "D45: account.hcl no longer declares dns_service_apex. "
        'Replace with local.domain_cfg["dns_service_apex"] (common/domains.json).'
    )
    assert "account_vars.locals.dns_pretty_apex" not in content, (
        f"terragrunt/_envcommon/{template_name} still reads dns_pretty_apex from account_vars. "
        "D45: account.hcl no longer declares dns_pretty_apex. "
        'Replace with local.domain_cfg["dns_pretty_apex"] (common/domains.json).'
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "template_name",
    ["acm-collector.hcl", "collector-ingestion.hcl"],
)
def test_template_uses_dns_service_apex_or_pretty_apex(template_name: str) -> None:
    """Domain-composing templates must reference dns_service_apex or dns_pretty_apex (D47)."""
    content = _template_content(template_name)
    assert "dns_service_apex" in content or "dns_pretty_apex" in content, (
        f"terragrunt/_envcommon/{template_name} does not reference dns_service_apex "
        "or dns_pretty_apex. "
        "D47: FQDNs must be composed from per-env apexes "
        "(dns_service_apex, dns_pretty_apex), not hardcoded prod literals."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded AWS access keys, no em-dash
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_no_hardcoded_aws_access_key(template_name: str) -> None:
    """No _envcommon template may contain a hardcoded AWS access key pattern."""
    path = ENVCOMMON_PATH / template_name
    if not path.exists():
        pytest.skip(f"{template_name} does not exist yet -- existence tests cover this.")
    content = path.read_text(encoding="utf-8")
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"terragrunt/_envcommon/{template_name} contains a pattern matching an AWS access key. "
        "No credentials may be hardcoded in HCL files."
    )


@pytest.mark.unit
@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_no_em_dash_in_template(template_name: str) -> None:
    """No _envcommon template may contain an em-dash character (U+2014)."""
    path = ENVCOMMON_PATH / template_name
    if not path.exists():
        pytest.skip(f"{template_name} does not exist yet -- existence tests cover this.")
    content = path.read_text(encoding="utf-8")
    assert "\u2014" not in content, (
        f"terragrunt/_envcommon/{template_name} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# tg_regression module unit tests (cover the script logic for py-test coverage)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_required_templates_exist_returns_errors_for_missing_dir(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assert_required_templates_exist returns errors when templates are missing."""
    import scripts.tg_regression as mod

    empty_dir = tmp_path / "envcommon"
    empty_dir.mkdir()
    monkeypatch.setattr(mod, "ENVCOMMON_DIR", empty_dir)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    errors = assert_required_templates_exist()
    assert len(errors) == len(REQUIRED_TEMPLATES), (
        f"Expected {len(REQUIRED_TEMPLATES)} errors for missing templates, got {len(errors)}."
    )
    for error in errors:
        assert "ERROR:" in error


@pytest.mark.unit
def test_assert_no_forbidden_templates_returns_error_for_state_bootstrap(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assert_no_forbidden_templates returns an error when state-bootstrap.hcl exists."""
    import scripts.tg_regression as mod

    state_bootstrap = tmp_path / "state-bootstrap.hcl"
    state_bootstrap.write_text("# forbidden\n")
    monkeypatch.setattr(mod, "ENVCOMMON_DIR", tmp_path)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path.parent)
    errors = assert_no_forbidden_templates()
    assert len(errors) == 1
    assert "state-bootstrap" in errors[0]
    assert "D46" in errors[0]


@pytest.mark.unit
def test_assert_no_forbidden_templates_passes_when_no_state_bootstrap(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assert_no_forbidden_templates returns no errors when state-bootstrap.hcl is absent."""
    import scripts.tg_regression as mod

    monkeypatch.setattr(mod, "ENVCOMMON_DIR", tmp_path)
    errors = assert_no_forbidden_templates()
    assert errors == []


@pytest.mark.unit
def test_assert_canonical_input_names_detects_missing_name() -> None:
    """assert_canonical_input_names returns an error when the canonical name is absent."""
    errors = assert_canonical_input_names("dns-prod-zone.hcl", "inputs = {\n  zone_id = x\n}")
    assert any("prod_hosted_zone_id" in e for e in errors)


@pytest.mark.unit
def test_assert_canonical_input_names_passes_when_name_present() -> None:
    """assert_canonical_input_names returns no errors when the canonical name is present."""
    errors = assert_canonical_input_names(
        "dns-prod-zone.hcl", "inputs = {\n  prod_hosted_zone_id = x\n}"
    )
    assert errors == []


@pytest.mark.unit
def test_assert_canonical_input_names_passes_for_non_checked_template() -> None:
    """assert_canonical_input_names returns no errors for templates with no canonical checks."""
    errors = assert_canonical_input_names("observability.hcl", "inputs = {}")
    assert errors == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "synonym_line,description",
    [
        ("  hosted_zone_id = x\n", "hosted_zone_id without prod_ prefix"),
        ("  service_fqdn = x\n", "ambiguous service_fqdn"),
        ("  pretty_fqdn = x\n", "ambiguous pretty_fqdn"),
        ("  collector_fqdn = x\n", "collector_fqdn shorthand"),
        ("  portal_fqdn = x\n", "portal_fqdn shorthand"),
    ],
)
def test_assert_no_synonym_inputs_detects_synonym(synonym_line: str, description: str) -> None:
    """assert_no_synonym_inputs detects each known synonym pattern."""
    errors = assert_no_synonym_inputs("test.hcl", synonym_line)
    assert len(errors) >= 1, f"Expected synonym detection for: {description}"


@pytest.mark.unit
def test_assert_no_synonym_inputs_passes_for_canonical_names() -> None:
    """assert_no_synonym_inputs returns no errors for canonical input names."""
    content = (
        "inputs = {\n"
        "  prod_hosted_zone_id   = x\n"
        "  collector_service_fqdn = x\n"
        "  collector_pretty_fqdn  = x\n"
        "  portal_service_fqdn    = x\n"
        "  portal_pretty_fqdn     = x\n"
        "}\n"
    )
    errors = assert_no_synonym_inputs("test.hcl", content)
    assert errors == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "placeholder",
    ["TODO", "PLACEHOLDER", "FIXME", "XXX", "TBD"],
)
def test_assert_no_todo_placeholders_detects_tokens(placeholder: str) -> None:
    """assert_no_todo_placeholders detects each forbidden placeholder token."""
    content = f"# {placeholder}: fill in later\n"
    errors = assert_no_todo_placeholders("collector-ingestion.hcl", content)
    assert len(errors) >= 1, f"Expected TODO detection for token: {placeholder}"


@pytest.mark.unit
def test_assert_no_todo_placeholders_passes_for_clean_content() -> None:
    """assert_no_todo_placeholders returns no errors for content without placeholders."""
    content = "inputs = {\n  max_request_body_size = 4194304\n}\n"
    errors = assert_no_todo_placeholders("collector-ingestion.hcl", content)
    assert errors == []


@pytest.mark.unit
def test_assert_abuse_limit_defaults_detects_missing_max_request_body_size() -> None:
    """assert_abuse_limit_defaults returns an error when max_request_body_size is absent."""
    content = "inputs = {\n  rate_limit_per_ip = 2000\n}\n"
    errors = assert_abuse_limit_defaults(content)
    assert any("max_request_body_size" in e for e in errors)


@pytest.mark.unit
def test_assert_abuse_limit_defaults_detects_missing_rate_limit_per_ip() -> None:
    """assert_abuse_limit_defaults returns an error when rate_limit_per_ip is absent."""
    content = "inputs = {\n  max_request_body_size = 4194304\n}\n"
    errors = assert_abuse_limit_defaults(content)
    assert any("rate_limit_per_ip" in e for e in errors)


@pytest.mark.unit
def test_assert_abuse_limit_defaults_passes_when_both_present() -> None:
    """assert_abuse_limit_defaults returns no errors when both concrete defaults are present."""
    content = "inputs = {\n  max_request_body_size = 4194304\n  rate_limit_per_ip = 2000\n}\n"
    errors = assert_abuse_limit_defaults(content)
    assert errors == []


@pytest.mark.unit
def test_assert_no_assume_role_detects_assume_role() -> None:
    """assert_no_assume_role returns an error when assume_role appears in content."""
    content = "  assume_role {\n    role_arn = x\n  }\n"
    errors = assert_no_assume_role("test.hcl", content)
    assert len(errors) == 1
    assert "D4" in errors[0]


@pytest.mark.unit
def test_assert_no_assume_role_passes_for_clean_content() -> None:
    """assert_no_assume_role returns no errors when assume_role is absent."""
    content = "inputs = {\n  region = local.region\n}\n"
    errors = assert_no_assume_role("test.hcl", content)
    assert errors == []


@pytest.mark.unit
def test_assert_no_hardcoded_prod_domains_detects_prod_literal() -> None:
    """assert_no_hardcoded_prod_domains detects hardcoded prod domain literals."""
    content = '  collector_service_fqdn = "collector.prod.telemetry.example.com"\n'
    errors = assert_no_hardcoded_prod_domains("test.hcl", content)
    assert len(errors) >= 1
    assert "D47" in errors[0]


@pytest.mark.unit
def test_assert_no_hardcoded_prod_domains_ignores_comment_lines() -> None:
    """assert_no_hardcoded_prod_domains ignores comment lines with prod domain literals."""
    content = (
        "# prod -> collector.prod.telemetry.example.com\n"
        '  collector_service_fqdn = "collector.${local.service_apex}"\n'
    )
    errors = assert_no_hardcoded_prod_domains("test.hcl", content)
    assert errors == []


@pytest.mark.unit
def test_assert_no_hardcoded_prod_domains_passes_for_apex_composition() -> None:
    """assert_no_hardcoded_prod_domains passes when FQDNs are composed from apexes."""
    content = '  collector_service_fqdn = "collector.${local.service_apex}"\n'
    errors = assert_no_hardcoded_prod_domains("test.hcl", content)
    assert errors == []


@pytest.mark.unit
def test_run_assertions_returns_errors_when_templates_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """run_assertions returns errors when _envcommon templates are missing."""
    import scripts.tg_regression as mod

    empty_dir = tmp_path / "envcommon"
    empty_dir.mkdir()
    monkeypatch.setattr(mod, "ENVCOMMON_DIR", empty_dir)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    errors = run_assertions()
    assert len(errors) > 0


@pytest.mark.unit
def test_envcommon_dir_constant_points_to_correct_path() -> None:
    """ENVCOMMON_DIR constant in tg_regression must point to terragrunt/_envcommon."""
    assert ENVCOMMON_DIR == REPO_ROOT / "terragrunt" / "_envcommon", (
        f"ENVCOMMON_DIR is {ENVCOMMON_DIR}, expected {REPO_ROOT / 'terragrunt' / '_envcommon'}. "
        "The constant must point to the correct directory."
    )


@pytest.mark.unit
def test_collector_ingestion_vpc_cidr_reads_from_common_networks_json() -> None:
    """collector-ingestion.hcl vpc_cidr_block must be sourced from common/networks.json.

    A hardcoded CIDR block (e.g., '10.0.0.0/16') is environment-specific network config
    that violates 12-factor app rules. collector-ingestion is a VPC-creating unit, so it
    keys the common/networks.json map by its fully-derived namespace (spec S4.1, S3.6),
    anchored on get_repo_root() (D3). account.hcl carries only aws_account_id +
    use_pinned_module_sources (E8-F2-S1-T1); it does NOT declare vpc_cidr_block, so the
    value cannot come from account_vars. The lookup fails fast (no fallback) when the
    namespace has no CIDR row (spec S3.5, S7).
    """
    content = _template_content("collector-ingestion.hcl")
    cidr_lines = [
        line
        for line in content.splitlines()
        if "vpc_cidr_block" in line and not line.strip().startswith("#") and "=" in line
    ]
    assert len(cidr_lines) >= 1, (
        "collector-ingestion.hcl does not assign vpc_cidr_block. "
        'Add: vpc_cidr_block = local.network_cfg["vpc_cidr_block"] (from common/networks.json).'
    )
    # Must NOT be a quoted string literal (hardcoded CIDR).
    for line in cidr_lines:
        stripped = line.strip()
        assert not re.search(r'vpc_cidr_block\s*=\s*"[^"]*"', stripped), (
            f"collector-ingestion.hcl has hardcoded vpc_cidr_block: '{stripped}'. "
            "12-factor: Source from common/networks.json keyed by the derived namespace."
        )
    # Must read common/networks.json (anchored on get_repo_root(), D3) and key it by the
    # derived namespace, then assign vpc_cidr_block from that network config.
    assert "${get_repo_root()}/terragrunt/common/networks.json" in content, (
        "collector-ingestion.hcl does not read common/networks.json "
        "(anchored on get_repo_root(), D3). "
        "vpc_cidr_block must be resolved from common/networks.json keyed by the derived "
        "namespace (spec S4.1, S3.6), not from account.hcl (which lacks vpc_cidr_block)."
    )
    assert any(
        re.search(r'vpc_cidr_block\s*=\s*local\.network_cfg\["vpc_cidr_block"\]', line)
        or re.search(r"vpc_cidr_block\s*=\s*local\.vpc_cidr_block", line)
        for line in cidr_lines
    ), (
        "collector-ingestion.hcl vpc_cidr_block input is not wired to the "
        f"common/networks.json-derived value: {cidr_lines}. "
        "Use: vpc_cidr_block = local.vpc_cidr_block where "
        'local.vpc_cidr_block = local.network_cfg["vpc_cidr_block"].'
    )


@pytest.mark.unit
def test_run_assertions_returns_no_errors_when_all_templates_present() -> None:
    """run_assertions returns an empty list when all ten templates are present and valid."""
    errors = run_assertions()
    assert errors == [], (
        f"run_assertions() returned {len(errors)} error(s) when all templates should be valid:\n"
        + "\n".join(errors)
    )


# ---------------------------------------------------------------------------
# E8-F6-S2-T1: oidc-bootstrap.hcl roles resolved from common/ (AC-FUNC-001)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oidc_bootstrap_has_no_inline_roles_block() -> None:
    """oidc-bootstrap.hcl must NOT contain an inline roles = { ... } block (AC-FUNC-001).

    The roles map must be sourced from common/ keyed by account id so that the
    _envcommon template is copy-safe (spec section 4.9, AC-16).
    """
    content = _template_content("oidc-bootstrap.hcl")
    inline_roles_pattern = re.compile(r"^\s*roles\s*=\s*\{", re.MULTILINE)
    assert not inline_roles_pattern.search(content), (
        "terragrunt/_envcommon/oidc-bootstrap.hcl contains an inline 'roles = {' block. "
        "The roles map must be resolved from common/ keyed by account id so the "
        "prod oidc-bootstrap template is copy-safe (AC-FUNC-001, spec section 4.9, AC-16)."
    )


@pytest.mark.unit
def test_oidc_bootstrap_resolves_roles_from_common() -> None:
    """oidc-bootstrap.hcl must reference the common/ layer for its roles map (AC-FUNC-001).

    The template must load roles via read_terragrunt_config or jsondecode/file anchored
    on get_repo_root() so the roles source is outside the copy boundary (spec D3).
    """
    content = _template_content("oidc-bootstrap.hcl")
    common_ref_pattern = re.compile(
        r"common/common\.hcl|common/accounts\.json|common/oidc-roles\.json|/common/",
        re.MULTILINE,
    )
    assert common_ref_pattern.search(content), (
        "terragrunt/_envcommon/oidc-bootstrap.hcl does not reference the common/ layer. "
        "The roles map must be resolved from common/ (e.g., common/oidc-roles.json) "
        "via get_repo_root()-anchored paths so a prod account deploy resolves roles "
        "without an inline literal (AC-FUNC-001, spec section 4.9, D3)."
    )


@pytest.mark.unit
def test_run_assertions_catches_violation_in_existing_template(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_assertions detects violations in an existing template (per-template loop coverage)."""
    import scripts.tg_regression as mod

    env_dir = tmp_path / "envcommon"
    env_dir.mkdir()

    # Write all required templates as minimal valid stubs except collector-ingestion.
    stub_content = "inputs = {}\n"
    for name in mod.REQUIRED_TEMPLATES:
        (env_dir / name).write_text(stub_content, encoding="utf-8")

    # Inject a synonym into one template to trigger an assertion failure.
    (env_dir / "dns-prod-zone.hcl").write_text(
        "inputs = {\n  hosted_zone_id = dependency.zone.outputs.zone_id\n}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "ENVCOMMON_DIR", env_dir)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    errors = run_assertions()
    # At minimum: missing canonical prod_hosted_zone_id in dns-prod-zone + synonym found.
    assert len(errors) >= 1


@pytest.mark.unit
def test_main_exits_zero_when_all_assertions_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() returns 0 and prints PASSED when all assertions pass."""
    import scripts.tg_regression as mod

    # Force run_assertions to return no errors.
    monkeypatch.setattr(mod, "run_assertions", lambda: [])
    result = mod.main()
    assert result == 0
    captured = capsys.readouterr()
    assert "PASSED" in captured.out


@pytest.mark.unit
def test_main_exits_one_when_assertions_fail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() returns 1 and prints FAILED when assertions return errors."""
    import scripts.tg_regression as mod

    monkeypatch.setattr(mod, "run_assertions", lambda: ["ERROR: something went wrong"])
    result = mod.main()
    assert result == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    assert "something went wrong" in captured.err
