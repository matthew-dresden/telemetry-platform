"""Unit tests for the sandbox dns-collector alias record terragrunt unit.

These tests assert the structural and content constraints for the sandbox account
(222222222222) dns-collector terragrunt unit. They validate:

- AC-3: the unit sources exactly one primitive module (primitives/route53-record)
  with no second source block; the alias block is set and records is left unset
  so the route53-record exactly-one-of guard passes.
- AC-8: dns-collector is the sole owner of alias record R4
  (collector.* -> collector CloudFront distribution);
  collector-ingestion must not declare R4.
- D37: canonical input names match the route53-record variable contract;
  cloudfront_domain_name and cloudfront_hosted_zone_id are read from the
  UNPREFIXED upstream unit outputs (D48: a prefixed read yields a dangling reference).
- D37: dependency blocks for dns-prod-zone and the respective upstream CloudFront
  unit are present in each leaf terragrunt.hcl.
- D45/D47: zone_id sourced from dns-prod-zone dependency, not hardcoded.
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
ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / SANDBOX_ACCOUNT
SANDBOX_BASE = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / "sandbox"
ENV_INSTANCE_BASE = SANDBOX_BASE / "000"

# ---------------------------------------------------------------------------
# dns-collector paths
# ---------------------------------------------------------------------------

DNS_COLLECTOR_SERVICE_DIR = ENV_INSTANCE_BASE / "dns-collector"
DNS_COLLECTOR_INSTANCE_DIR = DNS_COLLECTOR_SERVICE_DIR / "000"

DNS_COLLECTOR_SERVICE_HCL = DNS_COLLECTOR_SERVICE_DIR / "service.hcl"
DNS_COLLECTOR_SERVICE_INSTANCE_HCL = DNS_COLLECTOR_INSTANCE_DIR / "service_instance.hcl"
DNS_COLLECTOR_TERRAGRUNT_HCL = DNS_COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# primitives/route53-record module path (AC-3)
ROUTE53_RECORD_MODULE_PATH = "primitives/route53-record"

# The include.root.locals.common_tags pattern required by docs/terragrunt-concepts.md (expose=true).
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"

# Unprefixed CloudFront output names that the alias blocks must reference (D48).
CLOUDFRONT_DOMAIN_NAME_OUTPUT = "cloudfront_domain_name"
CLOUDFRONT_HOSTED_ZONE_ID_OUTPUT = "cloudfront_hosted_zone_id"

# Forbidden double-ownership identifiers (AC-8 / D39).
R4_RECORD_MARKER = "R4"


# ---------------------------------------------------------------------------
# Fixtures -- dns-collector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dns_collector_service_hcl_content() -> str:
    """Read dns-collector service.hcl content. Fails loudly if the file does not exist."""
    assert DNS_COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DNS_COLLECTOR_SERVICE_HCL}. "
        "This file must be created for the sandbox dns-collector service layer (E6-F5-S2-T1)."
    )
    return DNS_COLLECTOR_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def dns_collector_service_instance_hcl_content() -> str:
    """Read dns-collector service_instance.hcl. Fails loudly if the file does not exist."""
    assert DNS_COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DNS_COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the sandbox dns-collector service instance (E6-F5-S2-T1)."
    )
    return DNS_COLLECTOR_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def dns_collector_terragrunt_hcl_content() -> str:
    """Read dns-collector leaf terragrunt.hcl. Fails loudly if the file does not exist."""
    assert DNS_COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {DNS_COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox dns-collector terragrunt unit (E6-F5-S2-T1)."
    )
    return DNS_COLLECTOR_TERRAGRUNT_HCL.read_text()


# ---------------------------------------------------------------------------
# File existence assertions -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_service_hcl_exists() -> None:
    """service.hcl must exist at the dns-collector service layer path (E6-F5-S2-T1)."""
    assert DNS_COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DNS_COLLECTOR_SERVICE_HCL}. "
        "Required for the dns-collector service layer (AC-3, E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_service_instance_hcl_exists() -> None:
    """service_instance.hcl must exist at the dns-collector/000 service instance path."""
    assert DNS_COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DNS_COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "Required for the dns-collector service instance (AC-3, E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_exists() -> None:
    """Leaf terragrunt.hcl must exist at dns-collector/000/terragrunt.hcl."""
    assert DNS_COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {DNS_COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox dns-collector unit (AC-3, E6-F5-S2-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom assertions -- dns-collector (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_service_hcl_declares_basename_local(
    dns_collector_service_hcl_content: str,
) -> None:
    """dns-collector service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in dns_collector_service_hcl_content, (
        "dns-collector service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_service_dir_basename_is_correct() -> None:
    """dns-collector service directory basename must be 'dns-collector'."""
    assert DNS_COLLECTOR_SERVICE_DIR.name == "dns-collector", (
        f"dns-collector service dir basename is '{DNS_COLLECTOR_SERVICE_DIR.name}', "
        "expected 'dns-collector'. "
        "The service layer dir basename must be 'dns-collector' (E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_service_instance_hcl_declares_basename_local(
    dns_collector_service_instance_hcl_content: str,
) -> None:
    """dns-collector service_instance.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in dns_collector_service_instance_hcl_content, (
        "dns-collector service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F5-S2-T1)."
    )


# ---------------------------------------------------------------------------
# AC-3: single-module source (primitives/route53-record only) -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_sources_route53_record(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector leaf terragrunt.hcl must source primitives/route53-record (AC-3)."""
    assert ROUTE53_RECORD_MODULE_PATH in dns_collector_terragrunt_hcl_content, (
        f"dns-collector terragrunt.hcl does not reference '{ROUTE53_RECORD_MODULE_PATH}'. "
        "The unit must source the route53-record primitive module (AC-3, E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_sources_exactly_one_module(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector leaf terragrunt.hcl must contain exactly one terraform.source (AC-3)."""
    source_count = len(
        re.findall(r"^\s*source\s*=", dns_collector_terragrunt_hcl_content, re.MULTILINE)
    )
    assert source_count == 1, (
        f"dns-collector terragrunt.hcl contains {source_count} source declarations; "
        "expected exactly 1. AC-3 requires the leaf to source exactly one primitive "
        "module (primitives/route53-record). No second source is permitted."
    )


# ---------------------------------------------------------------------------
# AC-3: include patterns -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_includes_root(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector leaf terragrunt.hcl must include the root terragrunt.hcl."""
    assert 'include "root"' in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "Required for namespace-derived state backend inheritance (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_root_include_expose_true(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector root include block must set expose = true (docs/terragrunt-concepts.md)."""
    expose_set = (
        "expose" in dns_collector_terragrunt_hcl_content
        and "true" in dns_collector_terragrunt_hcl_content
    )
    assert expose_set, (
        'dns-collector terragrunt.hcl include "root" block must set expose = true. '
        "Required for include.root.locals.common_tags to be accessible "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_root_include_deep_merge(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector root include block must set merge_strategy = \"deep\"."""
    assert 'merge_strategy = "deep"' in dns_collector_terragrunt_hcl_content, (
        'dns-collector terragrunt.hcl include "root" block must set '
        'merge_strategy = "deep" (docs/terragrunt-concepts.md, E6-F5-S2-T1).'
    )


# ---------------------------------------------------------------------------
# D37: dependency blocks -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_has_dns_prod_zone_dependency(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector leaf must declare a dependency on dns-prod-zone (D37).

    The dns-prod-zone dependency provides the prod zone_id for the R4 alias record.
    """
    assert 'dependency "dns_prod_zone"' in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl does not declare a 'dns_prod_zone' dependency block. "
        "The unit requires dns-prod-zone.outputs.zone_id for R4 (D37, E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_has_collector_ingestion_dependency(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector leaf must declare a dependency on collector-ingestion (D37).

    The collector-ingestion dependency provides the unprefixed cloudfront_domain_name
    and cloudfront_hosted_zone_id for the R4 alias target.
    """
    assert "collector-ingestion" in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl does not reference 'collector-ingestion'. "
        "The unit requires collector-ingestion outputs for the R4 alias target (D37, D48)."
    )


@pytest.mark.unit
def test_dns_collector_terragrunt_hcl_dependency_has_mock_outputs(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector dependencies must declare mock_outputs for plan/validate (D37)."""
    assert "mock_outputs" in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl dependencies must declare mock_outputs so that "
        "plan/validate can run before upstream units are applied (D37, E6-F5-S2-T1)."
    )


# ---------------------------------------------------------------------------
# D48: unprefixed CloudFront output reads -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_reads_unprefixed_cloudfront_domain_name(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector must read the unprefixed cloudfront_domain_name output (D48).

    The alias target requires the raw CloudFront values. Reading a prefixed output
    (e.g. collector_cloudfront_domain_name) yields a dangling reference (D48).
    The unit must consume the UNPREFIXED 'cloudfront_domain_name' output.
    """
    assert CLOUDFRONT_DOMAIN_NAME_OUTPUT in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl does not reference 'cloudfront_domain_name'. "
        "D48: The alias target must use the UNPREFIXED 'cloudfront_domain_name' output "
        "from collector-ingestion. A prefixed output (e.g. collector_cloudfront_domain_name) "
        "yields a dangling reference and fails apply."
    )


@pytest.mark.unit
def test_dns_collector_reads_unprefixed_cloudfront_hosted_zone_id(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector must read the unprefixed cloudfront_hosted_zone_id output (D48)."""
    assert CLOUDFRONT_HOSTED_ZONE_ID_OUTPUT in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl does not reference 'cloudfront_hosted_zone_id'. "
        "D48: The alias target must use the UNPREFIXED 'cloudfront_hosted_zone_id' output "
        "from collector-ingestion. A prefixed output yields a dangling reference."
    )


# ---------------------------------------------------------------------------
# Alias block and records=null contract -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_service_instance_hcl_sets_alias_block(
    dns_collector_service_instance_hcl_content: str,
) -> None:
    """dns-collector service_instance.hcl must declare the alias block (AC-3, E6-F5-S2-T1).

    The route53-record primitive enforces an exactly-one-of contract: either alias
    or records must be set, never both. The dns-collector unit sets the alias block
    and leaves records unset so the guard passes.
    """
    assert "alias" in dns_collector_service_instance_hcl_content, (
        "dns-collector service_instance.hcl does not mention 'alias'. "
        "The unit must declare the alias block so the route53-record exactly-one-of "
        "guard passes (AC-3, E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_service_instance_hcl_declares_r4_ownership(
    dns_collector_service_instance_hcl_content: str,
) -> None:
    """dns-collector service_instance.hcl must declare R4 single ownership (AC-8)."""
    assert R4_RECORD_MARKER in dns_collector_service_instance_hcl_content, (
        "dns-collector service_instance.hcl does not mention 'R4'. "
        "This unit is the sole owner of DNS alias record R4 (AC-8, E6-F5-S2-T1). "
        "Document the single ownership in service_instance.hcl."
    )


# ---------------------------------------------------------------------------
# D37: zone_id sourced from dns-prod-zone dependency -- both units
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_passes_zone_id_from_dns_dep(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector must pass zone_id from dns_prod_zone dependency output (D37)."""
    assert "dependency.dns_prod_zone.outputs.zone_id" in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl must wire 'zone_id' from "
        "'dependency.dns_prod_zone.outputs.zone_id'. "
        "The zone_id provides the zone for R4 (D37, AC-8, E6-F5-S2-T1)."
    )


# ---------------------------------------------------------------------------
# Tags pattern: include.root.locals.common_tags -- both units
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_passes_tags_using_root_locals(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector inputs tags must use include.root.locals.common_tags
    (docs/terragrunt-concepts.md)."""
    assert ROOT_COMMON_TAGS_REF in dns_collector_terragrunt_hcl_content, (
        f"dns-collector terragrunt.hcl inputs.tags must reference '{ROOT_COMMON_TAGS_REF}', "
        "not 'local.common_tags'. The root include is expose=true so its locals are "
        "accessible via include.root.locals.<name> only (docs/terragrunt-concepts.md, AC-3)."
    )


# ---------------------------------------------------------------------------
# AC-8: single-ownership scan -- R4 not written by collector-ingestion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_only_dns_collector_unit_defines_r4() -> None:
    """R4 must be declared by ONLY dns-collector. collector-ingestion must NOT own R4 (AC-8).

    This test scans all service_instance.hcl files under sandbox/000/ for R4 ownership
    claims. Only dns-collector/000/service_instance.hcl may declare R4.
    """
    r4_owner_files: list[str] = []
    for hcl_file in ENV_INSTANCE_BASE.rglob("service_instance.hcl"):
        content = hcl_file.read_text()
        if R4_RECORD_MARKER in content and hcl_file != DNS_COLLECTOR_SERVICE_INSTANCE_HCL:
            r4_owner_files.append(str(hcl_file))
    assert len(r4_owner_files) == 0, (
        f"R4 ownership is declared in non-dns-collector service_instance.hcl files: "
        f"{r4_owner_files}. "
        "R4 must be owned exclusively by the dns-collector unit (AC-8, D39, E6-F5-S2-T1)."
    )


# ---------------------------------------------------------------------------
# D37: dependency config_paths -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_dns_prod_zone_dep_path(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector dns_prod_zone dependency config_path must point at the foundation tier.

    Phase 8 (Approach A): dns-prod-zone was relocated to bootstrap/<role>/ (a sibling of the
    env subtree). The config_path derives the bootstrap role from environment.hcl
    (bootstrap_role) and the active set from the foundation active.hcl (dns_prod_zone_active);
    neither is hardcoded (D31, D37, AC-FUNC-003).
    """
    assert (
        "bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"
        in dns_collector_terragrunt_hcl_content
    ), (
        "dns-collector terragrunt.hcl dns_prod_zone dependency config_path must point at the "
        "foundation zone 'bootstrap/${local.bootstrap_role}/dns-prod-zone/"
        "${local.dns_prod_zone_active}' (Phase 8 Approach A relocation; D37, D31)."
    )


@pytest.mark.unit
def test_dns_collector_collector_ingestion_dep_path(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector collector-ingestion dep path must use instance-relative interpolation.

    E8-F4-S1-T1 migrated the dns-collector leaf to use '${local.svc_instance}' rather than a
    hardcoded '/000' index (AC-FUNC-003, D37, E6-F5-S2-T1).
    """
    assert "collector-ingestion/${local.svc_instance}" in dns_collector_terragrunt_hcl_content, (
        "dns-collector terragrunt.hcl must interpolate "
        "'collector-ingestion/${local.svc_instance}' as the dependency config_path "
        "rather than 'collector-ingestion/000'. "
        "This wires the unit to the CloudFront outputs at the same service-instance "
        "index (D37, D48, AC-FUNC-003, E8-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# D31: no hardcoded prod account ids -- both units
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_service_hcl_content", "dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "dns-collector terragrunt.hcl"),
    ],
)
def test_no_hardcoded_prod_account_id(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """No HCL file may hardcode the prod account id (D31, D45)."""
    content: str = request.getfixturevalue(content_fixture)
    prod_account_id = "444444444444"
    assert prod_account_id not in content, (
        f"{file_label} contains the prod account id '{prod_account_id}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded credentials, no em-dash -- both units
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_service_hcl_content", "dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "dns-collector terragrunt.hcl"),
    ],
)
def test_no_hardcoded_aws_access_key(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """No HCL file may contain a hardcoded AWS access key pattern."""
    content: str = request.getfixturevalue(content_fixture)
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"{file_label} contains a pattern matching an AWS access key. "
        "No credentials may be hardcoded in HCL files."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_service_hcl_content", "dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "dns-collector terragrunt.hcl"),
    ],
)
def test_no_em_dash_in_hcl_files(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """No HCL file may contain an em-dash character (U+2014) per code standards."""
    content: str = request.getfixturevalue(content_fixture)
    assert "\u2014" not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# inputs block -- both units
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_has_inputs_block(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """dns-collector leaf terragrunt.hcl must contain an inputs block (D37)."""
    has_inputs = (
        "inputs = {" in dns_collector_terragrunt_hcl_content
        or "inputs={" in dns_collector_terragrunt_hcl_content
    )
    assert has_inputs, (
        "dns-collector terragrunt.hcl does not contain an inputs block. "
        "The leaf must pass module inputs (D37, E6-F5-S2-T1)."
    )


# ---------------------------------------------------------------------------
# Directory structure assertions -- both units
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_collector_service_dir_exists() -> None:
    """The dns-collector service directory must exist under sandbox/000/."""
    assert DNS_COLLECTOR_SERVICE_DIR.exists(), (
        f"dns-collector service directory not found at {DNS_COLLECTOR_SERVICE_DIR}. "
        "The 7-layer hierarchy requires a service directory named 'dns-collector' "
        "(docs/terragrunt-concepts.md, E6-F5-S2-T1)."
    )


@pytest.mark.unit
def test_dns_collector_instance_dir_exists() -> None:
    """The dns-collector/000 service instance directory must exist."""
    assert DNS_COLLECTOR_INSTANCE_DIR.exists(), (
        f"dns-collector instance directory not found at {DNS_COLLECTOR_INSTANCE_DIR}. "
        "The 7-layer hierarchy requires a service instance directory named '000' "
        "(docs/terragrunt-concepts.md, E6-F5-S2-T1)."
    )
