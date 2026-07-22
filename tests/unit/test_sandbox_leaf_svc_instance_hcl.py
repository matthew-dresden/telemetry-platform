"""Unit tests for sandbox leaf terragrunt.hcl instance-relative dependency paths.

These tests assert that all 9 sandbox leaf terragrunt.hcl files under
terragrunt/live/telemetry/us-east-1/222222222222/sandbox/000/ carry the
instance-relative svc_instance local and interpolate it in every same-tier
sibling dependency config_path, as required by E8-F4-S1-T1 (AC-FUNC-001,
AC-FUNC-002, AC-FUNC-003).

AC-FUNC-001: every leaf defines locals { svc_instance = basename(get_terragrunt_dir()) }
AC-FUNC-002: no same-tier sibling config_path contains a hardcoded three-digit instance index
AC-FUNC-003: every same-tier sibling config_path interpolates ${local.svc_instance}

Meta-guard (AC-T3-2): a test asserts that this test file itself contains zero literal
U+2014 em-dash bytes so the file cannot self-violate the no-em-dash rule.
All no-em-dash guard assertions build the character from chr(0x2014), never embedding
the literal glyph.

All assertions use file-content inspection to validate static HCL without requiring
a live AWS credential or a real Terraform init.
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

# The sandbox leaf service names that E8-F4-S1-T1 migrated to instance-relative
# dependency config_paths. Each leaf lives at:
#   ENV_INSTANCE_BASE / <service> / "000" / "terragrunt.hcl"
SANDBOX_LEAF_SERVICES = [
    "acm-validate-collector",
    "athena",
    "collector-ingestion",
    "data-lake",
    "dns-collector",
    "observability",
]

# The canonical instance-relative local declaration (AC-FUNC-001).
SVC_INSTANCE_LOCAL_DECL = "svc_instance = basename(get_terragrunt_dir())"

# The interpolation pattern every same-tier sibling config_path must carry (AC-FUNC-003).
SVC_INSTANCE_INTERPOLATION = "${local.svc_instance}"

# Regex that matches a three-digit numeric instance index hardcoded in a same-tier
# sibling config_path value, e.g. config_path = "../../foo/000" (AC-FUNC-002).
# Only paths with exactly two leading "../" components (../../) are matched -- these
# are same-tier siblings at the service-instance level. Cross-layer paths that traverse
# more directory levels (e.g. ../../../../../bootstrap/000/state-bootstrap/000) do NOT
# match because they have more than two leading "../" segments (E8-F4-S1-T2 scope).
HARDCODED_INSTANCE_INDEX_PATTERN = re.compile(
    r'config_path\s*=\s*"(\.\./){2}[^/"]+/\d{3}"',
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Meta-guard: this test file must contain zero literal U+2014 em-dashes (AC-T3-2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_this_file_contains_no_literal_em_dash() -> None:
    """This test file must not contain any literal U+2014 em-dash characters.

    The em-dash character (U+2014) is prohibited in all source files per CLAUDE.md.
    Every no-em-dash guard in this module builds the character from the Python
    escape sequence '\\u2014' (chr(0x2014)), never embedding the glyph directly.
    This meta-assertion guards against regression: if any editor pastes the glyph,
    this test fails first and surfaces the exact defect before the apply-amendment
    layer-3 em-dash scan blocks the commit.
    """
    this_file = pathlib.Path(__file__)
    raw_bytes = this_file.read_bytes()
    em_dash_bytes = chr(0x2014).encode("utf-8")
    assert em_dash_bytes not in raw_bytes, (
        f"{this_file.name} contains one or more literal U+2014 em-dash bytes "
        f"(UTF-8: {em_dash_bytes.hex()}). "
        "All em-dash guard assertions must use '\\u2014' or chr(0x2014), "
        "never the literal glyph (CLAUDE.md code standards, AC-T3-1, AC-T3-2)."
    )


# ---------------------------------------------------------------------------
# Helper: load a leaf terragrunt.hcl for a given service name
# ---------------------------------------------------------------------------


def _leaf_hcl_path(service: str) -> pathlib.Path:
    """Return the path to the leaf terragrunt.hcl for the given service."""
    _shared_base = (
        (SANDBOX_BASE / "_singletons" / "shared")
        if service in {"athena", "data-lake", "dns-prod-zone", "identity", "observability"}
        else ENV_INSTANCE_BASE
    )
    return _shared_base / service / "000" / "terragrunt.hcl"


def _load_leaf_hcl(service: str) -> str:
    """Read and return the leaf terragrunt.hcl content. Fails loudly if missing."""
    path = _leaf_hcl_path(service)
    assert path.exists(), (
        f"Leaf terragrunt.hcl not found at {path}. "
        f"The sandbox '{service}/000/terragrunt.hcl' must exist "
        "(E8-F4-S1-T1, AC-FUNC-001, AC-FUNC-002, AC-FUNC-003)."
    )
    return path.read_text()


# ---------------------------------------------------------------------------
# AC-FUNC-001: each leaf defines svc_instance = basename(get_terragrunt_dir())
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("service", SANDBOX_LEAF_SERVICES)
def test_leaf_defines_svc_instance_local(service: str) -> None:
    """Each sandbox leaf must define locals { svc_instance = basename(get_terragrunt_dir()) }.

    The svc_instance local makes every same-tier sibling dependency config_path
    instance-relative. Copying the service-instance folder to index "001" causes
    svc_instance to resolve to "001", wiring all sibling deps at the new index
    with zero edits (spec section G5, AC-FUNC-001, E8-F4-S1-T1).
    """
    content = _load_leaf_hcl(service)
    assert SVC_INSTANCE_LOCAL_DECL in content, (
        f"{service}/000/terragrunt.hcl does not declare "
        f"'svc_instance = basename(get_terragrunt_dir())'. "
        "Every sandbox leaf must carry this local so same-tier dependency "
        "config_paths are instance-relative (AC-FUNC-001, E8-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-FUNC-002: no same-tier sibling config_path contains a hardcoded 3-digit index
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("service", SANDBOX_LEAF_SERVICES)
def test_leaf_has_no_hardcoded_instance_index_in_same_tier_dep(service: str) -> None:
    """No same-tier sibling config_path may contain a hardcoded three-digit instance index.

    A same-tier sibling config_path must interpolate ${local.svc_instance} rather
    than a literal "/000" so that the leaf is portable across service-instance indexes
    without edits (spec section 4.4, AC-FUNC-002, E8-F4-S1-T1).

    Cross-layer paths (e.g. ../../../../../bootstrap/000/...) are excluded because
    the state-bootstrap dependency is intentionally cross-layer and not a same-tier
    sibling (E8-F4-S1-T2 scope).
    """
    content = _load_leaf_hcl(service)
    match = HARDCODED_INSTANCE_INDEX_PATTERN.search(content)
    assert match is None, (
        f"{service}/000/terragrunt.hcl contains a same-tier sibling config_path "
        f"with a hardcoded three-digit instance index: {match.group() if match else ''!r}. "
        "All same-tier sibling config_paths must interpolate "
        "'${local.svc_instance}' rather than a literal '/000' index "
        "(spec section 4.4, AC-FUNC-002, E8-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-FUNC-003: every same-tier sibling config_path interpolates ${local.svc_instance}
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("service", SANDBOX_LEAF_SERVICES)
def test_leaf_has_svc_instance_interpolation_in_config_paths(service: str) -> None:
    """Each sandbox leaf must have at least one config_path interpolating ${local.svc_instance}.

    Every leaf that declares same-tier sibling dependencies must use
    '${local.svc_instance}' in each sibling config_path so the dependency
    wiring is instance-relative (AC-FUNC-003, E8-F4-S1-T1).
    """
    content = _load_leaf_hcl(service)
    assert SVC_INSTANCE_INTERPOLATION in content, (
        f"{service}/000/terragrunt.hcl does not contain any config_path "
        f"interpolating '{SVC_INSTANCE_INTERPOLATION}'. "
        "At least one same-tier sibling dependency config_path must interpolate "
        "'${local.svc_instance}' so the leaf is portable across service-instance "
        "indexes (AC-FUNC-003, E8-F4-S1-T1)."
    )


# ---------------------------------------------------------------------------
# Security: no em-dash in any of the 9 leaf HCL files
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("service", SANDBOX_LEAF_SERVICES)
def test_leaf_hcl_has_no_em_dash(service: str) -> None:
    """No sandbox leaf terragrunt.hcl may contain an em-dash character (U+2014).

    Em-dashes are prohibited in all source files per CLAUDE.md code standards.
    The em-dash character is built here from '\\u2014' (chr(0x2014)) so that
    this test file itself contains no literal U+2014 bytes (AC-T3-1, AC-T3-2).
    """
    content = _load_leaf_hcl(service)
    em_dash = "\u2014"
    assert em_dash not in content, (
        f"{service}/000/terragrunt.hcl contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (CLAUDE.md code standards)."
    )
