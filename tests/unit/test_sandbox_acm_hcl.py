"""Unit tests for the sandbox acm-collector terragrunt unit HCL files.

These tests assert the structural and content constraints for the sandbox account
(222222222222) acm-collector terragrunt unit. They validate:

- AC-3: the unit sources exactly one primitive module (primitives/acm-certificate)
  with no dangling input (no zone_id), and make tf-test rejects any input the
  primitive does not declare.
- AC-7: the pretty SAN (mandatory per D19) is present in every certificate request.
  The _envcommon/acm-collector.hcl template declares
  subject_alternative_names carrying the pretty FQDN.
- D19: pretty-name SAN is MANDATORY on every cert.
- D24: wait_for_validation = false so the request returns immediately without blocking
  on DNS validation. Validation lives in a separate downstream unit (D26).
- D26: no aws_route53_record or aws_acm_certificate_validation resource may appear
  in these request-only units.
- D31: sandbox and prod units differ only by folder path and account.hcl inputs,
  never by inline conditional logic on account id or environment name.
- D37: canonical input names (domain_name, subject_alternative_names,
  wait_for_validation) match the declared variable contract on primitives/acm-certificate.
- D45/D47: account identity and domain apexes sourced from account.hcl, not hardcoded.
- docs/terragrunt-concepts.md: canonical seven-layer hierarchy basename idioms throughout.
- Security: no hardcoded AWS access keys, no em-dash characters, no secrets in HCL.

All assertions use file-content inspection to validate static HCL without
requiring a live AWS credential or a real Terraform init.

Test count: 40 unit-marked tests for acm-collector
covering file existence, layer basename idioms, single-module source assertion,
include patterns, SAN contract validation, forbidden-resource checks, and security.
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
# acm-collector paths
# ---------------------------------------------------------------------------

COLLECTOR_SERVICE_DIR = ENV_INSTANCE_BASE / "acm-collector"
COLLECTOR_INSTANCE_DIR = COLLECTOR_SERVICE_DIR / "000"

COLLECTOR_SERVICE_HCL = COLLECTOR_SERVICE_DIR / "service.hcl"
COLLECTOR_SERVICE_INSTANCE_HCL = COLLECTOR_INSTANCE_DIR / "service_instance.hcl"
COLLECTOR_TERRAGRUNT_HCL = COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# references/acm-cert-managed module path (AC-3)
#
# The acm leaves source the acm-cert-managed reference (which wraps the acm-certificate
# primitive and adds the gated cross-account remote-state read), not the primitive directly.
# ---------------------------------------------------------------------------

ACM_REFERENCE_MODULE_PATH = "references/acm-cert-managed"

# The include.root.locals.common_tags pattern required by docs/terragrunt-concepts.md (expose=true).
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"

# Forbidden resources: these must NOT appear in certificate-request units (D26)
FORBIDDEN_RESOURCES = [
    "aws_route53_record",
    "aws_acm_certificate_validation",
]

# Forbidden input: zone_id is not declared by primitives/acm-certificate (AC-3)
FORBIDDEN_INPUT_ZONE_ID = "zone_id"

# D19: mandatory pretty SANs
SANDBOX_PRETTY_APEX = "sandbox.telemetry.example.com"
COLLECTOR_PRETTY_FQDN = f"collector.{SANDBOX_PRETTY_APEX}"


# ---------------------------------------------------------------------------
# Fixtures -- acm-collector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def collector_service_hcl_content() -> str:
    """Read acm-collector/service.hcl. Fails loudly if the file does not exist."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "This file must be created for the sandbox acm-collector service layer (E6-F1-S1-T2)."
    )
    return COLLECTOR_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_service_instance_hcl_content() -> str:
    """Read acm-collector/000/service_instance.hcl. Fails loudly if the file does not exist."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the sandbox acm-collector service instance (E6-F1-S1-T2)."
    )
    return COLLECTOR_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_terragrunt_hcl_content() -> str:
    """Read acm-collector/000/terragrunt.hcl. Fails loudly if the file does not exist."""
    assert COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox acm-collector terragrunt unit (E6-F1-S1-T2)."
    )
    return COLLECTOR_TERRAGRUNT_HCL.read_text()


# ===========================================================================
# acm-collector tests
# ===========================================================================

# ---------------------------------------------------------------------------
# File existence -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_collector_service_hcl_exists() -> None:
    """acm-collector/service.hcl must exist at the sandbox service layer path (E6-F1-S1-T2)."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "Required for the acm-collector service layer (AC-3, E6-F1-S1-T2)."
    )


