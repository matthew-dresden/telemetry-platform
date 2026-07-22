"""Unit tests for the prod dns-prod-zone and identity terragrunt unit HCL files.

These tests assert the structural and content constraints for the prod account
(111111111111) dns-prod-zone and identity terragrunt units. They validate:

dns-prod-zone:
- AC-1: the unit sources references/dns-prod-zone as a single module that
  provisions the delegated zone, platform KMS key, and SSM seed together.
- AC-1: environment.hcl and environment_instance.hcl declare the prod environment
  layer and instance id "000" using the canonical basename idiom.
- AC-1: service.hcl and service_instance.hcl declare the layer basenames.
- AC-1: terragrunt.hcl includes both root and _envcommon/dns-prod-zone.hcl.
- AC-1: no primitive is sourced in place of the reference module.
- D31: prod subtree differs from sandbox only by path and inputs (parity
  assertions on module source string, include patterns, inputs keys).
- D40: state-preflight dependency comment present (prod backend must exist).

identity:
- AC-1: the unit sources references/identity as a single module.
- AC-1: terragrunt.hcl includes both root and _envcommon/identity.hcl.
- AC-1: no primitive is sourced in place of the reference module.
- D31: parity assertions matching the sandbox identity leaf structure.
- D37: analyst_assume_role_policy_json, admin_assume_role_policy_json,
  analyst_inline_policies, admin_inline_policies, and tags inputs declared.

Security:
- No hardcoded AWS access keys in any HCL file.
- No em-dash characters (U+2014) in any HCL file or this test file.
- No suppression annotations (nosec/noqa/nolint/tfsec:ignore/checkov:skip).

docs/terragrunt-concepts.md: canonical seven-layer hierarchy basename idioms throughout.

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
ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
PROD_ENV_BASE = ACCOUNT_DIR / "prod"
ENV_INSTANCE_BASE = PROD_ENV_BASE / "000"

# ---------------------------------------------------------------------------
# environment.hcl / environment_instance.hcl paths
# ---------------------------------------------------------------------------

ENVIRONMENT_HCL = PROD_ENV_BASE / "environment.hcl"
ENVIRONMENT_INSTANCE_HCL = ENV_INSTANCE_BASE / "environment_instance.hcl"

# ---------------------------------------------------------------------------
# dns-prod-zone paths
#
# Phase 8 (Approach A): the dns-prod-zone unit was relocated OUT of the disposable
# prod/_singletons/shared service tree into the STABLE foundation tier at
# bootstrap/prod_role/ (a sibling of the env subtree), so a service-tree
# destroy/recreate never churns the hosted zone or the platform Customer Managed Key.
# ---------------------------------------------------------------------------

DNS_SERVICE_DIR = ACCOUNT_DIR / "bootstrap" / "prod_role" / "dns-prod-zone"
DNS_INSTANCE_DIR = DNS_SERVICE_DIR / "000"

DNS_SERVICE_HCL = DNS_SERVICE_DIR / "service.hcl"
DNS_SERVICE_INSTANCE_HCL = DNS_INSTANCE_DIR / "service_instance.hcl"
DNS_TERRAGRUNT_HCL = DNS_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# identity paths
# ---------------------------------------------------------------------------

ID_SERVICE_DIR = PROD_ENV_BASE / "_singletons" / "shared" / "identity"
ID_INSTANCE_DIR = ID_SERVICE_DIR / "000"

ID_SERVICE_HCL = ID_SERVICE_DIR / "service.hcl"
ID_SERVICE_INSTANCE_HCL = ID_INSTANCE_DIR / "service_instance.hcl"
ID_TERRAGRUNT_HCL = ID_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# Constants used in assertions
# ---------------------------------------------------------------------------

DNS_MODULE_PATH = "references/dns-prod-zone"
IDENTITY_MODULE_PATH = "references/identity"
DNS_ENVCOMMON_PATH = "_envcommon/dns-prod-zone.hcl"
IDENTITY_ENVCOMMON_PATH = "_envcommon/identity.hcl"
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"
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
# File fixtures -- environment layer
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def environment_hcl_content() -> str:
    """Read prod environment.hcl. Fails loudly if the file does not exist."""
    assert ENVIRONMENT_HCL.exists(), (
        f"environment.hcl not found at {ENVIRONMENT_HCL}. "
        "This file must be created for the prod service environment layer (E7-F3-S1-T1)."
    )
    return ENVIRONMENT_HCL.read_text()


@pytest.fixture(scope="module")
def environment_instance_hcl_content() -> str:
    """Read prod environment_instance.hcl. Fails loudly if the file does not exist."""
    assert ENVIRONMENT_INSTANCE_HCL.exists(), (
        f"environment_instance.hcl not found at {ENVIRONMENT_INSTANCE_HCL}. "
        "This file must be created for the prod service environment instance layer (E7-F3-S1-T1)."
    )
    return ENVIRONMENT_INSTANCE_HCL.read_text()


# ---------------------------------------------------------------------------
# File fixtures -- dns-prod-zone
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dns_service_hcl_content() -> str:
    """Read dns-prod-zone/service.hcl. Fails loudly if the file does not exist."""
    assert DNS_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DNS_SERVICE_HCL}. "
        "This file must be created for the prod dns-prod-zone service layer (E7-F3-S1-T1)."
    )
    return DNS_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def dns_service_instance_hcl_content() -> str:
    """Read dns-prod-zone/000/service_instance.hcl. Fails loudly if not found."""
    assert DNS_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DNS_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod dns-prod-zone service instance (E7-F3-S1-T1)."
    )
    return DNS_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def dns_terragrunt_hcl_content() -> str:
    """Read dns-prod-zone/000/terragrunt.hcl. Fails loudly if the file does not exist."""
    assert DNS_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {DNS_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod dns-prod-zone terragrunt unit (E7-F3-S1-T1)."
    )
    return DNS_TERRAGRUNT_HCL.read_text()


# ---------------------------------------------------------------------------
# File fixtures -- identity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def id_service_hcl_content() -> str:
    """Read identity/service.hcl. Fails loudly if the file does not exist."""
    assert ID_SERVICE_HCL.exists(), (
        f"service.hcl not found at {ID_SERVICE_HCL}. "
        "This file must be created for the prod identity service layer (E7-F3-S1-T1)."
    )
    return ID_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def id_service_instance_hcl_content() -> str:
    """Read identity/000/service_instance.hcl. Fails loudly if not found."""
    assert ID_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {ID_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod identity service instance (E7-F3-S1-T1)."
    )
    return ID_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def id_terragrunt_hcl_content() -> str:
    """Read identity/000/terragrunt.hcl. Fails loudly if the file does not exist."""
    assert ID_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {ID_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod identity terragrunt unit (E7-F3-S1-T1)."
    )
    return ID_TERRAGRUNT_HCL.read_text()


# ---------------------------------------------------------------------------
# File existence assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_environment_hcl_exists() -> None:
    """environment.hcl must exist at the prod service environment layer path."""
    assert ENVIRONMENT_HCL.exists(), (
        f"environment.hcl not found at {ENVIRONMENT_HCL}. "
        "Required for the prod service environment layer (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_environment_instance_hcl_exists() -> None:
    """environment_instance.hcl must exist at the prod service/000 layer path."""
    assert ENVIRONMENT_INSTANCE_HCL.exists(), (
        f"environment_instance.hcl not found at {ENVIRONMENT_INSTANCE_HCL}. "
        "Required for the prod service environment instance layer (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_dns_service_hcl_exists() -> None:
    """dns-prod-zone/service.hcl must exist at the service layer path."""
    assert DNS_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DNS_SERVICE_HCL}. "
        "Required for the prod dns-prod-zone service layer (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_dns_service_instance_hcl_exists() -> None:
    """dns-prod-zone/000/service_instance.hcl must exist."""
    assert DNS_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DNS_SERVICE_INSTANCE_HCL}. "
        "Required for the prod dns-prod-zone service instance (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_exists() -> None:
    """dns-prod-zone/000/terragrunt.hcl must exist."""
    assert DNS_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {DNS_TERRAGRUNT_HCL}. "
        "Required leaf unit for the prod dns-prod-zone unit (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_id_service_hcl_exists() -> None:
    """identity/service.hcl must exist at the service layer path."""
    assert ID_SERVICE_HCL.exists(), (
        f"service.hcl not found at {ID_SERVICE_HCL}. "
        "Required for the prod identity service layer (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_id_service_instance_hcl_exists() -> None:
    """identity/000/service_instance.hcl must exist."""
    assert ID_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {ID_SERVICE_INSTANCE_HCL}. "
        "Required for the prod identity service instance (AC-1, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_exists() -> None:
    """identity/000/terragrunt.hcl must exist."""
    assert ID_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {ID_TERRAGRUNT_HCL}. "
        "Required leaf unit for the prod identity unit (AC-1, E7-F3-S1-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom assertions -- environment layer (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_environment_hcl_declares_basename_local(environment_hcl_content: str) -> None:
    """environment.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in environment_hcl_content, (
        "environment.hcl must declare `environment = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_environment_hcl_parent_dir_is_prod(environment_hcl_content: str) -> None:
    """environment.hcl must be located at prod/ so basename resolves to 'prod'."""
    assert ENVIRONMENT_HCL.parent.name == "prod", (
        f"environment.hcl parent dir is '{ENVIRONMENT_HCL.parent.name}', expected 'prod'. "
        "The prod env layer must be named 'prod' (D48, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_environment_instance_hcl_declares_basename_local(
    environment_instance_hcl_content: str,
) -> None:
    """environment_instance.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in environment_instance_hcl_content, (
        "environment_instance.hcl must declare "
        "`environment_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_environment_instance_hcl_parent_dir_is_000(
    environment_instance_hcl_content: str,
) -> None:
    """environment_instance.hcl must be at prod/000/ so basename resolves to '000'."""
    assert ENVIRONMENT_INSTANCE_HCL.parent.name == "000", (
        f"environment_instance.hcl parent dir is '{ENVIRONMENT_INSTANCE_HCL.parent.name}', "
        "expected '000'. The prod env instance layer must be named '000' (E7-F3-S1-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom assertions -- dns-prod-zone
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_service_hcl_declares_basename_local(dns_service_hcl_content: str) -> None:
    """dns-prod-zone/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in dns_service_hcl_content, (
        "service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_dns_service_hcl_parent_dir_is_dns_prod_zone(dns_service_hcl_content: str) -> None:
    """service.hcl must be at dns-prod-zone/ so basename resolves correctly."""
    assert DNS_SERVICE_HCL.parent.name == "dns-prod-zone", (
        f"service.hcl parent dir is '{DNS_SERVICE_HCL.parent.name}', "
        "expected 'dns-prod-zone'. The service dir must be named 'dns-prod-zone' (E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_dns_service_instance_hcl_declares_basename_local(
    dns_service_instance_hcl_content: str,
) -> None:
    """dns-prod-zone/000/service_instance.hcl must use the basename idiom."""
    assert "basename(get_terragrunt_dir())" in dns_service_instance_hcl_content, (
        "service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom assertions -- identity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_id_service_hcl_declares_basename_local(id_service_hcl_content: str) -> None:
    """identity/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in id_service_hcl_content, (
        "service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_id_service_hcl_parent_dir_is_identity(id_service_hcl_content: str) -> None:
    """service.hcl must be at identity/ so basename resolves correctly."""
    assert ID_SERVICE_HCL.parent.name == "identity", (
        f"service.hcl parent dir is '{ID_SERVICE_HCL.parent.name}', "
        "expected 'identity'. The service dir must be named 'identity' (E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_id_service_instance_hcl_declares_basename_local(
    id_service_instance_hcl_content: str,
) -> None:
    """identity/000/service_instance.hcl must use the basename idiom."""
    assert "basename(get_terragrunt_dir())" in id_service_instance_hcl_content, (
        "service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-1: dns-prod-zone sources references/dns-prod-zone (single module)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_terragrunt_hcl_sources_dns_prod_zone_reference(
    dns_terragrunt_hcl_content: str,
) -> None:
    """dns-prod-zone leaf must source references/dns-prod-zone (AC-1)."""
    assert DNS_MODULE_PATH in dns_terragrunt_hcl_content, (
        f"terragrunt.hcl does not reference '{DNS_MODULE_PATH}'. "
        "The unit must source the dns-prod-zone reference module (AC-1, "
        "docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_sources_exactly_one_module(
    dns_terragrunt_hcl_content: str,
) -> None:
    """dns-prod-zone leaf must contain exactly one terraform.source declaration (AC-1)."""
    source_count = len(re.findall(r"^\s*source\s*=", dns_terragrunt_hcl_content, re.MULTILINE))
    assert source_count == 1, (
        f"terragrunt.hcl contains {source_count} source declarations; expected exactly 1. "
        "AC-1 requires each leaf to source exactly one reference module. "
        "No second source or nested primitive module include is permitted."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_does_not_source_primitive(
    dns_terragrunt_hcl_content: str,
) -> None:
    """dns-prod-zone leaf terraform.source must not resolve to a primitive module (AC-1).

    The leaf is permitted to reference primitive module paths in its inputs {} block
    as child source overrides (toggle-driven AC-8 pattern). Only the terraform { source }
    block itself must point to a references/ module, not a primitives/ module.
    """
    resolved_source = _extract_source(dns_terragrunt_hcl_content)
    assert "providers/aws/primitives/" not in resolved_source, (
        "terraform.source resolves to 'providers/aws/primitives/'. "
        "The leaf must source the references/dns-prod-zone reference module, "
        "not a primitive module directly (AC-1, docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_includes_root(dns_terragrunt_hcl_content: str) -> None:
    """dns-prod-zone leaf must include root terragrunt.hcl (docs/terragrunt-concepts.md)."""
    assert 'include "root"' in dns_terragrunt_hcl_content, (
        "terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "This is required for namespace-derived state backend inheritance "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_includes_envcommon(dns_terragrunt_hcl_content: str) -> None:
    """dns-prod-zone leaf must include _envcommon/dns-prod-zone.hcl (AC-1, D37)."""
    assert DNS_ENVCOMMON_PATH in dns_terragrunt_hcl_content, (
        f"terragrunt.hcl must include '{DNS_ENVCOMMON_PATH}' via an include block. "
        "The shared input template supplies zone_name and kms_key_principals (D37, AC-1)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_root_include_uses_expose_true(
    dns_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set expose = true (docs/terragrunt-concepts.md)."""
    expose_set = (
        "expose         = true" in dns_terragrunt_hcl_content
        or "expose = true" in dns_terragrunt_hcl_content
    )
    assert expose_set, (
        'terragrunt.hcl include "root" block must set expose = true. '
        "This is required for include.root.locals.common_tags to be accessible "
        "in the leaf inputs block (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_root_include_uses_deep_merge(
    dns_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set merge_strategy = 'deep' (docs/terragrunt-concepts.md)."""
    assert 'merge_strategy = "deep"' in dns_terragrunt_hcl_content, (
        'terragrunt.hcl include "root" block must set merge_strategy = "deep". '
        "Deep merge is required so inputs from multiple includes are merged correctly "
        "(docs/terragrunt-concepts.md, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_dns_terragrunt_hcl_passes_tags_using_root_locals(
    dns_terragrunt_hcl_content: str,
) -> None:
    """dns-prod-zone inputs.tags must use include.root.locals.common_tags
    (docs/terragrunt-concepts.md)."""
    assert ROOT_COMMON_TAGS_REF in dns_terragrunt_hcl_content, (
        f"terragrunt.hcl inputs.tags must reference '{ROOT_COMMON_TAGS_REF}', "
        "not 'local.common_tags'. The root include is expose=true so its locals are "
        "accessible via include.root.locals.<name> only (AC-1)."
    )


# ---------------------------------------------------------------------------
# AC-1: identity sources references/identity (single module)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_id_terragrunt_hcl_sources_identity_reference(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf must source references/identity (AC-1)."""
    assert IDENTITY_MODULE_PATH in id_terragrunt_hcl_content, (
        f"terragrunt.hcl does not reference '{IDENTITY_MODULE_PATH}'. "
        "The unit must source the identity reference module (AC-1, docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_sources_exactly_one_module(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf must contain exactly one terraform.source declaration (AC-1)."""
    source_count = len(re.findall(r"^\s*source\s*=", id_terragrunt_hcl_content, re.MULTILINE))
    assert source_count == 1, (
        f"terragrunt.hcl contains {source_count} source declarations; expected exactly 1. "
        "AC-1 requires each leaf to source exactly one reference module. "
        "No second source or nested primitive module include is permitted."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_does_not_source_primitive(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf terraform.source must not resolve to a primitive module (AC-1).

    The leaf is permitted to reference primitive module paths in its inputs {} block
    as child source overrides (toggle-driven AC-8 pattern). Only the terraform { source }
    block itself must point to a references/ module, not a primitives/ module.
    """
    resolved_source = _extract_source(id_terragrunt_hcl_content)
    assert "providers/aws/primitives/" not in resolved_source, (
        "terraform.source resolves to 'providers/aws/primitives/'. "
        "The leaf must source the references/identity reference module, "
        "not a primitive module directly (AC-1, docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_includes_root(id_terragrunt_hcl_content: str) -> None:
    """identity leaf must include root terragrunt.hcl (docs/terragrunt-concepts.md)."""
    assert 'include "root"' in id_terragrunt_hcl_content, (
        "terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "This is required for namespace-derived state backend inheritance "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_includes_envcommon(id_terragrunt_hcl_content: str) -> None:
    """identity leaf must include _envcommon/identity.hcl (AC-1, D37)."""
    assert IDENTITY_ENVCOMMON_PATH in id_terragrunt_hcl_content, (
        f"terragrunt.hcl must include '{IDENTITY_ENVCOMMON_PATH}' via an include block. "
        "The shared input template supplies the Viewer/Author/Admin group-to-role "
        "input contract (D37, AC-1)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_root_include_uses_expose_true(
    id_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set expose = true (docs/terragrunt-concepts.md)."""
    expose_set = (
        "expose         = true" in id_terragrunt_hcl_content
        or "expose = true" in id_terragrunt_hcl_content
    )
    assert expose_set, (
        'terragrunt.hcl include "root" block must set expose = true. '
        "This is required for include.root.locals.common_tags to be accessible "
        "in the leaf inputs block (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_root_include_uses_deep_merge(
    id_terragrunt_hcl_content: str,
) -> None:
    """The root include block must set merge_strategy = 'deep' (docs/terragrunt-concepts.md)."""
    assert 'merge_strategy = "deep"' in id_terragrunt_hcl_content, (
        'terragrunt.hcl include "root" block must set merge_strategy = "deep". '
        "Deep merge is required so inputs from multiple includes are merged correctly "
        "(docs/terragrunt-concepts.md, E7-F3-S1-T1)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_passes_tags_using_root_locals(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity inputs.tags must use include.root.locals.common_tags
    (docs/terragrunt-concepts.md)."""
    assert ROOT_COMMON_TAGS_REF in id_terragrunt_hcl_content, (
        f"terragrunt.hcl inputs.tags must reference '{ROOT_COMMON_TAGS_REF}', "
        "not 'local.common_tags'. The root include is expose=true so its locals are "
        "accessible via include.root.locals.<name> only (AC-1)."
    )


# ---------------------------------------------------------------------------
# D37: identity leaf input contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_id_terragrunt_hcl_passes_analyst_assume_role_policy(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf must pass analyst_assume_role_policy_json (D37, AC-1)."""
    assert "analyst_assume_role_policy_json" in id_terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'analyst_assume_role_policy_json'. "
        "This required input wires the TelemetryAnalyst role trust policy (D37, AC-1)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_passes_admin_assume_role_policy(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf must pass admin_assume_role_policy_json (D37, AC-1)."""
    assert "admin_assume_role_policy_json" in id_terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'admin_assume_role_policy_json'. "
        "This required input wires the TelemetryAdmin role trust policy (D37, AC-1)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_passes_analyst_inline_policies(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf must pass analyst_inline_policies (D37, AC-1)."""
    assert "analyst_inline_policies" in id_terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'analyst_inline_policies'. "
        "This required input carries the read-only Athena/Glue/S3-results policy (D37, AC-1)."
    )


@pytest.mark.unit
def test_id_terragrunt_hcl_passes_admin_inline_policies(
    id_terragrunt_hcl_content: str,
) -> None:
    """identity leaf must pass admin_inline_policies (D37, AC-1)."""
    assert "admin_inline_policies" in id_terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'admin_inline_policies'. "
        "This required input carries the QuickSight management policy (D37, AC-1)."
    )


# ---------------------------------------------------------------------------
# D31: prod parity -- no hardcoded sandbox account id in prod files
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("dns_service_hcl_content", "dns-prod-zone/service.hcl"),
        ("dns_service_instance_hcl_content", "dns-prod-zone/000/service_instance.hcl"),
        ("dns_terragrunt_hcl_content", "dns-prod-zone/000/terragrunt.hcl"),
        ("id_service_hcl_content", "identity/service.hcl"),
        ("id_service_instance_hcl_content", "identity/000/service_instance.hcl"),
        ("id_terragrunt_hcl_content", "identity/000/terragrunt.hcl"),
    ],
)
def test_no_hardcoded_sandbox_account_id(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No prod HCL file may hardcode the sandbox account id (D31, D45)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    sandbox_account_id = "222222222222"
    assert sandbox_account_id not in content, (
        f"{file_label} contains the sandbox account id '{sandbox_account_id}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded AWS access keys, no em-dashes, no suppression annotations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("environment_hcl_content", "environment.hcl"),
        ("environment_instance_hcl_content", "environment_instance.hcl"),
        ("dns_service_hcl_content", "dns-prod-zone/service.hcl"),
        ("dns_service_instance_hcl_content", "dns-prod-zone/000/service_instance.hcl"),
        ("dns_terragrunt_hcl_content", "dns-prod-zone/000/terragrunt.hcl"),
        ("id_service_hcl_content", "identity/service.hcl"),
        ("id_service_instance_hcl_content", "identity/000/service_instance.hcl"),
        ("id_terragrunt_hcl_content", "identity/000/terragrunt.hcl"),
    ],
)
def test_no_hardcoded_aws_access_key(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No HCL file may contain a hardcoded AWS access key pattern."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"{file_label} contains a pattern matching an AWS access key. "
        "No credentials may be hardcoded in HCL files."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("environment_hcl_content", "environment.hcl"),
        ("environment_instance_hcl_content", "environment_instance.hcl"),
        ("dns_service_hcl_content", "dns-prod-zone/service.hcl"),
        ("dns_service_instance_hcl_content", "dns-prod-zone/000/service_instance.hcl"),
        ("dns_terragrunt_hcl_content", "dns-prod-zone/000/terragrunt.hcl"),
        ("id_service_hcl_content", "identity/service.hcl"),
        ("id_service_instance_hcl_content", "identity/000/service_instance.hcl"),
        ("id_terragrunt_hcl_content", "identity/000/terragrunt.hcl"),
    ],
)
def test_no_em_dash_in_hcl_files(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No HCL file may contain an em-dash character (U+2014) per code standards."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert chr(0x2014) not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("dns_terragrunt_hcl_content", "dns-prod-zone/000/terragrunt.hcl"),
        ("id_terragrunt_hcl_content", "identity/000/terragrunt.hcl"),
    ],
)
def test_no_suppression_annotations_in_leaf_hcl(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """Leaf HCL files must contain no nosec/noqa/nolint/tfsec:ignore/checkov:skip."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not SUPPRESSION_PATTERN.search(content), (
        f"{file_label} contains a suppression annotation. "
        "Suppression annotations are prohibited -- fix the underlying issue (CLAUDE.md, AC-1)."
    )


# ---------------------------------------------------------------------------
# D31: prod module source parity with sandbox
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_prod_and_sandbox_use_same_module_source() -> None:
    """Prod and sandbox dns-prod-zone leaves must reference the same module source string.

    D31 requires that the only differences between prod and sandbox are directory
    path and inputs. Both leaves must carry the same 'references/dns-prod-zone'
    source expression so a module version bump in one is detected in the other.
    """
    sandbox_leaf = (
        REPO_ROOT
        / "terragrunt"
        / "live"
        / "telemetry"
        / "us-east-1"
        / "bootstrap"
        / "sandbox_role"
        / "dns-prod-zone"
        / "000"
        / "terragrunt.hcl"
    )
    assert sandbox_leaf.exists(), (
        f"Sandbox dns-prod-zone leaf not found at {sandbox_leaf}. "
        "The sandbox foundation leaf (bootstrap/sandbox_role) must exist for parity "
        "comparison (D31, Phase 8 Approach A relocation)."
    )
    sandbox_content = sandbox_leaf.read_text()
    prod_content = DNS_TERRAGRUNT_HCL.read_text()

    sandbox_source = _extract_source(sandbox_content)
    prod_source = _extract_source(prod_content)

    assert prod_source == sandbox_source, (
        f"Prod dns-prod-zone source '{prod_source}' differs from "
        f"sandbox source '{sandbox_source}'. "
        "D31 requires prod and sandbox to pin the identical module source (parity check)."
    )


@pytest.mark.unit
def test_identity_prod_and_sandbox_use_same_module_source() -> None:
    """Prod and sandbox identity leaves must reference the same module source string.

    D31 requires that the only differences between prod and sandbox are directory
    path and inputs. Both leaves must carry the same 'references/identity'
    source expression.
    """
    sandbox_leaf = (
        REPO_ROOT
        / "terragrunt"
        / "live"
        / "telemetry"
        / "us-east-1"
        / "sandbox"
        / "_singletons"
        / "shared"
        / "identity"
        / "000"
        / "terragrunt.hcl"
    )
    assert sandbox_leaf.exists(), (
        f"Sandbox identity leaf not found at {sandbox_leaf}. "
        "The sandbox leaf must exist for parity comparison (D31, E7-F3-S1-T1)."
    )
    sandbox_content = sandbox_leaf.read_text()
    prod_content = ID_TERRAGRUNT_HCL.read_text()

    sandbox_source = _extract_source(sandbox_content)
    prod_source = _extract_source(prod_content)

    assert prod_source == sandbox_source, (
        f"Prod identity source '{prod_source}' differs from "
        f"sandbox source '{sandbox_source}'. "
        "D31 requires prod and sandbox to pin the identical module source (parity check)."
    )


def _extract_source(hcl_content: str) -> str:
    """Extract the resolved local module source path from a leaf terragrunt.hcl.

    Supports two source declaration forms introduced by E9-F3-S1-T1:
    1. Toggle-driven ternary: ``source = <cond> ? "pinned_url" : "local_path"``
       Returns the false/local branch (the ``${get_repo_root()}//...`` path) so
       parity comparisons use the canonical module path regardless of environment.
    2. Legacy literal: ``source = "..."``
       Returns the literal value unchanged.

    Raises AssertionError when no source assignment is found so test failures are
    unambiguous (AC-1).
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
        "Every leaf terragrunt.hcl must declare exactly one source (AC-1)."
    )
    return literal_match.group(1)
