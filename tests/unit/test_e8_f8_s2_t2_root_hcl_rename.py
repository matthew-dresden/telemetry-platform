"""Unit tests for E8-F8-S2-T2: rename root config to root.hcl and update live tree references.

These tests assert the structural constraints introduced by the rename of
terragrunt/terragrunt.hcl to terragrunt/root.hcl per the Terragrunt v1.0.7
migration guidance (anti-pattern warning), and the associated fixes to the
dns-owner account leaf units (AC-CODE-002, AC-CODE-003).

Validates:
- AC-CODE-001: terragrunt/root.hcl exists; every leaf and envcommon file that
  includes the root config uses find_in_parent_folders("root.hcl"), not
  find_in_parent_folders("terragrunt.hcl").
- AC-CODE-002: the acm-validate-collector leaf unit sources
  dns_owner_zone_id and route53_zone_domain from include.root.locals.common_vars.locals
  (not from account.hcl which lacks these attributes).
- AC-CODE-003: dns-delegation locals block must not contain include.root.* references;
  the required values are extracted via a local read_terragrunt_config call instead.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

TERRAGRUNT_ROOT = REPO_ROOT / "terragrunt"
ROOT_HCL = TERRAGRUNT_ROOT / "root.hcl"

DNS_OWNER_ACCOUNT = "444444444444"
DNS_OWNER_ACCOUNT_DIR = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"

ACM_VALIDATE_COLLECTOR_HCL = (
    DNS_OWNER_ACCOUNT_DIR
    / "prod"
    / "_singletons"
    / "pretty"
    / "validate-collector"
    / "000"
    / "terragrunt.hcl"
)
DNS_DELEGATION_HCL = (
    DNS_OWNER_ACCOUNT_DIR
    / "prod"
    / "_singletons"
    / "dns_owner"
    / "dns-delegation"
    / "000"
    / "terragrunt.hcl"
)

OLD_TERRAGRUNT_HCL = TERRAGRUNT_ROOT / "terragrunt.hcl"

# Pattern for any find_in_parent_folders("terragrunt.hcl") call (both quote styles)
FIND_PARENT_OLD = re.compile(r"""find_in_parent_folders\s*\(\s*["']terragrunt\.hcl["']\s*\)""")

# Pattern that detects a non-comment line with include.* inside a locals {} block.
# HCL comment lines (# ...) are excluded; only actual assignment lines matter.
# This is the constraint from Terragrunt 1.x evaluation order (AC-CODE-003).
# We extract the locals block by finding 'locals {' ... '}' and then check
# each non-comment line for an include. assignment. The check function is used
# directly rather than a single regex to avoid false-positives on comment text.
_LOCALS_BLOCK_PATTERN = re.compile(r"locals\s*\{([^}]*)\}", re.DOTALL)


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
# AC-CODE-001: root.hcl exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_root_hcl_file_exists() -> None:
    """terragrunt/root.hcl must exist after the rename from terragrunt.hcl (AC-CODE-001).

    Terragrunt v1.0.7 migration guidance warns against using 'terragrunt.hcl' as the
    root config filename. The canonical pattern is root.hcl, explicitly referenced via
    find_in_parent_folders("root.hcl") in every leaf include block.
    """
    assert ROOT_HCL.exists(), (
        f"terragrunt/root.hcl not found at {ROOT_HCL}. "
        "AC-CODE-001 requires renaming terragrunt/terragrunt.hcl to terragrunt/root.hcl "
        "per the Terragrunt v1.0.7 migration guidance (anti-pattern warning)."
    )


@pytest.mark.unit
def test_old_terragrunt_hcl_does_not_exist() -> None:
    """terragrunt/terragrunt.hcl must not exist after the rename to root.hcl (AC-CODE-001).

    The file was renamed to terragrunt/root.hcl per the Terragrunt v1.0.7 migration
    guidance. Leaving a stub or duplicate at terragrunt/terragrunt.hcl reintroduces the
    anti-pattern warning that this task was created to eliminate.
    """
    assert not OLD_TERRAGRUNT_HCL.exists(), (
        f"terragrunt/terragrunt.hcl still exists at {OLD_TERRAGRUNT_HCL}. "
        "The old root config filename is an anti-pattern per Terragrunt v1.0.7 guidance. "
        "The file must be deleted; the canonical root config is terragrunt/root.hcl (AC-CODE-001)."
    )


# ---------------------------------------------------------------------------
# AC-CODE-001: no HCL file under terragrunt/ references find_in_parent_folders("terragrunt.hcl")
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_hcl_file_uses_find_in_parent_folders_terragrunt_hcl() -> None:
    """No HCL file under terragrunt/ may use find_in_parent_folders("terragrunt.hcl") (AC-CODE-001).

    After the rename of terragrunt/terragrunt.hcl to terragrunt/root.hcl, every leaf
    include block and envcommon read_terragrunt_config call must reference root.hcl.
    Any remaining reference to the old filename will cause Terragrunt to fail to locate
    the root config and abort with a 'file not found' error.
    """
    offenders: list[str] = []
    for hcl_file in TERRAGRUNT_ROOT.rglob("*.hcl"):
        content = hcl_file.read_text()
        for line_num, line in enumerate(content.splitlines(), start=1):
            if FIND_PARENT_OLD.search(line):
                offenders.append(f"{hcl_file.relative_to(REPO_ROOT)}:{line_num}: {line.strip()}")
    assert not offenders, (
        'Found find_in_parent_folders("terragrunt.hcl") in the following HCL files. '
        'All references must be updated to find_in_parent_folders("root.hcl") '
        "after the rename (AC-CODE-001):\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# AC-CODE-002: acm-validate-collector sources dns_owner_zone_id from
# common/accounts.json (the common.hcl guard reproduced inline)
#
# NOTE (E11-F3-S1-T4, commit 1e37f6e): the original AC-CODE-002 design sourced
# dns_owner_zone_id via the root include's common_vars locals
# (include.root.locals.common_vars.locals or local.root_vars.locals.common_vars.locals).
# Both surfacing forms require decoding root.hcl as a dependency of the leaf:
# include.root.* resolves only after the root include is merged, and the
# local.root_vars form reads root.hcl via read_terragrunt_config. Terragrunt 1.0.7
# evaluates a config read by read_terragrunt_config in THAT file's own directory
# context, so root.hcl's own find_in_parent_folders("product.hcl"|...) hierarchy reads
# resolve relative to terragrunt/ (root.hcl's directory) and abort parse with
# ParentFileNotFoundError. The fix resolves dns_owner_zone_id from common/accounts.json
# directly (anchored on get_repo_root(), D3), reproducing the common.hcl placeholder
# guard inline. This is the dev-round-4 spec source ("dns_owner_zone_id ... from
# common/accounts.json via common.hcl", Section 1 / D-4) and keeps the copy-any-level
# property intact (D1, D3, D36, D38). account_vars is still read for the toggle-driven
# source resolution (use_pinned_module_sources, E9-F3-S1-T1) -- that is unrelated to
# zone-id sourcing and is NOT the removed dead pattern.
# ---------------------------------------------------------------------------

# The placeholder-guarded zone-id read from common/accounts.json: the for-comprehension
# selects the is_dns_owner account and the lookup of dns_owner_zone_id is guarded against
# the "<REAL_Z_ID>" placeholder (mirroring the common.hcl guard, D38, AC-1).
COMMON_ACCOUNTS_JSON_READ = "${get_repo_root()}/terragrunt/common/accounts.json"
DNS_OWNER_ZONE_ID_LOCAL = "dns_owner_zone_id"
DNS_OWNER_ZONE_ID_PLACEHOLDER_GUARD = "<REAL_Z_ID>"

# The genuinely-removed dead pattern: reading the zone id off account.hcl
# (account_vars.locals.dns_owner_zone_id). The 444444444444 account.hcl carries only
# aws_account_id + use_pinned_module_sources, so this dotted access aborts parse.
DEAD_ACCOUNT_VARS_ZONE_ID = re.compile(r"account_vars\.locals\.dns_owner_zone_id")
DEAD_ACCOUNT_VARS_ZONE_DOMAIN = re.compile(r"account_vars\.locals\.route53_zone_domain")


@pytest.mark.unit
def test_acm_validate_collector_zone_id_from_common_accounts_json() -> None:
    """acm-validate-collector must source dns_owner_zone_id from common/accounts.json (AC-CODE-002).

    account.hcl in account 444444444444 only exposes aws_account_id (and the source
    toggle); it does not carry dns_owner_zone_id or route53_zone_domain. The zone id is
    resolved from common/accounts.json directly (anchored on get_repo_root(), D3),
    reproducing the common.hcl placeholder guard inline. The root-include surfacing forms
    (include.root.locals.common_vars.locals / local.root_vars...) cannot be used because
    decoding root.hcl as a leaf dependency aborts terragrunt 1.0.7 parse with
    ParentFileNotFoundError (commit 1e37f6e). dev-round-4 Section 1 / D-4: zone id comes
    from common/accounts.json via the common.hcl guard.
    """
    assert ACM_VALIDATE_COLLECTOR_HCL.exists(), (
        f"acm-validate-collector/000/terragrunt.hcl not found at {ACM_VALIDATE_COLLECTOR_HCL}. "
        "This file must exist for AC-CODE-002 to be evaluated."
    )
    content = ACM_VALIDATE_COLLECTOR_HCL.read_text()
    assert COMMON_ACCOUNTS_JSON_READ in content, (
        "acm-validate-collector/000/terragrunt.hcl does not read common/accounts.json "
        f"via '{COMMON_ACCOUNTS_JSON_READ}'. "
        "dns_owner_zone_id must be resolved from common/accounts.json (anchored on "
        "get_repo_root(), D3), not from the root include (decoding root.hcl as a leaf "
        "dependency aborts terragrunt parse). dev-round-4 D-4, AC-CODE-002."
    )
    assert DNS_OWNER_ZONE_ID_LOCAL in content, (
        "acm-validate-collector/000/terragrunt.hcl does not resolve a dns_owner_zone_id "
        "value. The DNS-owner zone id must be selected from the is_dns_owner account row "
        "in common/accounts.json (D38, AC-1, AC-CODE-002)."
    )
    assert DNS_OWNER_ZONE_ID_PLACEHOLDER_GUARD in content, (
        "acm-validate-collector/000/terragrunt.hcl does not guard against the "
        f"'{DNS_OWNER_ZONE_ID_PLACEHOLDER_GUARD}' placeholder. The zone-id read must fail "
        "fast (no fallback) when the placeholder is still present, mirroring the common.hcl "
        "guard (D38, S7, AC-CODE-002)."
    )


@pytest.mark.unit
def test_acm_validate_collector_does_not_read_zone_from_account_vars() -> None:
    """acm-validate-collector must not read dns_owner_zone_id off account.hcl (AC-CODE-002).

    account.hcl in account 444444444444 lacks dns_owner_zone_id and route53_zone_domain.
    Accessing account_vars.locals.dns_owner_zone_id (or .route53_zone_domain) is the dead
    pattern that aborts terragrunt parse and must not survive. Reading account_vars itself
    for the toggle-driven source (use_pinned_module_sources) is permitted and unrelated.
    """
    assert ACM_VALIDATE_COLLECTOR_HCL.exists(), (
        f"acm-validate-collector/000/terragrunt.hcl not found at {ACM_VALIDATE_COLLECTOR_HCL}."
    )
    content = ACM_VALIDATE_COLLECTOR_HCL.read_text()
    assert not DEAD_ACCOUNT_VARS_ZONE_ID.search(content), (
        "acm-validate-collector/000/terragrunt.hcl still reads "
        "account_vars.locals.dns_owner_zone_id. "
        "The 444444444444 account.hcl lacks dns_owner_zone_id; this dotted access aborts "
        "parse. Source the zone id from common/accounts.json instead (AC-CODE-002)."
    )
    assert not DEAD_ACCOUNT_VARS_ZONE_DOMAIN.search(content), (
        "acm-validate-collector/000/terragrunt.hcl still reads "
        "account_vars.locals.route53_zone_domain. "
        "The 444444444444 account.hcl lacks route53_zone_domain; this dotted access aborts "
        "parse. Source the apex from common/domains.json instead (AC-CODE-002)."
    )


# ---------------------------------------------------------------------------
# AC-CODE-003: dns-delegation locals block must not contain include.* references
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dns_delegation_locals_block_has_no_include_references() -> None:
    """dns-delegation locals block must not reference include.* (AC-CODE-003).

    Terragrunt 1.x does not permit include.<name>.* references inside a locals {}
    block. The evaluation order for locals runs before include merging, so
    include.root.locals.* is not resolvable inside locals {}.

    Permitted contexts for include.* are: inputs {}, remote_state {}, generate {}.
    The fix is to extract the required values via a local read_terragrunt_config call
    pointing to the relevant HCL file, or to move the reference into inputs {}.
    """
    assert DNS_DELEGATION_HCL.exists(), (
        f"dns-delegation/000/terragrunt.hcl not found at {DNS_DELEGATION_HCL}. "
        "This file must exist for AC-CODE-003 to be evaluated."
    )
    content = DNS_DELEGATION_HCL.read_text()
    # Extract the first locals {} block and check only non-comment lines for include.
    locals_match = _LOCALS_BLOCK_PATTERN.search(content)
    assert locals_match is not None, (
        "dns-delegation/000/terragrunt.hcl has no locals {} block -- cannot validate."
    )
    locals_body = locals_match.group(1)
    non_comment_include_refs = [
        line.strip()
        for line in locals_body.splitlines()
        if not line.strip().startswith("#") and re.search(r"\binclude\.", line)
    ]
    assert not non_comment_include_refs, (
        "dns-delegation/000/terragrunt.hcl contains include.* reference(s) on "
        "non-comment lines inside the locals {} block. "
        "Terragrunt 1.x does not support include.* in locals blocks "
        "because locals evaluate before include merging. "
        "Extract the value via read_terragrunt_config() or move the reference to "
        "inputs {} / remote_state {} / generate {} (AC-CODE-003).\n"
        "Offending lines:\n" + "\n".join(non_comment_include_refs)
    )


@pytest.mark.unit
def test_dns_delegation_uses_read_terragrunt_config_for_common_vars() -> None:
    """dns-delegation must use read_terragrunt_config to load common_vars in locals (AC-CODE-003).

    After removing include.root.locals.* from the locals block, dns-delegation must
    load the common vars via a read_terragrunt_config call so the dns_owner_zone_id
    and dns_pretty_apex values are available as local.*.
    """
    assert DNS_DELEGATION_HCL.exists(), (
        f"dns-delegation/000/terragrunt.hcl not found at {DNS_DELEGATION_HCL}."
    )
    content = DNS_DELEGATION_HCL.read_text()
    assert "read_terragrunt_config" in content, (
        "dns-delegation/000/terragrunt.hcl must use read_terragrunt_config() inside the "
        "locals block to load common vars (dns_owner_zone_id, dns_pretty_apex). "
        "This replaces the forbidden include.root.locals.* reference that is not valid "
        "in Terragrunt 1.x locals blocks (AC-CODE-003)."
    )