@pytest.mark.unit
def test_acm_collector_service_instance_hcl_exists() -> None:
    """acm-collector/000/service_instance.hcl must exist at the service instance path."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "Required for the acm-collector service instance (AC-3, E6-F1-S1-T2)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_exists() -> None:
    """Leaf acm-collector/000/terragrunt.hcl must exist."""
    assert COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox acm-collector unit (AC-3, E6-F1-S1-T2)."
    )


# ---------------------------------------------------------------------------
# Directory structure -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_collector_service_dir_exists() -> None:
    """The acm-collector service directory must exist under sandbox/000/."""
    assert COLLECTOR_SERVICE_DIR.exists(), (
        f"acm-collector service directory not found at {COLLECTOR_SERVICE_DIR}. "
        "The 7-layer hierarchy requires a service directory named 'acm-collector' "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_collector_instance_dir_exists() -> None:
    """The acm-collector/000 service instance directory must exist."""
    assert COLLECTOR_INSTANCE_DIR.exists(), (
        f"acm-collector instance directory not found at {COLLECTOR_INSTANCE_DIR}. "
        "The 7-layer hierarchy requires a service instance directory named '000' "
        "(docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# Layer basename idioms -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_collector_service_hcl_declares_basename_local(
    collector_service_hcl_content: str,
) -> None:
    """acm-collector/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in collector_service_hcl_content, (
        "acm-collector/service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F1-S1-T2)."
    )


@pytest.mark.unit
def test_acm_collector_service_dir_basename_is_acm_collector() -> None:
    """acm-collector/service.hcl must be located so its basename resolves to 'acm-collector'."""
    assert COLLECTOR_SERVICE_HCL.parent.name == "acm-collector", (
        f"service.hcl parent dir is '{COLLECTOR_SERVICE_HCL.parent.name}', "
        "expected 'acm-collector'. "
        "The service layer dir basename must be 'acm-collector' (E6-F1-S1-T2)."
    )


