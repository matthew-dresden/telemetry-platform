"""Unit tests for the prod dns-collector and observability terragrunt units.

These tests assert the structural and content constraints for the prod account
(111111111111) dns-collector and observability terragrunt units. They
validate:

- AC-2: the dns-collector alias unit sources exactly one primitive module
  (primitives/route53-record),
  declares both DAG dependency blocks (upstream CloudFront plane + dns_prod_zone zone_id),
  reads the unprefixed CloudFront output (D48), and the dns_prod_zone dependency provides
  zone_id so alias record R4 lands in the prod hosted zone (docs/terragrunt-concepts.md,
  D39).
- AC-3: the observability unit sources exactly one reference module (references/observability)
  and wires every alarm action, cost-anomaly detector, and Budgets notifications to the SNS
  topic_arn (spec AC #12, D41).
- AC-4: observability/000/terragrunt.hcl supplies the `alarms` input with exactly six named
  CloudWatch alarms -- ECS RunningTaskCount (AWS/ECS), ALB HTTPCode_Target_5XX_Count
  (AWS/ApplicationELB), ALB TargetResponseTime p95 (AWS/ApplicationELB), WAF BlockedRequests
  (AWS/WAFV2), Firehose DeliveryToS3.DataFreshness (AWS/Firehose), Athena bytes-scanned
  (AWS/Athena) -- plus a CloudWatch dashboard body covering ALB/ECS/Firehose/WAF health
  (spec AC #12, docs/terragrunt-concepts.md). OBS-4b removed the seventh alarm, the dead ADOT
  memory_limiter drops alarm (logs-only collector publishes no self-metrics; e2e issue 015).
- AC-5: observability/000/terragrunt.hcl supplies budget_amount and budget notification
  thresholds as percentage increments wired to direct budget_subscriber_email_addresses
  (spec AC #12, docs/terragrunt-concepts.md).
- AC-6: observability/000/terragrunt.hcl declares its DAG dependency blocks for
  observability (collector-ingestion, data-lake, analytics) matching the sandbox
  sibling (docs/terragrunt-concepts.md, spec AC #18). The 'portal' edge was removed in
  the portal/QuickSight retirement (Phase 2).
- D31: prod and sandbox units differ only by path and inputs (parity contract).
- D39: R4 is owned ONLY by dns-collector -- collector-ingestion may not also declare
  this record.
- D48: alias targets read UNPREFIXED cloudfront_domain_name and cloudfront_hosted_zone_id
  outputs from the upstream CloudFront unit.
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

PROD_ACCOUNT = "111111111111"
SANDBOX_ACCOUNT = "222222222222"

PROD_ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
PROD_ENV_BASE = PROD_ACCOUNT_DIR / "prod"
PROD_INSTANCE_BASE = PROD_ENV_BASE / "000"

SANDBOX_ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
SANDBOX_ENV_BASE = SANDBOX_ACCOUNT_DIR / "sandbox"
SANDBOX_INSTANCE_BASE = SANDBOX_ENV_BASE / "000"

# ---------------------------------------------------------------------------
# dns-collector paths
# ---------------------------------------------------------------------------

DNS_COLLECTOR_SERVICE_DIR = PROD_INSTANCE_BASE / "dns-collector"
DNS_COLLECTOR_INSTANCE_DIR = DNS_COLLECTOR_SERVICE_DIR / "000"

DNS_COLLECTOR_SERVICE_HCL = DNS_COLLECTOR_SERVICE_DIR / "service.hcl"
DNS_COLLECTOR_SERVICE_INSTANCE_HCL = DNS_COLLECTOR_INSTANCE_DIR / "service_instance.hcl"
DNS_COLLECTOR_TERRAGRUNT_HCL = DNS_COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# observability paths
# ---------------------------------------------------------------------------

OBS_SERVICE_DIR = PROD_ENV_BASE / "_singletons" / "shared" / "observability"
OBS_INSTANCE_DIR = OBS_SERVICE_DIR / "000"

OBS_SERVICE_HCL = OBS_SERVICE_DIR / "service.hcl"
OBS_SERVICE_INSTANCE_HCL = OBS_INSTANCE_DIR / "service_instance.hcl"
OBS_TERRAGRUNT_HCL = OBS_INSTANCE_DIR / "terragrunt.hcl"

# ---------------------------------------------------------------------------
# Sandbox sibling paths (D31 parity)
# ---------------------------------------------------------------------------

SANDBOX_DNS_COLLECTOR_LEAF = SANDBOX_INSTANCE_BASE / "dns-collector" / "000" / "terragrunt.hcl"
SANDBOX_OBS_LEAF = (
    SANDBOX_INSTANCE_BASE.parent
    / "_singletons"
    / "shared"
    / "observability"
    / "000"
    / "terragrunt.hcl"
)

# Module paths (AC-2, AC-3)
ROUTE53_RECORD_MODULE_PATH = "primitives/route53-record"
OBSERVABILITY_MODULE_PATH = "references/observability"

# Root common_tags pattern required by docs/terragrunt-concepts.md (expose=true)
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"

# Unprefixed CloudFront output names (D48)
CLOUDFRONT_DOMAIN_NAME_OUTPUT = "cloudfront_domain_name"
CLOUDFRONT_HOSTED_ZONE_ID_OUTPUT = "cloudfront_hosted_zone_id"

# Record ownership identifiers (AC-8, D39)
R4_RECORD_MARKER = "R4"

# The six named alarm metric identifiers that must appear in observability (AC-4).
# OBS-4b removed the seventh, the ADOT memory_limiter drops alarm
# (otelcol_processor_refused_metric_points): the deployed collector is logs-only (D27.3)
# and publishes no self-metrics, so that alarm had no datapoints and could never
# transition -- a permanently no-data alarm dropped per operator decision (e2e issue 015).
REQUIRED_ALARM_METRICS = [
    "RunningTaskCount",  # ECS -- AWS/ECS
    "HTTPCode_Target_5XX_Count",  # ALB -- AWS/ApplicationELB
    "TargetResponseTime",  # ALB p95 -- AWS/ApplicationELB
    "BlockedRequests",  # WAF -- AWS/WAFV2
    "DeliveryToS3.DataFreshness",  # Firehose -- AWS/Firehose
    "workgroup_name",  # Athena bytes-scanned -- uses the athena unit's workgroup dimension
]

# The observability DAG dependency block names (AC-6, docs/terragrunt-concepts.md).
# The 'portal' run-order edge was removed with the portal/QuickSight retirement --
# observability never consumed portal outputs (the WAF alarm dimension uses
# collector-ingestion's waf_web_acl_id). The former 'analytics' edge is now the slim
# 'athena' unit that owns the Athena workgroup (analytics/QuickSight surface retired).
OBSERVABILITY_DAG_DEPS = [
    "collector_ingestion",
    "data_lake",
    "athena",
]

SUPPRESSION_PATTERN = re.compile(
    r"nosec|noqa|nolint|tfsec:ignore|checkov:skip|trivy:ignore",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Meta-guard: this test file must contain zero literal U+2014 em-dashes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_this_file_contains_no_literal_em_dash() -> None:
    """This test file must not contain any literal U+2014 em-dash characters."""
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
# Fixtures -- dns-collector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dns_collector_service_hcl_content() -> str:
    """Read prod dns-collector service.hcl. Fails loudly if the file does not exist."""
    assert DNS_COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DNS_COLLECTOR_SERVICE_HCL}. "
        "This file must be created for the prod dns-collector service layer (E7-F3-S3-T1)."
    )
    return DNS_COLLECTOR_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def dns_collector_service_instance_hcl_content() -> str:
    """Read prod dns-collector service_instance.hcl. Fails loudly if the file does not exist."""
    assert DNS_COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DNS_COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod dns-collector service instance (E7-F3-S3-T1)."
    )
    return DNS_COLLECTOR_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def dns_collector_terragrunt_hcl_content() -> str:
    """Read prod dns-collector leaf terragrunt.hcl. Fails loudly if the file does not exist."""
    assert DNS_COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {DNS_COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod dns-collector unit (E7-F3-S3-T1)."
    )
    return DNS_COLLECTOR_TERRAGRUNT_HCL.read_text()


# ---------------------------------------------------------------------------
# Fixtures -- observability
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def obs_service_hcl_content() -> str:
    """Read prod observability service.hcl. Fails loudly if the file does not exist."""
    assert OBS_SERVICE_HCL.exists(), (
        f"service.hcl not found at {OBS_SERVICE_HCL}. "
        "This file must be created for the prod observability service layer (E7-F3-S3-T1)."
    )
    return OBS_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def obs_service_instance_hcl_content() -> str:
    """Read prod observability service_instance.hcl. Fails loudly if the file does not exist."""
    assert OBS_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {OBS_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod observability service instance (E7-F3-S3-T1)."
    )
    return OBS_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def obs_terragrunt_hcl_content() -> str:
    """Read prod observability leaf terragrunt.hcl. Fails loudly if the file does not exist."""
    assert OBS_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {OBS_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod observability unit (E7-F3-S3-T1)."
    )
    return OBS_TERRAGRUNT_HCL.read_text()


# ---------------------------------------------------------------------------
# File existence -- dns-collector (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_service_hcl_exists() -> None:
    """Prod dns-collector service.hcl must exist (AC-2, E7-F3-S3-T1)."""
    assert DNS_COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DNS_COLLECTOR_SERVICE_HCL}. "
        "Required for the prod dns-collector service layer (AC-2, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_dns_collector_service_instance_hcl_exists() -> None:
    """Prod dns-collector service_instance.hcl must exist (AC-2, E7-F3-S3-T1)."""
    assert DNS_COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DNS_COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "Required for the prod dns-collector service instance (AC-2, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_dns_collector_terragrunt_hcl_exists() -> None:
    """Prod dns-collector terragrunt.hcl must exist (AC-2, E7-F3-S3-T1)."""
    assert DNS_COLLECTOR_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {DNS_COLLECTOR_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod dns-collector unit (AC-2, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# File existence -- observability (AC-3, AC-4, AC-5, AC-6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_service_hcl_exists() -> None:
    """Prod observability service.hcl must exist (AC-3, E7-F3-S3-T1)."""
    assert OBS_SERVICE_HCL.exists(), (
        f"service.hcl not found at {OBS_SERVICE_HCL}. "
        "Required for the prod observability service layer (AC-3, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_service_instance_hcl_exists() -> None:
    """Prod observability service_instance.hcl must exist (AC-3, E7-F3-S3-T1)."""
    assert OBS_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {OBS_SERVICE_INSTANCE_HCL}. "
        "Required for the prod observability service instance (AC-3, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_terragrunt_hcl_exists() -> None:
    """Prod observability terragrunt.hcl must exist (AC-3, E7-F3-S3-T1)."""
    assert OBS_TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {OBS_TERRAGRUNT_HCL}. "
        "Required leaf unit file for the prod observability unit (AC-3, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom (docs/terragrunt-concepts.md) -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_service_hcl_basename_idiom(
    dns_collector_service_hcl_content: str,
) -> None:
    """Prod dns-collector service.hcl must use basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in dns_collector_service_hcl_content, (
        "prod dns-collector service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_dns_collector_service_instance_hcl_basename_idiom(
    dns_collector_service_instance_hcl_content: str,
) -> None:
    """Prod dns-collector service_instance.hcl must use basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in dns_collector_service_instance_hcl_content, (
        "prod dns-collector service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom (docs/terragrunt-concepts.md) -- observability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_service_hcl_basename_idiom(
    obs_service_hcl_content: str,
) -> None:
    """Prod observability service.hcl must use basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in obs_service_hcl_content, (
        "prod observability service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_service_instance_hcl_basename_idiom(
    obs_service_instance_hcl_content: str,
) -> None:
    """Prod observability service_instance.hcl must use basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in obs_service_instance_hcl_content, (
        "prod observability service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# AC-2: single-module source (primitives/route53-record) -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_sources_route53_record(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector leaf must source primitives/route53-record (AC-2)."""
    assert ROUTE53_RECORD_MODULE_PATH in dns_collector_terragrunt_hcl_content, (
        f"prod dns-collector terragrunt.hcl does not reference '{ROUTE53_RECORD_MODULE_PATH}'. "
        "The unit must source the route53-record primitive module (AC-2, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_dns_collector_sources_exactly_one_module(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector leaf must contain exactly one terraform.source (AC-2)."""
    source_count = len(
        re.findall(r"^\s*source\s*=", dns_collector_terragrunt_hcl_content, re.MULTILINE)
    )
    assert source_count == 1, (
        f"prod dns-collector terragrunt.hcl contains {source_count} source declarations; "
        "expected exactly 1. AC-2 requires the leaf to source exactly one primitive "
        "module (primitives/route53-record). No second source is permitted."
    )


# ---------------------------------------------------------------------------
# AC-3: single-module source (references/observability) -- observability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_sources_references_observability(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must source references/observability (AC-3)."""
    assert OBSERVABILITY_MODULE_PATH in obs_terragrunt_hcl_content, (
        f"prod observability terragrunt.hcl does not reference '{OBSERVABILITY_MODULE_PATH}'. "
        "The unit must source the observability reference module (AC-3, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_sources_exactly_one_module(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must contain exactly one terraform.source (AC-3)."""
    source_count = len(re.findall(r"^\s*source\s*=", obs_terragrunt_hcl_content, re.MULTILINE))
    assert source_count == 1, (
        f"prod observability terragrunt.hcl contains {source_count} source declarations; "
        "expected exactly 1. AC-3 requires the leaf to source exactly one reference "
        "module (references/observability). No second source is permitted."
    )


# ---------------------------------------------------------------------------
# AC-3: topic_arn wiring -- observability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_references_topic_arn(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must reference topic_arn so every alarm action is wired (AC-3)."""
    assert "topic_arn" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl does not reference 'topic_arn'. "
        "The references/observability module wires every alarm_actions/ok_actions to "
        "topic_arn internally. The leaf must at least mention topic_arn or supply the "
        "alarms list so the module can wire the sink (AC-3, D41, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# AC-2: DAG dependency blocks -- dns-collector (docs/terragrunt-concepts.md, D39)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_has_dns_prod_zone_dependency(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector must declare dependency on dns-prod-zone (AC-2, D39).

    The dns-prod-zone dependency provides zone_id so R4 lands in the prod hosted zone.
    """
    assert 'dependency "dns_prod_zone"' in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl does not declare a 'dns_prod_zone' dependency block. "
        "The unit requires dns-prod-zone.outputs.zone_id for R4 (AC-2, D39, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_dns_collector_has_collector_ingestion_dependency(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector must declare dependency on collector-ingestion (AC-2, D48).

    The collector-ingestion dependency provides unprefixed cloudfront_domain_name
    and cloudfront_hosted_zone_id for the R4 alias target.
    """
    assert "collector-ingestion" in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl does not reference 'collector-ingestion'. "
        "The unit requires collector-ingestion outputs for the R4 alias target (AC-2, D48)."
    )


@pytest.mark.unit
def test_prod_dns_collector_dependency_has_mock_outputs(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector dependencies must declare mock_outputs for plan/validate (D37)."""
    assert "mock_outputs" in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl dependencies must declare mock_outputs so "
        "plan/validate can run before upstream units are applied (D37, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_dns_collector_mock_outputs_allowed_commands_restricted(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector mock_outputs must be restricted to plan/validate only (D31)."""
    assert "mock_outputs_allowed_terraform_commands" in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl dependencies must declare "
        "mock_outputs_allowed_terraform_commands restricted to [plan, validate]. "
        "Apply must use real upstream outputs (D31, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# D48: unprefixed CloudFront output reads -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_reads_unprefixed_cloudfront_domain_name(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector must read the unprefixed cloudfront_domain_name output (D48).

    Reading a prefixed output (e.g. collector_cloudfront_domain_name) yields a dangling
    reference that fails at plan/apply time (D48).
    """
    assert CLOUDFRONT_DOMAIN_NAME_OUTPUT in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl does not reference 'cloudfront_domain_name'. "
        "D48: The alias target must use the UNPREFIXED 'cloudfront_domain_name' output. "
        "A prefixed output yields a dangling reference and fails apply."
    )


@pytest.mark.unit
def test_prod_dns_collector_reads_unprefixed_cloudfront_hosted_zone_id(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector must read the unprefixed cloudfront_hosted_zone_id output (D48)."""
    assert CLOUDFRONT_HOSTED_ZONE_ID_OUTPUT in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl does not reference 'cloudfront_hosted_zone_id'. "
        "D48: The alias target must use the UNPREFIXED 'cloudfront_hosted_zone_id' output."
    )


# ---------------------------------------------------------------------------
# D37: zone_id sourced from dns_prod_zone dependency -- both dns alias units
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_passes_zone_id_from_dns_dep(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector must wire zone_id from dns_prod_zone dependency output (D37)."""
    assert "dependency.dns_prod_zone.outputs.zone_id" in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector terragrunt.hcl must wire 'zone_id' from "
        "'dependency.dns_prod_zone.outputs.zone_id'. "
        "The zone_id provides the zone for R4 (D37, AC-2, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# AC-2: record ownership documentation -- service_instance.hcl files
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_service_instance_hcl_declares_r4_ownership(
    dns_collector_service_instance_hcl_content: str,
) -> None:
    """Prod dns-collector service_instance.hcl must declare R4 single ownership (AC-2, D39)."""
    assert R4_RECORD_MARKER in dns_collector_service_instance_hcl_content, (
        "prod dns-collector service_instance.hcl does not mention 'R4'. "
        "This unit is the sole owner of DNS alias record R4 (AC-2, D39, E7-F3-S3-T1). "
        "Document the single ownership in service_instance.hcl."
    )


# ---------------------------------------------------------------------------
# AC-2 / D39: single-ownership scan -- R4 not written by any other prod unit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_only_prod_dns_collector_unit_defines_r4() -> None:
    """R4 must be declared by ONLY dns-collector in prod/000/. No other unit may claim R4.

    This test scans all service_instance.hcl files under prod/000/ for R4 ownership
    claims. Only dns-collector/000/service_instance.hcl may declare R4 (AC-2, D39).
    """
    r4_owner_files: list[str] = []
    for hcl_file in PROD_INSTANCE_BASE.rglob("service_instance.hcl"):
        content = hcl_file.read_text()
        if R4_RECORD_MARKER in content and hcl_file != DNS_COLLECTOR_SERVICE_INSTANCE_HCL:
            r4_owner_files.append(str(hcl_file))
    assert len(r4_owner_files) == 0, (
        f"R4 ownership is declared in non-dns-collector service_instance.hcl files: "
        f"{r4_owner_files}. "
        "R4 must be owned exclusively by the prod dns-collector unit (AC-2, D39, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# Tags pattern: include.root.locals.common_tags -- both dns alias units
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_passes_tags_using_root_locals(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector inputs.tags must use include.root.locals.common_tags
    (docs/terragrunt-concepts.md)."""
    assert ROOT_COMMON_TAGS_REF in dns_collector_terragrunt_hcl_content, (
        f"prod dns-collector terragrunt.hcl inputs.tags must reference '{ROOT_COMMON_TAGS_REF}'. "
        "The root include is expose=true so locals are accessible via "
        "include.root.locals.<name> only (docs/terragrunt-concepts.md, AC-2)."
    )


# ---------------------------------------------------------------------------
# AC-6: observability DAG dependencies (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("dep_name", OBSERVABILITY_DAG_DEPS)
def test_prod_observability_declares_all_dag_dependencies(
    obs_terragrunt_hcl_content: str,
    dep_name: str,
) -> None:
    """Prod observability must declare its DAG dependency blocks (AC-6,
    docs/terragrunt-concepts.md).

    Required edges: collector_ingestion, data_lake, athena. (The 'portal' edge was
    removed in the portal/QuickSight retirement -- observability never consumed portal
    outputs; the former 'analytics' edge is now the slim 'athena' unit.)
    """
    dep_headers = re.findall(r'^dependency\s+"(\w+)"', obs_terragrunt_hcl_content, re.MULTILINE)
    assert dep_name in dep_headers, (
        f"prod observability terragrunt.hcl is missing dependency block for '{dep_name}'. "
        f"Found dependency blocks: {dep_headers}. "
        "The DAG edges must be declared: collector_ingestion, data_lake, athena "
        "(AC-6, docs/terragrunt-concepts.md, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_does_not_declare_portal_dependency(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability must NOT declare a 'portal' dependency block.

    QuickSight/portal consumer surface retirement (portal/QuickSight retirement,
    Phase 2): observability never consumed portal outputs and the portal leaf is
    destroyed in a later phase, so the run-order edge was removed.
    """
    dep_headers = re.findall(r'^dependency\s+"(\w+)"', obs_terragrunt_hcl_content, re.MULTILINE)
    assert "portal" not in dep_headers, (
        "prod observability terragrunt.hcl still declares a 'portal' dependency block "
        f"(found: {dep_headers}); it must be removed for the portal retirement (Phase 2)."
    )


@pytest.mark.unit
def test_prod_observability_dag_dependency_mock_outputs_restricted(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability dependency mock_outputs must be restricted to plan/validate (D31)."""
    assert "mock_outputs_allowed_terraform_commands" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl dependencies must declare "
        "mock_outputs_allowed_terraform_commands restricted to [plan, validate]. "
        "Apply must use real upstream outputs (D31, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# AC-4: six named CloudWatch alarms -- observability (OBS-4b dropped the seventh,
# the dead ADOT memory_limiter drops alarm; see e2e issue 015)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_has_alarms_input(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must supply the alarms input (AC-4)."""
    assert "alarms" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl does not reference 'alarms'. "
        "The leaf must supply the alarms input with six named CloudWatch alarms "
        "(AC-4, docs/terragrunt-concepts.md, E7-F3-S3-T1; OBS-4b removed the dead ADOT alarm)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("metric_token", REQUIRED_ALARM_METRICS)
def test_prod_observability_has_required_alarm_metric(
    obs_terragrunt_hcl_content: str,
    metric_token: str,
) -> None:
    """Prod observability leaf must include all six named CloudWatch alarm metrics (AC-4).

    Each of the six alarms (ECS RunningTaskCount, ALB 5XX/TargetResponseTime p95,
    WAF BlockedRequests, Firehose DataFreshness, Athena bytes-scanned) must appear in the
    observability leaf by its metric name / dimension token. OBS-4b removed the seventh,
    the dead ADOT memory_limiter drops alarm (logs-only collector publishes no self-metrics).
    """
    assert metric_token in obs_terragrunt_hcl_content, (
        f"prod observability terragrunt.hcl does not reference '{metric_token}'. "
        "The alarms input must include all six named CloudWatch alarms from "
        "docs/terragrunt-concepts.md (AC-4, E7-F3-S3-T1; OBS-4b removed the dead ADOT alarm)."
    )


@pytest.mark.unit
def test_prod_observability_has_dashboard_body(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must supply a CloudWatch dashboard body (AC-4)."""
    assert "dashboard_body" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl does not reference 'dashboard_body'. "
        "The leaf must supply a CloudWatch dashboard body covering "
        "ALB/ECS/Firehose/WAF health (AC-4, docs/terragrunt-concepts.md, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# AC-5: budget wiring -- observability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_has_budget_amount(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must supply budget_amount (AC-5)."""
    assert "budget_amount" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl does not reference 'budget_amount'. "
        "The leaf must supply budget_amount for AWS Budgets notifications "
        "(AC-5, docs/terragrunt-concepts.md, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_has_budget_subscriber_email_addresses(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must supply budget_subscriber_email_addresses (AC-5)."""
    assert "budget_subscriber_email_addresses" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl does not reference 'budget_subscriber_email_addresses'. "
        "The leaf must supply direct budget_subscriber_email_addresses so cost crossings "
        "notify operators (AC-5, docs/terragrunt-concepts.md, E7-F3-S3-T1)."
    )


@pytest.mark.unit
def test_prod_observability_has_budget_notification_thresholds(
    obs_terragrunt_hcl_content: str,
) -> None:
    """Prod observability leaf must supply budget_notification_thresholds (AC-5)."""
    assert "budget_notification_thresholds" in obs_terragrunt_hcl_content, (
        "prod observability terragrunt.hcl does not reference 'budget_notification_thresholds'. "
        "The leaf must supply percentage-increment notification thresholds wired to "
        "budget_subscriber_email_addresses (AC-5, docs/terragrunt-concepts.md, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# AC-5: budget config sourced from service.hcl (not inline literals, D8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_observability_service_hcl_has_budget_amount(
    obs_service_hcl_content: str,
) -> None:
    """Prod observability service.hcl must declare budget_amount local (AC-5, D8)."""
    assert "budget_amount" in obs_service_hcl_content, (
        "prod observability service.hcl does not declare 'budget_amount'. "
        "Budget inputs must come from service.hcl, not inline literals in the leaf (D8, AC-5)."
    )


@pytest.mark.unit
def test_prod_observability_service_hcl_has_alarm_configs(
    obs_service_hcl_content: str,
) -> None:
    """Prod observability service.hcl must declare alarm_configs local (AC-4, D8)."""
    assert "alarm_configs" in obs_service_hcl_content, (
        "prod observability service.hcl does not declare 'alarm_configs'. "
        "Alarm metric/namespace/threshold config must come from service.hcl (D8, AC-4)."
    )


@pytest.mark.unit
def test_prod_observability_service_hcl_has_sns_kms_key_id(
    obs_service_hcl_content: str,
) -> None:
    """Prod observability service.hcl must declare sns_kms_key_id local (AC-3, D8)."""
    assert "sns_kms_key_id" in obs_service_hcl_content, (
        "prod observability service.hcl does not declare 'sns_kms_key_id'. "
        "The KMS CMK alias for the SNS topic must come from service.hcl (D8, AC-3)."
    )


# ---------------------------------------------------------------------------
# D31: instance-relative dependency config_paths -- dns-collector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_dns_prod_zone_dep_uses_svc_instance(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector dns_prod_zone dep config_path must point at the foundation tier."""
    assert (
        "bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}"
        in dns_collector_terragrunt_hcl_content
    ), (
        "prod dns-collector dns_prod_zone dependency config_path must point at the foundation "
        "zone 'bootstrap/${local.bootstrap_role}/dns-prod-zone/${local.dns_prod_zone_active}' "
        "(Phase 8 Approach A relocation; bootstrap_role + active set derived, not hardcoded; D31)."
    )


@pytest.mark.unit
def test_prod_dns_collector_collector_ingestion_dep_uses_svc_instance(
    dns_collector_terragrunt_hcl_content: str,
) -> None:
    """Prod dns-collector collector-ingestion dep config_path must use instance-relative path."""
    assert "collector-ingestion/${local.svc_instance}" in dns_collector_terragrunt_hcl_content, (
        "prod dns-collector collector-ingestion dependency config_path must interpolate "
        "'collector-ingestion/${local.svc_instance}' rather than a hardcoded index. "
        "This enables instance-copyable deps (D31, D48, E7-F3-S3-T1)."
    )


# ---------------------------------------------------------------------------
# D31: parity with sandbox -- module source identical
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_dns_collector_uses_same_module_source_as_sandbox() -> None:
    """Prod and sandbox dns-collector leaves must reference the same module source (D31).

    D31 parity: only path and input deltas are permitted between prod and sandbox.
    """
    if not DNS_COLLECTOR_TERRAGRUNT_HCL.exists():
        pytest.fail(
            f"Prod dns-collector leaf not found at {DNS_COLLECTOR_TERRAGRUNT_HCL}. "
            "Cannot verify D31 parity (E7-F3-S3-T1)."
        )
    if not SANDBOX_DNS_COLLECTOR_LEAF.exists():
        pytest.fail(
            f"Sandbox dns-collector leaf not found at {SANDBOX_DNS_COLLECTOR_LEAF}. "
            "Cannot verify D31 parity (E7-F3-S3-T1)."
        )
    prod_source = _extract_source(DNS_COLLECTOR_TERRAGRUNT_HCL.read_text())
    sandbox_source = _extract_source(SANDBOX_DNS_COLLECTOR_LEAF.read_text())
    assert prod_source == sandbox_source, (
        f"Prod dns-collector source '{prod_source}' differs from "
        f"sandbox source '{sandbox_source}'. "
        "D31 requires prod and sandbox to pin the identical module source string."
    )


@pytest.mark.unit
def test_prod_observability_uses_same_module_source_as_sandbox() -> None:
    """Prod and sandbox observability leaves must reference the same module source (D31)."""
    if not OBS_TERRAGRUNT_HCL.exists():
        pytest.fail(
            f"Prod observability leaf not found at {OBS_TERRAGRUNT_HCL}. "
            "Cannot verify D31 parity (E7-F3-S3-T1)."
        )
    if not SANDBOX_OBS_LEAF.exists():
        pytest.fail(
            f"Sandbox observability leaf not found at {SANDBOX_OBS_LEAF}. "
            "Cannot verify D31 parity (E7-F3-S3-T1)."
        )
    prod_source = _extract_source(OBS_TERRAGRUNT_HCL.read_text())
    sandbox_source = _extract_source(SANDBOX_OBS_LEAF.read_text())
    assert prod_source == sandbox_source, (
        f"Prod observability source '{prod_source}' differs from "
        f"sandbox source '{sandbox_source}'. "
        "D31 requires prod and sandbox to pin the identical module source string."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded credentials, no em-dash -- all prod units
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_service_hcl_content", "prod dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "prod dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_service_hcl_content", "prod observability service.hcl"),
        ("obs_service_instance_hcl_content", "prod observability service_instance.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
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
        ("dns_collector_service_hcl_content", "prod dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "prod dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_service_hcl_content", "prod observability service.hcl"),
        ("obs_service_instance_hcl_content", "prod observability service_instance.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
    ],
)
def test_no_em_dash_in_hcl_files(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """No HCL file may contain an em-dash character (U+2014) per code standards."""
    content: str = request.getfixturevalue(content_fixture)
    assert chr(0x2014) not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_service_hcl_content", "prod dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "prod dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_service_hcl_content", "prod observability service.hcl"),
        ("obs_service_instance_hcl_content", "prod observability service_instance.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
    ],
)
def test_no_suppression_annotations_in_hcl_files(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """No HCL file may contain nosec/noqa/nolint/tfsec:ignore/checkov:skip annotations."""
    content: str = request.getfixturevalue(content_fixture)
    assert not SUPPRESSION_PATTERN.search(content), (
        f"{file_label} contains a suppression annotation. "
        "Suppression annotations are prohibited -- fix the underlying issue (CLAUDE.md)."
    )


# ---------------------------------------------------------------------------
# D31: no hardcoded account ids in prod files
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_service_hcl_content", "prod dns-collector service.hcl"),
        ("dns_collector_service_instance_hcl_content", "prod dns-collector service_instance.hcl"),
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_service_hcl_content", "prod observability service.hcl"),
        ("obs_service_instance_hcl_content", "prod observability service_instance.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
    ],
)
def test_no_hardcoded_sandbox_account_id(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """No prod HCL file may hardcode the sandbox account id (D31, D45)."""
    content: str = request.getfixturevalue(content_fixture)
    assert SANDBOX_ACCOUNT not in content, (
        f"{file_label} contains the sandbox account id '{SANDBOX_ACCOUNT}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# include block patterns -- dns alias units
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
    ],
)
def test_leaf_includes_root_block(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """Every leaf terragrunt.hcl must include the root terragrunt.hcl via include 'root' block."""
    content: str = request.getfixturevalue(content_fixture)
    assert 'include "root"' in content, (
        f"{file_label} does not contain 'include \"root\"'. "
        "The root include block provides namespace-derived state backend inheritance "
        "(docs/terragrunt-concepts.md, E7-F3-S3-T1)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
    ],
)
def test_leaf_root_include_expose_true(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """Every leaf root include block must set expose = true (docs/terragrunt-concepts.md)."""
    content: str = request.getfixturevalue(content_fixture)
    assert "expose" in content and "true" in content, (
        f"{file_label} root include block must set expose = true. "
        "Required for include.root.locals.common_tags to be accessible "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "content_fixture,file_label",
    [
        ("dns_collector_terragrunt_hcl_content", "prod dns-collector terragrunt.hcl"),
        ("obs_terragrunt_hcl_content", "prod observability terragrunt.hcl"),
    ],
)
def test_leaf_root_include_deep_merge(
    request: pytest.FixtureRequest,
    content_fixture: str,
    file_label: str,
) -> None:
    """Every leaf root include block must set merge_strategy = 'deep'
    (docs/terragrunt-concepts.md)."""
    content: str = request.getfixturevalue(content_fixture)
    assert 'merge_strategy = "deep"' in content, (
        f'{file_label} root include block must set merge_strategy = "deep" '
        f"(docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_source(hcl_content: str) -> str:
    """Extract the resolved local module source path from a leaf terragrunt.hcl.

    Supports two source declaration forms introduced by E9-F3-S1-T1:
    1. Toggle-driven ternary: ``source = <cond> ? "pinned_url" : "local_path"``
       Returns the false/local branch (the ``${get_repo_root()}//...`` path) so
       parity comparisons use the canonical module path regardless of environment.
    2. Legacy literal: ``source = "..."``
       Returns the literal value unchanged.

    Raises AssertionError when no source assignment is found (AC-2, AC-3).
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
        "Every leaf terragrunt.hcl must declare exactly one source (AC-2, AC-3)."
    )
    return literal_match.group(1)
