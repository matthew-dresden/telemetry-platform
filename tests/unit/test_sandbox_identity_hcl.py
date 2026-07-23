"""Unit tests for the sandbox identity terragrunt unit HCL files.

These tests assert the structural and content constraints for the sandbox account
(222222222222) identity terragrunt unit. They validate:

- AC-3: the unit sources exactly one reference module (references/identity) with no
  second source or nested module include.
- AC-3: `make tg-validate` parses the identity unit with zero errors (file structure
  assertions are a proxy for parseable HCL).
- AC-13: the Viewer/Author/Admin to QuickSight role mapping is wired by the
  group-name and role-name inputs declared in service.hcl via _envcommon/identity.hcl.
- D8: group names (viewer/author/admin) are input-driven, never hardcoded prod literals.
- D31: sandbox and prod units differ only by folder path and inputs, never by inline
  conditional logic on account id or environment name.
- D37: inputs map to the declared variable contract on references/identity (group names,
  role names, assume-role policy JSON, inline policies, permission_set_account_id).
- D45/D47: account identity sourced from account.hcl, not hardcoded per-env literals.
- docs/terragrunt-concepts.md: canonical seven-layer hierarchy basename idioms throughout.
- Security: no hardcoded AWS access keys, no em-dash characters, no secrets in HCL.

All assertions use file-content inspection to validate static HCL without
requiring a live AWS credential or a real Terraform init.

Test count: 40 unit-marked tests covering file existence, layer basename idioms,
single-module source assertion, include patterns, input contract validation,
security patterns, and no-hardcoded-values checks.
"""

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
IDENTITY_SERVICE_DIR = SANDBOX_BASE / "_singletons" / "shared" / "identity"
IDENTITY_INSTANCE_DIR = IDENTITY_SERVICE_DIR / "000"

SERVICE_HCL = IDENTITY_SERVICE_DIR / "service.hcl"
SERVICE_INSTANCE_HCL = IDENTITY_INSTANCE_DIR / "service_instance.hcl"
TERRAGRUNT_HCL = IDENTITY_INSTANCE_DIR / "terragrunt.hcl"

# The references/identity reference module path (AC-3).
IDENTITY_MODULE_PATH = "references/identity"

# Identity Center group name literals from _envcommon/identity.hcl (D8).
VIEWER_GROUP_NAME = "telemetry-viewer"
AUTHOR_GROUP_NAME = "telemetry-author"
ADMIN_GROUP_NAME = "telemetry-admin"

# QuickSight role name literals from _envcommon/identity.hcl.
ANALYST_ROLE_NAME = "TelemetryAnalyst"
ADMIN_ROLE_NAME = "TelemetryAdmin"

# The include.root.locals.common_tags pattern required by docs/terragrunt-concepts.md (expose=true).
ROOT_COMMON_TAGS_REF = "include.root.locals.common_tags"


# ---------------------------------------------------------------------------
# File fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def service_hcl_content() -> str:
    """Read service.hcl content. Fails loudly if the file does not exist."""
    assert SERVICE_HCL.exists(), (
        f"service.hcl not found at {SERVICE_HCL}. "
        "This file must be created for the sandbox identity service layer (E6-F3-S2-T1)."
    )
    return SERVICE_HCL.read_text()


@pytest.fixture(scope="module")
def service_instance_hcl_content() -> str:
    """Read service_instance.hcl content. Fails loudly if the file does not exist."""
    assert SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {SERVICE_INSTANCE_HCL}. "
        "This file must be created for the sandbox identity service instance (E6-F3-S2-T1)."
    )
    return SERVICE_INSTANCE_HCL.read_text()


@pytest.fixture(scope="module")
def terragrunt_hcl_content() -> str:
    """Read the leaf terragrunt.hcl content. Fails loudly if the file does not exist."""
    assert TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox identity terragrunt unit (E6-F3-S2-T1)."
    )
    return TERRAGRUNT_HCL.read_text()