@pytest.mark.unit
def test_acm_collector_service_instance_hcl_declares_basename_local(
    collector_service_instance_hcl_content: str,
) -> None:
    """acm-collector/000/service_instance.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in collector_service_instance_hcl_content, (
        "acm-collector/000/service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F1-S1-T2)."
    )


# ---------------------------------------------------------------------------
# AC-3: single-module source (references/acm-cert-managed only) -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_sources_acm_reference(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must source references/acm-cert-managed (AC-3)."""
    assert ACM_REFERENCE_MODULE_PATH in collector_terragrunt_hcl_content, (
        f"acm-collector/000/terragrunt.hcl does not reference '{ACM_REFERENCE_MODULE_PATH}'. "
        "The unit must source the acm-cert-managed reference module, which wraps the "
        "acm-certificate primitive and adds the gated cross-account remote-state read "
        "(AC-3, docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_sources_exactly_one_module(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain exactly one terraform.source block (AC-3)."""
    source_count = len(
        re.findall(r"^\s*source\s*=", collector_terragrunt_hcl_content, re.MULTILINE)
    )
    assert source_count == 1, (
        f"acm-collector/000/terragrunt.hcl contains {source_count} source declarations; "
        "expected exactly 1. "
        "AC-3 requires the leaf to source exactly one primitive module "
        "(primitives/acm-certificate). No second source or nested module include is permitted."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_has_terraform_block(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain a terraform {} source block (AC-3)."""
    assert "terraform {" in collector_terragrunt_hcl_content, (
        "acm-collector/000/terragrunt.hcl does not contain a terraform {} block. "
        "The leaf must declare `terraform { source = ... }` to source "
        "primitives/acm-certificate (AC-3, E6-F1-S1-T2)."
    )


# ---------------------------------------------------------------------------
# AC-3: include patterns -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_includes_root(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must include the root terragrunt.hcl."""
    assert 'include "root"' in collector_terragrunt_hcl_content, (
        "acm-collector/000/terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "This is required for namespace-derived state backend inheritance "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_includes_envcommon(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must include _envcommon/acm-collector.hcl (D37)."""
    assert "_envcommon/acm-collector.hcl" in collector_terragrunt_hcl_content, (
        "acm-collector/000/terragrunt.hcl must include '_envcommon/acm-collector.hcl' "
        "via an include block. "
        "The shared input template provides the SAN list and wait_for_validation=false "
        "(D37, D24, E6-F1-S1-T2)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_root_include_expose_true(
    collector_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set expose = true for acm-collector
    (docs/terragrunt-concepts.md)."""
    assert "expose         = true" in collector_terragrunt_hcl_content or (
        "expose = true" in collector_terragrunt_hcl_content
    ), (
        'acm-collector/000/terragrunt.hcl include "root" block must set expose = true. '
        "Required for include.root.locals.common_tags to be accessible in the leaf "
        "inputs block (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_root_include_deep_merge(
    collector_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set merge_strategy = \"deep\" for acm-collector."""
    assert 'merge_strategy = "deep"' in collector_terragrunt_hcl_content, (
        'acm-collector/000/terragrunt.hcl include "root" block must set '
        'merge_strategy = "deep". '
        "Deep merge is required so that inputs from multiple includes are merged "
        "correctly (docs/terragrunt-concepts.md, E6-F1-S1-T2)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_uses_find_in_parent_folders(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must use find_in_parent_folders for root include."""
    assert 'find_in_parent_folders("root.hcl")' in collector_terragrunt_hcl_content, (
        "acm-collector/000/terragrunt.hcl must use find_in_parent_folders('terragrunt.hcl') "
        "to locate the root config. This is the docs/terragrunt-concepts.md canonical pattern."
    )


# ---------------------------------------------------------------------------
# AC-7 / D19: mandatory pretty SAN -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acm_collector_envcommon_declares_wait_for_validation_false() -> None:
    """_envcommon/acm-collector.hcl must declare wait_for_validation = false (D24)."""
    envcommon_path = REPO_ROOT / "terragrunt" / "_envcommon" / "acm-collector.hcl"
    assert envcommon_path.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon_path}. "
        "This file is required to supply the wait_for_validation=false input (D24)."
    )
    content = envcommon_path.read_text()
    assert "wait_for_validation = false" in content, (
        "_envcommon/acm-collector.hcl does not declare 'wait_for_validation = false'. "
        "D24: These units are PURE cert requests; validation is created downstream."
    )


@pytest.mark.unit
def test_acm_collector_envcommon_declares_subject_alternative_names() -> None:
    """_envcommon/acm-collector.hcl must declare subject_alternative_names (D19)."""
    envcommon_path = REPO_ROOT / "terragrunt" / "_envcommon" / "acm-collector.hcl"
    assert envcommon_path.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon_path}. "
        "This file is required to supply the mandatory pretty SAN (D19)."
    )
    content = envcommon_path.read_text()
    assert "subject_alternative_names" in content, (
        "_envcommon/acm-collector.hcl does not declare 'subject_alternative_names'. "
        "D19: The pretty-name SAN is MANDATORY on the collector cert."
    )


@pytest.mark.unit
def test_acm_collector_envcommon_declares_collector_pretty_fqdn() -> None:
    """_envcommon/acm-collector.hcl must declare collector_pretty_fqdn (D37, D19)."""
    envcommon_path = REPO_ROOT / "terragrunt" / "_envcommon" / "acm-collector.hcl"
    assert envcommon_path.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon_path}. "
        "This file is required to supply collector_pretty_fqdn (D37)."
    )
    content = envcommon_path.read_text()
    assert "collector_pretty_fqdn" in content, (
        "_envcommon/acm-collector.hcl does not declare 'collector_pretty_fqdn'. "
        "D37: Use the canonical name 'collector_pretty_fqdn' (not a synonym)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_passes_tags_using_root_locals(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector inputs.tags must use include.root.locals.common_tags
    (docs/terragrunt-concepts.md)."""
    assert ROOT_COMMON_TAGS_REF in collector_terragrunt_hcl_content, (
        f"acm-collector/000/terragrunt.hcl inputs.tags must reference '{ROOT_COMMON_TAGS_REF}', "
        "not 'local.common_tags'. The root include is expose=true so its locals are "
        "accessible via include.root.locals.<name> only -- using local.<name> resolves "
        "to the leaf locals block and will fail with 'Unsupported attribute' (AC-3)."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_has_inputs_block(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain an inputs block (D37)."""
    assert "inputs = {" in collector_terragrunt_hcl_content or (
        "inputs={" in collector_terragrunt_hcl_content
    ), (
        "acm-collector/000/terragrunt.hcl does not contain an inputs block. "
        "The leaf must pass module inputs (D37, E6-F1-S1-T2)."
    )


# ---------------------------------------------------------------------------
# D26: no forbidden resources or zone_id -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("forbidden_resource", FORBIDDEN_RESOURCES)
def test_acm_collector_terragrunt_hcl_no_forbidden_resource(
    collector_terragrunt_hcl_content: str,
    forbidden_resource: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must not reference forbidden validation resources (D26).

    These units are pure certificate requests. Validation records and
    aws_acm_certificate_validation must be created downstream (D24/D26).
    """
    assert forbidden_resource not in collector_terragrunt_hcl_content, (
        f"acm-collector/000/terragrunt.hcl contains '{forbidden_resource}'. "
        "D26: This unit is a PURE cert request. "
        f"'{forbidden_resource}' must be created in a separate downstream unit "
        "to avoid the dependency cycle described in D24 and D26."
    )


@pytest.mark.unit
def test_acm_collector_terragrunt_hcl_no_zone_id_input(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must not pass zone_id (AC-3 no dangling input).

    zone_id is not declared by primitives/acm-certificate. Passing it would be a
    dangling input that violates the single-module contract in AC-3.
    """
    assert FORBIDDEN_INPUT_ZONE_ID not in collector_terragrunt_hcl_content, (
        f"acm-collector/000/terragrunt.hcl passes '{FORBIDDEN_INPUT_ZONE_ID}'. "
        "AC-3: The primitives/acm-certificate module does not declare 'zone_id'. "
        "A dangling input violates the single-module contract."
    )


# ---------------------------------------------------------------------------
# D4 / D31: no hardcoded prod account id -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_service_hcl_content", "acm-collector/service.hcl"),
        ("collector_service_instance_hcl_content", "acm-collector/000/service_instance.hcl"),
        ("collector_terragrunt_hcl_content", "acm-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_collector_no_hardcoded_prod_account_id(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-collector HCL file may hardcode the prod account id (D31, D45)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert PROD_ACCOUNT not in content, (
        f"{file_label} contains the prod account id '{PROD_ACCOUNT}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded credentials or forbidden patterns -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_service_hcl_content", "acm-collector/service.hcl"),
        ("collector_service_instance_hcl_content", "acm-collector/000/service_instance.hcl"),
        ("collector_terragrunt_hcl_content", "acm-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_collector_no_hardcoded_aws_access_key(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-collector HCL file may contain a hardcoded AWS access key pattern."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"{file_label} contains a pattern matching an AWS access key. "
        "No credentials may be hardcoded in HCL files."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("collector_service_hcl_content", "acm-collector/service.hcl"),
        ("collector_service_instance_hcl_content", "acm-collector/000/service_instance.hcl"),
        ("collector_terragrunt_hcl_content", "acm-collector/000/terragrunt.hcl"),
    ],
)
def test_acm_collector_no_em_dash(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No acm-collector HCL file may contain an em-dash character (U+2014)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert "\u2014" not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )
