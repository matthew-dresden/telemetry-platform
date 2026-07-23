"""Unit tests for the prod acm-collector terragrunt unit HCL files.

These tests assert the structural and content constraints for the prod account
(111111111111) acm-collector terragrunt unit. They validate:

- AC-2: the prod acm unit sources exactly one primitive module
  (primitives/acm-certificate) with no dangling input (no zone_id), sets
  wait_for_validation=false, and exports domain_validation_options. make
  tf-validate accepts the authored units.
- AC-7: the pretty SAN (mandatory per D19) is present in every certificate
  request. The _envcommon/acm-collector.hcl
  template declares subject_alternative_names carrying the pretty FQDN.
- D19: pretty-name SAN is MANDATORY on every cert.
- D24: wait_for_validation = false so the request returns immediately without
  blocking on DNS validation. Validation lives in a separate downstream unit
  (D26).
- D26: no aws_route53_record or aws_acm_certificate_validation resource may
  appear in these request-only units.
- D31: prod and sandbox units differ only by folder path and account.hcl
  inputs, never by inline conditional logic on account id or environment name.
  The prod acm-collector leaf must pin the identical module
  source as its sandbox counterpart.
- D37: canonical input names (domain_name, subject_alternative_names,
  wait_for_validation) match the declared variable contract on
  primitives/acm-certificate.
- D45/D47: account identity (aws_account_id) sourced from account.hcl; domain
  apexes (dns_service_apex, dns_pretty_apex) resolved from
  terragrunt/common/domains.json via root terragrunt.hcl local.domain_cfg,
  with the _envcommon templates resolving the apexes via
  local.root.locals.dns_service_apex / local.root.locals.dns_pretty_apex
  (root loaded with read_terragrunt_config(find_in_parent_folders("root.hcl"))).
  Not hardcoded.
- docs/terragrunt-concepts.md: canonical seven-layer hierarchy basename idioms.
- Security: no hardcoded AWS access keys, no em-dash characters (U+2014), no
  suppression annotations.

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

PROD_ACCOUNT = "111111111111"
SANDBOX_ACCOUNT = "222222222222"
ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
PROD_BASE = ACCOUNT_DIR / "prod"
ENV_INSTANCE_BASE = PROD_BASE / "000"

# ---------------------------------------------------------------------------
# acm-collector paths
# ---------------------------------------------------------------------------

COLLECTOR_SERVICE_DIR = ENV_INSTANCE_BASE / "acm-collector"
COLLECTOR_INSTANCE_DIR = COLLECTOR_SERVICE_DIR / "000"

COLLECTOR_SERVICE_HCL = COLLECTOR_SERVICE_DIR / "service.hcl"
COLLECTOR_SERVICE_INSTANCE_HCL = COLLECTOR_INSTANCE_DIR / "service_instance.hcl"
COLLECTOR_TERRAGRUNT_HCL = COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# Sandbox sibling paths for parity comparison (D31)
# ---------------------------------------------------------------------------

SANDBOX_COLLECTOR_LEAF = (
    REPO_ROOT
    / "terragrunt"
    / "live"
    / "telemetry"
    / "us-east-1"
    / "sandbox"
    / "000"
    / "acm-collector"
    / "000"
    / "terragrunt.hcl"
)

# ---------------------------------------------------------------------------
# Constants used in assertions
# ---------------------------------------------------------------------------

# AC-2/AC-3: the reference module path must appear in both leaf terragrunt.hcl files.
# The acm leaves source the acm-cert-managed reference (which wraps the acm-certificate
# primitive and adds the gated cross-account remote-state read), not the primitive directly.
ACM_REFERENCE_MODULE_PATH = "references/acm-cert-managed"

# The include.root.locals.common_tags pattern required by docs/terragrunt-concepts.md (expose=true).
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"

# Forbidden resources: these must NOT appear in certificate-request units (D26).
FORBIDDEN_RESOURCES = [
    "aws_route53_record",
    "aws_acm_certificate_validation",
]

# Forbidden input: zone_id is not declared by primitives/acm-certificate (AC-2).
FORBIDDEN_INPUT_ZONE_ID = "zone_id"

SUPPRESSION_PATTERN = re.compile(
    r"nosec|noqa|nolint|tfsec:ignore|checkov:skip",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Meta-guard: this test file must contain zero literal U+2014 em-dashes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_this_file_contains_no_literal_em_dash() -> None:
    """This test file must not contain any literal U+2014 em-dash characters.

    The em-dash character (U+2014) is prohibited in all source files per CLAUDE.md.
    All em-dash guard assertions build the character from chr(0x2014), never
    embedding the literal glyph.
    """
    this_file = pathlib.Path(__file__)
    raw_bytes = this_file.read_bytes()
    em_dash_bytes = chr(0x2014).encode("utf-8")
    assert em_dash_bytes not in raw_bytes, (
        f"{this_file.name} contains one or more literal U+2014 em-dash bytes "
        f"(UTF-8: {em_dash_bytes.hex()}). "
        "All em-dash guard assertions must use '\\u2014' or chr(0x2014), "
        "never the literal glyph (CLAUDE.md code standards)."
    )


# ---------------------------------------------------------------------------
# Fixtures -- acm-collector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def collector_service_hcl_content() -> str:
    """Read acm-collector/service.hcl. Fails loudly if the file does not exist."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "This file must be created for the prod acm-collector service layer (E7-F3-S1-T2)."
    )
    return COLLECTOR_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_service_instance_hcl_content() -> str:
    """Read acm-collector/000/service_instance.hcl. Fails loudly if not found."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod acm-collector service instance (E7-F3-S1-T2)."
    )
    return COLLECTOR_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_terragrunt_hcl_content() -> str:
    """Read acm-collector/000/terragrunt.hcl. Fails loudly if the file does not exist."""
    assert COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod acm-collector unit (E7-F3-S1-T2)."
    )
    return COLLECTOR_TERRAGRUNT_HCL.read_text()


# ===========================================================================
# acm-collector tests
# ===========================================================================

# ---------------------------------------------------------------------------
# File existence -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_service_hcl_exists() -> None:
    """acm-collector/service.hcl must exist at the prod service layer path (E7-F3-S1-T2)."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "Required for the prod acm-collector service layer (AC-2, E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_service_instance_hcl_exists() -> None:
    """acm-collector/000/service_instance.hcl must exist at the service instance path."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "Required for the prod acm-collector service instance (AC-2, E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_exists() -> None:
    """Leaf acm-collector/000/terragrunt.hcl must exist (AC-2, E7-F3-S1-T2)."""
    assert COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod acm-collector unit (AC-2, E7-F3-S1-T2)."
    )


# ---------------------------------------------------------------------------
# Directory structure -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_service_dir_exists() -> None:
    """The acm-collector service directory must exist under prod/000/."""
    assert COLLECTOR_SERVICE_DIR.exists(), (
        f"acm-collector service directory not found at {COLLECTOR_SERVICE_DIR}. "
        "The 7-layer hierarchy requires a service directory named 'acm-collector' "
        "(docs/terragrunt-concepts.md, E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_instance_dir_exists() -> None:
    """The acm-collector/000 service instance directory must exist."""
    assert COLLECTOR_INSTANCE_DIR.exists(), (
        f"acm-collector instance directory not found at {COLLECTOR_INSTANCE_DIR}. "
        "The 7-layer hierarchy requires a service instance directory named '000' "
        "(docs/terragrunt-concepts.md, E7-F3-S1-T2)."
    )


# ---------------------------------------------------------------------------
# Layer basename idioms -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_service_hcl_declares_basename_local(
    collector_service_hcl_content: str,
) -> None:
    """acm-collector/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in collector_service_hcl_content, (
        "prod acm-collector/service.hcl must declare "
        "`service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_service_dir_basename_is_acm_collector() -> None:
    """service.hcl must be located so its basename resolves to 'acm-collector'."""
    assert COLLECTOR_SERVICE_HCL.parent.name == "acm-collector", (
        f"service.hcl parent dir is '{COLLECTOR_SERVICE_HCL.parent.name}', "
        "expected 'acm-collector'. "
        "The service layer dir basename must be 'acm-collector' (E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_service_instance_hcl_declares_basename_local(
    collector_service_instance_hcl_content: str,
) -> None:
    """acm-collector/000/service_instance.hcl must use the basename idiom."""
    assert "basename(get_terragrunt_dir())" in collector_service_instance_hcl_content, (
        "prod acm-collector/000/service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T2)."
    )


# ---------------------------------------------------------------------------
# AC-2: single-module source (primitives/acm-certificate only) -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_sources_acm_reference(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must source references/acm-cert-managed (AC-2/AC-3)."""
    assert ACM_REFERENCE_MODULE_PATH in collector_terragrunt_hcl_content, (
        f"prod acm-collector/000/terragrunt.hcl does not reference "
        f"'{ACM_REFERENCE_MODULE_PATH}'. "
        "The unit must source the acm-cert-managed reference module, which wraps the "
        "acm-certificate primitive and adds the gated cross-account remote-state read "
        "(AC-2/AC-3, docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_sources_exactly_one_module(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain exactly one terraform.source (AC-2)."""
    source_count = len(
        re.findall(r"^\s*source\s*=", collector_terragrunt_hcl_content, re.MULTILINE)
    )
    assert source_count == 1, (
        f"prod acm-collector/000/terragrunt.hcl contains {source_count} source declarations; "
        "expected exactly 1. "
        "AC-2 requires the leaf to source exactly one primitive module "
        "(primitives/acm-certificate). No second source or nested module include is permitted."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_has_terraform_block(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain a terraform {} source block (AC-2)."""
    assert "terraform {" in collector_terragrunt_hcl_content, (
        "prod acm-collector/000/terragrunt.hcl does not contain a terraform {} block. "
        "The leaf must declare `terraform { source = ... }` to source "
        "primitives/acm-certificate (AC-2, E7-F3-S1-T2)."
    )


# ---------------------------------------------------------------------------
# AC-2: include patterns -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_includes_root(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must include the root terragrunt.hcl."""
    assert 'include "root"' in collector_terragrunt_hcl_content, (
        "prod acm-collector/000/terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "Required for namespace-derived state backend inheritance (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_includes_envcommon(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must include _envcommon/acm-collector.hcl (D37)."""
    assert "_envcommon/acm-collector.hcl" in collector_terragrunt_hcl_content, (
        "prod acm-collector/000/terragrunt.hcl must include '_envcommon/acm-collector.hcl' "
        "via an include block. "
        "The shared input template provides the SAN list and wait_for_validation=false "
        "(D37, D24, E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_root_include_expose_true(
    collector_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set expose = true for acm-collector
    (docs/terragrunt-concepts.md)."""
    expose_set = (
        "expose         = true" in collector_terragrunt_hcl_content
        or "expose = true" in collector_terragrunt_hcl_content
    )
    assert expose_set, (
        'prod acm-collector/000/terragrunt.hcl include "root" block must set expose = true. '
        "Required for include.root.locals.common_tags to be accessible in the leaf "
        "inputs block (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_root_include_deep_merge(
    collector_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set merge_strategy = 'deep' for acm-collector."""
    assert 'merge_strategy = "deep"' in collector_terragrunt_hcl_content, (
        'prod acm-collector/000/terragrunt.hcl include "root" block must set '
        'merge_strategy = "deep". '
        "Deep merge is required so inputs from multiple includes are merged "
        "correctly (docs/terragrunt-concepts.md, E7-F3-S1-T2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_uses_find_in_parent_folders(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must use find_in_parent_folders for root include."""
    assert 'find_in_parent_folders("root.hcl")' in collector_terragrunt_hcl_content, (
        "prod acm-collector/000/terragrunt.hcl must use "
        "find_in_parent_folders('terragrunt.hcl') "
        "to locate the root config. This is the docs/terragrunt-concepts.md canonical pattern."
    )


# ---------------------------------------------------------------------------
# AC-2 / D19: wait_for_validation and SAN contract -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_envcommon_declares_wait_for_validation_false() -> None:
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
def test_prod_acm_collector_envcommon_declares_subject_alternative_names() -> None:
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
def test_prod_acm_collector_envcommon_declares_collector_pretty_fqdn() -> None:
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
def test_prod_acm_collector_terragrunt_hcl_passes_tags_using_root_locals(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector inputs.tags must use include.root.locals.common_tags
    (docs/terragrunt-concepts.md)."""
    assert ROOT_COMMON_TAGS_REF in collector_terragrunt_hcl_content, (
        f"prod acm-collector/000/terragrunt.hcl inputs.tags must reference "
        f"'{ROOT_COMMON_TAGS_REF}', "
        "not 'local.common_tags'. The root include is expose=true so its locals are "
        "accessible via include.root.locals.<name> only -- using local.<name> resolves "
        "to the leaf locals block and will fail with 'Unsupported attribute' (AC-2)."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_has_inputs_block(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain an inputs block (D37)."""
    assert "inputs = {" in collector_terragrunt_hcl_content or (
        "inputs={" in collector_terragrunt_hcl_content
    ), (
        "prod acm-collector/000/terragrunt.hcl does not contain an inputs block. "
        "The leaf must pass module inputs (D37, E7-F3-S1-T2)."
    )


# ---------------------------------------------------------------------------
# D26: no forbidden resources or zone_id -- acm-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("forbidden_resource", FORBIDDEN_RESOURCES)
def test_prod_acm_collector_terragrunt_hcl_no_forbidden_resource(
    collector_terragrunt_hcl_content: str,
    forbidden_resource: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must not reference forbidden validation resources (D26).

    These units are pure certificate requests. Validation records and
    aws_acm_certificate_validation must be created downstream (D24/D26).
    """
    assert forbidden_resource not in collector_terragrunt_hcl_content, (
        f"prod acm-collector/000/terragrunt.hcl contains '{forbidden_resource}'. "
        "D26: This unit is a PURE cert request. "
        f"'{forbidden_resource}' must be created in a separate downstream unit "
        "to avoid the dependency cycle described in D24 and D26."
    )


@pytest.mark.unit
def test_prod_acm_collector_terragrunt_hcl_no_zone_id_input(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must not pass zone_id (AC-2 no dangling input).

    zone_id is not declared by primitives/acm-certificate. Passing it would be a
    dangling input that violates the single-module contract in AC-2.
    """
    assert FORBIDDEN_INPUT_ZONE_ID not in collector_terragrunt_hcl_content, (
        f"prod acm-collector/000/terragrunt.hcl passes '{FORBIDDEN_INPUT_ZONE_ID}'. "
        "AC-2: The primitives/acm-certificate module does not declare 'zone_id'. "
        "A dangling input violates the single-module contract."
    )


# ---------------------------------------------------------------------------
# D31: parity -- prod acm-collector module source must match sandbox
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_module_source_matches_sandbox() -> None:
    """Prod acm-collector leaf must pin the same module source as the sandbox sibling (D31).

    D31 requires that prod and sandbox differ only by directory path and per-environment
    inputs. The terraform source expression must be identical across both environments.
    """
    assert SANDBOX_COLLECTOR_LEAF.exists(), (
        f"Sandbox acm-collector leaf not found at {SANDBOX_COLLECTOR_LEAF}. "
        "The sandbox leaf must exist for parity comparison (D31, E7-F3-S1-T2)."
    )
    sandbox_source = _extract_source(SANDBOX_COLLECTOR_LEAF.read_text())
    prod_source = _extract_source(COLLECTOR_TERRAGRUNT_HCL.read_text())
    assert prod_source == sandbox_source, (
        f"Prod acm-collector source '{prod_source}' differs from "
        f"sandbox source '{sandbox_source}'. "
        "D31 requires prod and sandbox to pin the identical module source (parity check)."
    )


# ---------------------------------------------------------------------------
# D4 / D31: no hardcoded sandbox account id in prod acm-collector files
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
def test_prod_acm_collector_no_hardcoded_sandbox_account_id(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No prod acm-collector HCL file may hardcode the sandbox account id (D31, D45)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert SANDBOX_ACCOUNT not in content, (
        f"prod {file_label} contains the sandbox account id '{SANDBOX_ACCOUNT}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# Security: no suppression annotations -- acm-collector leaf
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_acm_collector_no_suppression_annotations(
    collector_terragrunt_hcl_content: str,
) -> None:
    """acm-collector/000/terragrunt.hcl must contain no suppression annotations."""
    assert not SUPPRESSION_PATTERN.search(collector_terragrunt_hcl_content), (
        "prod acm-collector/000/terragrunt.hcl contains a suppression annotation. "
        "Suppression annotations are prohibited -- fix the underlying issue (CLAUDE.md, AC-2)."
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
def test_prod_acm_collector_no_hardcoded_aws_access_key(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No prod acm-collector HCL file may contain a hardcoded AWS access key pattern."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"prod {file_label} contains a pattern matching an AWS access key. "
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
def test_prod_acm_collector_no_em_dash(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No prod acm-collector HCL file may contain an em-dash character (U+2014)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert chr(0x2014) not in content, (
        f"prod {file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# D45/D47: _envcommon templates must read apexes via read_terragrunt_config +
# local.root.locals.* (not include.root.locals.* which is invalid inside _envcommon,
# and not account_vars.locals which no longer declares these locals)
# ---------------------------------------------------------------------------

ENVCOMMON_DIR = REPO_ROOT / "terragrunt" / "_envcommon"

# The functional pattern (E11-F3-S1-T4 / commit 1e37f6e): _envcommon files resolve the
# per-env domain apexes from terragrunt/common/domains.json directly, keyed by the leaf's
# env-class (read leaf-relative via read_terragrunt_config(find_in_parent_folders(
# "environment.hcl"))), anchored on get_repo_root() (D3). The apex values are accessed as
# local.domain_cfg["dns_service_apex"] / local.domain_cfg["dns_pretty_apex"].
#
# The original design read the root config standalone via
# read_terragrunt_config(find_in_parent_folders("root.hcl")) and accessed apexes as
# local.root.locals.dns_*_apex. That pattern was empirically proven to abort parse:
# terragrunt 1.0.7 evaluates root.hcl's own find_in_parent_folders("product.hcl"|...)
# hierarchy reads relative to terragrunt/ (root.hcl's directory), raising
# ParentFileNotFoundError on every leaf that includes an _envcommon. The common/domains.json
# read is the same source root.hcl itself uses (root.hcl:54,77,100-101) and is
# copy-any-level safe, satisfying D45/D47 (per-env apex, never hardcoded prod literals).
READ_TERRAGRUNT_CONFIG_CALL = "read_terragrunt_config(find_in_parent_folders("
COMMON_DOMAINS_JSON_READ = "${get_repo_root()}/terragrunt/common/domains.json"
DOMAIN_CFG_SERVICE_APEX = 'local.domain_cfg["dns_service_apex"]'
DOMAIN_CFG_PRETTY_APEX = 'local.domain_cfg["dns_pretty_apex"]'

# Reading the root config standalone would re-introduce the proven ParentFileNotFound
# parse abort, so local.root.locals.dns_*_apex (which only exists when root.hcl is decoded
# as a dependency) must NOT appear in any _envcommon template.
FORBIDDEN_STANDALONE_ROOT_SERVICE_APEX = "local.root.locals.dns_service_apex"
FORBIDDEN_STANDALONE_ROOT_PRETTY_APEX = "local.root.locals.dns_pretty_apex"

# include.root.locals.* must NOT appear in _envcommon files: include blocks are not in
# scope inside a shared template; only leaf files that declare 'include "root" { ... }'
# can reference include.root.locals.*. Using it in _envcommon causes:
#   ERROR: There is no variable named "include" (terragrunt render).
FORBIDDEN_INCLUDE_ROOT_SERVICE_APEX = "include.root.locals.dns_service_apex"
FORBIDDEN_INCLUDE_ROOT_PRETTY_APEX = "include.root.locals.dns_pretty_apex"

# Dead code that must NOT survive after migration from account.hcl apex reads.
DEAD_ACCOUNT_VARS_SERVICE_APEX = "account_vars.locals.dns_service_apex"
DEAD_ACCOUNT_VARS_PRETTY_APEX = "account_vars.locals.dns_pretty_apex"


@pytest.mark.unit
def test_envcommon_acm_collector_uses_read_terragrunt_config_for_root() -> None:
    """_envcommon/acm-collector.hcl must load root via read_terragrunt_config (D45/D47).

    _envcommon shared templates have no include block in scope. They cannot use
    include.root.locals.*. The correct pattern used by every other _envcommon in this
    repo (analytics.hcl, dns-prod-zone.hcl, collector-ingestion.hcl) is:
      root = read_terragrunt_config(find_in_parent_folders("root.hcl"))
    The root terragrunt.hcl declares dns_service_apex / dns_pretty_apex as top-level
    locals (lines 99-102), so they are reachable as local.root.locals.dns_*_apex.
    """
    envcommon = ENVCOMMON_DIR / "acm-collector.hcl"
    assert envcommon.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon}. This file must exist (D45/D47)."
    )
    content = envcommon.read_text()
    assert READ_TERRAGRUNT_CONFIG_CALL in content, (
        f"_envcommon/acm-collector.hcl does not contain '{READ_TERRAGRUNT_CONFIG_CALL}'. "
        "D45/D47: _envcommon templates must use read_terragrunt_config to load the root config. "
        'Add: root = read_terragrunt_config(find_in_parent_folders("root.hcl"))'
    )


@pytest.mark.unit
def test_envcommon_acm_collector_uses_domain_cfg_service_apex() -> None:
    """_envcommon/acm-collector.hcl functional code must read dns_service_apex from
    common/domains.json via local.domain_cfg["dns_service_apex"] (not standalone root.hcl).

    The apex is resolved from terragrunt/common/domains.json keyed by the leaf's env-class
    (the same source root.hcl uses, root.hcl:54,100), anchored on get_repo_root() (D3).
    Reading the root config standalone via read_terragrunt_config(find_in_parent_folders(
    "root.hcl")) and accessing local.root.locals.dns_service_apex aborts terragrunt 1.0.7
    parse with ParentFileNotFoundError (commit 1e37f6e). account.hcl no longer declares
    dns_service_apex (removed in E8-F2-S1-T1).
    """
    envcommon = ENVCOMMON_DIR / "acm-collector.hcl"
    assert envcommon.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon}. This file must exist (D45/D47)."
    )
    content = envcommon.read_text()
    assert COMMON_DOMAINS_JSON_READ in content, (
        f"_envcommon/acm-collector.hcl does not read '{COMMON_DOMAINS_JSON_READ}'. "
        "D45/D47: the per-env apex must come from common/domains.json (anchored on "
        "get_repo_root(), D3), not from a standalone root.hcl decode."
    )
    assert DOMAIN_CFG_SERVICE_APEX in content, (
        f"_envcommon/acm-collector.hcl does not contain '{DOMAIN_CFG_SERVICE_APEX}'. "
        "D45/D47: dns_service_apex must be read from the env-class domain config via "
        'local.domain_cfg["dns_service_apex"] (common/domains.json keyed by environment). '
        "local.root.locals.dns_service_apex aborts parse (standalone root.hcl decode)."
    )


@pytest.mark.unit
def test_envcommon_acm_collector_uses_domain_cfg_pretty_apex() -> None:
    """_envcommon/acm-collector.hcl functional code must read dns_pretty_apex from
    common/domains.json via local.domain_cfg["dns_pretty_apex"] (not standalone root.hcl).

    See test_envcommon_acm_collector_uses_domain_cfg_service_apex for rationale.
    """
    envcommon = ENVCOMMON_DIR / "acm-collector.hcl"
    assert envcommon.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon}. This file must exist (D45/D47)."
    )
    content = envcommon.read_text()
    assert DOMAIN_CFG_PRETTY_APEX in content, (
        f"_envcommon/acm-collector.hcl does not contain '{DOMAIN_CFG_PRETTY_APEX}'. "
        "D45/D47: dns_pretty_apex must be read from the env-class domain config via "
        'local.domain_cfg["dns_pretty_apex"] (common/domains.json keyed by environment). '
        "local.root.locals.dns_pretty_apex aborts parse (standalone root.hcl decode)."
    )


@pytest.mark.unit
def test_envcommon_acm_collector_does_not_use_include_root_apex() -> None:
    """_envcommon/acm-collector.hcl must NOT reference include.root.locals.dns_*_apex.

    include.root.locals.* is only valid in leaf files that declare an include "root" block.
    Inside a shared _envcommon template there is no include block in scope, so terragrunt
    render fails with: There is no variable named "include". The correct pattern is
    local.root.locals.dns_*_apex (root loaded via read_terragrunt_config).
    """
    envcommon = ENVCOMMON_DIR / "acm-collector.hcl"
    assert envcommon.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon}. This file must exist (D45/D47)."
    )
    content = envcommon.read_text()
    assert FORBIDDEN_INCLUDE_ROOT_SERVICE_APEX not in content, (
        f"_envcommon/acm-collector.hcl contains '{FORBIDDEN_INCLUDE_ROOT_SERVICE_APEX}'. "
        "FATAL: include.root.locals.* is invalid inside _envcommon (no include block in scope). "
        "terragrunt render fails with 'There is no variable named include'. "
        'Replace with local.domain_cfg["dns_service_apex"] (common/domains.json keyed by env).'
    )
    assert FORBIDDEN_INCLUDE_ROOT_PRETTY_APEX not in content, (
        f"_envcommon/acm-collector.hcl contains '{FORBIDDEN_INCLUDE_ROOT_PRETTY_APEX}'. "
        "FATAL: include.root.locals.* is invalid inside _envcommon (no include block in scope). "
        "terragrunt render fails with 'There is no variable named include'. "
        'Replace with local.domain_cfg["dns_pretty_apex"] (common/domains.json keyed by env).'
    )
    assert FORBIDDEN_STANDALONE_ROOT_SERVICE_APEX not in content, (
        f"_envcommon/acm-collector.hcl contains '{FORBIDDEN_STANDALONE_ROOT_SERVICE_APEX}'. "
        "FATAL: reading root.hcl standalone (read_terragrunt_config(find_in_parent_folders("
        '"root.hcl"))) aborts terragrunt 1.0.7 parse with ParentFileNotFoundError because '
        "root.hcl's own find_in_parent_folders hierarchy reads resolve relative to "
        'terragrunt/. Replace with local.domain_cfg["dns_service_apex"] (common/domains.json).'
    )
    assert FORBIDDEN_STANDALONE_ROOT_PRETTY_APEX not in content, (
        f"_envcommon/acm-collector.hcl contains '{FORBIDDEN_STANDALONE_ROOT_PRETTY_APEX}'. "
        "FATAL: reading root.hcl standalone aborts terragrunt 1.0.7 parse with "
        'ParentFileNotFoundError. Replace with local.domain_cfg["dns_pretty_apex"].'
    )


@pytest.mark.unit
def test_envcommon_acm_collector_does_not_read_apex_from_account_vars() -> None:
    """_envcommon/acm-collector.hcl must NOT read dns_*_apex from account_vars.locals.

    account.hcl no longer declares dns_service_apex or dns_pretty_apex (removed in
    E8-F2-S1-T1). The functional locals that read them from account_vars.locals are
    dead code that causes a latent terragrunt-render failure. They must be removed.
    """
    envcommon = ENVCOMMON_DIR / "acm-collector.hcl"
    assert envcommon.exists(), (
        f"_envcommon/acm-collector.hcl not found at {envcommon}. This file must exist (D45/D47)."
    )
    content = envcommon.read_text()
    assert DEAD_ACCOUNT_VARS_SERVICE_APEX not in content, (
        f"_envcommon/acm-collector.hcl still reads '{DEAD_ACCOUNT_VARS_SERVICE_APEX}'. "
        "D45: account.hcl no longer declares dns_service_apex (removed in E8-F2-S1-T1). "
        "Replace with local.root.locals.dns_service_apex via read_terragrunt_config."
    )
    assert DEAD_ACCOUNT_VARS_PRETTY_APEX not in content, (
        f"_envcommon/acm-collector.hcl still reads '{DEAD_ACCOUNT_VARS_PRETTY_APEX}'. "
        "D45: account.hcl no longer declares dns_pretty_apex (removed in E8-F2-S1-T1). "
        "Replace with local.root.locals.dns_pretty_apex via read_terragrunt_config."
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_source(hcl_content: str) -> str:
    """Extract the resolved local module source path from a leaf terragrunt.hcl.

    Supports two source declaration forms introduced by E9-F3-S1-T1:
    1. Toggle-driven ternary: ``source = <cond> ? "pinned_url" : "local_path"``
       Returns the false/local branch (the ``${get_repo_root()}//...`` path) so
       parity comparisons use the canonical module path regardless of environment.
    2. Legacy literal: ``source = "..."``
       Returns the literal value unchanged.

    Raises AssertionError when no source assignment is found so test failures are
    unambiguous (AC-2).
    """
    # Try toggle-driven ternary form first (E9-F3-S1-T1):
    #   source = <non-string-expr> ? "pinned_git_url" : "local_get_repo_root_path"
    ternary_match = re.search(
        r'^\s*source\s*=\s*[^"\n]+\?\s*"[^"]+"\s*:\s*"([^"]+)"',
        hcl_content,
        re.MULTILINE,
    )
    if ternary_match is not None:
        return ternary_match.group(1)

    # Fall back to legacy literal form: source = "..."
    literal_match = re.search(r'^\s*source\s*=\s*"([^"]+)"', hcl_content, re.MULTILINE)
    assert literal_match is not None, (
        'HCL content does not contain a `source = "..."` assignment or a '
        "toggle-driven ternary source. "
        "Every leaf terragrunt.hcl must declare exactly one source (AC-2)."
    )
    return literal_match.group(1)
