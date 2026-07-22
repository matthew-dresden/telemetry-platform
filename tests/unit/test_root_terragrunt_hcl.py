"""Unit regression tests for the root terragrunt.hcl and its scope files.

These tests implement the regression assertions from docs/terragrunt-concepts.md
as a fail-closed pytest suite (make tg-regression). They validate:

- AC-4: remote_state configures a hardened S3 backend with the required hardening keys
- AC-4: deterministic state_kms_key_alias and state_access_log_bucket locals are derived
- AC-4: common_tags map is defined and referenced by s3_bucket_tags (no DynamoDB lock table)
- AC-14: S3-native locking via use_lockfile = true; no dynamodb_table, no lock-table naming locals
- AC-4: generated provider sets allowed_account_ids and default_tags with NO assume_role
- AC-4: make tg-regression asserts grep -RIn 'assume_role' terragrunt/ returns nothing (D4)
- AC-5: no removed v0.x Terragrunt CLI tokens (D35)
- AC-5: non-12-digit account folder basename trips the declared_account_id guard (D2)
- scope files: env.hcl, product.hcl, region.hcl use the basename idiom (Section 1.3)
"""

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

TERRAGRUNT_ROOT = REPO_ROOT / "terragrunt"
ROOT_TERRAGRUNT_HCL = TERRAGRUNT_ROOT / "root.hcl"
ENV_HCL = TERRAGRUNT_ROOT / "env.hcl"
PRODUCT_HCL = TERRAGRUNT_ROOT / "live" / "telemetry" / "product.hcl"
REGION_HCL = TERRAGRUNT_ROOT / "live" / "telemetry" / "us-east-1" / "region.hcl"

# Removed v0.x Terragrunt CLI tokens (D35 -- no-backport policy, AC-5)
REMOVED_V0X_TOKENS = [
    "run-all",
    "--terragrunt-include-dir",
    "hclfmt",
    "terragrunt-info",
    "output-all",
    "validate-all",
    "plan-all",
    "apply-all",
    "destroy-all",
    "get-all",
]

# State-bucket hardening keys required in terragrunt/terragrunt.hcl (Section 8 item 13, B19).
# enable_lock_table_ssencryption was removed in E8-F3-S1-T2 (DynamoDB lock table dropped);
# use_lockfile replaces it as the S3-native locking indicator (spec section 4.3, AC-14).
HARDENING_KEYS = [
    "bucket_sse_kms_key_id",
    "skip_bucket_public_access_blocking",
    "accesslogging_bucket_name",
    "use_lockfile",
]

# Lock-table locals that must NOT appear after E8-F3-S1-T2 migration (spec section 4.3, AC-14).
REMOVED_LOCK_TABLE_TOKENS = [
    "dynamodb_base_components",
    "dynamodb_table_name",
    "enable_lock_table_ssencryption",
    "dynamodb_table",
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_s3_backend_config(root_hcl_content: str) -> str:
    """Extract the local.s3_backend_config = { ... } object from root.hcl.

    The hardened backend keys live inside this local since the offline-validate switch
    was introduced (spec section 4.8): a real deploy always selects this object via the
    "false" key of the backend_config map. Brace-depth scanning handles the nested
    s3_bucket_tags = local.common_tags reference and any other nested structure.
    Returns the object body (between the outermost braces), or an empty string when the
    local is absent so the caller's key assertions fail loudly.
    """
    # The backend object is wrapped in merge(...) since named-profile injection was added:
    #   s3_backend_config = merge(local.aws_profile != "" ? { profile = ... } : {},
    #                             { bucket = ... })
    # The hardening keys live in the second merge argument (the object containing "bucket").
    # Locate that object by finding the "{" that opens the block containing the bucket key,
    # which handles both the plain "= { ... }" and the "= merge(..., { ... })" forms.
    assign = re.search(r"s3_backend_config\s*=\s*", root_hcl_content)
    if not assign:
        return ""
    bucket_idx = root_hcl_content.find("bucket", assign.end())
    if bucket_idx == -1:
        return ""
    open_idx = root_hcl_content.rfind("{", assign.end(), bucket_idx)
    if open_idx == -1:
        return ""
    start = open_idx + 1
    depth = 1
    pos = start
    while pos < len(root_hcl_content) and depth > 0:
        if root_hcl_content[pos] == "{":
            depth += 1
        elif root_hcl_content[pos] == "}":
            depth -= 1
        pos += 1
    return root_hcl_content[start : pos - 1]


# ---------------------------------------------------------------------------
# File-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def root_hcl_content() -> str:
    """Read the root root.hcl content. Fails if the file does not exist."""
    assert ROOT_TERRAGRUNT_HCL.exists(), (
        f"Root root.hcl not found at {ROOT_TERRAGRUNT_HCL}. "
        "This file must be created for the root Terragrunt config (docs/terragrunt-concepts.md)."
    )
    return ROOT_TERRAGRUNT_HCL.read_text()


@pytest.fixture(scope="module")
def env_hcl_content() -> str:
    """Read env.hcl content. Fails if the file does not exist."""
    assert ENV_HCL.exists(), (
        f"env.hcl not found at {ENV_HCL}. "
        "This file must be created for repo-wide non-secret defaults (docs/terragrunt-concepts.md)."
    )
    return ENV_HCL.read_text()


@pytest.fixture(scope="module")
def product_hcl_content() -> str:
    """Read product.hcl content. Fails if the file does not exist."""
    assert PRODUCT_HCL.exists(), (
        f"product.hcl not found at {PRODUCT_HCL}. "
        "This file must be created for the product layer (docs/terragrunt-concepts.md)."
    )
    return PRODUCT_HCL.read_text()


@pytest.fixture(scope="module")
def region_hcl_content() -> str:
    """Read region.hcl content. Fails if the file does not exist."""
    assert REGION_HCL.exists(), (
        f"region.hcl not found at {REGION_HCL}. "
        "This file must be created for the region layer (docs/terragrunt-concepts.md)."
    )
    return REGION_HCL.read_text()


# ---------------------------------------------------------------------------
# File existence tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_terragrunt_hcl_exists() -> None:
    """The root config must exist at terragrunt/root.hcl (docs/terragrunt-concepts.md,
    AC-CODE-001)."""
    assert ROOT_TERRAGRUNT_HCL.exists(), (
        f"Root root.hcl not found at {ROOT_TERRAGRUNT_HCL}. "
        "Required as the single source of truth for remote state and provider generation."
    )


@pytest.mark.unit
def test_env_hcl_exists() -> None:
    """env.hcl must exist at terragrunt/env.hcl (docs/terragrunt-concepts.md)."""
    assert ENV_HCL.exists(), (
        f"env.hcl not found at {ENV_HCL}. "
        "Required for repo-wide non-secret defaults (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_product_hcl_exists() -> None:
    """product.hcl must exist at terragrunt/live/telemetry/product.hcl
    (docs/terragrunt-concepts.md)."""
    assert PRODUCT_HCL.exists(), (
        f"product.hcl not found at {PRODUCT_HCL}. "
        "Required for the product layer basename idiom (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_region_hcl_exists() -> None:
    """region.hcl must exist at terragrunt/live/telemetry/us-east-1/region.hcl
    (docs/terragrunt-concepts.md)."""
    assert REGION_HCL.exists(), (
        f"region.hcl not found at {REGION_HCL}. "
        "Required for the region layer basename idiom (docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# Scope file basename idiom tests (Section 1.3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_product_hcl_uses_basename_idiom(product_hcl_content: str) -> None:
    """product.hcl must use locals { product = basename(get_terragrunt_dir()) } (Section 1.3)."""
    assert "basename(get_terragrunt_dir())" in product_hcl_content, (
        "product.hcl must declare `product = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom."
    )


@pytest.mark.unit
def test_product_hcl_parent_is_telemetry() -> None:
    """product.hcl must be in the 'telemetry' directory so basename resolves correctly."""
    assert PRODUCT_HCL.parent.name == "telemetry", (
        f"product.hcl parent dir is '{PRODUCT_HCL.parent.name}', expected 'telemetry'. "
        "docs/terragrunt-concepts.md: the product layer basename must resolve to 'telemetry'."
    )


@pytest.mark.unit
def test_region_hcl_uses_basename_idiom(region_hcl_content: str) -> None:
    """region.hcl must use locals { aws_region = basename(get_terragrunt_dir()) } (Section 1.3)."""
    assert "basename(get_terragrunt_dir())" in region_hcl_content, (
        "region.hcl must declare `aws_region = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom."
    )


@pytest.mark.unit
def test_region_hcl_parent_is_us_east_1() -> None:
    """region.hcl must be in 'us-east-1' so basename resolves correctly (Section 1.3)."""
    assert REGION_HCL.parent.name == "us-east-1", (
        f"region.hcl parent dir is '{REGION_HCL.parent.name}', expected 'us-east-1'. "
        "docs/terragrunt-concepts.md: the region layer basename must resolve to 'us-east-1'."
    )


# ---------------------------------------------------------------------------
# AC-4: root reads service_instance.hcl leaf-relative, not via find_in_parent_folders (D36)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_reads_service_instance_leaf_relative(root_hcl_content: str) -> None:
    """Root must read service_instance.hcl via get_terragrunt_dir(), not find_in_parent_folders.

    D36: service_instance.hcl lives in the LEAF dir alongside terragrunt.hcl.
    find_in_parent_folders searches ancestors only and would not find it.
    """
    leaf_relative_pattern = re.compile(
        r'read_terragrunt_config\s*\(\s*"\$\{get_terragrunt_dir\(\)\}/service_instance\.hcl"'
    )
    assert leaf_relative_pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not read service_instance.hcl leaf-relative. "
        'D36 requires: read_terragrunt_config("${get_terragrunt_dir()}/service_instance.hcl"). '
        "Using find_in_parent_folders for service_instance.hcl would fail to find it."
    )


@pytest.mark.unit
def test_root_hcl_does_not_use_find_in_parent_for_service_instance(
    root_hcl_content: str,
) -> None:
    """Root must NOT use find_in_parent_folders('service_instance.hcl') (D36)."""
    pattern = re.compile(r'find_in_parent_folders\s*\(\s*"service_instance\.hcl"\s*\)')
    assert not pattern.search(root_hcl_content), (
        "Root terragrunt.hcl uses find_in_parent_folders('service_instance.hcl'). "
        "D36 prohibits this: service_instance.hcl must be read leaf-relative via "
        'read_terragrunt_config("${get_terragrunt_dir()}/service_instance.hcl").'
    )


# ---------------------------------------------------------------------------
# AC-4: remote_state block and hardening keys (Section 8 item 13, B19)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_has_remote_state_block(root_hcl_content: str) -> None:
    """Root terragrunt.hcl must define a remote_state block (docs/terragrunt-concepts.md)."""
    assert "remote_state" in root_hcl_content, (
        "Root terragrunt.hcl does not contain a 'remote_state' block. "
        "Required for namespace-derived S3 state backend (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_uses_s3_backend(root_hcl_content: str) -> None:
    """remote_state must default to the hardened S3 backend (docs/terragrunt-concepts.md, section
    4.8).

    The backend type is now selected via a local (backend_type = offline_backend ?
    "local" : "s3") so the tg-copyability-test harness can swap to a `local` backend for
    its offline structural validate (spec section 4.8). The offline_backend flag is read
    from TG_OFFLINE_BACKEND with a default of "false", so the production default ALWAYS
    resolves to "s3": the env var is unset for any real deploy. This test enforces that
    the production default is S3 by asserting (a) the offline flag defaults to "false"
    and (b) the s3_backend_config local is selected for the "false" key, keeping S3 as
    the only backend a real deploy ever uses.
    """
    # The offline switch must default to "false" so a real deploy uses S3 (section 4.8).
    offline_default_false = re.compile(r'get_env\s*\(\s*"TG_OFFLINE_BACKEND"\s*,\s*"false"\s*\)')
    assert offline_default_false.search(root_hcl_content), (
        "Root terragrunt.hcl does not default the offline backend switch to S3. "
        'TG_OFFLINE_BACKEND must default to "false" (get_env("TG_OFFLINE_BACKEND", "false")) '
        "so a real deploy always uses the hardened S3 backend (spec section 4.8)."
    )
    # backend_type must resolve to "s3" when offline is false (the production default).
    backend_type_selects_s3 = re.compile(
        r'backend_type\s*=\s*local\.offline_backend\s*\?\s*"local"\s*:\s*"s3"'
    )
    assert backend_type_selects_s3.search(root_hcl_content), (
        "Root terragrunt.hcl backend_type does not select S3 as the default branch. "
        'backend_type must be `local.offline_backend ? "local" : "s3"` so the production '
        "default (offline=false) is the hardened S3 backend (docs/terragrunt-concepts.md, section "
        "4.8)."
    )
    # remote_state.backend must be wired to the selecting local, and the S3 config local
    # must be the branch chosen for the "false" (production) key.
    assert "backend = local.backend_type" in root_hcl_content, (
        "Root terragrunt.hcl remote_state does not wire `backend = local.backend_type`. "
        "The backend type must be the selecting local so the production default is S3 "
        "(docs/terragrunt-concepts.md, section 4.8)."
    )
    s3_config_selected_for_false = re.compile(r'"false"\s*=\s*local\.s3_backend_config')
    assert s3_config_selected_for_false.search(root_hcl_content), (
        "Root terragrunt.hcl backend_config map does not select local.s3_backend_config "
        'for the "false" key. '
        "The hardened S3 backend config must be the production (non-offline) branch "
        "(docs/terragrunt-concepts.md, section 4.8)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("hardening_key", HARDENING_KEYS)
def test_root_hcl_has_hardening_key(root_hcl_content: str, hardening_key: str) -> None:
    """All four state-bucket hardening keys must be present in root terragrunt.hcl (Sec 8, B19)."""
    assert hardening_key in root_hcl_content, (
        f"Root terragrunt.hcl is missing hardening key '{hardening_key}'. "
        "All four hardening keys must be present: bucket_sse_kms_key_id, "
        "skip_bucket_public_access_blocking, accesslogging_bucket_name, "
        "enable_lock_table_ssencryption (docs/terragrunt-concepts.md, B19)."
    )


@pytest.mark.unit
def test_root_hcl_uses_kms_sse_algorithm(root_hcl_content: str) -> None:
    """The S3 backend config must set bucket_sse_algorithm to aws:kms (docs/terragrunt-concepts.md,
    D10/B19).

    The hardened backend keys now live inside the local.s3_backend_config object (the
    production branch of the offline-validate switch, spec section 4.8). The assertion
    is whitespace-tolerant because HCL fmt aligns the `=` of adjacent keys, so the
    spacing around bucket_sse_algorithm depends on its neighbours.
    """
    kms_sse_algorithm = re.compile(r'bucket_sse_algorithm\s*=\s*"aws:kms"')
    assert kms_sse_algorithm.search(root_hcl_content), (
        'Root terragrunt.hcl does not set bucket_sse_algorithm = "aws:kms" in the '
        "S3 backend config (local.s3_backend_config). "
        "Customer-managed KMS encryption is required for the state bucket (D10/B19)."
    )


@pytest.mark.unit
def test_root_hcl_kms_key_references_local_alias(root_hcl_content: str) -> None:
    """bucket_sse_kms_key_id must reference the local state_kms_key_alias
    (docs/terragrunt-concepts.md)."""
    assert "state_kms_key_alias" in root_hcl_content, (
        "Root terragrunt.hcl does not reference 'state_kms_key_alias'. "
        "The state CMK alias must be derived deterministically from the account id "
        "as alias/${local.aws_account_id}-tfstate (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_derives_state_kms_key_alias(root_hcl_content: str) -> None:
    """state_kms_key_alias must be derived as alias/${local.aws_account_id}-tfstate
    (docs/terragrunt-concepts.md)."""
    pattern = re.compile(r'state_kms_key_alias\s*=\s*"alias/\$\{local\.aws_account_id\}-tfstate"')
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not derive state_kms_key_alias as "
        "'alias/${local.aws_account_id}-tfstate'. "
        "This deterministic alias is required so state-bootstrap matches by exact name "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_derives_state_access_log_bucket(root_hcl_content: str) -> None:
    """state_access_log_bucket must be derived as ${local.aws_account_id}-tfstate-access-logs."""
    pattern = re.compile(
        r'state_access_log_bucket\s*=\s*"\$\{local\.aws_account_id\}-tfstate-access-logs"'
    )
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not derive state_access_log_bucket as "
        "'${local.aws_account_id}-tfstate-access-logs'. "
        "This deterministic name is required so state-bootstrap matches by exact name "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_has_skip_bucket_versioning_false(root_hcl_content: str) -> None:
    """Versioning must be enabled: skip_bucket_versioning = false (docs/terragrunt-concepts.md)."""
    pattern = re.compile(r"skip_bucket_versioning\s*=\s*false")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not set skip_bucket_versioning = false. "
        "State bucket versioning must be enabled (docs/terragrunt-concepts.md, D10/B19)."
    )


@pytest.mark.unit
def test_root_hcl_has_skip_bucket_enforced_tls_false(root_hcl_content: str) -> None:
    """TLS must be enforced: skip_bucket_enforced_tls = false (docs/terragrunt-concepts.md)."""
    pattern = re.compile(r"skip_bucket_enforced_tls\s*=\s*false")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not set skip_bucket_enforced_tls = false. "
        "TLS enforcement is required for the state bucket (docs/terragrunt-concepts.md, D10/B19)."
    )


@pytest.mark.unit
def test_root_hcl_has_skip_bucket_root_access_false(root_hcl_content: str) -> None:
    """Root access must be blocked: skip_bucket_root_access = false
    (docs/terragrunt-concepts.md)."""
    pattern = re.compile(r"skip_bucket_root_access\s*=\s*false")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not set skip_bucket_root_access = false. "
        "Root-access bucket policy is required (docs/terragrunt-concepts.md, D10/B19)."
    )


@pytest.mark.unit
def test_root_hcl_has_use_lockfile_true(root_hcl_content: str) -> None:
    """S3-native locking must be enabled: use_lockfile = true in remote_state config (AC-14).

    E8-F3-S1-T2 migrates from DynamoDB lock table to S3-native conditional-write locking.
    use_lockfile = true is the Terragrunt 1.0.7 / Terraform >= 1.10 S3 locking flag
    (spec section 1.1, section 4.3, decision D5, AC-14).
    """
    pattern = re.compile(r"use_lockfile\s*=\s*true")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not set use_lockfile = true in the remote_state config. "
        "S3-native locking via use_lockfile = true is required after the DynamoDB lock table "
        "migration (spec section 4.3, decision D5, AC-14)."
    )


@pytest.mark.unit
def test_root_hcl_tags_s3_bucket_with_common_tags(root_hcl_content: str) -> None:
    """s3_bucket_tags must be set to local.common_tags (docs/terragrunt-concepts.md)."""
    pattern = re.compile(r"s3_bucket_tags\s*=\s*local\.common_tags")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not set s3_bucket_tags = local.common_tags. "
        "The state bucket must be tagged with common_tags (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_has_no_dynamodb_table_tags(root_hcl_content: str) -> None:
    """dynamodb_table_tags must NOT be present after the DynamoDB lock table removal (AC-14).

    E8-F3-S1-T2 removes the DynamoDB lock table from the remote_state config.
    The dynamodb_table_tags key is no longer applicable and must be absent
    (spec section 4.3, AC-14).
    """
    pattern = re.compile(r"dynamodb_table_tags")
    assert not pattern.search(root_hcl_content), (
        "Root terragrunt.hcl still contains dynamodb_table_tags. "
        "This key must be removed along with the DynamoDB lock table in E8-F3-S1-T2 "
        "(spec section 4.3, AC-14)."
    )


# ---------------------------------------------------------------------------
# AC-4: final_bucket_name derivation (B21 hash-suffixed, collision-free, <=63 chars)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_derives_bucket_name_raw(root_hcl_content: str) -> None:
    """Root must derive bucket_name_raw incorporating aws_account_id and namespace
    (docs/terragrunt-concepts.md)."""
    assert "bucket_name_raw" in root_hcl_content, (
        "Root terragrunt.hcl does not define 'bucket_name_raw'. "
        "The namespace-derived raw bucket name is required before the 63-char shortening "
        "(docs/terragrunt-concepts.md, D10/B21)."
    )


@pytest.mark.unit
def test_root_hcl_derives_final_bucket_name(root_hcl_content: str) -> None:
    """Root must derive final_bucket_name that stays at or under 63 chars (B21)."""
    assert "final_bucket_name" in root_hcl_content, (
        "Root terragrunt.hcl does not define 'final_bucket_name'. "
        "The hash-suffixed, collision-free bucket name is required (docs/terragrunt-concepts.md, "
        "B21)."
    )


@pytest.mark.unit
def test_root_hcl_uses_md5_hash_for_bucket_shortening(root_hcl_content: str) -> None:
    """Bucket name shortening must use md5 hash suffix for collision freedom (D10/B21)."""
    assert "md5(" in root_hcl_content, (
        "Root terragrunt.hcl does not use md5() for bucket name shortening. "
        "The hash suffix is required to ensure uniqueness when multiple services share "
        "the same 28-char namespace prefix (docs/terragrunt-concepts.md, D10/B21)."
    )


@pytest.mark.unit
def test_root_hcl_uses_63_char_limit_for_bucket(root_hcl_content: str) -> None:
    """Bucket name shortening must apply only when the raw name exceeds 63 chars (B21)."""
    assert "> 63" in root_hcl_content, (
        "Root terragrunt.hcl does not check '> 63' for bucket name shortening. "
        "The S3 63-character limit check is required to trigger the hash-suffixed scheme "
        "(docs/terragrunt-concepts.md, D10/B21)."
    )


# ---------------------------------------------------------------------------
# AC-4: generated provider -- allowed_account_ids, default_tags, NO assume_role (D4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_generates_provider_block(root_hcl_content: str) -> None:
    """Root must generate a provider block (docs/terragrunt-concepts.md)."""
    assert 'generate "provider"' in root_hcl_content, (
        "Root terragrunt.hcl does not generate a 'provider' block. "
        "Provider generation is required so leaves inherit the hardened AWS provider "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_provider_sets_allowed_account_ids(root_hcl_content: str) -> None:
    """Generated provider must set allowed_account_ids for fail-fast account guard
    (docs/terragrunt-concepts.md)."""
    assert "allowed_account_ids" in root_hcl_content, (
        "Root terragrunt.hcl generated provider does not set allowed_account_ids. "
        "This guard fails fast if assumed credentials target the wrong account "
        "(docs/terragrunt-concepts.md, D4)."
    )


@pytest.mark.unit
def test_root_hcl_provider_sets_default_tags(root_hcl_content: str) -> None:
    """Generated provider must set default_tags from local.common_tags
    (docs/terragrunt-concepts.md)."""
    assert "default_tags" in root_hcl_content, (
        "Root terragrunt.hcl generated provider does not set default_tags. "
        "The default_tags block propagates common_tags to all AWS resources "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_root_hcl_generates_versions_block(root_hcl_content: str) -> None:
    """Root must generate a versions block (docs/terragrunt-concepts.md)."""
    assert 'generate "versions"' in root_hcl_content, (
        "Root terragrunt.hcl does not generate a 'versions' block. "
        "Version pinning is required to ensure consistent Terraform + provider versions "
        "(docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# Section 8 item 8: NO assume_role anywhere in terragrunt/ (D4) -- AC-4
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_assume_role_in_terragrunt_directory() -> None:
    """No provider-level assume_role block may appear in terragrunt/ HCL files (D4, AC-4).

    The OIDC-assumed or SSO-assumed role IS the deploy identity.
    No second assume_role is introduced in the backend or provider config.
    This is verified by scanning all HCL files under terragrunt/ for the pattern
    `assume_role {` (a provider-level block opener), which is the construct D4 prohibits.

    The bare substring `assume_role` is intentionally excluded from the scan because
    it legitimately appears in module input variable names such as
    `analyst_assume_role_policy_json` and `admin_assume_role_policy_json`
    (references/identity module, E1-F7-S2-T2). The D4 constraint targets the
    provider-level assume_role block -- not every variable name that contains the
    substring.
    """
    # Match only the provider-level block opener `assume_role {` (with optional
    # leading whitespace). This is the exact construct D4 prohibits: a second role
    # assumption inside a provider or backend stanza.
    import re as _re

    _assume_role_block = _re.compile(r"^\s*assume_role\s*\{")
    matches = []
    for hcl_file in TERRAGRUNT_ROOT.rglob("*.hcl"):
        content = hcl_file.read_text()
        for line_num, line in enumerate(content.splitlines(), start=1):
            if _assume_role_block.search(line):
                matches.append(f"{hcl_file.relative_to(REPO_ROOT)}:{line_num}: {line.strip()}")
    assert not matches, (
        "Found a provider-level 'assume_role {' block in terragrunt/ HCL files "
        "(Section 8 item 8, D4). "
        "No second role assumption is allowed: the OIDC-assumed role IS the deploy identity. "
        "Offending lines:\n" + "\n".join(matches)
    )


# ---------------------------------------------------------------------------
# AC-5: no removed v0.x Terragrunt CLI tokens (D35)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("token", REMOVED_V0X_TOKENS)
def test_no_v0x_token_in_root_hcl(root_hcl_content: str, token: str) -> None:
    """Root terragrunt.hcl must not contain removed v0.x Terragrunt CLI tokens (D35, AC-5)."""
    assert token not in root_hcl_content, (
        f"Root terragrunt.hcl contains removed v0.x Terragrunt CLI token '{token}'. "
        "Only the Terragrunt 1.0.7 CLI surface is permitted (D35, AC-5). "
        "v0.x tokens were removed in the 1.x CLI rewrite and must not appear in any HCL file."
    )


@pytest.mark.unit
@pytest.mark.parametrize("token", REMOVED_V0X_TOKENS)
def test_no_v0x_token_in_any_terragrunt_hcl(token: str) -> None:
    """No HCL file under terragrunt/ may contain removed v0.x Terragrunt CLI tokens (D35, AC-5)."""
    matches = []
    for hcl_file in TERRAGRUNT_ROOT.rglob("*.hcl"):
        content = hcl_file.read_text()
        for line_num, line in enumerate(content.splitlines(), start=1):
            if token in line:
                matches.append(f"{hcl_file.relative_to(REPO_ROOT)}:{line_num}: {line.strip()}")
    assert not matches, (
        f"Found removed v0.x Terragrunt CLI token '{token}' in terragrunt/ HCL files "
        "(D35, AC-5). Only the 1.0.7 CLI surface is permitted. "
        "Offending lines:\n" + "\n".join(matches)
    )


# ---------------------------------------------------------------------------
# AC-5: declared_account_id guard fails closed for non-12-digit basename (D2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_reads_account_vars_with_find_in_parent_folders(root_hcl_content: str) -> None:
    """Root must read account.hcl via find_in_parent_folders (D2, AC-5)."""
    assert 'find_in_parent_folders("account.hcl")' in root_hcl_content, (
        "Root terragrunt.hcl does not read account.hcl via find_in_parent_folders. "
        "The account id must be inherited from account.hcl which carries the "
        "declared_account_id guard against non-12-digit basenames (D2, AC-5)."
    )


@pytest.mark.unit
def test_root_hcl_extracts_aws_account_id_from_account_vars(root_hcl_content: str) -> None:
    """Root must extract aws_account_id from account_vars.locals (D2, AC-5)."""
    pattern = re.compile(r"aws_account_id\s*=\s*local\.account_vars\.locals\.aws_account_id")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not extract aws_account_id from account_vars.locals. "
        "The account id must flow through the declared_account_id guard in account.hcl "
        "which fails fast for non-12-digit basenames (D2, AC-5, docs/terragrunt-concepts.md)."
    )


# ---------------------------------------------------------------------------
# Section 8 item 11: remote_state appears in exactly one file (F1/F11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_remote_state_in_exactly_one_file() -> None:
    """remote_state block must appear in exactly one file: terragrunt/root.hcl (F1/F11)."""
    files_with_remote_state = []
    for hcl_file in TERRAGRUNT_ROOT.rglob("*.hcl"):
        content = hcl_file.read_text()
        if re.search(r"^\s*remote_state\s*\{", content, re.MULTILINE):
            files_with_remote_state.append(str(hcl_file.relative_to(REPO_ROOT)))
    assert files_with_remote_state == ["terragrunt/root.hcl"], (
        "remote_state block must appear in exactly one file: terragrunt/root.hcl. "
        "Found remote_state in: " + str(files_with_remote_state) + " "
        "(Section 8 item 11, F1/F11 DRY violation prevention)."
    )


# ---------------------------------------------------------------------------
# Section 8 item 6: the ONLY get_env in root.hcl is the sanctioned TG_OFFLINE_BACKEND
# offline-validate switch (D2/D4 supersede F7)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_get_env_in_root_hcl(root_hcl_content: str) -> None:
    """Root terragrunt.hcl must read NO env var other than the sanctioned switches (item 6, D2/D4).

    D2/D4 intent (preserved): identity is NOT env-derived. The account id is
    basename-derived and the deploy role ARN is constructed from the account id, so no
    get_env() reads identity, region, role, or any other deploy-shaping value.

    Four SANCTIONED, production-inert deploy-context switches are permitted (NONE shapes THIS
    unit's identity): get_env("TG_OFFLINE_BACKEND", "false") (the offline-validate switch, spec
    section 4.8), get_env("TG_USE_OIDC", "false") (the CICD credential-source switch),
    get_env("BOOTSTRAP_LOCAL_BACKEND", "false") (the one-time state-bootstrap first-apply
    override, D-15), and get_env("TG_CI_PRIMARY_ACCOUNT_ID", "") (the cross-account CI
    credential-routing switch, multi-account CI WALL 2).

    The first three default to "false" and the fourth to "" (named-profile deploy / no
    cross-account routing). TG_CI_PRIMARY_ACCOUNT_ID carries the account id of the AMBIENT
    OIDC session, NOT this unit's identity: the unit's aws_account_id stays basename-derived
    and its deploy_role_arn stays CONSTRUCTED from that basename-derived id. The env value is
    compared against the basename-derived account only to decide WHETHER a cross-account
    assume_role to the unit-account deploy role is generated -- a pure credential-transport
    decision (the CI analog of the local env-keyed named-profile model, D8). It never selects
    the account id, region, or deploy role of the unit. This test asserts that EVERY get_env
    call in root.hcl reads one of those sanctioned keys (no identity-shaping read has crept in).
    """
    sanctioned = {
        "TG_OFFLINE_BACKEND",
        "TG_USE_OIDC",
        "BOOTSTRAP_LOCAL_BACKEND",
        "TG_CI_PRIMARY_ACCOUNT_ID",
    }
    get_env_calls = re.findall(r'get_env\s*\(\s*"([^"]+)"', root_hcl_content)
    assert get_env_calls, (
        "Root terragrunt.hcl contains no get_env() call. The only sanctioned get_env keys are "
        'get_env("TG_OFFLINE_BACKEND", "false"), get_env("TG_USE_OIDC", "false"), '
        'get_env("BOOTSTRAP_LOCAL_BACKEND", "false"), and get_env("TG_CI_PRIMARY_ACCOUNT_ID", "");'
        " if those switches were removed this assertion must be re-evaluated (spec section 4.8)."
    )
    disallowed = [key for key in get_env_calls if key not in sanctioned]
    assert not disallowed, (
        "Root terragrunt.hcl reads env var(s) other than the sanctioned deploy-context "
        f"switches {sorted(sanctioned)}: {disallowed}. "
        "D2/D4 require identity (account id, region, deploy role) to be basename-derived "
        "and constructed, never read from the environment. The only permitted get_env keys are "
        'get_env("TG_OFFLINE_BACKEND", "false"), get_env("TG_USE_OIDC", "false"), '
        'get_env("BOOTSTRAP_LOCAL_BACKEND", "false"), and '
        'get_env("TG_CI_PRIMARY_ACCOUNT_ID", "") (Section 8 item 6, D2/D4, multi-account WALL 2).'
    )


# ---------------------------------------------------------------------------
# No hardcoded credentials or forbidden patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_path,file_label",
    [
        (ROOT_TERRAGRUNT_HCL, "terragrunt/terragrunt.hcl"),
        (ENV_HCL, "terragrunt/env.hcl"),
        (PRODUCT_HCL, "terragrunt/live/telemetry/product.hcl"),
        (REGION_HCL, "terragrunt/live/telemetry/us-east-1/region.hcl"),
    ],
)
def test_no_hardcoded_aws_access_key(hcl_path: pathlib.Path, file_label: str) -> None:
    """No HCL file may contain a hardcoded AWS access key pattern."""
    if not hcl_path.exists():
        pytest.skip(f"{file_label} does not exist yet -- file existence tests cover this.")
    content = hcl_path.read_text()
    assert not re.search(r"(AKIA|ASIA)[A-Z0-9]{16}", content), (
        f"{file_label} contains a pattern matching an AWS access key. "
        "No credentials may be hardcoded in HCL files."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_path,file_label",
    [
        (ROOT_TERRAGRUNT_HCL, "terragrunt/terragrunt.hcl"),
        (ENV_HCL, "terragrunt/env.hcl"),
        (PRODUCT_HCL, "terragrunt/live/telemetry/product.hcl"),
        (REGION_HCL, "terragrunt/live/telemetry/us-east-1/region.hcl"),
    ],
)
def test_no_em_dash_in_hcl_files(hcl_path: pathlib.Path, file_label: str) -> None:
    """No HCL file may contain an em-dash character (U+2014) per code standards."""
    if not hcl_path.exists():
        pytest.skip(f"{file_label} does not exist yet -- file existence tests cover this.")
    content = hcl_path.read_text()
    assert "\u2014" not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# E8-F3-S1-T1: root reads common/common.hcl and loads JSON maps via get_repo_root()
# (spec S4.1, AC-3, D3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_reads_common_hcl_via_get_repo_root(root_hcl_content: str) -> None:
    """Root must read common/common.hcl via read_terragrunt_config anchored on get_repo_root().

    D3: common/ is OUTSIDE the copy boundary. All paths to common/ must use
    get_repo_root() so the read resolves correctly from any copied subtree depth.
    The root assigns the result to common_vars (spec S4.1, AC-3).
    """
    pattern = re.compile(
        r'read_terragrunt_config\s*\(\s*"\$\{get_repo_root\(\)\}/terragrunt/common/common\.hcl"'
    )
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not read common/common.hcl via "
        'read_terragrunt_config("${get_repo_root()}/terragrunt/common/common.hcl"). '
        "D3 requires get_repo_root()-anchored paths for all common/ reads so the path "
        "resolves from any copied subtree depth (spec S4.1, AC-3, D3)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("map_name", ["accounts", "domains", "contacts"])
def test_root_hcl_loads_json_map_via_jsondecode_and_get_repo_root(
    root_hcl_content: str, map_name: str
) -> None:
    """Root must load each JSON map via jsondecode(file(get_repo_root()/...)) (spec S4.1, D3).

    Per spec section 4.3, per-scope keyed lookups (accounts[aws_account_id],
    domains[environment_name], contacts[environment_name]) belong at the root
    terragrunt.hcl layer because caller-derived keys are unavailable inside
    common.hcl's evaluation context. The maps are loaded via get_repo_root()-anchored
    paths so they resolve from any copied subtree (D3).
    """
    pattern = re.compile(
        rf'jsondecode\s*\(\s*file\s*\(\s*"\$\{{get_repo_root\(\)\}}/terragrunt/common/{map_name}\.json"\s*\)\s*\)'
    )
    assert pattern.search(root_hcl_content), (
        f"Root terragrunt.hcl does not load {map_name}.json via "
        f'jsondecode(file("${{get_repo_root()}}/terragrunt/common/{map_name}.json")). '
        "All three JSON maps must be loaded in the root file with get_repo_root()-anchored "
        "paths so per-scope keyed lookups can use caller-derived keys (spec S4.1, S4.3, D3)."
    )


# ---------------------------------------------------------------------------
# E8-F3-S1-T1: root resolves account_cfg/domain_cfg/contacts_cfg via bracket lookups
# (spec S4.1, S4.3, AC-4, AC-5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_resolves_account_cfg_via_bracket_lookup(root_hcl_content: str) -> None:
    """Root must resolve account_cfg via accounts[local.aws_account_id] (spec S4.1, AC-4).

    The bracket lookup (not lookup()) is used in the truthy branch after the null guard
    confirms the key is present, providing deterministic fail-fast (D2, AC-4).
    """
    pattern = re.compile(r"account_cfg\s*=\s*lookup\s*\(local\.accounts,")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not resolve account_cfg via "
        "lookup(local.accounts, local.aws_account_id, null) with bracket fallback. "
        "The account config must be keyed by the basename-derived aws_account_id "
        "(spec S4.1, AC-4, D2)."
    )


@pytest.mark.unit
def test_root_hcl_account_cfg_uses_aws_account_id_key(root_hcl_content: str) -> None:
    """account_cfg lookup must use local.aws_account_id as the key (spec S4.1, AC-4)."""
    pattern = re.compile(r"local\.accounts\[local\.aws_account_id\]")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not index accounts with local.aws_account_id. "
        "The bracket access local.accounts[local.aws_account_id] is required in the "
        "truthy branch of the account_cfg ternary (spec S4.1, AC-4, D2)."
    )


@pytest.mark.unit
def test_root_hcl_resolves_domain_cfg_via_bracket_lookup(root_hcl_content: str) -> None:
    """Root must resolve domain_cfg via domains[local.environment_name] (spec S4.1, AC-5)."""
    pattern = re.compile(r"domain_cfg\s*=\s*lookup\s*\(local\.domains,")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not resolve domain_cfg via "
        "lookup(local.domains, local.environment_name, null) with bracket fallback. "
        "The domain config must be keyed by environment_name (spec S4.1, AC-5)."
    )


@pytest.mark.unit
def test_root_hcl_domain_cfg_uses_environment_name_key(root_hcl_content: str) -> None:
    """domain_cfg lookup must use local.environment_name as the key (spec S4.1, AC-5)."""
    pattern = re.compile(r"local\.domains\[local\.environment_name\]")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not index domains with local.environment_name. "
        "The bracket access local.domains[local.environment_name] is required in the "
        "truthy branch of the domain_cfg ternary (spec S4.1, AC-5)."
    )


@pytest.mark.unit
def test_root_hcl_resolves_contacts_cfg_via_bracket_lookup(root_hcl_content: str) -> None:
    """Root must resolve contacts_cfg via contacts[local.environment_name] (spec S4.1, AC-5)."""
    pattern = re.compile(r"contacts_cfg\s*=\s*lookup\s*\(local\.contacts,")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not resolve contacts_cfg via "
        "lookup(local.contacts, local.environment_name, null) with bracket fallback. "
        "The contacts config must be keyed by environment_name (spec S4.1, AC-5)."
    )


@pytest.mark.unit
def test_root_hcl_contacts_cfg_uses_environment_name_key(root_hcl_content: str) -> None:
    """contacts_cfg lookup must use local.environment_name as the key (spec S4.1, AC-5)."""
    pattern = re.compile(r"local\.contacts\[local\.environment_name\]")
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl does not index contacts with local.environment_name. "
        "The bracket access local.contacts[local.environment_name] is required in the "
        "truthy branch of the contacts_cfg ternary (spec S4.1, AC-5)."
    )


# ---------------------------------------------------------------------------
# E8-F3-S1-T1: each scope resolution guarded by tobool("ERROR: ...") (AC-4, AC-5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_account_cfg_guarded_by_tobool_error(root_hcl_content: str) -> None:
    """account_cfg resolution must be guarded by tobool("ERROR: ...") naming id and file (AC-4).

    A derived account id absent from accounts.json must abort Terragrunt parsing with
    an actionable message naming the id and the file. No fallback is permitted (spec S3.5).
    """
    pattern = re.compile(r'tobool\s*\(\s*"ERROR:[^"]*accounts\.json')
    assert pattern.search(root_hcl_content), (
        'Root terragrunt.hcl account_cfg guard does not use tobool("ERROR: ...") '
        "naming common/accounts.json. "
        "The fail-fast guard for a missing account id must name the file so the operator "
        "knows where to add a row (AC-4, spec S3.5, S7)."
    )


@pytest.mark.unit
def test_root_hcl_account_cfg_error_names_account_id(root_hcl_content: str) -> None:
    """account_cfg tobool error must embed the derived account id in the message (AC-4)."""
    pattern = re.compile(
        r'tobool\s*\(\s*"ERROR:[^"]*\$\{local\.aws_account_id\}[^"]*accounts\.json'
    )
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl account_cfg tobool error does not embed "
        "${local.aws_account_id} and reference accounts.json. "
        "The error message must name the scope id so the operator can identify the "
        "missing row (AC-4, spec S7)."
    )


@pytest.mark.unit
def test_root_hcl_domain_cfg_guarded_by_tobool_error(root_hcl_content: str) -> None:
    """domain_cfg resolution must be guarded by tobool("ERROR: ...") naming scope and file (AC-5).

    A missing env-class key in domains.json must abort parsing with an actionable message.
    No fallback is permitted (spec S3.5).
    """
    pattern = re.compile(r'tobool\s*\(\s*"ERROR:[^"]*domains\.json')
    assert pattern.search(root_hcl_content), (
        'Root terragrunt.hcl domain_cfg guard does not use tobool("ERROR: ...") '
        "naming common/domains.json. "
        "The fail-fast guard for a missing env-class must name the file (AC-5, spec S3.5, S7)."
    )


@pytest.mark.unit
def test_root_hcl_domain_cfg_error_names_environment_name(root_hcl_content: str) -> None:
    """domain_cfg tobool error must embed the environment_name in the message (AC-5)."""
    pattern = re.compile(
        r'tobool\s*\(\s*"ERROR:[^"]*\$\{local\.environment_name\}[^"]*domains\.json'
    )
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl domain_cfg tobool error does not embed "
        "${local.environment_name} and reference domains.json. "
        "The error message must name the scope so the operator can identify the "
        "missing row (AC-5, spec S7)."
    )


@pytest.mark.unit
def test_root_hcl_contacts_cfg_guarded_by_tobool_error(root_hcl_content: str) -> None:
    """contacts_cfg resolution must be guarded by tobool("ERROR: ...") naming scope and file (AC-5).

    A missing env-class key in contacts.json must abort parsing with an actionable message.
    No fallback is permitted (spec S3.5).
    """
    pattern = re.compile(r'tobool\s*\(\s*"ERROR:[^"]*contacts\.json')
    assert pattern.search(root_hcl_content), (
        'Root terragrunt.hcl contacts_cfg guard does not use tobool("ERROR: ...") '
        "naming common/contacts.json. "
        "The fail-fast guard for a missing env-class must name the file "
        "(AC-5, spec S3.5, S7)."
    )


@pytest.mark.unit
def test_root_hcl_contacts_cfg_error_names_environment_name(root_hcl_content: str) -> None:
    """contacts_cfg tobool error must embed the environment_name in the message (AC-5)."""
    pattern = re.compile(
        r'tobool\s*\(\s*"ERROR:[^"]*\$\{local\.environment_name\}[^"]*contacts\.json'
    )
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl contacts_cfg tobool error does not embed "
        "${local.environment_name} and reference contacts.json. "
        "The error message must name the scope so the operator can identify the "
        "missing row (AC-5, spec S7)."
    )