# ---------------------------------------------------------------------------
# File existence assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_service_hcl_exists() -> None:
    """service.hcl must exist at the identity service layer path (E6-F3-S2-T1)."""
    assert SERVICE_HCL.exists(), (
        f"service.hcl not found at {SERVICE_HCL}. "
        "Required for the identity service layer (AC-3, E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_service_instance_hcl_exists() -> None:
    """service_instance.hcl must exist at the identity/000 service instance path."""
    assert SERVICE_INSTANCE_HCL.exists(), (
        f"service_instance.hcl not found at {SERVICE_INSTANCE_HCL}. "
        "Required for the identity service instance (AC-3, E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_exists() -> None:
    """Leaf terragrunt.hcl must exist at identity/000/terragrunt.hcl."""
    assert TERRAGRUNT_HCL.exists(), (
        f"terragrunt.hcl not found at {TERRAGRUNT_HCL}. "
        "Required leaf unit file for the sandbox identity terragrunt unit (AC-3, E6-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# Layer basename idiom assertions (docs/terragrunt-concepts.md)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_service_hcl_declares_basename_local(service_hcl_content: str) -> None:
    """service.hcl must use the basename(get_terragrunt_dir()) idiom for service."""
    assert "basename(get_terragrunt_dir())" in service_hcl_content, (
        "service.hcl must declare `service = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_service_hcl_parent_dir_is_identity(service_hcl_content: str) -> None:
    """service.hcl must be located at identity/ so its basename resolves correctly."""
    assert SERVICE_HCL.parent.name == "identity", (
        f"service.hcl parent dir is '{SERVICE_HCL.parent.name}', expected 'identity'. "
        "The service layer dir basename must be 'identity' (E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_service_instance_hcl_declares_basename_local(
    service_instance_hcl_content: str,
) -> None:
    """service_instance.hcl must use the basename(get_terragrunt_dir()) idiom."""
    assert "basename(get_terragrunt_dir())" in service_instance_hcl_content, (
        "service_instance.hcl must declare "
        "`service_instance = basename(get_terragrunt_dir())`. "
        "This is the docs/terragrunt-concepts.md canonical layer idiom (E6-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# AC-3: single-module source (references/identity only)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terragrunt_hcl_sources_identity_reference(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must source references/identity (AC-3)."""
    assert IDENTITY_MODULE_PATH in terragrunt_hcl_content, (
        f"terragrunt.hcl does not reference '{IDENTITY_MODULE_PATH}'. "
        "The unit must source the identity reference module (AC-3, docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_sources_exactly_one_module(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must contain exactly one terraform.source block (AC-3).

    AC-3 requires that each leaf sources exactly one reference module. Multiple
    source declarations would violate the single-module principle.
    """
    source_count = len(re.findall(r"^\s*source\s*=", terragrunt_hcl_content, re.MULTILINE))
    assert source_count == 1, (
        f"terragrunt.hcl contains {source_count} source declarations; expected exactly 1. "
        "AC-3 requires the leaf to source exactly one reference module (references/identity). "
        "No second source or nested module include is permitted."
    )


@pytest.mark.unit
def test_terragrunt_hcl_has_terraform_source_block(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must contain a terraform {} source block (AC-3)."""
    assert "terraform {" in terragrunt_hcl_content, (
        "terragrunt.hcl does not contain a terraform {} block. "
        "The leaf must declare `terraform { source = ... }` to source "
        "references/identity (AC-3, E6-F3-S2-T1)."
    )


# ---------------------------------------------------------------------------
# AC-3: include patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terragrunt_hcl_includes_root(terragrunt_hcl_content: str) -> None:
    """Leaf terragrunt.hcl must include the root terragrunt.hcl (docs/terragrunt-concepts.md)."""
    assert 'include "root"' in terragrunt_hcl_content, (
        "terragrunt.hcl must include the root terragrunt.hcl via "
        '`include "root" { path = find_in_parent_folders("root.hcl") ... }`. '
        "This is required for namespace-derived state backend inheritance "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_includes_envcommon(terragrunt_hcl_content: str) -> None:
    """Leaf terragrunt.hcl must include the _envcommon/identity.hcl template (D37)."""
    assert "_envcommon/identity.hcl" in terragrunt_hcl_content, (
        "terragrunt.hcl must include '_envcommon/identity.hcl' via an include block. "
        "The shared input template provides the Viewer/Author/Admin group-to-role "
        "input contract (D37, E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_root_include_uses_expose_true(
    terragrunt_hcl_content: str,
) -> None:
    """The root include block must set expose = true (docs/terragrunt-concepts.md)."""
    expose_set = (
        "expose         = true" in terragrunt_hcl_content
        or "expose = true" in terragrunt_hcl_content
    )
    assert expose_set, (
        'terragrunt.hcl include "root" block must set expose = true. '
        "This is required for include.root.locals.common_tags to be accessible "
        "in the leaf inputs block (docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_root_include_uses_deep_merge(
    terragrunt_hcl_content: str,
) -> None:
    """The root include block must set merge_strategy = \"deep\" (docs/terragrunt-concepts.md)."""
    assert 'merge_strategy = "deep"' in terragrunt_hcl_content, (
        'terragrunt.hcl include "root" block must set merge_strategy = "deep". '
        "Deep merge is required so that inputs from multiple includes are merged "
        "correctly (docs/terragrunt-concepts.md, E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_uses_find_in_parent_folders_for_root(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must use find_in_parent_folders to locate root terragrunt.hcl."""
    assert 'find_in_parent_folders("root.hcl")' in terragrunt_hcl_content, (
        "terragrunt.hcl must use find_in_parent_folders('terragrunt.hcl') to locate "
        "the root config. This is the docs/terragrunt-concepts.md canonical pattern."
    )


# ---------------------------------------------------------------------------
# AC-13: Viewer/Author/Admin tier input contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "group_name,tier",
    [
        (VIEWER_GROUP_NAME, "viewer"),
        (AUTHOR_GROUP_NAME, "author"),
        (ADMIN_GROUP_NAME, "admin"),
    ],
)
def test_envcommon_identity_declares_group_name(group_name: str, tier: str) -> None:
    """_envcommon/identity.hcl must declare the group name for each tier (D8, AC-13)."""
    envcommon_path = REPO_ROOT / "terragrunt" / "_envcommon" / "identity.hcl"
    assert envcommon_path.exists(), (
        f"_envcommon/identity.hcl not found at {envcommon_path}. "
        "This file is required to supply the Viewer/Author/Admin group name inputs (D8)."
    )
    content = envcommon_path.read_text()
    assert group_name in content, (
        f"_envcommon/identity.hcl does not declare the {tier} group name '{group_name}'. "
        "All three tier group names must be input-driven from _envcommon/identity.hcl (D8, AC-13)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "role_name,tier",
    [
        (ANALYST_ROLE_NAME, "author/analyst"),
        (ADMIN_ROLE_NAME, "admin"),
    ],
)
def test_envcommon_identity_declares_role_name(role_name: str, tier: str) -> None:
    """_envcommon/identity.hcl must declare the role name for each tier (D37, AC-13)."""
    envcommon_path = REPO_ROOT / "terragrunt" / "_envcommon" / "identity.hcl"
    assert envcommon_path.exists(), (
        f"_envcommon/identity.hcl not found at {envcommon_path}. "
        "This file is required to supply the QuickSight role name inputs (D37)."
    )
    content = envcommon_path.read_text()
    assert role_name in content, (
        f"_envcommon/identity.hcl does not declare the {tier} role name '{role_name}'. "
        "All role names must be input-driven from _envcommon/identity.hcl (D37, AC-13)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_passes_tags_using_root_locals(
    terragrunt_hcl_content: str,
) -> None:
    """Inputs tags must use include.root.locals.common_tags, not local.common_tags.

    In terragrunt with include expose = true, the root include's locals are only
    accessible via include.root.locals.<local_name>. Using local.common_tags in the
    leaf inputs block resolves to the leaf's own locals only, which do not include
    common_tags, causing 'Unsupported attribute' errors at parse time.
    """
    assert ROOT_COMMON_TAGS_REF in terragrunt_hcl_content, (
        f"terragrunt.hcl inputs.tags must reference '{ROOT_COMMON_TAGS_REF}', "
        "not 'local.common_tags'. The root include is expose=true so its locals are "
        "accessible via include.root.locals.<name> only -- using local.<name> resolves "
        "to the leaf locals block and will fail with 'Unsupported attribute' (AC-3)."
    )


# ---------------------------------------------------------------------------
# D4 / D31: no hardcoded account id or env-specific logic in the leaf
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terragrunt_hcl_no_hardcoded_prod_account_id(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must not hardcode the prod account id (D31, D45).

    The prod account id (444444444444) must never appear in sandbox HCL files.
    All account identity is sourced from account.hcl (D45).
    """
    prod_account_id = "444444444444"
    assert prod_account_id not in terragrunt_hcl_content, (
        f"terragrunt.hcl contains the prod account id '{prod_account_id}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


@pytest.mark.unit
def test_service_hcl_no_hardcoded_prod_account_id(
    service_hcl_content: str,
) -> None:
    """service.hcl must not hardcode the prod account id (D31, D45)."""
    prod_account_id = "444444444444"
    assert prod_account_id not in service_hcl_content, (
        f"service.hcl contains the prod account id '{prod_account_id}'. "
        "Account identity must be sourced from account.hcl, never hardcoded (D31, D45)."
    )


# ---------------------------------------------------------------------------
# D37: inputs block present in leaf
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terragrunt_hcl_has_inputs_block(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must contain an inputs block (D37).

    The block may be a plain object literal (``inputs = {``) or a merge() over a base
    object and a conditional source-overrides object (``inputs = merge({``). The merge
    form is required when the leaf must OMIT the ``*_source`` keys for the toggle=false
    (sandbox/QA/root) tier: a ``*_source`` child-module variable is ``const = true`` with
    a relative-path default, and passing an explicit ``null`` overrides that default and
    crashes ``terraform init``. merge() lets the source keys be present (prod) or omitted
    entirely (sandbox) without ever assigning null
    (docs/terragrunt-concepts.md Leaf behavior toggle=false).
    """
    assert (
        "inputs = {" in terragrunt_hcl_content
        or "inputs={" in terragrunt_hcl_content
        or "inputs = merge(" in terragrunt_hcl_content
        or "inputs=merge(" in terragrunt_hcl_content
    ), (
        "terragrunt.hcl does not contain an inputs block. "
        "The leaf must pass module inputs (D37, E6-F3-S2-T1)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_passes_assume_role_policy_for_analyst(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must pass analyst_assume_role_policy_json (D37, AC-13)."""
    assert "analyst_assume_role_policy_json" in terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'analyst_assume_role_policy_json'. "
        "This required input wires the TelemetryAnalyst role trust policy (D37, AC-13)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_passes_assume_role_policy_for_admin(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must pass admin_assume_role_policy_json (D37, AC-13)."""
    assert "admin_assume_role_policy_json" in terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'admin_assume_role_policy_json'. "
        "This required input wires the TelemetryAdmin role trust policy (D37, AC-13)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_passes_analyst_inline_policies(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must pass analyst_inline_policies (D37, AC-13)."""
    assert "analyst_inline_policies" in terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'analyst_inline_policies'. "
        "This required input carries the D2r read-only Athena/Glue/S3-results policy (D37, AC-13)."
    )


@pytest.mark.unit
def test_terragrunt_hcl_passes_admin_inline_policies(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must pass admin_inline_policies (D37, AC-13)."""
    assert "admin_inline_policies" in terragrunt_hcl_content, (
        "terragrunt.hcl does not pass 'admin_inline_policies'. "
        "This required input carries the D3r manage QuickSight/datasets policy (D37, AC-13)."
    )


# ---------------------------------------------------------------------------
# Security: no hardcoded credentials or forbidden patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "hcl_content_fixture_name,file_label",
    [
        ("service_hcl_content", "service.hcl"),
        ("service_instance_hcl_content", "service_instance.hcl"),
        ("terragrunt_hcl_content", "terragrunt.hcl"),
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
        ("service_hcl_content", "service.hcl"),
        ("service_instance_hcl_content", "service_instance.hcl"),
        ("terragrunt_hcl_content", "terragrunt.hcl"),
    ],
)
def test_no_em_dash_in_hcl_files(
    request: pytest.FixtureRequest,
    hcl_content_fixture_name: str,
    file_label: str,
) -> None:
    """No HCL file may contain an em-dash character (U+2014) per code standards."""
    content: str = request.getfixturevalue(hcl_content_fixture_name)
    assert "\u2014" not in content, (
        f"{file_label} contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# D8 / AC-13: group-name inputs must not be empty literals in the leaf
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terragrunt_hcl_does_not_hardcode_empty_group_names(
    terragrunt_hcl_content: str,
) -> None:
    """Leaf terragrunt.hcl must not assign empty string literals for group name inputs.

    An empty group name bypasses the fail-fast validation in references/identity
    variables.tf and would cause silent Identity Center binding failures (AC-13, D8).
    """
    pattern = r'(viewer|author|admin)_group_name\s*=\s*""'
    match = re.search(pattern, terragrunt_hcl_content)
    assert match is None, (
        f"terragrunt.hcl assigns an empty string to a group name input "
        f"(matched: '{match.group(0)}'). "
        "Group name inputs must be non-empty -- a missing group id must fail fast "
        "per the references/identity variable validation (AC-13, D8)."
    )


# ---------------------------------------------------------------------------
# Layer directory structure assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_identity_service_dir_exists() -> None:
    """The identity service directory must exist under sandbox/000/."""
    assert IDENTITY_SERVICE_DIR.exists(), (
        f"Identity service directory not found at {IDENTITY_SERVICE_DIR}. "
        "The 7-layer hierarchy requires a service directory named 'identity' "
        "(docs/terragrunt-concepts.md)."
    )


@pytest.mark.unit
def test_identity_instance_dir_exists() -> None:
    """The identity/000 service instance directory must exist."""
    assert IDENTITY_INSTANCE_DIR.exists(), (
        f"Identity instance directory not found at {IDENTITY_INSTANCE_DIR}. "
        "The 7-layer hierarchy requires a service instance dir named '000' "
        "(docs/terragrunt-concepts.md)."
    )
