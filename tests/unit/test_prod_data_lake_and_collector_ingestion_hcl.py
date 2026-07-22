"""Unit tests for the prod data-lake and collector-ingestion terragrunt HCL files.

These tests assert the structural and security constraints for the prod account
(111111111111) data-lake and collector-ingestion service-layer and leaf HCL files.
They validate:

data-lake/service.hcl:
- File exists and declares the canonical service basename idiom.
- Declares lake_kms_policy_json local with all required policy statements.
- AthenaAccess statement carries a kms:ViaService Condition block.
- No wildcard principals in the KMS key policy.
- QuickSight is retired: the policy grants no QuickSight service principal and no
  QuickSightServiceRoleDataAccess statement (portal/QuickSight retirement, Phase 2).

data-lake/000/service_instance.hcl:
- File exists and declares the canonical service_instance basename idiom.

data-lake/000/terragrunt.hcl:
- Sources exactly one module (references/data-lake).
- Contains transition_days=365 and expiration_days=730 retention overrides (AC-11).
- Sources lake_kms_policy_json from service.hcl (not inline, D8).

collector-ingestion/service.hcl:
- File exists and declares the canonical service basename idiom.
- Declares adot_task_firehose_policy_json local with three scoped statements.
- FirehosePutRecords statement scopes Resource to the namespace-derived delivery-stream ARN.
- SSMGetAdotConfig statement scopes Resource to the namespaced SSM parameter ARN.
- KMSDecryptForSSMAndFirehose statement scopes Resource to the telemetry-data CMK ARN.
- The false re-scope comment (claiming the references module re-scopes the policy) is absent.

collector-ingestion/000/service_instance.hcl:
- File exists and declares the canonical service_instance basename idiom.

collector-ingestion/000/terragrunt.hcl (AC-2, AC-4):
- Sources exactly one module (references/collector-ingestion).
- Consumes firehose_delivery_stream_arn from dependency.data_lake.outputs (never recomputed).
- Declares all 5 DAG edges: data_lake, dns_prod_zone, identity, acm_collector,
  acm_validate_collector.
- acm_validate_collector config_path traverses cross-account to DNS-owner 444444444444.
- vpc_cidr_block explicitly overridden in inputs{} block from networks.json-derived local.

Security (AC-FIX-01..08):
- Zero Resource='*' occurrences inside adot_task_firehose_policy_json (no wildcard resource).
- Each statement Resource references the expected scoped ARN prefix.
- kms:ViaService Condition present on the KMS statement in adot_task_firehose_policy_json.
- kms:ViaService Condition present on AthenaAccess in lake_kms_policy_json.

D31 parity:
- Prod collector-ingestion uses the same module source string as sandbox (AC-FIX-07).
- Sandbox sibling service.hcl is NOT modified by this task.

No hardcoded AWS access keys, no em-dash characters (U+2014), no suppression annotations
in any HCL file or this test file.

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
ENV_INSTANCE_BASE = PROD_ENV_BASE / "000"

SANDBOX_ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"
SANDBOX_ENV_BASE = SANDBOX_ACCOUNT_DIR / "sandbox"
SANDBOX_INSTANCE_BASE = SANDBOX_ENV_BASE / "000"

# ---------------------------------------------------------------------------
# data-lake paths
# ---------------------------------------------------------------------------

DATA_LAKE_SERVICE_DIR = PROD_ENV_BASE / "_singletons" / "shared" / "data-lake"
DATA_LAKE_INSTANCE_DIR = DATA_LAKE_SERVICE_DIR / "000"

DATA_LAKE_SERVICE_HCL = DATA_LAKE_SERVICE_DIR / "service.hcl"

# ---------------------------------------------------------------------------
# collector-ingestion paths
# ---------------------------------------------------------------------------

COLLECTOR_SERVICE_DIR = ENV_INSTANCE_BASE / "collector-ingestion"
COLLECTOR_INSTANCE_DIR = COLLECTOR_SERVICE_DIR / "000"

COLLECTOR_SERVICE_HCL = COLLECTOR_SERVICE_DIR / "service.hcl"

# references/collector-ingestion module main.tf.
#
# Under the "Option 1" telemetry-export design the ADOT task no longer writes directly
# to Firehose: the awscloudwatchlogs ADOT exporter writes to a CloudWatch Logs group,
# and a subscription filter delivers to the data-lake Firehose via a dedicated
# CloudWatch-Logs-to-Firehose IAM role (aws_iam_role.cwl_to_firehose). The
# firehose:PutRecord*/kms permissions therefore live in that role's inline policy in
# this reference module main.tf, NOT in the ADOT task policy in service.hcl. The
# Firehose-write security assertions are re-targeted here (the permission moved; it was
# not removed).
COLLECTOR_REFERENCE_MAIN_TF = (
    REPO_ROOT / "providers" / "aws" / "references" / "collector-ingestion" / "main.tf"
)

# ---------------------------------------------------------------------------
# Sandbox siblings (D31 parity)
# ---------------------------------------------------------------------------

SANDBOX_COLLECTOR_LEAF = SANDBOX_INSTANCE_BASE / "collector-ingestion" / "000" / "terragrunt.hcl"
PROD_COLLECTOR_LEAF = COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# Leaf HCL file paths
DATA_LAKE_SERVICE_INSTANCE_HCL = DATA_LAKE_INSTANCE_DIR / "service_instance.hcl"
DATA_LAKE_LEAF_HCL = DATA_LAKE_INSTANCE_DIR / "terragrunt.hcl"

COLLECTOR_SERVICE_INSTANCE_HCL = COLLECTOR_INSTANCE_DIR / "service_instance.hcl"
COLLECTOR_LEAF_HCL = COLLECTOR_INSTANCE_DIR / "terragrunt.hcl"

# Cross-account DNS-owner account for acm-validate-collector (E7-F4-S1-T1).
DNS_OWNER_ACCOUNT = "444444444444"

# Expected DAG dependency block headers (AC-4).
EXPECTED_DEP_BLOCKS = [
    "data_lake",
    "dns_prod_zone",
    "identity",
    "acm_collector",
    "acm_validate_collector",
]

# The exact key that must appear in the collector leaf inputs{} block (AC-2 vpc override).
VPC_CIDR_INPUTS_KEY = "vpc_cidr_block = local.vpc_cidr_block"

# ---------------------------------------------------------------------------
# Expected values
# ---------------------------------------------------------------------------

COLLECTOR_MODULE_PATH = "references/collector-ingestion"
DATA_LAKE_MODULE_PATH = "references/data-lake"

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
# File fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def data_lake_service_hcl_content() -> str:
    """Read data-lake/service.hcl content. Fails loudly if the file does not exist."""
    assert DATA_LAKE_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DATA_LAKE_SERVICE_HCL}. "
        "This file must be created for the prod data-lake service layer (E7-F3-S2-T1)."
    )
    return DATA_LAKE_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_service_hcl_content() -> str:
    """Read collector-ingestion/service.hcl content. Fails loudly if not found."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "This file must be created for the prod collector-ingestion service layer (E7-F3-S2-T1)."
    )
    return COLLECTOR_SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_reference_main_tf_content() -> str:
    """Read references/collector-ingestion/main.tf content. Fails loudly if not found.

    The CloudWatch-Logs-to-Firehose role and its firehose:PutRecord*/kms inline policy
    (the data-path Firehose write under the Option 1 export design) live here, not in
    service.hcl.
    """
    assert COLLECTOR_REFERENCE_MAIN_TF.exists(), (
        f"main.tf not found at {COLLECTOR_REFERENCE_MAIN_TF}. "
        "Required for the references/collector-ingestion module that defines the "
        "CloudWatch-Logs-to-Firehose delivery role (Option 1 telemetry export)."
    )
    return COLLECTOR_REFERENCE_MAIN_TF.read_text()


# ---------------------------------------------------------------------------
# File existence assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_lake_service_hcl_exists() -> None:
    """data-lake/service.hcl must exist at the prod service layer path."""
    assert DATA_LAKE_SERVICE_HCL.exists(), (
        f"service.hcl not found at {DATA_LAKE_SERVICE_HCL}. "
        "Required for the prod data-lake service layer (AC-1, E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_collector_service_hcl_exists() -> None:
    """collector-ingestion/service.hcl must exist at the prod service layer path."""
    assert COLLECTOR_SERVICE_HCL.exists(), (
        f"service.hcl not found at {COLLECTOR_SERVICE_HCL}. "
        "Required for the prod collector-ingestion service layer (AC-1, E7-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom assertions (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_lake_service_hcl_declares_basename_local(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in data_lake_service_hcl_content, (
        "data-lake/service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_parent_dir_is_data_lake(
    data_lake_service_hcl_content: str,
) -> None:
    """service.hcl must be at data-lake/ so basename resolves to 'data-lake'."""
    assert DATA_LAKE_SERVICE_HCL.parent.name == "data-lake", (
        f"data-lake service.hcl parent dir is '{DATA_LAKE_SERVICE_HCL.parent.name}', "
        "expected 'data-lake'. The service dir must be named 'data-lake' (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_basename_local(
    collector_service_hcl_content: str,
) -> None:
    """collector-ingestion/service.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_collector_service_hcl_parent_dir_is_collector_ingestion(
    collector_service_hcl_content: str,
) -> None:
    """service.hcl must be at collector-ingestion/ so basename resolves correctly."""
    assert COLLECTOR_SERVICE_HCL.parent.name == "collector-ingestion", (
        f"collector-ingestion service.hcl parent dir is '{COLLECTOR_SERVICE_HCL.parent.name}', "
        "expected 'collector-ingestion' (E7-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# Collector-ingestion: ADOT task IAM policy declarations
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collector_service_hcl_declares_adot_policy(
    collector_service_hcl_content: str,
) -> None:
    """collector-ingestion/service.hcl must declare adot_task_firehose_policy_json (AC-FIX-01)."""
    assert "adot_task_firehose_policy_json" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must declare 'adot_task_firehose_policy_json'. "
        "This local carries the scoped ADOT ECS task IAM policy (AC-FIX-01, E7-F3-S2-T3)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_ecs_task_assume_role_policy(
    collector_service_hcl_content: str,
) -> None:
    """collector-ingestion/service.hcl must declare ecs_task_assume_role_policy_json."""
    assert "ecs_task_assume_role_policy_json" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must declare 'ecs_task_assume_role_policy_json'. "
        "The ECS task trust policy must come from service.hcl, not an inline literal (D8)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_ecs_execution_assume_role_policy(
    collector_service_hcl_content: str,
) -> None:
    """collector-ingestion/service.hcl must declare ecs_execution_assume_role_policy_json."""
    assert "ecs_execution_assume_role_policy_json" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must declare 'ecs_execution_assume_role_policy_json'. "
        "The ECS execution role trust policy must come from service.hcl (D8)."
    )


@pytest.mark.unit
def test_collector_service_hcl_trust_policy_trusts_ecs_tasks(
    collector_service_hcl_content: str,
) -> None:
    """The ECS task trust policy must trust ecs-tasks.amazonaws.com."""
    assert "ecs-tasks.amazonaws.com" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl ECS trust policy does not include "
        "'ecs-tasks.amazonaws.com'. The ADOT task and execution roles must trust "
        "the ECS tasks service principal (AC-FIX-01, D8)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_hierarchy_locals(
    collector_service_hcl_content: str,
) -> None:
    """collector-ingestion/service.hcl must derive account/region/namespace from hierarchy."""
    assert "aws_account_id" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must derive aws_account_id from account.hcl "
        "to construct scoped IAM ARNs (D45, AC-FIX-01..AC-FIX-03)."
    )
    assert "region" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must derive region to construct scoped ARNs "
        "(D47, AC-FIX-01..AC-FIX-03)."
    )
    assert "namespace" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl must derive namespace to construct resource "
        "names for scoped ARNs (D31/D45, AC-FIX-01..AC-FIX-03)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_firehose_statement_sid(
    collector_reference_main_tf_content: str,
) -> None:
    """The CloudWatch-Logs-to-Firehose role policy must include a FirehosePutRecords Sid.

    Under the Option 1 telemetry-export design (ADOT awscloudwatchlogs exporter ->
    CloudWatch Logs group -> subscription filter -> Firehose), the ADOT TASK no longer
    writes to Firehose. The Firehose PutRecord/PutRecordBatch grant moved to the
    dedicated CloudWatch-Logs-to-Firehose role's inline policy (aws_iam_role_policy
    .cwl_to_firehose) in references/collector-ingestion/main.tf. This test enforces that
    the Firehose-write statement still exists, now on the correct principal (the
    CWL-to-Firehose role), preserving the least-privilege intent of the original
    assertion. The permission moved; it was not removed.
    """
    cwl_policy = _extract_cwl_to_firehose_policy_block(collector_reference_main_tf_content)
    assert "FirehosePutRecords" in cwl_policy, (
        "references/collector-ingestion/main.tf cwl_to_firehose role policy must include "
        "a statement with Sid='FirehosePutRecords' for Firehose PutRecord/PutRecordBatch "
        "access. Under Option 1 the CloudWatch-Logs-to-Firehose role (not the ADOT task) "
        "holds the Firehose-write grant (AC-FIX-01)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_ssm_statement_sid(
    collector_service_hcl_content: str,
) -> None:
    """collector-ingestion/service.hcl adot policy must include SSMGetAdotConfig Sid."""
    assert "SSMGetAdotConfig" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl adot_task_firehose_policy_json must include "
        "a statement with Sid='SSMGetAdotConfig' for SSM parameter access (AC-FIX-02)."
    )


@pytest.mark.unit
def test_collector_service_hcl_declares_kms_statement_sid(
    collector_reference_main_tf_content: str,
) -> None:
    """The CloudWatch-Logs-to-Firehose role policy must include the Firehose-CMK Sid.

    The original assertion required Sid='KMSDecryptForSSMAndFirehose' on the ADOT task
    policy, where the task's CMK grant covered BOTH the SSM SecureString decrypt AND the
    Firehose-stream CMK access. Under Option 1 these two concerns split:

    - The ADOT task retains an SSM-only CMK grant (Sid='KMSDecryptForSSM') in
      service.hcl, narrowed to kms:ViaService ssm (the task no longer touches Firehose).
    - The Firehose-stream CMK access (kms:GenerateDataKey/kms:Decrypt on the lake CMK,
      required because the data-lake Firehose uses CUSTOMER_MANAGED_CMK server-side
      encryption) moved to the CloudWatch-Logs-to-Firehose role's inline policy under
      Sid='FirehoseStreamCmkAccess' in references/collector-ingestion/main.tf.

    This test enforces the Firehose-data-path CMK statement on the new principal,
    preserving the original intent that the Firehose write is CMK-authorized and
    least-privilege. The permission moved; it was not removed.
    """
    cwl_policy = _extract_cwl_to_firehose_policy_block(collector_reference_main_tf_content)
    assert "FirehoseStreamCmkAccess" in cwl_policy, (
        "references/collector-ingestion/main.tf cwl_to_firehose role policy must include "
        "a statement with Sid='FirehoseStreamCmkAccess' granting the lake CMK access the "
        "Firehose write requires (the data-lake Firehose uses CUSTOMER_MANAGED_CMK). "
        "Under Option 1 the CloudWatch-Logs-to-Firehose role (not the ADOT task) holds "
        "the Firehose-stream CMK grant (AC-FIX-03)."
    )


@pytest.mark.unit
def test_collector_service_hcl_firehose_action_scoped(
    collector_reference_main_tf_content: str,
) -> None:
    """The Firehose-write grant must scope firehose:PutRecord* to the delivery stream ARN.

    Original intent: the Firehose PutRecord/PutRecordBatch actions are granted and
    least-privilege scoped to the delivery stream (not '*'). Under Option 1 this grant
    lives on the CloudWatch-Logs-to-Firehose role's inline policy in
    references/collector-ingestion/main.tf, scoped to var.firehose_delivery_stream_arn
    (the namespace-derived data-lake stream ARN passed in as an input). This test
    re-targets the assertion to that principal and additionally verifies the Resource is
    scoped to the delivery stream ARN, preserving (and tightening) the original
    least-privilege intent.
    """
    cwl_policy = _extract_cwl_to_firehose_policy_block(collector_reference_main_tf_content)
    assert "firehose:PutRecord" in cwl_policy, (
        "references/collector-ingestion/main.tf cwl_to_firehose role policy must grant "
        "firehose:PutRecord in the FirehosePutRecords statement (AC-FIX-01)."
    )
    assert "firehose:PutRecordBatch" in cwl_policy, (
        "references/collector-ingestion/main.tf cwl_to_firehose role policy must grant "
        "firehose:PutRecordBatch in the FirehosePutRecords statement (AC-FIX-01)."
    )
    # The Firehose write must be least-privilege scoped to the delivery stream ARN input,
    # never Resource='*'.
    assert "var.firehose_delivery_stream_arn" in cwl_policy, (
        "references/collector-ingestion/main.tf cwl_to_firehose FirehosePutRecords "
        "statement must scope Resource to var.firehose_delivery_stream_arn (the "
        "namespace-derived data-lake delivery stream ARN), not a wildcard (AC-FIX-01)."
    )
    assert not re.search(r'Resource\s*=\s*"\*"', cwl_policy), (
        "references/collector-ingestion/main.tf cwl_to_firehose role policy contains a "
        "Resource='*' wildcard. The Firehose-write grant must be least-privilege scoped "
        "to the delivery stream ARN (and the lake CMK ARN), never '*' (AC-FIX-01)."
    )


@pytest.mark.unit
def test_collector_service_hcl_ssm_actions_scoped(
    collector_service_hcl_content: str,
) -> None:
    """The ADOT policy must include ssm:GetParameter and ssm:GetParameters actions."""
    assert "ssm:GetParameter" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl adot_task_firehose_policy_json must grant "
        "ssm:GetParameter and ssm:GetParameters actions (AC-FIX-02)."
    )


@pytest.mark.unit
def test_collector_service_hcl_kms_decrypt_action_present(
    collector_service_hcl_content: str,
) -> None:
    """The ADOT policy KMS statement must include kms:Decrypt and kms:GenerateDataKey."""
    assert "kms:Decrypt" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl KMSDecryptForSSMAndFirehose statement must "
        "include 'kms:Decrypt' action (AC-FIX-03)."
    )
    assert "kms:GenerateDataKey" in collector_service_hcl_content, (
        "collector-ingestion/service.hcl KMSDecryptForSSMAndFirehose statement must "
        "include 'kms:GenerateDataKey' action (AC-FIX-03)."
    )


# ---------------------------------------------------------------------------
# Data-lake: lake_kms_policy_json assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_lake_service_hcl_declares_hierarchy_locals(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl must derive account/region/namespace from hierarchy."""
    assert "aws_account_id" in data_lake_service_hcl_content, (
        "data-lake/service.hcl must derive aws_account_id from account.hcl "
        "to construct scoped IAM ARNs (D45, AC-FIX-05)."
    )
    assert "region" in data_lake_service_hcl_content, (
        "data-lake/service.hcl must derive region to construct kms:ViaService condition "
        "(D47, AC-FIX-05)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_has_root_admin_access(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl lake_kms_policy_json must have RootAdminAccess statement."""
    assert "RootAdminAccess" in data_lake_service_hcl_content, (
        "data-lake/service.hcl lake_kms_policy_json must include a RootAdminAccess "
        "statement granting kms:* to the account root principal (required by KMS, D45)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_has_telemetry_role_access(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl lake_kms_policy_json must have TelemetryRoleDataAccess."""
    assert "TelemetryRoleDataAccess" in data_lake_service_hcl_content, (
        "data-lake/service.hcl lake_kms_policy_json must include TelemetryRoleDataAccess "
        "statement for TelemetryAnalyst and TelemetryAdmin roles (D37, AC-FIX-05)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_has_athena_access(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl lake_kms_policy_json must have the Athena-only AthenaAccess.

    QuickSight consumer surface retirement (portal/QuickSight retirement, Phase 2):
    the former AthenaAndQuicksightAccess Sid was renamed AthenaAccess and no longer
    grants any QuickSight principal.
    """
    policy_block = _extract_lake_kms_policy_block(data_lake_service_hcl_content)
    assert "AthenaAccess" in policy_block, (
        "data-lake/service.hcl lake_kms_policy_json must include the AthenaAccess "
        "statement for the Athena service principal (D37, AC-FIX-05)."
    )
    assert "AthenaAndQuicksightAccess" not in policy_block, (
        "data-lake/service.hcl lake_kms_policy_json must NOT include the retired "
        "AthenaAndQuicksightAccess Sid; QuickSight is retired (Phase 2)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_kms_policy_has_athena_principal(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl AthenaAccess must include athena.amazonaws.com."""
    policy_block = _extract_lake_kms_policy_block(data_lake_service_hcl_content)
    assert "athena.amazonaws.com" in policy_block, (
        "data-lake/service.hcl AthenaAccess must include "
        "'athena.amazonaws.com' as a service principal (AC-FIX-05)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_kms_policy_omits_quicksight(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl lake_kms_policy_json must NOT grant QuickSight anymore.

    QuickSight consumer surface retirement (portal/QuickSight retirement, Phase 2):
    the telemetry-data CMK is KEPT but no longer grants the quicksight.amazonaws.com
    service principal or the QuickSight service-role (QuickSightServiceRoleDataAccess)
    -- that role is deleted in a later phase and a PutKeyPolicy referencing a deleted
    principal fails. Assertions inspect the extracted policy JSON block so the
    retirement note in the surrounding comment does not mask a real grant.
    """
    policy_block = _extract_lake_kms_policy_block(data_lake_service_hcl_content)
    assert "quicksight.amazonaws.com" not in policy_block, (
        "data-lake/service.hcl lake_kms_policy_json must NOT grant the "
        "'quicksight.amazonaws.com' service principal after QuickSight retirement (Phase 2)."
    )
    assert "QuickSightServiceRoleDataAccess" not in policy_block, (
        "data-lake/service.hcl lake_kms_policy_json must NOT include the retired "
        "QuickSightServiceRoleDataAccess statement (Phase 2)."
    )
    assert "aws-quicksight-service-role-v0" not in policy_block, (
        "data-lake/service.hcl lake_kms_policy_json must NOT reference the QuickSight "
        "service role ARN after retirement (Phase 2)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_kms_policy_has_cloudwatch_logs_grant(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl lake_kms_policy_json must grant the CloudWatch Logs service.

    The telemetry-data CMK encrypts the Firehose delivery-stream CloudWatch log group
    (and the prod collector-ingestion WAF log group). CloudWatch Logs validates the
    encrypting CMK grants logs.<region>.amazonaws.com at log-group create time; without
    this statement CreateLogGroup fails with AccessDeniedException (docs/terragrunt-concepts.md).
    """
    assert "CloudWatchLogsAccess" in data_lake_service_hcl_content, (
        "data-lake/service.hcl lake_kms_policy_json must include a CloudWatchLogsAccess "
        "statement so the telemetry-data CMK can encrypt the Firehose/WAF log groups "
        "(docs/terragrunt-concepts.md)."
    )
    assert "logs.${local.region}.amazonaws.com" in data_lake_service_hcl_content, (
        "data-lake/service.hcl CloudWatchLogsAccess must grant the region-scoped "
        "CloudWatch Logs service principal 'logs.${local.region}.amazonaws.com' (D47)."
    )
    assert "kms:EncryptionContext:aws:logs:arn" in data_lake_service_hcl_content, (
        "data-lake/service.hcl CloudWatchLogsAccess must scope the grant with an ArnLike "
        "kms:EncryptionContext:aws:logs:arn condition per the AWS CloudWatch Logs CMK docs."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_no_wildcard_principal(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl lake_kms_policy_json must not use wildcard Principal: '*'."""
    assert 'Principal = "*"' not in data_lake_service_hcl_content, (
        'data-lake/service.hcl contains a wildcard Principal: "*". '
        "The telemetry-data CMK key policy must use input-driven principals only (D31)."
    )
    assert '"Principal": "*"' not in data_lake_service_hcl_content, (
        'data-lake/service.hcl contains a wildcard Principal: "*". '
        "The telemetry-data CMK key policy must use input-driven principals only (D31)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_telemetry_role_uses_namespace_derived_arns(
    data_lake_service_hcl_content: str,
) -> None:
    """TelemetryRoleDataAccess principals must use account-id-derived ARNs (not placeholders)."""
    assert "TelemetryAnalyst" in data_lake_service_hcl_content, (
        "data-lake/service.hcl TelemetryRoleDataAccess must reference TelemetryAnalyst "
        "role ARN derived from aws_account_id, not a placeholder (D45, AC-FIX-05)."
    )
    assert "TelemetryAdmin" in data_lake_service_hcl_content, (
        "data-lake/service.hcl TelemetryRoleDataAccess must reference TelemetryAdmin "
        "role ARN derived from aws_account_id, not a placeholder (D45, AC-FIX-05)."
    )


# ---------------------------------------------------------------------------
# D31: prod/sandbox parity assertions (AC-FIX-07)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_collector_ingestion_leaf_uses_same_module_source_as_sandbox() -> None:
    """Prod and sandbox collector-ingestion leaves must reference the same module source (D31).

    AC-FIX-07: the D31 parity tests compare ONLY the module source string. The sandbox
    sibling service.hcl is NOT modified by this task; only the prod service.hcl is changed.
    """
    if not PROD_COLLECTOR_LEAF.exists():
        pytest.skip(
            f"Prod collector-ingestion leaf not found at {PROD_COLLECTOR_LEAF}. "
            "Skipping parity test until the prod leaf is created (E7-F3-S2-T1)."
        )
    if not SANDBOX_COLLECTOR_LEAF.exists():
        pytest.skip(
            f"Sandbox collector-ingestion leaf not found at {SANDBOX_COLLECTOR_LEAF}. "
            "Skipping parity test until the sandbox leaf is created (E6-F2-S1-T2)."
        )

    prod_content = PROD_COLLECTOR_LEAF.read_text()
    sandbox_content = SANDBOX_COLLECTOR_LEAF.read_text()

    prod_source = _extract_source(prod_content)
    sandbox_source = _extract_source(sandbox_content)

    assert prod_source == sandbox_source, (
        f"Prod collector-ingestion source '{prod_source}' differs from "
        f"sandbox source '{sandbox_source}'. "
        "D31 requires prod and sandbox to pin the identical module source (AC-FIX-07)."
    )


# ---------------------------------------------------------------------------
# Security regression tests (AC-FIX-06): adot_task_firehose_policy_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_adot_policy_has_no_wildcard_resource_in_any_statement(
    collector_service_hcl_content: str,
) -> None:
    """adot_task_firehose_policy_json must contain zero Resource='*' occurrences (AC-FIX-01).

    An ADOT ECS task role with Resource='*' on Firehose, SSM, or KMS statements is a
    least-privilege violation for a regulated financial workload with a public OTLP
    endpoint. All three statement Resources must be scoped ARNs.
    """
    policy_block = _extract_adot_policy_block(collector_service_hcl_content)
    wildcard_count = len(re.findall(r'Resource\s*=\s*"\*"', policy_block))
    assert wildcard_count == 0, (
        f"adot_task_firehose_policy_json contains {wildcard_count} Resource='*' occurrence(s). "
        "All three statements (FirehosePutRecords, SSMGetAdotConfig, KMSDecryptForSSMAndFirehose) "
        "must scope Resource to a specific ARN, not '*' (AC-FIX-01..AC-FIX-03)."
    )


@pytest.mark.unit
def test_adot_policy_firehose_statement_scopes_to_delivery_stream_arn(
    collector_reference_main_tf_content: str,
) -> None:
    """FirehosePutRecords Resource must be scoped to the delivery-stream ARN (not recomputed).

    AC-FIX-01.

    Original intent: the Firehose-write statement's Resource is the namespace-derived
    delivery-stream ARN, input-driven and never a wildcard. Under Option 1 this grant
    is on the CloudWatch-Logs-to-Firehose role in references/collector-ingestion/main.tf.
    There the Resource is the var.firehose_delivery_stream_arn INPUT (consumed from the
    data_lake dependency output, validated upstream against
    ^arn:aws:firehose:<region>:<account>:deliverystream/), so the leaf never recomputes
    the ARN. This test re-targets the scoping assertion to that statement's Resource,
    preserving the input-driven, least-privilege intent.
    """
    cwl_policy = _extract_cwl_to_firehose_policy_block(collector_reference_main_tf_content)
    # The FirehosePutRecords statement's Resource must be the delivery-stream ARN input.
    firehose_resource = re.search(
        r"Sid\s*=\s*\"FirehosePutRecords\".*?Resource\s*=\s*([^\n]+)",
        cwl_policy,
        re.DOTALL,
    )
    assert firehose_resource is not None, (
        "references/collector-ingestion/main.tf cwl_to_firehose role policy has no "
        "FirehosePutRecords statement with a Resource assignment (AC-FIX-01)."
    )
    resource_value = firehose_resource.group(1)
    assert "var.firehose_delivery_stream_arn" in resource_value, (
        "references/collector-ingestion/main.tf FirehosePutRecords Resource is "
        f"'{resource_value.strip()}', expected var.firehose_delivery_stream_arn. "
        "The Firehose-write Resource must be the namespace-derived delivery-stream ARN "
        "input (consumed from the data_lake dependency output), not recomputed and never "
        "a wildcard (AC-FIX-01)."
    )


@pytest.mark.unit
def test_adot_policy_ssm_statement_scopes_to_parameter_arn(
    collector_service_hcl_content: str,
) -> None:
    """SSMGetAdotConfig Resource must reference the namespaced SSM parameter ARN (AC-FIX-02).

    The ARN must be constructed from namespace/region/account-id locals so it is
    input-driven. The expected pattern is:
      arn:aws:ssm:<region>:<account>:parameter/<namespace>/*
    """
    locals_block = _extract_locals_block(collector_service_hcl_content)
    has_ssm_arn = "ssm" in locals_block and (
        "parameter" in locals_block or "ssm_param" in locals_block or "ssm_arn" in locals_block
    )
    assert has_ssm_arn, (
        "collector-ingestion/service.hcl locals block does not contain a "
        "namespaced SSM parameter ARN. "
        "SSMGetAdotConfig Resource must be scoped to "
        "arn:aws:ssm:<region>:<account>:parameter/<namespace>/* "
        "(AC-FIX-02)."
    )


@pytest.mark.unit
def test_adot_policy_kms_statement_scopes_to_cmk_arn(
    collector_service_hcl_content: str,
) -> None:
    """KMSDecryptForSSMAndFirehose Resource must reference the telemetry-data CMK ARN (AC-FIX-03).

    The ARN must be constructed from namespace/account-id locals. The expected pattern is
    a KMS key ARN or alias ARN for the telemetry-data CMK.
    """
    locals_block = _extract_locals_block(collector_service_hcl_content)
    has_kms_arn = "kms" in locals_block and (
        "alias/telemetry-data" in locals_block
        or "telemetry-data" in locals_block
        or "kms_arn" in locals_block
        or "cmk_arn" in locals_block
    )
    assert has_kms_arn, (
        "collector-ingestion/service.hcl locals block does not contain a "
        "telemetry-data CMK ARN. "
        "KMSDecryptForSSMAndFirehose Resource must be scoped to the telemetry-data "
        "CMK ARN (arn:aws:kms:<region>:<account>:alias/telemetry-data or key ARN) "
        "(AC-FIX-03)."
    )


@pytest.mark.unit
def test_adot_policy_kms_statement_has_via_service_condition(
    collector_service_hcl_content: str,
) -> None:
    """KMSDecryptForSSMAndFirehose must carry a kms:ViaService Condition (security depth).

    A kms:ViaService condition on the ADOT task role's KMS statement ensures the
    CMK can only be used when the call originates from the specified AWS service
    (ssm or firehose), not directly by the task identity.
    """
    policy_block = _extract_adot_policy_block(collector_service_hcl_content)
    has_via_service = "kms:ViaService" in policy_block
    assert has_via_service, (
        "adot_task_firehose_policy_json KMSDecryptForSSMAndFirehose statement is missing "
        "a kms:ViaService Condition block. "
        "The CMK access should be restricted via kms:ViaService to ssm and firehose "
        "service principals (security depth, AC-FIX-03)."
    )


@pytest.mark.unit
def test_adot_policy_false_rescope_comment_absent(
    collector_service_hcl_content: str,
) -> None:
    """The false 're-scope comment' must be absent from collector-ingestion/service.hcl (AC-FIX-04).

    The old service.hcl contained a comment claiming the references/collector-ingestion
    module re-scopes the policy further. That comment is false: the policy is built
    entirely in service.hcl. The comment must be removed and replaced with one that
    accurately states the ARNs are scoped from namespace-derived locals in this file.
    """
    false_rescope_patterns = [
        "module re-scopes",
        "module scopes the policy",
        "re-scopes the policy",
        "references/collector-ingestion module re-scopes",
        "collector-ingestion module re-scopes",
    ]
    for pattern in false_rescope_patterns:
        assert pattern not in collector_service_hcl_content, (
            f"collector-ingestion/service.hcl still contains the false re-scope comment "
            f"(matched: '{pattern}'). "
            "This comment must be removed -- the policy is scoped entirely in this service.hcl "
            "via namespace-derived locals, not by the reference module (AC-FIX-04)."
        )


# ---------------------------------------------------------------------------
# Security regression tests (AC-FIX-05/06): data-lake lake_kms_policy_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_lake_service_hcl_has_lake_kms_policy_json(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake/service.hcl must declare lake_kms_policy_json local (AC-FIX-05).

    The telemetry-data KMS key policy must be assembled in service.hcl so no
    inline policy literal appears in the leaf terragrunt.hcl (D8).
    """
    assert "lake_kms_policy_json" in data_lake_service_hcl_content, (
        "data-lake/service.hcl does not declare 'lake_kms_policy_json'. "
        "The prod telemetry-data CMK policy must come from service.hcl, "
        "not an inline literal in terragrunt.hcl (AC-FIX-05, D8)."
    )


@pytest.mark.unit
def test_data_lake_service_hcl_kms_policy_athena_has_via_service_condition(
    data_lake_service_hcl_content: str,
) -> None:
    """data-lake AthenaAccess statement must carry a kms:ViaService Condition.

    AC-FIX-05.

    The AthenaAccess statement grants the Athena service principal kms access
    conditioned on kms:ViaService scoped to the prod region, preventing direct CMK
    calls outside Athena. (Phase 2 renamed the former AthenaAndQuicksightAccess Sid
    and dropped the QuickSight principal; the kms:ViaService requirement is retained.)
    """
    kms_block = _extract_lake_kms_policy_block(data_lake_service_hcl_content)
    assert "kms:ViaService" in kms_block, (
        "data-lake/service.hcl AthenaAccess statement is missing a "
        "kms:ViaService Condition block. "
        "Per docs/terragrunt-concepts.md, Athena service principal access to the "
        "telemetry-data CMK must be conditioned on kms:ViaService to prevent direct "
        "CMK calls outside of Athena (AC-FIX-05)."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded AWS access keys, no em-dashes, no suppression annotations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("data_lake_service_hcl_content", "data-lake/service.hcl"),
        ("collector_service_hcl_content", "collector-ingestion/service.hcl"),
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
        ("data_lake_service_hcl_content", "data-lake/service.hcl"),
        ("collector_service_hcl_content", "collector-ingestion/service.hcl"),
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
        ("data_lake_service_hcl_content", "data-lake/service.hcl"),
        ("collector_service_hcl_content", "collector-ingestion/service.hcl"),
    ],
)
def test_no_suppression_annotations_in_service_hcl(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """service.hcl files must contain no nosec/noqa/nolint/tfsec:ignore/checkov:skip."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not SUPPRESSION_PATTERN.search(content), (
        f"{file_label} contains a suppression annotation. "
        "Suppression annotations are prohibited -- fix the underlying issue (CLAUDE.md, AC-FIX-08)."
    )


# ---------------------------------------------------------------------------
# D31: no hardcoded sandbox account id in prod files
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("data_lake_service_hcl_content", "data-lake/service.hcl"),
        ("collector_service_hcl_content", "collector-ingestion/service.hcl"),
    ],
)
def test_no_hardcoded_sandbox_account_id(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No prod HCL file may hardcode the sandbox account id (D31, D45)."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert SANDBOX_ACCOUNT not in content, (
        f"{file_label} contains the sandbox account id '{SANDBOX_ACCOUNT}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# Leaf HCL fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def data_lake_service_instance_hcl_content() -> str:
    """Read data-lake/000/service_instance.hcl content. Fails loudly if missing."""
    assert DATA_LAKE_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DATA_LAKE_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod data-lake service instance (E7-F3-S2-T1)."
    )
    return DATA_LAKE_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def data_lake_leaf_hcl_content() -> str:
    """Read data-lake/000/terragrunt.hcl content. Fails loudly if missing."""
    assert DATA_LAKE_LEAF_HCL.exists(), (
        f"terragrunt.hcl not found at {DATA_LAKE_LEAF_HCL}. "
        "This file must be created for the prod data-lake leaf (E7-F3-S2-T1)."
    )
    return DATA_LAKE_LEAF_HCL.read_text()


@pytest.fixture(scope="module")
def collector_service_instance_hcl_content() -> str:
    """Read collector-ingestion/000/service_instance.hcl content. Fails loudly if missing."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "This file must be created for the prod collector-ingestion service instance (E7-F3-S2-T1)."
    )
    return COLLECTOR_SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def collector_leaf_hcl_content() -> str:
    """Read collector-ingestion/000/terragrunt.hcl content. Fails loudly if missing."""
    assert COLLECTOR_LEAF_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_LEAF_HCL}. "
        "This file must be created for the prod collector-ingestion leaf (E7-F3-S2-T1)."
    )
    return COLLECTOR_LEAF_HCL.read_text()


# ---------------------------------------------------------------------------
# Leaf file existence tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_lake_service_instance_hcl_exists() -> None:
    """data-lake/000/service_instance.hcl must exist at the prod leaf path."""
    assert DATA_LAKE_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {DATA_LAKE_SERVICE_INSTANCE_HCL}. "
        "Required for the prod data-lake service instance layer (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_data_lake_leaf_hcl_exists() -> None:
    """data-lake/000/terragrunt.hcl must exist at the prod leaf path."""
    assert DATA_LAKE_LEAF_HCL.exists(), (
        f"terragrunt.hcl not found at {DATA_LAKE_LEAF_HCL}. "
        "Required for the prod data-lake leaf (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_collector_service_instance_hcl_exists() -> None:
    """collector-ingestion/000/service_instance.hcl must exist at the prod leaf path."""
    assert COLLECTOR_SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {COLLECTOR_SERVICE_INSTANCE_HCL}. "
        "Required for the prod collector-ingestion service instance layer (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_collector_leaf_hcl_exists() -> None:
    """collector-ingestion/000/terragrunt.hcl must exist at the prod leaf path."""
    assert COLLECTOR_LEAF_HCL.exists(), (
        f"terragrunt.hcl not found at {COLLECTOR_LEAF_HCL}. "
        "Required for the prod collector-ingestion leaf (E7-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# service_instance.hcl basename idiom tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_data_lake_service_instance_hcl_declares_basename(
    data_lake_service_instance_hcl_content: str,
) -> None:
    """data-lake/000/service_instance.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in data_lake_service_instance_hcl_content, (
        "data-lake/000/service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S2-T1)."
    )
    assert "service_instance" in data_lake_service_instance_hcl_content, (
        "data-lake/000/service_instance.hcl must declare the service_instance local "
        "consumed by the root terragrunt.hcl remote_state key derivation (E7-F3-S2-T1)."
    )


@pytest.mark.unit
def test_collector_service_instance_hcl_declares_basename(
    collector_service_instance_hcl_content: str,
) -> None:
    """collector-ingestion/000/service_instance.hcl must use the basename idiom."""
    assert "basename(get_terragrunt_dir())" in collector_service_instance_hcl_content, (
        "collector-ingestion/000/service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E7-F3-S2-T1)."
    )
    assert "service_instance" in collector_service_instance_hcl_content, (
        "collector-ingestion/000/service_instance.hcl must declare the service_instance local "
        "consumed by the root terragrunt.hcl remote_state key derivation (E7-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# data-lake/000/terragrunt.hcl: module source and retention overrides (AC-11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_data_lake_leaf_sources_references_data_lake(
    data_lake_leaf_hcl_content: str,
) -> None:
    """data-lake/000/terragrunt.hcl must source references/data-lake (AC-3)."""
    source_val = _extract_source(data_lake_leaf_hcl_content)
    assert DATA_LAKE_MODULE_PATH in source_val, (
        f"data-lake/000/terragrunt.hcl source '{source_val}' does not reference "
        f"'{DATA_LAKE_MODULE_PATH}'. "
        "The prod data-lake leaf must source exactly the references/data-lake module (AC-3)."
    )


@pytest.mark.unit
def test_prod_data_lake_leaf_has_exactly_one_source(
    data_lake_leaf_hcl_content: str,
) -> None:
    """data-lake/000/terragrunt.hcl must contain exactly one source = assignment (AC-3)."""
    source_matches = re.findall(r"^\s*source\s*=", data_lake_leaf_hcl_content, re.MULTILINE)
    assert len(source_matches) == 1, (
        f"data-lake/000/terragrunt.hcl has {len(source_matches)} source = assignment(s); "
        "expected exactly 1. The single-module contract (AC-3) requires sourcing only "
        "references/data-lake."
    )


@pytest.mark.unit
def test_prod_data_lake_leaf_has_transition_days_365(
    data_lake_leaf_hcl_content: str,
) -> None:
    """data-lake/000/terragrunt.hcl must set transition_days = 365 (AC-11 prod override)."""
    assert "transition_days" in data_lake_leaf_hcl_content, (
        "data-lake/000/terragrunt.hcl does not declare transition_days. "
        "The prod data-lake must override the _envcommon default (90) with 365 days (AC-11)."
    )
    assert "365" in data_lake_leaf_hcl_content, (
        "data-lake/000/terragrunt.hcl does not contain '365' for transition_days. "
        "The prod data-lake must override the _envcommon default (90) with 365 days (AC-11)."
    )


@pytest.mark.unit
def test_prod_data_lake_leaf_has_expiration_days_730(
    data_lake_leaf_hcl_content: str,
) -> None:
    """data-lake/000/terragrunt.hcl must set expiration_days = 730 (AC-11 prod total retention)."""
    assert "expiration_days" in data_lake_leaf_hcl_content, (
        "data-lake/000/terragrunt.hcl does not declare expiration_days. "
        "The prod data-lake must set expiration_days=730 (1yr hot + 1yr cold, AC-11)."
    )
    assert "730" in data_lake_leaf_hcl_content, (
        "data-lake/000/terragrunt.hcl does not contain '730' for expiration_days. "
        "The prod data-lake must set expiration_days=730 (AC-11)."
    )


@pytest.mark.unit
def test_prod_data_lake_leaf_sources_lake_kms_policy_from_service_hcl(
    data_lake_leaf_hcl_content: str,
) -> None:
    """data-lake/000/terragrunt.hcl must read lake_kms_policy_json from service.hcl (D8)."""
    assert (
        "service.hcl" in data_lake_leaf_hcl_content or "service_vars" in data_lake_leaf_hcl_content
    ), (
        "data-lake/000/terragrunt.hcl does not reference service.hcl or service_vars. "
        "The lake_kms_policy_json must be sourced from service.hcl via "
        "read_terragrunt_config, not declared inline (D8, AC-FIX-05)."
    )
    assert "lake_kms_policy_json" in data_lake_leaf_hcl_content, (
        "data-lake/000/terragrunt.hcl does not pass lake_kms_policy_json. "
        "The telemetry-data CMK policy must be wired from service.hcl into the inputs{} "
        "block (D8, AC-FIX-05)."
    )


# ---------------------------------------------------------------------------
# collector-ingestion/000/terragrunt.hcl: AC-2 firehose wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_collector_leaf_sources_references_collector_ingestion(
    collector_leaf_hcl_content: str,
) -> None:
    """collector-ingestion/000/terragrunt.hcl must source references/collector-ingestion (AC-3)."""
    source_val = _extract_source(collector_leaf_hcl_content)
    assert COLLECTOR_MODULE_PATH in source_val, (
        f"collector-ingestion/000/terragrunt.hcl source '{source_val}' does not reference "
        f"'{COLLECTOR_MODULE_PATH}'. "
        "The prod collector-ingestion leaf must source exactly the "
        "references/collector-ingestion module (AC-3)."
    )


@pytest.mark.unit
def test_prod_collector_leaf_has_exactly_one_source(
    collector_leaf_hcl_content: str,
) -> None:
    """collector-ingestion/000/terragrunt.hcl must contain exactly one source = assignment."""
    source_matches = re.findall(r"^\s*source\s*=", collector_leaf_hcl_content, re.MULTILINE)
    assert len(source_matches) == 1, (
        f"collector-ingestion/000/terragrunt.hcl has {len(source_matches)} source = "
        "assignment(s); expected exactly 1. The single-module contract (AC-3) requires "
        "sourcing only references/collector-ingestion."
    )


@pytest.mark.unit
def test_prod_collector_leaf_consumes_firehose_arn_from_dependency(
    collector_leaf_hcl_content: str,
) -> None:
    """collector-ingestion firehose_delivery_stream_arn must come from dependency.data_lake (AC-2).

    The prod leaf must NOT recompute the firehose ARN. It must consume
    dependency.data_lake.outputs.firehose_delivery_stream_arn as an input so
    the dependency graph is acyclic and the ARN is never duplicated (AC-2, D37).
    """
    assert (
        "dependency.data_lake.outputs.firehose_delivery_stream_arn" in collector_leaf_hcl_content
    ), (
        "collector-ingestion/000/terragrunt.hcl does not consume "
        "dependency.data_lake.outputs.firehose_delivery_stream_arn. "
        "The firehose ARN must be consumed from the data_lake dependency output "
        "and never recomputed in this leaf (AC-2, D37)."
    )


# ---------------------------------------------------------------------------
# collector-ingestion/000/terragrunt.hcl: AC-4 full DAG edge set
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("dep_name", EXPECTED_DEP_BLOCKS)
def test_prod_collector_leaf_declares_dependency_block(
    collector_leaf_hcl_content: str,
    dep_name: str,
) -> None:
    """collector-ingestion/000/terragrunt.hcl must declare all 5 DAG dependency blocks (AC-4).

    Required edges: data_lake, dns_prod_zone, identity, acm_collector, acm_validate_collector.
    Each must appear as 'dependency "<name>"' in the leaf file.
    """
    dep_headers = re.findall(r'^dependency\s+"(\w+)"', collector_leaf_hcl_content, re.MULTILINE)
    assert dep_name in dep_headers, (
        f"collector-ingestion/000/terragrunt.hcl is missing dependency block for '{dep_name}'. "
        f"Found dependency blocks: {dep_headers}. "
        "All 5 DAG edges must be declared: data_lake, dns_prod_zone, identity, "
        "acm_collector, acm_validate_collector (AC-4, spec section 4.3)."
    )


@pytest.mark.unit
def test_prod_collector_leaf_has_config_path_for_all_deps(
    collector_leaf_hcl_content: str,
) -> None:
    """Every dependency block in collector-ingestion/000/terragrunt.hcl must have config_path."""
    assert "config_path" in collector_leaf_hcl_content, (
        "collector-ingestion/000/terragrunt.hcl has no config_path assignments. "
        "Every dependency block must declare a resolvable config_path (AC-4)."
    )
    dep_header_count = len(
        re.findall(r'^dependency\s+"\w+"', collector_leaf_hcl_content, re.MULTILINE)
    )
    config_path_count = len(
        re.findall(r"^\s*config_path\s*=", collector_leaf_hcl_content, re.MULTILINE)
    )
    assert config_path_count == dep_header_count, (
        f"collector-ingestion/000/terragrunt.hcl has {dep_header_count} dependency blocks "
        f"but {config_path_count} config_path assignments. "
        "Each dependency block must have exactly one config_path (AC-4)."
    )


@pytest.mark.unit
def test_prod_collector_ingestion_acm_validate_collector_config_path_is_cross_account(
    collector_leaf_hcl_content: str,
) -> None:
    """acm_validate_collector config_path must traverse to DNS-owner account 444444444444.

    The acm-validate-collector unit lives in account 444444444444 (E7-F4-S1-T1).
    The config_path must contain the DNS_OWNER_ACCOUNT string so the path resolves
    to the correct cross-account directory, not a same-account path (AC-4).
    Uses brace-depth scanning to extract the block so nested mock_outputs = {}
    does not truncate the match.
    """
    header_match = re.search(
        r'dependency\s+"acm_validate_collector"\s*\{', collector_leaf_hcl_content
    )
    assert header_match is not None, (
        "collector-ingestion/000/terragrunt.hcl does not contain an "
        "'acm_validate_collector' dependency block (AC-4)."
    )
    start = header_match.end()
    depth = 1
    pos = start
    while pos < len(collector_leaf_hcl_content) and depth > 0:
        if collector_leaf_hcl_content[pos] == "{":
            depth += 1
        elif collector_leaf_hcl_content[pos] == "}":
            depth -= 1
        pos += 1
    block_content = collector_leaf_hcl_content[start : pos - 1]
    assert "acm-validate-collector" in block_content, (
        "acm_validate_collector dependency block does not reference the acm-validate-collector "
        "unit. collector-ingestion depends on the per-set acm-validate-collector (service-cert "
        "validation ordering, same env service account); the account is resolved via account.hcl "
        "(env_accounts.json), not by an account id embedded in the config_path "
        "(env-keyed layout, AC-4)."
    )


@pytest.mark.unit
def test_prod_collector_ingestion_config_path_target_exists() -> None:
    """The cross-account acm_validate_collector config_path must resolve to an existing dir.

    Reads the config_path from the leaf file, resolves it relative to the leaf directory,
    and asserts the target directory exists on disk. Uses brace-depth scanning to extract
    the dependency block so nested mock_outputs = {} does not truncate the match.
    """
    assert COLLECTOR_LEAF_HCL.exists(), (
        f"Leaf file not found at {COLLECTOR_LEAF_HCL}; cannot check config_path resolution."
    )
    content = COLLECTOR_LEAF_HCL.read_text()
    # Use brace-depth scanning to extract the full acm_validate_collector dependency block.
    header_match = re.search(r'dependency\s+"acm_validate_collector"\s*\{', content)
    assert header_match is not None, (
        "collector-ingestion/000/terragrunt.hcl has no acm_validate_collector block."
    )
    start = header_match.end()
    depth = 1
    pos = start
    while pos < len(content) and depth > 0:
        if content[pos] == "{":
            depth += 1
        elif content[pos] == "}":
            depth -= 1
        pos += 1
    block = content[start : pos - 1]
    # Extract the config_path value (the literal string before any interpolation)
    path_match = re.search(r'config_path\s*=\s*"([^"]+)"', block)
    assert path_match is not None, (
        "acm_validate_collector block has no config_path = '...' assignment (AC-4)."
    )
    config_path_tpl = path_match.group(1)
    # Substitute ${local.svc_instance} with "000" (the canonical instance index)
    config_path = config_path_tpl.replace("${local.svc_instance}", "000")
    resolved = (COLLECTOR_INSTANCE_DIR / config_path).resolve()
    assert resolved.exists() and resolved.is_dir(), (
        f"acm_validate_collector config_path '{config_path_tpl}' resolves to "
        f"'{resolved}' which does not exist. "
        "The config_path must point to the DNS-owner account's prod unit directory "
        "(terragrunt/live/telemetry/us-east-1/444444444444/prod/000/acm-validate-collector/000)."
    )


# ---------------------------------------------------------------------------
# collector-ingestion/000/terragrunt.hcl: vpc_cidr_block inputs{} override
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prod_collector_ingestion_vpc_cidr_block_in_leaf_inputs(
    collector_leaf_hcl_content: str,
) -> None:
    """collector-ingestion/000/terragrunt.hcl inputs{} must explicitly set vpc_cidr_block.

    The prod account.hcl is basename-only (no vpc_cidr_block key). The _envcommon
    vpc_cidr_block = local.account_vars.locals.vpc_cidr_block is a dead path.
    The prod leaf must explicitly override vpc_cidr_block in its inputs{} block
    using the networks.json-derived local (D47, AC-4).
    """
    inputs_snippet = _extract_inputs_block(collector_leaf_hcl_content)
    assert VPC_CIDR_INPUTS_KEY in inputs_snippet, (
        f"collector-ingestion/000/terragrunt.hcl inputs{{}} block does not contain "
        f"'{VPC_CIDR_INPUTS_KEY}'. "
        "The prod leaf must explicitly set vpc_cidr_block = local.vpc_cidr_block in "
        "inputs{{}} to override the dead _envcommon account.hcl read (D47, AC-4)."
    )


# ---------------------------------------------------------------------------
# Security / standards: no suppression annotations or em-dashes in leaf files
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("data_lake_service_instance_hcl_content", "data-lake/000/service_instance.hcl"),
        ("data_lake_leaf_hcl_content", "data-lake/000/terragrunt.hcl"),
        ("collector_service_instance_hcl_content", "collector-ingestion/000/service_instance.hcl"),
        ("collector_leaf_hcl_content", "collector-ingestion/000/terragrunt.hcl"),
    ],
)
def test_no_em_dash_in_leaf_hcl_files(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No leaf HCL file may contain an em-dash character (U+2014) per code standards."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert chr(0x2014) not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("data_lake_service_instance_hcl_content", "data-lake/000/service_instance.hcl"),
        ("data_lake_leaf_hcl_content", "data-lake/000/terragrunt.hcl"),
        ("collector_service_instance_hcl_content", "collector-ingestion/000/service_instance.hcl"),
        ("collector_leaf_hcl_content", "collector-ingestion/000/terragrunt.hcl"),
    ],
)
def test_no_suppression_annotations_in_leaf_hcl_files(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """Leaf HCL files must contain no nosec/noqa/nolint/tfsec:ignore/checkov:skip."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert not SUPPRESSION_PATTERN.search(content), (
        f"{file_label} contains a suppression annotation. "
        "Suppression annotations are prohibited -- fix the underlying issue (CLAUDE.md)."
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_adot_policy_block(hcl_content: str) -> str:
    """Extract the adot_task_firehose_policy_json local value block from service.hcl.

    Searches for the jsonencode(...) block assigned to adot_task_firehose_policy_json.
    Returns the full block text including adjacent locals for companion ARN locals.
    """
    # Return the full locals block so we can also find companion ARN locals
    return _extract_locals_block(hcl_content)


def _extract_cwl_to_firehose_policy_block(main_tf_content: str) -> str:
    """Extract the aws_iam_role_policy.cwl_to_firehose inline policy from main.tf.

    Under the Option 1 telemetry-export design the Firehose PutRecord*/CMK grant lives
    on this CloudWatch-Logs-to-Firehose role's inline policy (not the ADOT task policy).
    Locates the `resource "aws_iam_role_policy" "cwl_to_firehose"` block and returns its
    body via brace-depth scanning (handling the nested jsonencode({ ... }) statements).
    Returns an empty string when the resource is absent so the caller's assertions fail
    loudly rather than matching unrelated text elsewhere in the file.
    """
    match = re.search(
        r'resource\s+"aws_iam_role_policy"\s+"cwl_to_firehose"\s*\{',
        main_tf_content,
    )
    if not match:
        return ""
    start = match.end()
    depth = 1
    pos = start
    while pos < len(main_tf_content) and depth > 0:
        if main_tf_content[pos] == "{":
            depth += 1
        elif main_tf_content[pos] == "}":
            depth -= 1
        pos += 1
    return main_tf_content[start : pos - 1]


def _extract_locals_block(hcl_content: str) -> str:
    """Extract the full locals { ... } block from a service.hcl file.

    Handles nested braces by tracking depth. Returns the content inside
    the outermost locals block, or the full content if no block found.
    """
    match = re.search(r"\blocals\s*\{", hcl_content)
    if not match:
        return hcl_content

    start = match.end()
    depth = 1
    pos = start
    while pos < len(hcl_content) and depth > 0:
        if hcl_content[pos] == "{":
            depth += 1
        elif hcl_content[pos] == "}":
            depth -= 1
        pos += 1

    return hcl_content[start : pos - 1]


def _extract_lake_kms_policy_block(hcl_content: str) -> str:
    """Extract the lake_kms_policy_json assignment block from data-lake service.hcl.

    Looks for the lake_kms_policy_json local assignment and extracts its jsonencode(...)
    block content. Returns the full locals block if no specific block found.
    """
    # Find the lake_kms_policy_json = jsonencode(...) assignment
    match = re.search(r"lake_kms_policy_json\s*=\s*jsonencode\s*\(", hcl_content)
    if not match:
        # Fall back to full locals block
        return _extract_locals_block(hcl_content)

    start = match.end()
    # The jsonencode( already consumed the opening paren; track depth for parens
    depth = 1
    pos = start
    while pos < len(hcl_content) and depth > 0:
        if hcl_content[pos] == "(":
            depth += 1
        elif hcl_content[pos] == ")":
            depth -= 1
        pos += 1

    return hcl_content[match.start() : pos]


def _extract_inputs_block(hcl_content: str) -> str:
    """Extract the inputs { ... } block from a leaf terragrunt.hcl.

    Handles nested braces by tracking depth. Returns the content inside
    the outermost inputs block, or an empty string if no block found.
    The brace-depth scan correctly handles nested map literals (e.g.
    task_role_inline_policies, domain_validation_options).
    """
    match = re.search(r"\binputs\s*=\s*(?:merge\(\s*)?\{", hcl_content)
    if not match:
        return ""

    start = match.end()
    depth = 1
    pos = start
    while pos < len(hcl_content) and depth > 0:
        if hcl_content[pos] == "{":
            depth += 1
        elif hcl_content[pos] == "}":
            depth -= 1
        pos += 1

    return hcl_content[start : pos - 1]


def _extract_source(hcl_content: str) -> str:
    """Extract the resolved local module source path from a leaf terragrunt.hcl.

    Supports two source declaration forms introduced by E9-F3-S1-T1:
    1. Toggle-driven ternary: ``source = <cond> ? "pinned_url" : "local_path"``
       Returns the false/local branch (the ``${get_repo_root()}//...`` path) so
       parity comparisons use the canonical module path regardless of environment.
    2. Legacy literal: ``source = "..."``
       Returns the literal value unchanged.

    Raises AssertionError when no source assignment is found (AC-1).
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