@pytest.mark.unit
def test_root_hcl_has_three_tobool_error_guards(root_hcl_content: str) -> None:
    """Root must define at least three tobool("ERROR: ...") guards for each scope (AC-4, AC-5).

    Each of the three scope resolutions (account_cfg, domain_cfg, contacts_cfg) requires
    an independent fail-fast guard. Three guards means that any one missing key produces
    a specific actionable message naming the id, the scope, and the file.
    """
    pattern = re.compile(r'tobool\s*\(\s*"ERROR:')
    matches = pattern.findall(root_hcl_content)
    assert len(matches) >= 3, (
        f'Root terragrunt.hcl has {len(matches)} tobool("ERROR: ...") guards; '
        "expected at least 3 (one for each of account_cfg, domain_cfg, contacts_cfg). "
        "Each scope resolution must have its own fail-fast guard naming the id/scope "
        "and the JSON file (AC-4, AC-5, spec S3.5, S7)."
    )


# ---------------------------------------------------------------------------
# E8-F3-S1-T2: S3-native locking (use_lockfile); DynamoDB lock table removed (AC-14)
# (spec section 0.3, section 4.3, decision D5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("token", REMOVED_LOCK_TABLE_TOKENS)
def test_root_hcl_has_no_lock_table_token(root_hcl_content: str, token: str) -> None:
    """Removed DynamoDB lock-table tokens must not appear in root terragrunt.hcl (AC-14).

    E8-F3-S1-T2 removes:
    - dynamodb_base_components local
    - dynamodb_table_name local
    - enable_lock_table_ssencryption remote_state config key
    - dynamodb_table remote_state config key
    All four tokens must be absent from the root file after this migration
    (spec section 4.3, decision D5, AC-14).
    """
    assert token not in root_hcl_content, (
        f"Root terragrunt.hcl still contains DynamoDB lock-table token '{token}'. "
        "All lock-table locals and config keys must be removed in E8-F3-S1-T2: "
        "dynamodb_base_components, dynamodb_table_name, enable_lock_table_ssencryption, "
        "dynamodb_table (spec section 4.3, decision D5, AC-14)."
    )


