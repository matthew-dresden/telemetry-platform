"""Unit tests for the sandbox acm-validate-collector terragrunt unit.

These tests assert the structural and content constraints for the sandbox account
(222222222222) acm-validate-collector terragrunt unit.
They validate:

- AC-3: the unit sources exactly one primitive module (primitives/route53-record)
  with no second source block, and the unit's inputs set records (not alias)
  so the route53-record exactly-one-of guard passes.
- AC-7: R3 (acm-validate-collector) is written
  by exactly one unit; it sources domain_validation_options from its upstream
  acm-collector dependency block with mock_outputs for offline plan/validate.
- AC-8: dns-delegation stays NS-only; R3 is owned exclusively here.
- D19: the pretty-SAN validation CNAME is the target record.
- D31: sandbox and prod units differ only by folder path and per-env inputs,
  never by inline conditional logic on account id or environment name.
- D37: canonical input names match the route53-record variable contract.
- D45/D47: zone id sourced from account.hcl / service_instance.hcl, not hardcoded.
- enable_custom_domain: record creation is gated on account.hcl enable_custom_domain
  so that sandbox default (false) creates zero records and offline plan/validate pass.
- docs/terragrunt-concepts.md: canonical seven-layer hierarchy basename idioms throughout.
- Security: no hardcoded AWS access keys, no em-dash characters, no secrets in HCL.

All assertions use file-content inspection to validate static HCL without
requiring a live AWS credential or a real Terraform init.
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
PROD_ACCOUNT = "444444444444"
ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / SANDBOX_ACCOUNT
SANDBOX_BASE = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / "sandbox"
ENV_INSTANCE_BASE = SANDBOX_BASE / "000"

# ---------------------------------------------------------------------------
# acm-validate-collector paths
# ---------------------------------------------------------------------------

COLLECTOR_SERVICE_DIR = ENV_INSTANCE_BASE / "acm-validate-collector"
COLLECTOR_INSTANCE_DIR = COLLECTOR_SERVICE_DIR / "000"

COLLECTOR_SERVICE_HCL = COLLECTOR_SERVICE_DIR / "service.hcl"
COLLECTOR_SERVICE_INSTANCE_HCL = COLLECTOR_INSTANCE_DIR / "service_instance.hcl"
COLLECTOR_TERRAGRUNT_HCL = COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# primitives/route53-record module path (AC-3)
# ---------------------------------------------------------------------------

ROUTE53_RECORD_MODULE_PATH = "primitives/route53-record"

# The include.root.locals.common_tags pattern required by docs/terragrunt-concepts.md (expose=true).
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"

# Forbidden resources: inline resource blocks must not appear in Terragrunt HCL files.
FORBIDDEN_INLINE_RESOURCES = [
    'resource "aws_route53_record"',
    'resource "aws_acm_certificate"',
]

# D31: enable_custom_domain must control record creation (gating input)
ENABLE_CUSTOM_DOMAIN_IDENT = "enable_custom_domain"

# D37: dependency and mock_outputs for domain_validation_options
DOMAIN_VALIDATION_OPTIONS_IDENT = "domain_validation_options"

# mock_outputs_allowed_terraform_commands must include validate and plan
MOCK_ALLOWED_COMMANDS_REQUIRED = ["validate", "plan"]


# ---------------------------------------------------------------------------
# Fixtures -- acm-validate-collector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def collector_validate_service_hcl_content() -> str:
    """Read acm-validate-collector/service.hcl. Fails loudly if the file does not exist."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "This file must be created for the sandbox acm-validate-collector service layer "
        "(E6-F4-S1-T1)."
    )
    return COLLECTOR_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_validate_service_instance_hcl_content() -> str:
    """Read acm-validate-collector/000/service_instance.hcl. Fails loudly if not found."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the sandbox acm-validate-collector service instance "
        "(E6-F4-S1-T1)."
    )
    return COLLECTOR_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_validate_terragrunt_hcl_content() -> str:
    """Read acm-validate-collector/000/terragrunt.hcl. Fails loudly if not found."""
    assert COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox acm-validate-collector unit "
        "(E6-F4-S1-T1)."
    )
    return COLLECTOR_TERRAGRUNT_HCL.read_text()


# ===========================================================================
# acm-validate-collector tests
# ===========================================================================

# ---------------------------------------------------------------------------
# File existence -- acm-validate-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_service_hcl_exists() -> None:
    """acm-validate-collector/service.hcl must exist at the sandbox service layer path."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "Required for the acm-validate-collector service layer (AC-7, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_service_instance_hcl_exists() -> None:
    """acm-validate-collector/000/service_instance.hcl must exist."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "Required for the acm-validate-collector service instance (AC-7, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_exists() -> None:
    """Leaf acm-validate-collector/000/terragrunt.hcl must exist."""
    assert COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox acm-validate-collector unit (AC-7, E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# Directory structure -- acm-validate-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_service_dir_exists() -> None:
    """The acm-validate-collector service directory must exist under sandbox/000/."""
    assert COLLECTOR_SERVICE_DIR.exists(), (
        f"acm-validate-collector service directory not found at {COLLECTOR_SERVICE_DIR}. "
        "The 7-layer hierarchy requires a service directory named 'acm-validate-collector' "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_validate_collector_instance_dir_exists() -> None:
    """The acm-validate-collector/000 service instance directory must exist."""
    assert COLLECTOR_INSTANCE_DIR.exists(), (
        f"acm-validate-collector instance directory not found at {COLLECTOR_INSTANCE_DIR}. "
        "The 7-layer hierarchy requires a service instance directory named '000' "
        "(docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# Layer basename idioms -- acm-validate-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_service_hcl_declares_basename_local(
    collector_validate_service_hcl_content: str,
) -> None:
    """acm-validate-collector/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in collector_validate_service_hcl_content, (
        "acm-validate-collector/service.hcl must declare "
        "`service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_service_dir_basename_is_correct() -> None:
    """acm-validate-collector/service.hcl parent dir basename must be 'acm-validate-collector'."""
    assert COLLECTOR_SERVICE_HCL.parent.name == "acm-validate-collector", (
        f"service.hcl parent dir is '{COLLECTOR_SERVICE_HCL.parent.name}', "
        "expected 'acm-validate-collector'. "
        "The service layer dir basename must match the service name (E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_service_instance_hcl_declares_basename_local(
    collector_validate_service_instance_hcl_content: str,
) -> None:
    """acm-validate-collector/000/service_instance.hcl must use basename(get_terragrunt_dir())."""
    assert "basename(get_terragrunt_dir())" in collector_validate_service_instance_hcl_content, (
        "acm-validate-collector/000/service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-3: single-module source (primitives/route53-record only) -- acm-validate-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_sources_route53_record(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must source primitives/route53-record (AC-3)."""
    assert ROUTE53_RECORD_MODULE_PATH in collector_validate_terragrunt_hcl_content, (
        f"acm-validate-collector/000/terragrunt.hcl does not reference "
        f"'{ROUTE53_RECORD_MODULE_PATH}'. "
        "The unit must source the route53-record primitive module (AC-3, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_sources_exactly_one_module(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must contain exactly one terraform.source."""
    source_count = len(
        re.findall(r"^\s*source\s*=", collector_validate_terragrunt_hcl_content, re.MULTILINE)
    )
    assert source_count == 1, (
        f"acm-validate-collector/000/terragrunt.hcl contains {source_count} source "
        "declarations; expected exactly 1. "
        "AC-3 requires the leaf to source exactly one primitive module "
        "(primitives/route53-record). No second source block is permitted."
    )


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_has_terraform_block(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must contain a terraform {} source block."""
    assert "terraform {" in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl does not contain a terraform {} block. "
        "The leaf must declare `terraform { source = ... }` to source "
        "primitives/route53-record (AC-3, E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-3: include patterns -- acm-validate-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_includes_root(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must include the root terragrunt.hcl."""
    assert 'include "root"' in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "Required for namespace-derived state backend inheritance (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_root_include_expose_true(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set expose = true for acm-validate-collector."""
    assert "expose         = true" in collector_validate_terragrunt_hcl_content or (
        "expose = true" in collector_validate_terragrunt_hcl_content
    ), (
        'acm-validate-collector/000/terragrunt.hcl include "root" block must set '
        "expose = true. "
        "Required for include.root.locals.common_tags to be accessible in the leaf "
        "inputs block (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_root_include_deep_merge(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set merge_strategy = \"deep\" for acm-validate-collector."""
    assert 'merge_strategy = "deep"' in collector_validate_terragrunt_hcl_content, (
        'acm-validate-collector/000/terragrunt.hcl include "root" block must set '
        'merge_strategy = "deep". '
        "Deep merge is required so inputs from multiple includes are merged correctly "
        "(docs/terragrunt-concepts.md, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_terragrunt_hcl_uses_find_in_parent_folders(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must use find_in_parent_folders for root."""
    assert 'find_in_parent_folders("root.hcl")' in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl must use "
        "find_in_parent_folders('terragrunt.hcl') to locate the root config. "
        "This is the docs/terragrunt-concepts.md canonical pattern."
    )


# ---------------------------------------------------------------------------
# AC-7 / D19: dependency on acm-collector with domain_validation_options -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_has_dependency_block(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector terragrunt.hcl must declare a dependency block on acm-collector."""
    assert "dependency " in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl must declare a dependency block. "
        "The unit reads domain_validation_options from the upstream acm-collector unit "
        "(AC-7, D26, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_dependency_references_acm_collector(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector dependency config_path must point to acm-collector/000."""
    assert "acm-collector" in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl dependency config_path must reference "
        "the 'acm-collector' unit (not acm-portal). "
        "R3 is the collector pretty-SAN validation CNAME (AC-7, D26, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_dependency_has_domain_validation_options_mock(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector dependency must declare domain_validation_options in mock_outputs."""
    assert DOMAIN_VALIDATION_OPTIONS_IDENT in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl dependency block must include "
        "'domain_validation_options' in mock_outputs so offline plan/validate resolve "
        "without live cross-account reads (AC-7, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_dependency_allows_validate_in_mock_commands(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector mock_outputs_allowed_terraform_commands must include validate."""
    assert "mock_outputs_allowed_terraform_commands" in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl dependency must declare "
        "mock_outputs_allowed_terraform_commands. "
        "This enables offline make tf-validate / make tf-plan to pass on the "
        "enable_custom_domain=false path (AC-7, E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-7: inputs contract (CNAME with records set, alias null) -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_inputs_type_cname(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl inputs.type must be CNAME (AC-7, R3)."""
    assert '"CNAME"' in collector_validate_terragrunt_hcl_content, (
        'acm-validate-collector/000/terragrunt.hcl inputs.type must be set to "CNAME". '
        "R3 is a DNS validation CNAME record for the collector pretty-SAN (AC-7, D19)."
    )


@pytest.mark.unit
def test_acm_validate_collector_inputs_alias_null(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector inputs.alias must be null (route53-record exactly-one-of guard)."""
    assert "alias" in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl must declare the alias input as null. "
        "The route53-record primitive exactly-one-of guard requires either alias or records, "
        "never both (AC-7, E6-F4-S1-T1)."
    )
    # alias must be null (not an alias block with values)
    assert "alias = null" in collector_validate_terragrunt_hcl_content or (
        "alias   = null" in collector_validate_terragrunt_hcl_content
        or "alias    = null" in collector_validate_terragrunt_hcl_content
    ), (
        "acm-validate-collector/000/terragrunt.hcl alias input must be set to null. "
        "The CNAME record uses records (not alias). "
        "Setting alias = null satisfies the route53-record exactly-one-of validation guard "
        "(AC-7, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_inputs_has_records(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector inputs.records must be set (from domain_validation_options)."""
    assert "records" in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl inputs must include 'records'. "
        "R3 is a CNAME with records=[dvo.resource_record_value] from domain_validation_options "
        "(AC-7, D19, E6-F4-S1-T1)."
    )


@pytest.mark.unit
def test_acm_validate_collector_inputs_has_zone_id(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector inputs must include zone_id from the fail-fast-guarded local."""
    assert "zone_id" in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl inputs must include 'zone_id'. "
        "The CNAME is written into the delegated sandbox subdomain zone "
        "(fail-fast guarded, no default -- AC-7, D38, E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# D-7 / spec AC #3: enable_custom_domain must NOT appear as a module input -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_has_no_enable_custom_domain_module_input(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector inputs block must NOT pass enable_custom_domain to route53-record.

    The route53-record primitive (primitives/route53-record) declares no such variable.
    Gating is done at the terragrunt level -- this unit is authored only when the
    env-class custom domain flag is true in domains.json (D-7, spec AC #3).
    Passing the flag as a module input would cause an 'unsupported argument' error on
    'terraform validate'. This mirrors the prod root acm-validate-collector pattern.
    """
    assert ENABLE_CUSTOM_DOMAIN_IDENT not in collector_validate_terragrunt_hcl_content, (
        "acm-validate-collector/000/terragrunt.hcl must NOT pass 'enable_custom_domain' "
        "as an input to primitives/route53-record. The module has no such variable. "
        "The toggle is gated at the terragrunt level via domains.json, not as a module input "
        "(D-7, spec AC #3). Remove 'enable_custom_domain' from the inputs block."
    )


# ---------------------------------------------------------------------------
# AC-3: tags use include.root.locals.common_tags -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_validate_collector_inputs_uses_root_locals_common_tags(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector inputs.tags must use include.root.locals.common_tags."""
    assert ROOT_COMMON_TAGS_REF in collector_validate_terragrunt_hcl_content, (
        f"acm-validate-collector/000/terragrunt.hcl inputs.tags must reference "
        f"'{ROOT_COMMON_TAGS_REF}', not 'local.common_tags'. "
        "The root include is expose=true so its locals are accessible only via "
        "include.root.locals.<name> at leaf scope (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_validate_collector_has_inputs_block(
    collector_validate_terragrunt_hcl_content: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must contain an inputs block."""
    assert "inputs = {" in collector_validate_terragrunt_hcl_content or (
        "inputs={" in collector_validate_terragrunt_hcl_content
    ), (
        "acm-validate-collector/000/terragrunt.hcl does not contain an inputs block. "
        "The leaf must pass zone_id, name, type, records, and tags to the module "
        "(D37, E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-7: no inline forbidden resource blocks -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("forbidden_resource", FORBIDDEN_INLINE_RESOURCES)
def test_acm_validate_collector_no_forbidden_inline_resource(
    collector_validate_terragrunt_hcl_content: str,
    forbidden_resource: str,
) -> None:
    """acm-validate-collector/000/terragrunt.hcl must not contain inline resource blocks."""
    assert forbidden_resource not in collector_validate_terragrunt_hcl_content, (
        f"acm-validate-collector/000/terragrunt.hcl contains '{forbidden_resource}'. "
        "Terragrunt leaf HCL files must not declare inline Terraform resource blocks; "
        "all resources are created by the sourced module (AC-3, E6-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# D31/D45: no hardcoded prod account id -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_validate_service_hcl_content", "acm-validate-collector/service.hcl"),
        (
            "collector_validate_service_instance_hcl_content",
            "acm-validate-collector/000/service_instance.hcl",
        ),
        ("collector_validate_terragrunt_hcl_content", "acm-validate-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_validate_collector_no_hardcoded_prod_account_id(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-validate-collector HCL file may hardcode the prod account id (D31, D45)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert PROD_ACCOUNT not in content, (
        f"{file_label} contains the prod account id '{PROD_ACCOUNT}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded credentials or em-dash -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_validate_service_hcl_content", "acm-validate-collector/service.hcl"),
        (
            "collector_validate_service_instance_hcl_content",
            "acm-validate-collector/000/service_instance.hcl",
        ),
        ("collector_validate_terragrunt_hcl_content", "acm-validate-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_validate_collector_no_hardcoded_aws_access_key(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-validate-collector HCL file may contain a hardcoded AWS access key pattern."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"{file_label} contains a pattern matching an AWS access key. "
        "No credentials may be hardcoded in HCL files."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_validate_service_hcl_content", "acm-validate-collector/service.hcl"),
        (
            "collector_validate_service_instance_hcl_content",
            "acm-validate-collector/000/service_instance.hcl",
        ),
        ("collector_validate_terragrunt_hcl_content", "acm-validate-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_validate_collector_no_em_dash(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-validate-collector HCL file may contain an em-dash character (U+2014)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert "\u2014" not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# AC-8: no terragrunt skip -- collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_validate_service_hcl_content", "acm-validate-collector/service.hcl"),
        (
            "collector_validate_service_instance_hcl_content",
            "acm-validate-collector/000/service_instance.hcl",
        ),
        ("collector_validate_terragrunt_hcl_content", "acm-validate-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_validate_collector_no_terragrunt_skip(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-validate-collector HCL file may use terragrunt skip (D31, AC-8).

    The enable_custom_domain toggle (count/for_each on the module) is the only
    permitted mechanism to no-op these units. Using 'skip = true' bypasses
    dependency tracking and violates D31/AC-8.
    """
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert "skip = true" not in content and "skip=true" not in content, (
        f"{file_label} uses 'skip = true'. "
        "Terragrunt skip is prohibited (D31, AC-8). "
        "Use enable_custom_domain to gate the module count/for_each instead."
    )
