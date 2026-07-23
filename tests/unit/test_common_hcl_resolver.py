"""Unit regression tests for terragrunt/common/common.hcl -- shared locals + resolver.

These tests assert the structural and content constraints for the common mapping layer
at terragrunt/common/common.hcl (spec S4.1, AC-3, AC-4, AC-5).

Assertions (by AC):

AC-3:
- common.hcl exists at terragrunt/common/common.hcl and NOT inside terragrunt/live/.
- common.hcl exposes base shared locals: common_tags, default_region,
  terraform_modules_path = "${get_repo_root()}/providers/aws".
- Every JSON map is loaded via jsondecode(file("${get_repo_root()}/terragrunt/common/<map>.json"))
  with no relative-path read.

AC-4:
- common.hcl uses the lookup(map, key, null)-then-tobool("ERROR: ...") idiom for
  the dns_owner_zone_id guard (the only scope-invariant fail-fast that can be live
  in common.hcl, per AC-FUNC-004 and Terragrunt 1.0.7 evaluation-context constraint).
- The error message names common/accounts.json + remediation.
- Per-scope keyed lookups (accounts[aws_account_id], domains[environment_name],
  networks[namespace]) require caller-derived keys unavailable inside common.hcl
  when loaded via read_terragrunt_config(); those belong at the root terragrunt.hcl
  layer (spec section 4.3, task E8-F2-S1-T1).

AC-5:
- The dns_owner_zone_id guard fails fast when the value is missing or a placeholder.
- common.hcl documents the canonical caller-side lookup-then-tobool idiom for each map
  so consumers apply one shared pattern (spec AC-5, S4.1, S7).
- Each map filename (accounts.json, domains.json, networks.json, contacts.json) is
  referenced in non-comment code lines (loading code and/or error messages).
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
COMMON_HCL_PATH = REPO_ROOT / "terragrunt" / "common" / "common.hcl"
LIVE_DIR = REPO_ROOT / "terragrunt" / "live"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def common_hcl_content() -> str:
    """Read common.hcl content. Fails if the file does not exist (AC-3)."""
    assert COMMON_HCL_PATH.exists(), (
        f"terragrunt/common/common.hcl not found at {COMMON_HCL_PATH}. "
        "Author this file as part of E8-F1-S1-T1 (spec AC-3, S4.1)."
    )
    return COMMON_HCL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def common_hcl_code_lines(common_hcl_content: str) -> str:
    """Return only the non-comment, non-blank lines of common.hcl joined as a string.

    This fixture enables assertions that distinguish executable HCL from comment
    templates. A line is a comment if its first non-whitespace character is '#'.
    This mirrors the HCL single-line comment convention (block comments /* */ are
    not used in Terragrunt config files by convention, so single-line suffices).
    """
    code_lines = [
        line
        for line in common_hcl_content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(code_lines)


# ---------------------------------------------------------------------------
# AC-3: existence and location
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_exists() -> None:
    """common.hcl must exist at terragrunt/common/common.hcl (spec AC-3)."""
    assert COMMON_HCL_PATH.exists(), (
        f"terragrunt/common/common.hcl not found at {COMMON_HCL_PATH}. "
        "Author this file as part of E8-F1-S1-T1 (spec AC-3)."
    )


@pytest.mark.unit
def test_common_hcl_not_inside_live() -> None:
    """common.hcl must NOT reside inside terragrunt/live/ (spec AC-3, S3)."""
    live_path = LIVE_DIR / "common" / "common.hcl"
    assert not live_path.exists(), (
        f"common.hcl must NOT reside inside terragrunt/live/ (spec AC-3). "
        f"Found forbidden copy at {live_path}. "
        "The common/ layer must be outside every copyable subtree."
    )


# ---------------------------------------------------------------------------
# AC-3: base shared locals
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_exposes_common_tags(common_hcl_content: str) -> None:
    """common.hcl must expose a common_tags local (spec AC-3, S4.1)."""
    assert "common_tags" in common_hcl_content, (
        "terragrunt/common/common.hcl does not expose 'common_tags'. "
        "The base shared locals must include a common_tags map (spec AC-3, S4.1)."
    )


@pytest.mark.unit
def test_common_hcl_exposes_default_region(common_hcl_content: str) -> None:
    """common.hcl must expose a default_region local (spec AC-3, S4.1)."""
    assert "default_region" in common_hcl_content, (
        "terragrunt/common/common.hcl does not expose 'default_region'. "
        "The base shared locals must include default_region (spec AC-3, S4.1)."
    )


@pytest.mark.unit
def test_common_hcl_exposes_terraform_modules_path(common_hcl_content: str) -> None:
    """common.hcl must expose terraform_modules_path = get_repo_root()/providers/aws (spec AC-3)."""
    assert "terraform_modules_path" in common_hcl_content, (
        "terragrunt/common/common.hcl does not expose 'terraform_modules_path'. "
        "The base shared locals must include terraform_modules_path (spec AC-3, S4.1)."
    )


@pytest.mark.unit
def test_common_hcl_terraform_modules_path_uses_get_repo_root(common_hcl_content: str) -> None:
    """terraform_modules_path must use get_repo_root()/providers/aws (spec AC-3, S4.1)."""
    pattern = re.compile(r'terraform_modules_path\s*=\s*"\$\{get_repo_root\(\)\}/providers/aws"')
    assert pattern.search(common_hcl_content), (
        "terragrunt/common/common.hcl does not set "
        'terraform_modules_path = "${get_repo_root()}/providers/aws". '
        "The spec AC-3 and S4.1 require this exact form."
    )


# ---------------------------------------------------------------------------
# AC-3: JSON map loading via get_repo_root()
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "map_name",
    ["accounts", "domains", "networks", "contacts"],
)
def test_common_hcl_loads_map_via_jsondecode_and_get_repo_root(
    common_hcl_content: str, map_name: str
) -> None:
    """Each JSON map must be loaded via jsondecode(file("${get_repo_root()}/...")) (spec AC-3)."""
    pattern = re.compile(
        rf'jsondecode\s*\(\s*file\s*\(\s*"\$\{{get_repo_root\(\)\}}/terragrunt/common/{map_name}\.json"\s*\)\s*\)'
    )
    assert pattern.search(common_hcl_content), (
        f"terragrunt/common/common.hcl does not load {map_name}.json via "
        f'jsondecode(file("${{get_repo_root()}}/terragrunt/common/{map_name}.json")). '
        "All maps must use get_repo_root() to anchor the path so common/ resolves "
        "from any copied subtree depth (spec AC-3, S4.1)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "map_name",
    ["accounts", "domains", "networks", "contacts"],
)
def test_common_hcl_no_relative_path_for_map(common_hcl_content: str, map_name: str) -> None:
    """No JSON map may be read via a relative path (spec AC-3, S4.1)."""
    relative_pattern = re.compile(rf'jsondecode\s*\(\s*file\s*\(\s*"[^$][^"]*{map_name}\.json"')
    assert not relative_pattern.search(common_hcl_content), (
        f"terragrunt/common/common.hcl reads {map_name}.json via a relative path. "
        "All maps must use get_repo_root() to ensure common/ resolves from any depth "
        "(spec AC-3, S4.1)."
    )


# ---------------------------------------------------------------------------
# AC-4: fail-fast idiom -- dns_owner_zone_id guard is the scope-invariant guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_accounts_resolver_uses_lookup_then_tobool(
    common_hcl_content: str,
) -> None:
    """common.hcl must use the lookup() idiom for the dns-owner fail-fast guard (spec AC-4)."""
    assert "lookup(" in common_hcl_content, (
        "terragrunt/common/common.hcl does not use lookup() for fail-fast key lookups. "
        'The dns-owner guard must use lookup(map, key, null) then tobool("ERROR: ...") '
        "(spec AC-4, S4.1)."
    )


@pytest.mark.unit
def test_common_hcl_accounts_resolver_has_tobool_error(common_hcl_content: str) -> None:
    """common.hcl must use tobool(\"ERROR: ...\") for the dns-owner fail-fast guard (spec AC-4)."""
    pattern = re.compile(r'tobool\s*\(\s*"ERROR:')
    assert pattern.search(common_hcl_content), (
        'terragrunt/common/common.hcl does not use tobool("ERROR: ...") for fail-fast abort. '
        "The dns-owner zone id guard must abort Terragrunt parse on missing/placeholder value "
        "(spec AC-4, AC-5, S4.1)."
    )


@pytest.mark.unit
def test_common_hcl_accounts_error_names_accounts_json(common_hcl_content: str) -> None:
    """The dns-owner tobool error must name common/accounts.json (spec AC-4)."""
    assert "accounts.json" in common_hcl_content, (
        "terragrunt/common/common.hcl tobool error does not name 'accounts.json'. "
        "The error message must identify the file so the operator knows where to add a row "
        "(spec AC-4, S7)."
    )


# ---------------------------------------------------------------------------
# AC-5: domains, networks, contacts, dns_owner_zone_id resolvers fail-fast
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_domains_error_names_domains_json(common_hcl_content: str) -> None:
    """domains.json must be referenced in common.hcl (spec AC-5, AC-3)."""
    assert "domains.json" in common_hcl_content, (
        "terragrunt/common/common.hcl does not mention 'domains.json'. "
        "The file must load domains.json and document the caller-side idiom "
        "(spec AC-5, AC-3, S7)."
    )


@pytest.mark.unit
def test_common_hcl_networks_error_names_networks_json(common_hcl_content: str) -> None:
    """networks.json must be referenced in common.hcl (spec AC-5, AC-3)."""
    assert "networks.json" in common_hcl_content, (
        "terragrunt/common/common.hcl does not mention 'networks.json'. "
        "The file must load networks.json and document the caller-side idiom "
        "(spec AC-5, AC-3, S7)."
    )


@pytest.mark.unit
def test_common_hcl_dns_owner_zone_id_guard_exists(common_hcl_content: str) -> None:
    """The dns_owner_zone_id guard must be present for the dns-owner account (spec AC-5)."""
    assert "dns_owner_zone_id" in common_hcl_content, (
        "terragrunt/common/common.hcl does not reference 'dns_owner_zone_id'. "
        "The dns-owner account zone ID must be guarded with a fail-fast check "
        "(spec AC-5, S0.2, S7)."
    )


@pytest.mark.unit
def test_common_hcl_no_fallback_for_accounts_lookup(common_hcl_content: str) -> None:
    """The accounts lookup must not supply a non-null fallback (spec AC-4, S3.5: no fallback logic).

    Every lookup against the accounts map must use null as the third argument so that a
    missing key triggers the tobool(\"ERROR: ...\") fail-fast pattern rather than silently
    falling back to a default value. The test verifies that no lookup(local.*accounts*, ...)
    supplies a non-null third argument.
    """
    pattern = re.compile(
        r"lookup\s*\(\s*local\.[a-z_]*accounts[a-z_]*\s*,"  # lookup(local.*accounts*,
        r"\s*[^,\n)]+,"  # key argument (stops at comma)
        r"\s*(?!null[\s,\n)])"  # third arg must be null (not a non-null value)
        r"[^\s\n]"  # something non-whitespace after the second comma (a non-null value)
    )
    assert not pattern.search(common_hcl_content), (
        "terragrunt/common/common.hcl uses a non-null fallback default in the accounts lookup. "
        "No fallback logic is permitted for identity-scoped lookups (spec S3.5, AC-4)."
    )


# ---------------------------------------------------------------------------
# AC-3: locals block structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_has_locals_block(common_hcl_content: str) -> None:
    """common.hcl must have a locals { } block (spec AC-3, S4.1)."""
    assert "locals {" in common_hcl_content or "locals{" in common_hcl_content, (
        "terragrunt/common/common.hcl does not have a locals block. "
        "All shared values must be declared inside a locals { } block."
    )


# ---------------------------------------------------------------------------
# No em-dashes or bypass annotations (code standards)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_no_em_dash() -> None:
    """common.hcl must not contain an em-dash character (U+2014) (code standards)."""
    if not COMMON_HCL_PATH.exists():
        pytest.fail("common.hcl does not exist -- file existence tests cover this.")
    content = COMMON_HCL_PATH.read_text(encoding="utf-8")
    assert "\u2014" not in content, (
        "terragrunt/common/common.hcl contains an em-dash character (U+2014). "
        "Em-dashes are prohibited in all source files (code standards)."
    )


# ---------------------------------------------------------------------------
# AC-4/AC-5: dns_owner_zone_id guard is the live scope-invariant fail-fast in common.hcl
#
# On Terragrunt 1.0.7, read_terragrunt_config() evaluates a loaded file's locals in
# that file's OWN directory context. Caller-derived keys (aws_account_id from
# basename(get_terragrunt_dir()), environment_name, namespace) are unavailable inside
# common.hcl's evaluation context. Per spec section 4.3, per-scope keyed lookups
# (accounts[aws_account_id], domains[environment_name], networks[namespace],
# contacts[environment_name]) belong at the root terragrunt.hcl layer (E8-F2-S1-T1).
#
# The dns_owner_zone_id guard (keyed by is_dns_owner=true in accounts.json) is the
# ONLY scope-invariant fail-fast that common.hcl can perform directly. The per-map
# lookup idiom is documented in common.hcl's comments so callers apply one pattern.
#
# These tests strip comment lines before asserting tobool occurrences to ensure
# commented-out resolver templates do not produce false PASSes.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_common_hcl_dns_owner_tobool_guard_is_in_live_code(
    common_hcl_code_lines: str,
) -> None:
    """The dns_owner_zone_id tobool("ERROR:") guard must be in non-comment HCL (spec AC-4, AC-5).

    The dns_owner_zone_id guard is the scope-invariant fail-fast live in common.hcl
    (keyed by is_dns_owner=true, no caller context required). On Terragrunt 1.0.7,
    per-scope keyed lookups (accounts[aws_account_id] etc.) cannot be evaluated inside
    common.hcl because read_terragrunt_config() resolves locals in the loaded file's own
    directory context; those belong at the root terragrunt.hcl layer (spec section 4.3).
    This test asserts at least 1 live tobool guard -- the dns_owner_zone_id guard.
    """
    pattern = re.compile(r'tobool\s*\(\s*"ERROR:')
    matches = pattern.findall(common_hcl_code_lines)
    assert len(matches) >= 1, (
        f'terragrunt/common/common.hcl has {len(matches)} tobool("ERROR: ...") calls '
        "in executable (non-comment) HCL. "
        "The dns_owner_zone_id guard must be live code (spec AC-4, AC-5, S7). "
        "Commenting out or removing the guard causes this test to fail."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "map_filename",
    ["accounts.json", "domains.json", "networks.json"],
)
def test_common_hcl_map_filename_in_code_lines(
    common_hcl_code_lines: str, map_filename: str
) -> None:
    """Each map's filename must appear in non-comment executable HCL (spec AC-3, AC-4, AC-5).

    Asserts that the <map>.json filename appears on a non-comment code line. The filename
    may appear in the jsondecode(file(...)) loading expression OR in a tobool error message.
    A file that only mentions map names inside comment blocks cannot satisfy the loading
    (AC-3) or error-message (AC-4, AC-5) requirements at the same time.
    """
    assert map_filename in common_hcl_code_lines, (
        f"terragrunt/common/common.hcl does not reference '{map_filename}' in any "
        "non-comment executable HCL line. The map must be loaded via jsondecode(file(...)) "
        f"in live code so common/ resolves from any copied subtree depth (spec AC-3, S4.1)."
    )