@pytest.mark.unit
def test_root_hcl_s3_hardening_intact_after_lock_table_removal(root_hcl_content: str) -> None:
    """S3 hardening keys must remain after DynamoDB lock table removal (spec section 4.3, AC-14).

    Two refactors must not have dropped any S3 hardening key:
    1. the DynamoDB lock table removal (E8-F3-S1-T2), and
    2. moving the hardened backend keys into local.s3_backend_config as the
       production branch of the offline-validate switch (spec section 4.8).

    The following must still be present in the S3 backend config:
    - bucket_sse_algorithm = "aws:kms"
    - bucket_sse_kms_key_id referencing state_kms_key_alias
    - all five skip_bucket_* flags
    - accesslogging_bucket_name
    - s3_bucket_tags = local.common_tags

    The hardening keys are asserted against the extracted local.s3_backend_config object
    so the test fails if any key is dropped or relocated out of the S3 backend branch
    (it must not pass merely because a key survives in an unrelated part of the file).
    """
    s3_backend_config = _extract_s3_backend_config(root_hcl_content)
    # bucket_sse_algorithm spacing is fmt-aligned, so match it whitespace-tolerantly.
    assert re.search(r'bucket_sse_algorithm\s*=\s*"aws:kms"', s3_backend_config), (
        'local.s3_backend_config does not set bucket_sse_algorithm = "aws:kms" '
        "(spec section 4.3, AC-14)."
    )
    required_keys = [
        "bucket_sse_kms_key_id",
        "skip_bucket_versioning",
        "skip_bucket_ssencryption",
        "skip_bucket_public_access_blocking",
        "skip_bucket_enforced_tls",
        "skip_bucket_root_access",
        "accesslogging_bucket_name",
        "s3_bucket_tags",
    ]
    missing = [k for k in required_keys if k not in s3_backend_config]
    assert not missing, (
        "S3 hardening keys were dropped from local.s3_backend_config alongside the "
        "DynamoDB lock table removal / offline-backend refactor. "
        f"Missing keys: {missing}. "
        "The following must be preserved in the S3 backend config: bucket_sse_algorithm, "
        "bucket_sse_kms_key_id, all five skip_bucket_* flags, accesslogging_bucket_name, "
        "s3_bucket_tags (spec section 4.3, section 4.8, AC-14)."
    )


# ---------------------------------------------------------------------------
# AC-15: provider allowed_account_ids derived; backend has no hardcoded profile
# (spec S3.6, D4, D8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_provider_allowed_account_ids_derived_from_local(
    root_hcl_content: str,
) -> None:
    """Generated provider must set allowed_account_ids to ["${local.aws_account_id}"] (AC-15).

    allowed_account_ids must be keyed on the basename-derived aws_account_id, not a
    hardcoded literal. This makes the provider copy-safe: any subtree copy resolves
    to its own account id without edits (spec S3.6, AC-15).
    """
    pattern = re.compile(r'allowed_account_ids\s*=\s*\[\s*"\$\{local\.aws_account_id\}"\s*\]')
    assert pattern.search(root_hcl_content), (
        "Root terragrunt.hcl generated provider does not set "
        'allowed_account_ids = ["${local.aws_account_id}"]. '
        "The value must be derived from the basename account id, not hardcoded, "
        "so the provider is copy-safe across accounts (spec S3.6, AC-15)."
    )


@pytest.mark.unit
def test_root_hcl_remote_state_has_no_hardcoded_profile(root_hcl_content: str) -> None:
    """The backend 'profile' must be DERIVED (local.aws_profile), never a literal.

    The env-keyed instance-set design selects the AWS account per unit via a named profile
    resolved from common/env_accounts.json (account.hcl exposes local.aws_profile). root.hcl
    injects that profile into the backend conditionally (only when non-empty), so under CICD
    (OIDC, blank profile) ambient role credentials are used and locally the named profile
    selects the account. The remaining requirement is that the profile is NEVER a hardcoded
    string literal: every 'profile =' assignment must reference local.aws_profile (or be the
    conditional that emits it), so the artifact stays environment-agnostic.
    """
    non_comment_lines = [
        line
        for line in root_hcl_content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    profile_key_pattern = re.compile(r"\bprofile\s*=")
    profile_lines = [line.strip() for line in non_comment_lines if profile_key_pattern.search(line)]
    # A profile assignment is acceptable only when it is derived from local.aws_profile;
    # a quoted string literal (e.g. profile = "telemetry-sandbox") would be hardcoded.
    offending = [line for line in profile_lines if "local.aws_profile" not in line]
    assert not offending, (
        "Root terragrunt.hcl assigns a hardcoded 'profile' literal on a non-comment line. "
        "The backend profile must be derived from local.aws_profile (resolved from "
        "common/env_accounts.json via account.hcl), never a string literal, so the artifact "
        f"is environment-agnostic. Offending lines: {offending}."
    )
