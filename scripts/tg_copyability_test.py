"""tg_copyability_test -- proves the copy-at-any-level property for the Terragrunt live tree.

Run via: uv run python -m scripts.tg_copyability_test --level all

For each live-tree hierarchy level (region, env-class, env-index, service-instance; the account
is abstracted out of the path per D2, so there is no account-level copy)
copies a representative scope to a temporary basename, seeds the minimal common/
rows that scope needs, runs `terragrunt run --all -- validate` over the copied
scope, and asserts:
  1. The run exits 0 (the copy is immediately valid with zero edits).
  2. The copied .hcl files are byte-for-byte identical to their sources.
  3. The derived namespace, S3 bucket name, and state CMK alias differ from
     the source scope (state isolation).

Also exercises one generalized scope (G6) -- a synthetic region or account name
not present in the live tree -- to show the property holds for arbitrary future
scope names, not only the worked examples.

The validate runs fully offline: the runner exports TG_OFFLINE_BACKEND=true, which
switches root.hcl's generated backend from S3 to a local backend. That keeps the
structural proof free of AWS access -- there is no real S3 state to read, and
dependency-output resolution falls back to each dependency block's mock_outputs
(which already allow validate). No live AWS apply, plan, or state access occurs;
mocks only (spec section 10, AC-23).

Environment variables (all optional):
  TG_LIVE_ROOT  -- path to the Terragrunt live tree root (default: terragrunt/live)
  TG_COMMON_DIR -- path to the common/ directory (default: terragrunt/common)
  TG_BIN        -- terragrunt binary path (default: terragrunt)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, cast

# ---------------------------------------------------------------------------
# Level constants
# ---------------------------------------------------------------------------

LEVEL_REGION: str = "region"
LEVEL_ACCOUNT: str = "account"
LEVEL_ENV_CLASS: str = "env-class"
LEVEL_ENV_INDEX: str = "env-index"
LEVEL_SERVICE_INSTANCE: str = "service-instance"

# LEVEL_ACCOUNT is intentionally NOT a live-tree copy level: the env-keyed tree abstracts the
# AWS account OUT of the folder path (D2), so there is no account directory to copy. Cross-account
# portability is covered by env-class copies whose env maps to a different account in
# env_accounts.json. The LEVEL_ACCOUNT constant + its state-isolation handling are retained for
# the generic _assert_state_isolation check (unit-tested) but excluded from the live run.
ALL_LEVELS: list[str] = [
    LEVEL_REGION,
    LEVEL_ENV_CLASS,
    LEVEL_ENV_INDEX,
    LEVEL_SERVICE_INSTANCE,
]

# ---------------------------------------------------------------------------
# Constants (no inline magic values; all expressed as named constants)
# ---------------------------------------------------------------------------

# Prefix for all synthetic test-scope names (never collides with real names)
_CPYTST_PREFIX: str = "cpytst-"

# S3 bucket name limit
_MAX_BUCKET_NAME_LENGTH: int = 63

# B21 shortening parameters
_NAMESPACE_PREFIX_LENGTH: int = 28
_HASH_SUFFIX_LENGTH: int = 8

# Synthetic zone id -- satisfies the common.hcl dns_owner_zone_id guard without
# being the real production zone id
_SYNTHETIC_ZONE_ID: str = "ZFAKEZONEID00000001"

# The dns-owner account id in the current live tree
_DNS_OWNER_ACCOUNT_ID: str = "444444444444"

# Placeholder value that triggers the dns-owner guard failure
_ZONE_ID_PLACEHOLDER: str = "<REAL_Z_ID>"

# Representative source paths (relative segment counts from live root)
# Level -> path components from live root
_LIVE_ROOT_SEGMENT: str = "telemetry"
_REGION_SEGMENT: str = "us-east-1"
_ENV_CLASS_SEGMENT: str = "prod"
_ENV_INDEX_SEGMENT: str = "000"
# A regular (non-singleton) service unit living at <env_class>/<env_index>/<service>/<svc_instance>.
# data-lake became a _singletons/shared unit in the env-keyed refactor, so it no longer sits at this
# position; collector-ingestion is a regular service-instance unit (and the VPC-creating unit).
_SERVICE_SEGMENT: str = "collector-ingestion"
_SVC_INSTANCE_SEGMENT: str = "000"

# Foundation (bootstrap) tier segments. Phase 8 (Approach A) relocated the dns-prod-zone
# unit OUT of the disposable <env>/_singletons/shared service tree into the stable
# bootstrap/<role>/ foundation tier (a sibling of the env subtree). Every env-subtree
# consumer derives its bootstrap role from environment.hcl as "<env-class>_role", so an
# env-class copy to a synthetic env-class needs a matching bootstrap/<dest>_role/ foundation
# dir for the dns_prod_zone dependency config_paths to resolve. See _dependency_closure_copies.
_BOOTSTRAP_SEGMENT: str = "bootstrap"
_BOOTSTRAP_ROLE_SUFFIX: str = "_role"

# Synthetic destination names per level.
#
# Every level except the region uses the _CPYTST_PREFIX synthetic marker so the
# copied scope never collides with a real account/env/instance name. The REGION
# level is the exception: region.hcl derives aws_region = basename(get_terragrunt_dir())
# and the root backend sets region = local.region, so the region directory basename
# becomes the literal AWS region the S3 backend is configured for. Terraform's S3
# backend validates that region against the real AWS region list at init time, so a
# cpytst-prefixed region name (e.g. "cpytst-eu-west-1") is rejected with
# "Invalid AWS Region" before validate runs. The region copy therefore uses a real
# AWS region that is not present in the live tree (which only uses us-east-1). A
# different real region still isolates state: region_clean changes, so the derived
# namespace and S3 state bucket name differ from the source scope.
_REGION_DEST_NAME: str = "eu-west-1"

_DEST_NAMES: dict[str, str] = {
    LEVEL_REGION: _REGION_DEST_NAME,
    LEVEL_ACCOUNT: f"{_CPYTST_PREFIX}111111111111",
    LEVEL_ENV_CLASS: f"{_CPYTST_PREFIX}envclass",
    LEVEL_ENV_INDEX: f"{_CPYTST_PREFIX}001",
    LEVEL_SERVICE_INSTANCE: f"{_CPYTST_PREFIX}001",
}

# Generalized scope (G6): a real AWS region not present in the live tree, proving the
# copy property holds for an arbitrary future region. Like the region-level
# destination above, this must be a valid AWS region (not a cpytst-prefixed synthetic
# name) because it becomes the backend region the S3 backend validates at init time.
_GENERALIZED_REGION_NAME: str = "ap-southeast-2"

# ---------------------------------------------------------------------------
# Operator-supplied terragrunt environment inputs (spec D8, AC-13).
#
# Several prod leaves source runtime config through get_env(). In production the CI
# apply workflow exports these before invoking terragrunt. The copyability test is the
# operator for its synthetic run, so it provides valid test values here. These are
# TEST INPUTS the runner supplies to terragrunt (passed via the child env), NOT in-code
# production fallbacks.
#
#   OBSERVABILITY_BUDGET_AMOUNT -- tonumber()-d in observability/service.hcl; read via a
#       NO-default get_env() (D8 missing-value fail-fast: a missing value aborts at parse
#       time). The observability reference validates budget_amount > 0.
#   OBSERVABILITY_BUDGET_EMAIL  -- split(",", ...)-d into the budget subscriber address
#       list in observability/service.hcl; also a NO-default get_env() (D8).
#   PORTAL_EMBED_LAMBDA_HASH    -- the base64 SHA256 of the embed-URL Lambda zip. Since
#       #183 the portal/service.hcl reads this via get_env("PORTAL_EMBED_LAMBDA_HASH", "")
#       -- with an explicit "" default so a `terragrunt run --all` discovery parses
#       non-portal applies without aborting at parse time. Its missing-artifact fail-fast
#       moved to the references/portal length(source_code_hash) > 0 validation; the
#       copyability run supplies a non-empty value so that validation passes.
# ---------------------------------------------------------------------------
_TG_ENV_INPUTS: dict[str, str] = {
    "OBSERVABILITY_BUDGET_AMOUNT": "100",
    "OBSERVABILITY_BUDGET_EMAIL": "cpytst-alerts@example.com",
    # 44-char base64 of a 32-byte SHA256 digest (non-empty; passes length > 0).
    "PORTAL_EMBED_LAMBDA_HASH": "Y3B5dHN0LWVtYmVkLWxhbWJkYS1zb3VyY2UtaGFzaHg=",
}

# ---------------------------------------------------------------------------
# Offline-backend switch (spec section 10, AC-23: mocks only, no live AWS).
#
# The copyability proof is structural: it asserts the copied scope validates with
# zero edits. It must NOT contact AWS. Left on the default S3 backend, `terragrunt
# run --all -- validate` fails offline two ways:
#   (a) terraform auto-runs `init` with the S3 backend; the synthetic copied scope's
#       state bucket does not exist (NoSuchBucket), and
#   (b) dependency-output resolution runs `terraform output` against an uninitialized
#       S3 backend ("Backend initialization required"), so the dependency block's
#       mock_outputs never engage.
# root.hcl gates its remote_state backend on TG_OFFLINE_BACKEND: when truthy it emits
# a `local` backend instead of S3. A local backend lets validate (and dependency
# `terraform output`, which then returns no outputs so mock_outputs engage) run with
# no AWS access. The variable is unset in production, so this never affects an apply.
# ---------------------------------------------------------------------------
_TG_OFFLINE_BACKEND_ENV: str = "TG_OFFLINE_BACKEND"

# common/networks.json filename and CIDR field (spec S4.1/S3.6). VPC-creating units
# key this map by their fully derived namespace.
_NETWORKS_JSON_NAME: str = "networks.json"
_VPC_CIDR_FIELD: str = "vpc_cidr_block"

# Synthetic CIDR seeded for a copied VPC-creating unit's namespace. The address space
# is distinct from the live sandbox (10.1.0.0/16) and prod (10.0.0.0/16) rows so a
# copied scope never overlaps a real deployment's allocation.
_SYNTHETIC_VPC_CIDR: str = "10.255.0.0/16"

# Service directory name of the only VPC-creating (networks.json-keyed) unit. Used to
# locate copied collector-ingestion instances whose namespace needs a CIDR row.
_VPC_SERVICE_SEGMENT: str = "collector-ingestion"

# common/cloudfront.json filename. It maps an AWS region to that region's AWS-managed
# CloudFront origin-facing managed prefix list id (collector-ingestion v1.0.4 REQUIRED
# input, read by _envcommon/collector-ingestion.hcl keyed by region). A region-level (or
# the G6 generalized-region) copy targets a region with no row, so the fail-fast lookup in
# _envcommon aborts validate unless a row is seeded -- exactly like the networks.json
# namespace seeding. Env-class / env-index / service-instance copies keep the source region
# (us-east-1), which already has a row, so they seed nothing.
_CLOUDFRONT_JSON_NAME: str = "cloudfront.json"

# Synthetic CloudFront origin-facing managed prefix list id seeded for a copied region.
# Matches the module's required ^pl-[0-9a-f]+$ shape and is clearly synthetic so it never
# collides with a real region's AWS-managed prefix list id.
_SYNTHETIC_CLOUDFRONT_PREFIX_LIST_ID: str = "pl-0a0a0a0a"

# Minimal but structurally valid common/oidc-roles.json row seeded for a copied
# (synthetic) account id. The oidc-bootstrap unit fails fast on an account id absent
# from oidc-roles.json (spec section 4.9, E8-F6-S2-T1); a single read-only plan role
# satisfies that lookup with the same field shape as the real rows. cpytst-prefixed so
# it never collides with a real role name.
_CPYTST_OIDC_ROLES_VALUE: dict[str, Any] = {
    "roles": {
        "cpytst-gha-tg-plan": {
            "sub": "repo:matthew-dresden/telemetry-platform:*",
            "managed_policy_arns": [],
            "inline_policies": {},
            "description": "Copyability test synthetic read-only plan role.",
        }
    }
}

# Instance-relative dependency config_path forms used across the live tree. A single
# copied service-instance leaf wires its dependencies to sibling instances at the SAME
# (new) instance index via the ${local.svc_instance} interpolation, which appears in two
# directory shapes after the env-keyed _singletons refactor:
#   same-tier serving sibling : "../../<service>/${local.svc_instance}"
#   cross-tier shared singleton: "../../../_singletons/shared/<service>/${local.svc_instance}"
# Both end in "/${local.svc_instance}" and are purely "../"-relative to the referencing
# unit. The capture group is the RELATIVE PATH PREFIX up to (not including) the trailing
# "/${local.svc_instance}" segment, so the dependency directory can be resolved relative
# to the referencing unit's own directory for ANY tier depth (DRY: one resolver handles
# the same-tier and cross-tier shapes). The prefix is constrained to literal "../"-relative
# path segments with NO "$" (no interpolation): only ${local.svc_instance}-indexed
# dependencies whose PREFIX is a fixed literal path are part of a single-leaf copy closure.
# Active-pointer config_paths whose prefix interpolates a set selector
# (e.g. "../../../../${local.env_active}/portal/${local.svc_instance}") target a
# separately-resolved active set, not the copied instance closure, so excluding "$" from
# the captured prefix leaves them unmatched (spec B24, G5).
_INSTANCE_CONFIG_PATH_PATTERN: str = (
    r'config_path\s*=\s*"((?:\.\./)+[^"$]+?)/\$\{local\.svc_instance\}"'
)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class CopyabilityError(RuntimeError):
    """Raised when any copyability precondition or assertion fails.

    Every instance must carry an ERROR:-prefixed message naming the level, the
    relevant paths or values, and the operator's next step.
    """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyConfig:
    """Configuration for a single copy operation.

    Attributes:
        source: Absolute path to the scope being copied.
        destination: Absolute path for the copy destination.
        level: One of ALL_LEVELS -- the hierarchy depth being exercised.
    """

    source: pathlib.Path
    destination: pathlib.Path
    level: str


@dataclass(frozen=True)
class RunnerConfig:
    """Runtime configuration injected into CopyabilityRunner.

    Attributes:
        live_root: Root of the Terragrunt live tree.
        common_dir: Path to the common/ directory.
        tg_bin: Terragrunt binary name or path.
    """

    live_root: pathlib.Path
    common_dir: pathlib.Path
    tg_bin: str


@dataclass(frozen=True)
class _SeedSpec:
    """Specification for a single common/ JSON row that must be seeded.

    Attributes:
        json_file: Absolute path to the JSON file.
        key: The key to add.
        value: The value to write under that key. Either a JSON object (e.g. a
            networks.json CIDR row) or a scalar string (e.g. a cloudfront.json
            region -> managed-prefix-list-id mapping).
        parent_key: When set, key/value is seeded under data[parent_key] (a nested object)
            instead of at the top level -- e.g. env_accounts.json keys env-classes under "envs".
    """

    json_file: pathlib.Path
    key: str
    value: dict[str, Any] | str
    parent_key: str | None = None


# ---------------------------------------------------------------------------
# Namespace / bucket / alias derivation
# ---------------------------------------------------------------------------


def derive_namespace(scope_path: pathlib.Path) -> str:
    """Derive the Terragrunt namespace string from a scope directory path.

    The namespace mirrors the root terragrunt.hcl formula:
      join('-', [product, region_clean, env_class, env_index, service, svc_instance])
    where region_clean = replace(region, '-', '').

    The path must contain a 'live' segment followed by at least 6 components (the account is
    abstracted OUT of the env-keyed path, D2 -- there is no account segment):
      live/<product>/<region>/<env_class>/<env_index>/<service>/<svc_instance>

    Args:
        scope_path: Absolute path to the scope directory (service-instance leaf or
            any ancestor down to 'live').

    Returns:
        The namespace string (e.g. "telemetry-useast1-prod-000-data-lake-000").

    Raises:
        CopyabilityError: If the path does not contain a 'live' segment or does not
            have the minimum required components after 'live/'.
    """
    parts = list(scope_path.parts)
    try:
        live_idx = parts.index("live")
    except ValueError as exc:
        raise CopyabilityError(
            f"ERROR: cannot derive namespace from '{scope_path}' -- "
            "the path does not contain a 'live' segment. "
            "Ensure the scope path is under the Terragrunt live tree."
        ) from exc

    # product, region, env_class, env_index, service, svc_instance (account abstracted out, D2)
    below_live = parts[live_idx + 1 :]
    if len(below_live) < 6:
        raise CopyabilityError(
            f"ERROR: cannot derive namespace from '{scope_path}' -- "
            f"expected at least 6 path components below 'live/', got {len(below_live)}: "
            f"{below_live}. "
            "The scope path must reach the service-instance level "
            "(live/<product>/<region>/<env_class>/<env_index>/<service>/<svc_instance>)."
        )

    product = below_live[0]
    region = below_live[1]
    env_class = below_live[2]
    env_index = below_live[3]
    service = below_live[4]
    svc_instance = below_live[5]

    # Mirror root terragrunt.hcl: join the six fields with "-" after replacing each field's
    # internal hyphens with "_" (region_clean already has none; service collector-ingestion
    # -> collector_ingestion is the case that matters for the networks.json namespace key).
    region_clean = region.replace("-", "")
    fields = [product, region_clean, env_class, env_index, service, svc_instance]
    return "-".join(field.replace("-", "_") for field in fields)


def _account_segment_from_leaf(leaf_path: pathlib.Path) -> str:
    """Return the account-id path segment from a service-instance leaf path.

    The account is the third component below 'live'
    (live/<product>/<region>/<account>/...), extracted positionally rather than by
    scanning for a 12-digit segment. Positional extraction is required because a
    copied account-level scope uses a synthetic account directory name (e.g.
    'cpytst-111111111111') that is not a bare 12-digit string, and because higher-level
    scope roots (region) do not contain the account in their own path -- only the leaf
    does. This mirrors derive_namespace's positional reads (DRY of the layout contract).

    Args:
        leaf_path: A service-instance leaf directory (full depth below 'live').

    Returns:
        The account directory name (real id or synthetic copy name).

    Raises:
        CopyabilityError: If the path does not reach the account segment below 'live'.
    """
    parts = list(leaf_path.parts)
    try:
        live_idx = parts.index("live")
    except ValueError as exc:
        raise CopyabilityError(
            f"ERROR: cannot derive account id from leaf path '{leaf_path}' -- "
            "the path does not contain a 'live' segment."
        ) from exc

    below_live = parts[live_idx + 1 :]
    if len(below_live) < 3:
        raise CopyabilityError(
            f"ERROR: cannot derive account id from leaf path '{leaf_path}' -- "
            f"expected at least 3 path components below 'live/', got {len(below_live)}. "
            "The scope path must reach the account level "
            "(live/<product>/<region>/<account>/...)."
        )
    return below_live[2]


def _region_segment_from_leaf(leaf_path: pathlib.Path) -> str:
    """Return the region path segment from a service-instance leaf path.

    The region is the second component below 'live'
    (live/<product>/<region>/<env_class>/...), extracted positionally -- the same
    contract derive_namespace reads (DRY of the layout). cloudfront.json is keyed by this
    region string (the same value _envcommon resolves as local.region_vars.locals.aws_region
    = basename(region.hcl dir)), so a copied scope whose region differs from the source needs
    a seeded cloudfront.json row at this region key.

    Args:
        leaf_path: A service-instance leaf directory (full depth below 'live').

    Returns:
        The region directory name (e.g. "us-east-1", "eu-west-1").

    Raises:
        CopyabilityError: If the path does not reach the region segment below 'live'.
    """
    parts = list(leaf_path.parts)
    try:
        live_idx = parts.index("live")
    except ValueError as exc:
        raise CopyabilityError(
            f"ERROR: cannot derive region from leaf path '{leaf_path}' -- "
            "the path does not contain a 'live' segment."
        ) from exc

    below_live = parts[live_idx + 1 :]
    if len(below_live) < 2:
        raise CopyabilityError(
            f"ERROR: cannot derive region from leaf path '{leaf_path}' -- "
            f"expected at least 2 path components below 'live/', got {len(below_live)}. "
            "The scope path must reach the region level (live/<product>/<region>/...)."
        )
    return below_live[1]


def _leaf_scope_under(scope_path: pathlib.Path) -> pathlib.Path:
    """Find a representative service-instance leaf under a scope directory.

    Returns the first leaf whose path has a 'live' segment and 7+ components
    below 'live'. Used to derive namespace/bucket/alias from any level scope.

    Raises:
        CopyabilityError: If no leaf is found.
    """
    # If the scope itself is at service-instance depth, return it directly
    try:
        derive_namespace(scope_path)
        return scope_path
    except CopyabilityError:
        pass

    # Otherwise search for a leaf terragrunt.hcl
    for hcl in sorted(scope_path.rglob("terragrunt.hcl")):
        leaf = hcl.parent
        try:
            derive_namespace(leaf)
            return leaf
        except CopyabilityError:
            continue

    raise CopyabilityError(
        f"ERROR: no service-instance leaf found under '{scope_path}'. "
        "Cannot derive namespace/bucket/alias for this scope. "
        "Check that the scope contains at least one terragrunt.hcl at the full depth."
    )


def derive_bucket_name(account_id: str, namespace: str) -> str:
    """Derive the final S3 state bucket name using the B21 hash-suffix shortening scheme.

    Mirrors the root terragrunt.hcl final_bucket_name derivation:
      raw = '<account_id>-<namespace>-tfstate'
      if len(raw) > 63:
        shortened = '<account_id>-<namespace[:28]>-<md5(namespace)[:8]>-tfstate'
      final = lower(replace(shortened, '_', '-'))

    Args:
        account_id: 12-digit AWS account id.
        namespace: Fully composed namespace string.

    Returns:
        The final S3 bucket name (lowercased, at most 63 chars).
    """
    ns_clean = namespace.replace("_", "-")
    raw = f"{account_id}-{ns_clean}-tfstate"

    if len(raw) > _MAX_BUCKET_NAME_LENGTH:
        ns_prefix = ns_clean[:_NAMESPACE_PREFIX_LENGTH]
        ns_hash = hashlib.md5(namespace.encode(), usedforsecurity=False).hexdigest()[
            :_HASH_SUFFIX_LENGTH
        ]
        raw = f"{account_id}-{ns_prefix}-{ns_hash}-tfstate"

    return raw.lower().replace("_", "-")


def derive_cmk_alias(account_id: str) -> str:
    """Derive the state CMK alias from the account id.

    Mirrors root terragrunt.hcl:  alias/<account_id>-tfstate

    Args:
        account_id: 12-digit AWS account id.

    Returns:
        The KMS alias string (e.g. "alias/123456789012-tfstate").
    """
    return f"alias/{account_id}-tfstate"


# ---------------------------------------------------------------------------
# State isolation assertion
# ---------------------------------------------------------------------------


def _assert_state_isolation(
    src_scope: pathlib.Path,
    dst_scope: pathlib.Path,
    level: str = LEVEL_SERVICE_INSTANCE,
) -> None:
    """Assert that the copied scope isolates state from the source scope.

    For non-account levels, the derived namespace must differ.
    For all levels, the derived S3 bucket name must differ.
    For account-level copies, the CMK alias must also differ (different account id).

    Args:
        src_scope: Absolute path to the source scope directory.
        dst_scope: Absolute path to the destination scope directory.
        level: The hierarchy level being tested.

    Raises:
        CopyabilityError: If any isolation property is violated.
    """
    src_leaf = _leaf_scope_under(src_scope)
    dst_leaf = _leaf_scope_under(dst_scope)

    src_ns = derive_namespace(src_leaf)
    dst_ns = derive_namespace(dst_leaf)

    src_acct = _account_segment_from_leaf(src_leaf)
    dst_acct = _account_segment_from_leaf(dst_leaf)

    if level != LEVEL_ACCOUNT and src_ns == dst_ns:
        raise CopyabilityError(
            f"ERROR: namespace collision at level '{level}': "
            f"source namespace '{src_ns}' equals destination namespace '{dst_ns}'. "
            "The copied scope must produce a distinct namespace for state isolation. "
            "Verify that the destination path uses a different basename at the copied level."
        )

    src_bucket = derive_bucket_name(src_acct, src_ns)
    dst_bucket = derive_bucket_name(dst_acct, dst_ns)

    if src_bucket == dst_bucket:
        raise CopyabilityError(
            f"ERROR: S3 bucket name collision at level '{level}': "
            f"source bucket '{src_bucket}' equals destination bucket '{dst_bucket}'. "
            "State isolation violated -- each scope must write to its own state bucket."
        )

    if level == LEVEL_ACCOUNT:
        src_alias = derive_cmk_alias(src_acct)
        dst_alias = derive_cmk_alias(dst_acct)
        if src_alias == dst_alias:
            raise CopyabilityError(
                f"ERROR: CMK alias collision at level '{level}': "
                f"source alias '{src_alias}' equals destination alias '{dst_alias}'. "
                "Account-level copy must produce a distinct CMK alias."
            )


# ---------------------------------------------------------------------------
# Common/ JSON row seeding
# ---------------------------------------------------------------------------


def _seed_common_row(
    json_file: pathlib.Path,
    key: str,
    value: dict[str, Any] | str,
    parent_key: str | None = None,
) -> None:
    """Add a new key to a common/ JSON file (top-level, or nested under parent_key).

    Args:
        json_file: Absolute path to the JSON file.
        key: The key to add.
        value: The value to write (a JSON object or a scalar string).

    Raises:
        CopyabilityError: If the file does not exist, cannot be parsed, or the
            key already exists.
    """
    if not json_file.exists():
        raise CopyabilityError(
            f"ERROR: cannot seed common/ row -- file '{json_file}' does not exist. "
            "Verify the common_dir configuration points to the correct directory."
        )

    try:
        content = json_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CopyabilityError(
            f"ERROR: cannot seed common/ row -- '{json_file}' contains malformed JSON: {exc}. "
            "Repair the JSON file before running the copyability test."
        ) from exc

    container = data
    if parent_key is not None:
        if not isinstance(data.get(parent_key), dict):
            raise CopyabilityError(
                f"ERROR: cannot seed common/ row -- parent key '{parent_key}' is missing or not "
                f"an object in '{json_file}'. The nested seed target must already exist."
            )
        container = data[parent_key]

    if key in container:
        raise CopyabilityError(
            f"ERROR: cannot seed common/ row -- key '{key}' already exists in '{json_file}'. "
            "Choose a unique synthetic key (prefix with 'cpytst-') to avoid collisions."
        )

    container[key] = value
    _write_json_preserving_trailing_newline(json_file, content, data)


def _restore_common_row(json_file: pathlib.Path, key: str, parent_key: str | None = None) -> None:
    """Remove a previously seeded key from a common/ JSON file.

    Args:
        json_file: Absolute path to the JSON file.
        key: The key to remove.

    Raises:
        CopyabilityError: If the file does not exist, cannot be parsed, or the
            key is not present.
    """
    if not json_file.exists():
        raise CopyabilityError(
            f"ERROR: cannot restore common/ row -- file '{json_file}' does not exist."
        )

    try:
        content = json_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CopyabilityError(
            f"ERROR: cannot restore common/ row -- '{json_file}' contains malformed JSON: {exc}."
        ) from exc

    container = data
    if parent_key is not None and isinstance(data.get(parent_key), dict):
        container = data[parent_key]
    if key not in container:
        raise CopyabilityError(
            f"ERROR: cannot restore common/ row -- key '{key}' not found in '{json_file}'. "
            "Was the row seeded correctly before the restore attempt?"
        )

    del container[key]
    _write_json_preserving_trailing_newline(json_file, content, data)


def _write_json_preserving_trailing_newline(
    json_file: pathlib.Path,
    original_content: str,
    new_data: dict[str, Any],
) -> None:
    """Write new_data to json_file, preserving the trailing newline from original_content.

    Args:
        json_file: Path to write.
        original_content: The original raw text (used to detect trailing newline).
        new_data: The updated data dict to serialize.
    """
    new_content = json.dumps(new_data, indent=2)
    if original_content.endswith("\n"):
        new_content += "\n"
    json_file.write_text(new_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# DNS-owner zone-id guard patching
# ---------------------------------------------------------------------------


def _read_accounts(accounts_json: pathlib.Path) -> dict[str, Any]:
    """Read accounts.json and return the parsed dict."""
    if not accounts_json.exists():
        raise CopyabilityError(
            f"ERROR: accounts.json not found at '{accounts_json}'. "
            "Verify the common_dir configuration."
        )
    try:
        return cast(dict[str, Any], json.loads(accounts_json.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise CopyabilityError(
            f"ERROR: malformed accounts.json at '{accounts_json}': {exc}."
        ) from exc


def _patch_dns_owner_zone_id(accounts_json: pathlib.Path) -> str:
    """Replace the dns-owner zone-id placeholder with a synthetic value.

    This temporarily satisfies the common.hcl dns_owner_zone_id guard so
    terragrunt can parse the root config during the copyability test.

    Returns the original zone-id value (to restore after the test).

    Raises:
        CopyabilityError: If the dns-owner account is not found or the zone-id
            field is absent.
    """
    content = accounts_json.read_text(encoding="utf-8")
    data = json.loads(content)

    if _DNS_OWNER_ACCOUNT_ID not in data:
        raise CopyabilityError(
            f"ERROR: dns-owner account '{_DNS_OWNER_ACCOUNT_ID}' not found in "
            f"'{accounts_json}'. Cannot patch the zone-id guard."
        )

    original = data[_DNS_OWNER_ACCOUNT_ID].get("dns_owner_zone_id", "")
    data[_DNS_OWNER_ACCOUNT_ID]["dns_owner_zone_id"] = _SYNTHETIC_ZONE_ID
    _write_json_preserving_trailing_newline(accounts_json, content, data)
    return str(original)


def _restore_dns_owner_zone_id(accounts_json: pathlib.Path, original: str) -> None:
    """Restore the original dns-owner zone-id value."""
    content = accounts_json.read_text(encoding="utf-8")
    data = json.loads(content)
    if _DNS_OWNER_ACCOUNT_ID in data:
        data[_DNS_OWNER_ACCOUNT_ID]["dns_owner_zone_id"] = original
        _write_json_preserving_trailing_newline(accounts_json, content, data)


# ---------------------------------------------------------------------------
# Byte-for-byte identity assertion
# ---------------------------------------------------------------------------


def _assert_byte_identical(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Assert that all .hcl files in src are byte-for-byte identical in dst.

    Args:
        src: Source scope directory.
        dst: Destination scope directory.

    Raises:
        CopyabilityError: If any file differs or is missing.
    """
    src_files = sorted(src.rglob("*.hcl"))
    if not src_files:
        raise CopyabilityError(
            f"ERROR: no .hcl files found under source scope '{src}'. "
            "Cannot assert zero-edit property for an empty scope."
        )

    for src_file in src_files:
        rel = src_file.relative_to(src)
        dst_file = dst / rel
        if not dst_file.exists():
            raise CopyabilityError(
                f"ERROR: zero-edit assertion failed -- copied scope is missing '{rel}' "
                f"(expected at '{dst_file}'). "
                "The copy did not reproduce the full scope."
            )
        src_bytes = src_file.read_bytes()
        dst_bytes = dst_file.read_bytes()
        if src_bytes != dst_bytes:
            raise CopyabilityError(
                f"ERROR: zero-edit property violated -- '{rel}' in the copied scope "
                f"differs from the source (spec section 4.8). "
                f"Source: {len(src_bytes)} bytes. Destination: {len(dst_bytes)} bytes. "
                "A copyability regression means a file was modified during or after the copy. "
                "Restore the copied scope and investigate which rule caused the edit."
            )


# ---------------------------------------------------------------------------
# Run validate/plan via terragrunt
# ---------------------------------------------------------------------------


def _run_tg_command(
    tg_bin: str,
    scope_path: pathlib.Path,
    tf_command: str,
) -> subprocess.CompletedProcess[str]:
    """Run `terragrunt run --all -- <tf_command>` in scope_path.

    Args:
        tg_bin: Terragrunt binary path.
        scope_path: Directory to run the command from (cwd).
        tf_command: Terraform command to pass (e.g. "validate").

    Returns:
        The CompletedProcess result.

    Raises:
        CopyabilityError: If the binary cannot be found.
    """
    # --parallelism 1 serialises the per-unit `terraform init` calls. The shared plugin
    # cache (TF_PLUGIN_CACHE_DIR below) is NOT safe for concurrent writers: terragrunt's
    # default parallel `run --all` lets multiple inits populate/read the same cache dir at
    # once, which races into "failed to instantiate provider" / "Failed to load plugin
    # schemas" / "timeout while waiting for plugin to start". Serialising init makes the
    # shared cache single-writer. Validate is fast and offline, so the throughput cost is
    # small and the copyability proof stays deterministic.
    cmd = [tg_bin, "run", "--all", "--parallelism", "1", "--", tf_command]
    # Provide the operator-supplied terragrunt env inputs the prod leaves read via
    # get_env() (spec D8). These are test inputs for the synthetic run, layered onto
    # the inherited environment so PATH and the terragrunt toolchain still resolve.
    # TG_OFFLINE_BACKEND switches root.hcl to a local backend so the structural
    # validate runs with no AWS access (no real S3 state, no live dependency outputs --
    # the dependency mock_outputs engage instead). See _TG_OFFLINE_BACKEND_ENV.
    #
    # Shared provider plugin cache (TF_PLUGIN_CACHE_DIR): `terragrunt run --all`
    # validates every unit in the copied scope, and without a shared cache each unit's
    # `terraform init` downloads its OWN ~700MB copy of the AWS provider into its
    # .terragrunt-cache, exhausting the CI runner disk ("no space left on device") once
    # ~20 units are reached. A shared cache downloads each provider version once and
    # symlinks it into every unit's working dir, so total provider disk is O(1) in the
    # number of units. Honour an externally-set TF_PLUGIN_CACHE_DIR (CI may pre-create
    # one), else default to a stable per-invocation cache under the system temp dir. The
    # directory MUST exist before terraform runs or the cache is silently ignored.
    #
    # TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE=true: the copied modules ship no
    # .terraform.lock.hcl, so a cache-backed install otherwise fails with "the cached
    # package ... does not match any of the checksums recorded in the dependency lock
    # file" (Terraform records only the registry h1: hash, which the cache copy cannot
    # satisfy). This flag tells Terraform to trust the cache for the structural validate
    # (the proof asserts HCL validity, not provider supply-chain pinning -- which the real
    # apply path enforces via the live lockfile + checksums).
    plugin_cache_dir = os.environ.get(
        "TF_PLUGIN_CACHE_DIR",
        str(pathlib.Path(tempfile.gettempdir()) / "tg-copyability-plugin-cache"),
    )
    pathlib.Path(plugin_cache_dir).mkdir(parents=True, exist_ok=True)
    child_env = {
        **os.environ,
        **_TG_ENV_INPUTS,
        _TG_OFFLINE_BACKEND_ENV: "true",
        "TF_PLUGIN_CACHE_DIR": plugin_cache_dir,
        "TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE": "true",
    }
    try:
        return subprocess.run(
            cmd,
            cwd=str(scope_path),
            capture_output=True,
            text=True,
            env=child_env,
        )
    except FileNotFoundError as exc:
        raise CopyabilityError(
            f"ERROR: terragrunt binary '{tg_bin}' not found. "
            "Install terragrunt or set the TG_BIN environment variable. "
            f"Original error: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Seed spec derivation per level
# ---------------------------------------------------------------------------


def _seed_specs_for_level(level: str, dest_name: str, common_dir: pathlib.Path) -> list[_SeedSpec]:
    """Return the list of common/ rows that must be seeded for the given level.

    Args:
        level: The hierarchy level being exercised.
        dest_name: The synthetic destination name (used as the JSON key).
        common_dir: Path to the common/ directory.

    Returns:
        List of _SeedSpec instances describing the rows to seed.
    """
    if level == LEVEL_ACCOUNT:
        return [
            _SeedSpec(
                json_file=common_dir / "accounts.json",
                key=dest_name,
                value={
                    "account_role": "cpytst-infra",
                    "aws_profile": "cpytst",
                    # ci_deploy gates the CI-deploy-role KMS principal in several
                    # _envcommon units (e.g. dns-prod-zone.hcl). It is a required field
                    # of the accounts.json schema; false keeps the synthetic copy a
                    # non-CI test account.
                    "ci_deploy": False,
                    "deploy_role_name": "telemetry-platform-gha-tg-apply",
                    "is_dns_owner": False,
                },
            ),
            # The oidc-bootstrap unit fails fast on a derived account id absent from
            # common/oidc-roles.json (spec section 4.9, E8-F6-S2-T1). An account-level
            # copy derives the new (synthetic) account id, which has no roles row, so
            # seed a minimal but structurally valid one (single read-only plan role)
            # so oidc-bootstrap validates. The row lives in common/, not the copied
            # scope, so the zero-edit property holds.
            _SeedSpec(
                json_file=common_dir / "oidc-roles.json",
                key=dest_name,
                value=_CPYTST_OIDC_ROLES_VALUE,
            ),
        ]

    if level == LEVEL_ENV_CLASS:
        env_apex = f"{dest_name}.telemetry.example.com"
        return [
            _SeedSpec(
                json_file=common_dir / "domains.json",
                key=dest_name,
                value={
                    "dns_service_apex": env_apex,
                    "dns_pretty_apex": env_apex,
                    "enable_custom_domain": False,
                },
            ),
            _SeedSpec(
                json_file=common_dir / "contacts.json",
                key=dest_name,
                value={
                    "alert_emails": ["cpytst-alerts@example.com"],
                    "budget_emails": ["cpytst-alerts@example.com"],
                },
            ),
            # account.hcl resolves env-class -> account/profile/pinned-toggle from
            # env_accounts.json (keyed under "envs"); downstream, accounts.json (keyed by the
            # resolved account id) supplies the account role/dns details. A new synthetic env-class
            # needs an env_accounts row or account.hcl fails fast -- but its account_id MUST already
            # exist in accounts.json, so we reuse the source env-class's real account id (state
            # isolation still holds: the env-class differs, so the derived namespace + S3 bucket
            # differ from the source). use_pinned=false so the copied leaves validate against local
            # sources offline.
            _SeedSpec(
                json_file=common_dir / "env_accounts.json",
                key=dest_name,
                value={
                    "account_id": "111111111111",
                    "aws_profile": f"{_CPYTST_PREFIX}{dest_name}",
                    "use_pinned_module_sources": False,
                },
                parent_key="envs",
            ),
        ]

    # Region, env-index, and service-instance copies re-use the source
    # account (already in accounts.json) and source env-class (already in
    # domains.json / contacts.json). No seeding required.
    return []


def _networks_seed_specs(
    source: pathlib.Path,
    destination: pathlib.Path,
    common_dir: pathlib.Path,
) -> list[_SeedSpec]:
    """Return networks.json seed rows for every VPC-creating unit in the copied scope.

    The collector-ingestion unit keys common/networks.json by its fully derived
    namespace (spec S4.1/S3.6). A copied scope produces a NEW namespace for that unit,
    which has no CIDR row, so the fail-fast lookup in _envcommon/collector-ingestion.hcl
    aborts validate. This function seeds a minimal valid CIDR row for each such copied
    namespace, completing what the test must provide (it never edits the copied files).

    The collector-ingestion leaves are located in the SOURCE scope and their paths are
    translated to the DESTINATION scope so the namespace can be derived from the copied
    path before the copy has run (networks.json is keyed by namespace, not by the
    level's scope basename, so dest_name alone is insufficient).

    Namespaces that already have a row in networks.json are skipped: the account
    segment is not part of the namespace formula, so an account-level copy yields the
    SAME namespace as the source unit, and the existing CIDR row already satisfies the
    copied unit's lookup. Seeding a duplicate key would collide. Only namespaces the
    copy actually introduces (e.g. a changed region in a region-level copy) are seeded.

    Args:
        source: The source scope directory being copied.
        destination: The destination scope directory (the copy target root).
        common_dir: Path to the common/ directory containing networks.json.

    Returns:
        One _SeedSpec per collector-ingestion leaf whose copied namespace is not already
        present in networks.json. Empty when the scope contains no VPC-creating unit
        (e.g. a single non-VPC leaf such as data-lake) or when every copied namespace
        already has a row (e.g. an account-level copy, where the namespace is unchanged).
    """
    networks_json = common_dir / _NETWORKS_JSON_NAME
    existing_keys = _existing_top_level_keys(networks_json)
    specs: list[_SeedSpec] = []
    seen_keys: set[str] = set()

    for hcl in sorted(source.rglob("terragrunt.hcl")):
        src_leaf = hcl.parent
        if src_leaf.parent.name != _VPC_SERVICE_SEGMENT:
            continue
        dst_leaf = destination / src_leaf.relative_to(source)
        try:
            namespace = derive_namespace(dst_leaf)
        except CopyabilityError:
            continue
        if namespace in seen_keys or namespace in existing_keys:
            continue
        seen_keys.add(namespace)
        specs.append(
            _SeedSpec(
                json_file=networks_json,
                key=namespace,
                value={_VPC_CIDR_FIELD: _SYNTHETIC_VPC_CIDR},
            )
        )

    return specs


def _existing_top_level_keys(json_file: pathlib.Path) -> set[str]:
    """Return the set of top-level keys already present in a common/ JSON file.

    Used to skip seeding a row whose key a copied scope already has (e.g. an account-level
    copy yields an unchanged networks.json namespace; an env-class/env-index/service-instance
    copy keeps the source region so cloudfront.json already has that key). An absent file
    means no keys. Shared by the networks.json (namespace-keyed) and cloudfront.json
    (region-keyed) seeders (DRY).

    Raises:
        CopyabilityError: If the file exists but contains malformed JSON.
    """
    if not json_file.exists():
        return set()
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CopyabilityError(
            f"ERROR: cannot read existing rows -- '{json_file}' contains "
            f"malformed JSON: {exc}. Repair the file before running the copyability test."
        ) from exc
    return set(data.keys())


def _cloudfront_seed_specs(
    source: pathlib.Path,
    destination: pathlib.Path,
    common_dir: pathlib.Path,
) -> list[_SeedSpec]:
    """Return cloudfront.json seed rows for every copied region a collector-ingestion unit needs.

    The collector-ingestion unit reads common/cloudfront.json keyed by AWS region (the
    region-derived CloudFront origin-facing managed prefix list id, collector-ingestion
    v1.0.4 REQUIRED input). A copy that changes the region (the region-level copy and the G6
    generalized-region copy) targets a region with no row, so the fail-fast lookup in
    _envcommon/collector-ingestion.hcl aborts validate. This seeds a minimal valid prefix
    list id for each such copied region, completing what the test must provide (it never
    edits the copied files) -- mirroring _networks_seed_specs.

    The collector-ingestion leaves are located in the SOURCE scope and their paths are
    translated to the DESTINATION scope so the region can be derived from the copied path
    (cloudfront.json is keyed by region, not by the level's scope basename). Regions already
    present in cloudfront.json are skipped: an env-class / env-index / service-instance copy
    keeps the source region (us-east-1), whose row already satisfies the copied unit's lookup,
    and seeding a duplicate key would collide. Only regions the copy actually introduces
    (e.g. a region-level copy to eu-west-1) are seeded.

    Args:
        source: The source scope directory being copied.
        destination: The destination scope directory (the copy target root).
        common_dir: Path to the common/ directory containing cloudfront.json.

    Returns:
        One _SeedSpec per copied region not already present in cloudfront.json. Empty when
        the scope contains no collector-ingestion unit or when the copied region is unchanged.
    """
    cloudfront_json = common_dir / _CLOUDFRONT_JSON_NAME
    existing_keys = _existing_top_level_keys(cloudfront_json)
    specs: list[_SeedSpec] = []
    seen_keys: set[str] = set()

    for hcl in sorted(source.rglob("terragrunt.hcl")):
        src_leaf = hcl.parent
        if src_leaf.parent.name != _VPC_SERVICE_SEGMENT:
            continue
        dst_leaf = destination / src_leaf.relative_to(source)
        try:
            region = _region_segment_from_leaf(dst_leaf)
        except CopyabilityError:
            continue
        if region in seen_keys or region in existing_keys:
            continue
        seen_keys.add(region)
        specs.append(
            _SeedSpec(
                json_file=cloudfront_json,
                key=region,
                value=_SYNTHETIC_CLOUDFRONT_PREFIX_LIST_ID,
            )
        )

    return specs


# ---------------------------------------------------------------------------
# Single-leaf dependency-closure resolution (spec B24, G5)
# ---------------------------------------------------------------------------


def _instance_dependency_dirs(unit: pathlib.Path) -> list[pathlib.Path]:
    """Return the dependency instance directories a unit's config_paths reference.

    A unit's instance-relative dependency config_paths interpolate
    ${local.svc_instance} as the trailing instance segment and take one of two
    directory shapes after the env-keyed _singletons refactor (spec section 4.4, D6):
        same-tier serving sibling : `../../<service>/${local.svc_instance}`
        cross-tier shared singleton: `../../../_singletons/shared/<service>/${local.svc_instance}`
    When a single service-instance leaf is copied to a new instance index, every such
    config_path resolves to a dependency instance at the NEW index, which must exist for
    terragrunt to mock the dependency (spec B24: every config_path must resolve to a real
    directory). This parses the unit's terragrunt.hcl, resolves each instance-relative
    config_path prefix against the unit's own directory, and returns the dependency
    instance directories at the unit's CURRENT (source) instance index. Resolving the
    literal relative path (rather than reconstructing from a service name) handles both
    the same-tier and cross-tier shapes uniformly, for any "../"-depth (DRY).

    Args:
        unit: A service-instance unit directory (contains terragrunt.hcl).

    Returns:
        Sorted unique list of dependency instance directories at the unit's current
        instance index. Empty when the unit has no ${local.svc_instance}-indexed
        dependencies.

    Raises:
        CopyabilityError: If the unit's terragrunt.hcl is missing.
    """
    hcl = unit / "terragrunt.hcl"
    if not hcl.is_file():
        raise CopyabilityError(
            f"ERROR: cannot resolve dependency closure -- '{hcl}' does not exist. "
            "The service-instance unit must contain a terragrunt.hcl."
        )
    text = hcl.read_text(encoding="utf-8")
    instance_index = unit.name
    dep_dirs: set[pathlib.Path] = set()
    for match in re.finditer(_INSTANCE_CONFIG_PATH_PATTERN, text):
        # rel_prefix is the config_path minus the trailing "/${local.svc_instance}" --
        # e.g. "../../dns-prod-zone" or "../../../_singletons/shared/data-lake".
        rel_prefix = match.group(1)
        # The config_path is "<rel_prefix>/${local.svc_instance}"; the dependency instance
        # at the unit's own (source) index is "<unit>/<rel_prefix>/<instance_index>".
        # os.path.normpath collapses the "../" segments to a real tree directory.
        dep_dir = pathlib.Path(os.path.normpath(unit / rel_prefix / instance_index))
        dep_dirs.add(dep_dir)
    return sorted(dep_dirs)


def _service_instance_closure(leaf: pathlib.Path) -> list[pathlib.Path]:
    """Return the transitive instance dependency closure of a service-instance leaf.

    Starting from the chosen leaf, follows every instance-relative dependency
    config_path (`../../<service>/${local.svc_instance}` for a same-tier serving
    sibling, or `../../../_singletons/shared/<service>/${local.svc_instance}` for a
    cross-tier shared singleton) to its dependency-unit instance directory, recursively,
    and returns the unique set of source instance directories that must be copied
    together so the copied leaf's dependency config_paths all resolve to existing
    directories at the new instance index (spec B24, G5). The chosen leaf is always
    first in the returned list.

    Each dependency is resolved at the SAME instance index as the chosen leaf (the
    leaf's basename), because every instance-relative config_path interpolates
    ${local.svc_instance}, which resolves to the copied unit's own instance basename.
    The shared singletons live OUTSIDE the env-index serving directory (under
    .../_singletons/shared/), so resolving the literal relative config_path -- rather
    than reconstructing a path from a service name under one fixed parent -- is required
    to reach them (and keeps a single resolver for both tier shapes, DRY).

    Args:
        leaf: The chosen service-instance leaf directory.

    Returns:
        Ordered list of source instance directories: the chosen leaf first, then its
        (transitive) dependency instances. Each dependency is resolved at the same
        instance index as the chosen leaf (the leaf's basename).

    Raises:
        CopyabilityError: If a referenced dependency directory does not exist (the
            spec B24 contract is violated in the source tree, not by the test).
    """
    ordered: list[pathlib.Path] = [leaf]
    seen: set[pathlib.Path] = {leaf.resolve()}
    queue: list[pathlib.Path] = [leaf]

    while queue:
        current = queue.pop(0)
        for dep_dir in _instance_dependency_dirs(current):
            resolved = dep_dir.resolve()
            if resolved in seen:
                continue
            if not dep_dir.is_dir():
                raise CopyabilityError(
                    f"ERROR: dependency closure incomplete -- '{current.name}' references "
                    f"dependency instance '{dep_dir}', but that directory does not exist. "
                    "The source live tree violates the spec B24 contract (every dependency "
                    "config_path must resolve to an existing directory). Fix the source tree "
                    "before testing copyability."
                )
            seen.add(resolved)
            ordered.append(dep_dir)
            queue.append(dep_dir)

    return ordered


# ---------------------------------------------------------------------------
# Context manager: copy + seed + run + teardown
# ---------------------------------------------------------------------------


@contextmanager
def _copy_and_seed_scope(
    source: pathlib.Path,
    destination: pathlib.Path,
    seed_specs: list[_SeedSpec],
    accounts_json: pathlib.Path,
    extra_copies: list[tuple[pathlib.Path, pathlib.Path]] | None = None,
) -> Generator[pathlib.Path]:
    """Copy source to destination, seed common/ rows, yield destination, then clean up.

    The dns-owner zone-id guard is patched for the duration of the context so that
    `terragrunt run --all` can parse common.hcl without the placeholder failing.

    Args:
        source: Scope directory to copy (the primary scope under assertion).
        destination: Where to place the primary copy.
        seed_specs: Common/ rows to seed before yielding.
        accounts_json: Path to common/accounts.json for zone-id patching.
        extra_copies: Additional (source, destination) directory pairs to copy
            alongside the primary scope. Used to materialize a single leaf's
            sibling dependency closure (spec B24, G5) so every instance-relative
            config_path resolves to an existing directory. Each is removed on
            teardown. None or empty means only the primary scope is copied.

    Yields:
        The destination path (valid for running terragrunt commands against).

    Raises:
        CopyabilityError: If copy, seeding, or teardown fails.
    """
    all_copies: list[tuple[pathlib.Path, pathlib.Path]] = [
        (source, destination),
        *(extra_copies or []),
    ]

    copied: list[pathlib.Path] = []
    original_zone_id = _patch_dns_owner_zone_id(accounts_json)
    seeded: list[_SeedSpec] = []

    try:
        for src, dst in all_copies:
            if dst.exists():
                shutil.rmtree(str(dst))
            shutil.copytree(str(src), str(dst))
            copied.append(dst)
        for spec in seed_specs:
            _seed_common_row(spec.json_file, spec.key, spec.value, spec.parent_key)
            seeded.append(spec)
        yield destination
    finally:
        _restore_dns_owner_zone_id(accounts_json, original_zone_id)
        for spec in reversed(seeded):
            with suppress(CopyabilityError):
                _restore_common_row(spec.json_file, spec.key, spec.parent_key)
        for dst in reversed(copied):
            if dst.exists():
                shutil.rmtree(str(dst))


# ---------------------------------------------------------------------------
# CopyabilityRunner
# ---------------------------------------------------------------------------


class CopyabilityRunner:
    """Executes the per-level and generalized-scope copyability assertions.

    Each public method copies a representative scope, seeds minimal common/
    rows, runs terragrunt validate and plan (mocks on), and asserts zero-edit
    and state-isolation properties.

    All state is injected via RunnerConfig (DIP). No inline path literals or
    hardcoded configuration values inside this class.
    """

    def __init__(self, cfg: RunnerConfig) -> None:
        self._cfg = cfg

    def _source_for_level(self, level: str) -> pathlib.Path:
        """Derive the representative source scope path for a level."""
        base = self._cfg.live_root / _LIVE_ROOT_SEGMENT / _REGION_SEGMENT
        # Account is abstracted out of the env-keyed path (D2): env_class sits directly under
        # the region, with no account directory in between.
        env_path = base / _ENV_CLASS_SEGMENT
        env_index_path = env_path / _ENV_INDEX_SEGMENT
        leaf_path = env_index_path / _SERVICE_SEGMENT / _SVC_INSTANCE_SEGMENT

        mapping = {
            LEVEL_REGION: base,
            LEVEL_ENV_CLASS: env_path,
            LEVEL_ENV_INDEX: env_index_path,
            LEVEL_SERVICE_INSTANCE: leaf_path,
        }

        if level not in mapping:
            raise CopyabilityError(f"ERROR: unknown level '{level}'. Valid levels: {ALL_LEVELS}.")
        src = mapping[level]
        if not src.exists():
            raise CopyabilityError(
                f"ERROR: representative source scope for level '{level}' does not exist: "
                f"'{src}'. "
                "The live tree must be present before running the copyability test."
            )
        return src

    def _run_and_assert(
        self,
        level: str,
        source: pathlib.Path,
        destination: pathlib.Path,
        seed_specs: list[_SeedSpec],
        extra_copies: list[tuple[pathlib.Path, pathlib.Path]] | None = None,
    ) -> None:
        """Copy scope, seed, run validate, assert zero-edit and state isolation, tear down.

        extra_copies carries the sibling dependency-closure (source, destination) pairs
        that must be materialized alongside the primary scope so that a single copied
        leaf's instance-relative config_paths all resolve (spec B24, G5).
        """
        accounts_json = self._cfg.common_dir / "accounts.json"

        # Every VPC-creating unit in the copied scope (primary + closure) produces a new
        # namespace that must have a CIDR row in common/networks.json before validate
        # (spec S4.1/S3.6). Append those rows to the level-specific seeds so the copy
        # validates with zero edits to the copied files (the row lives in common/, not
        # in the copied scope).
        all_seed_specs = [
            *seed_specs,
            *_networks_seed_specs(source, destination, self._cfg.common_dir),
            *_cloudfront_seed_specs(source, destination, self._cfg.common_dir),
        ]
        for extra_src, extra_dst in extra_copies or []:
            all_seed_specs.extend(_networks_seed_specs(extra_src, extra_dst, self._cfg.common_dir))
            all_seed_specs.extend(
                _cloudfront_seed_specs(extra_src, extra_dst, self._cfg.common_dir)
            )

        with _copy_and_seed_scope(
            source, destination, all_seed_specs, accounts_json, extra_copies
        ) as dst:
            _assert_byte_identical(source, dst)

            result = _run_tg_command(self._cfg.tg_bin, dst, "validate")
            if result.returncode != 0:
                raise CopyabilityError(
                    f"ERROR: `terragrunt run --all -- validate` failed for level '{level}' "
                    f"at copied scope '{dst}' (exit {result.returncode}).\n"
                    f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}\n"
                    "Check that the copied scope needs no edits to validate. "
                    "If edits are required, the zero-edit copyability property has regressed "
                    "(spec section 4.8)."
                )

            _assert_state_isolation(source, dst, level)

    def run_level(self, level: str) -> None:
        """Run the copyability assertion for the given hierarchy level.

        Args:
            level: One of ALL_LEVELS.

        Raises:
            CopyabilityError: If any precondition, assertion, or teardown fails.
        """
        source = self._source_for_level(level)
        dest_name = _DEST_NAMES[level]
        destination = source.parent / dest_name
        seed_specs = _seed_specs_for_level(level, dest_name, self._cfg.common_dir)
        extra_copies = self._dependency_closure_copies(level, source, dest_name)
        self._run_and_assert(level, source, destination, seed_specs, extra_copies)

    def _dependency_closure_copies(
        self, level: str, source: pathlib.Path, dest_name: str
    ) -> list[tuple[pathlib.Path, pathlib.Path]]:
        """Return dependency (source, destination) copy pairs for a single-leaf copy.

        Only the service-instance level copies a single leaf, whose instance-relative
        dependency config_paths point at dependency instances at the same (new) index --
        both same-tier serving siblings and cross-tier shared singletons (under
        .../_singletons/shared/). Those dependency instances must be copied too so every
        config_path resolves to an existing directory (spec B24, G5). Each dependency is
        copied from its source instance to the same new instance basename next to it
        (sibling.parent / dest_name resolves correctly for any tier depth). Higher levels
        copy a whole subtree in which all dependencies already exist, so they need no
        extra copies.

        Args:
            level: The hierarchy level being exercised.
            source: The primary source scope (the chosen leaf at the service-instance
                level; a subtree root otherwise).
            dest_name: The synthetic destination basename for the copied instance.

        Returns:
            List of (dependency_source, dependency_destination) pairs (possibly empty).
        """
        if level == LEVEL_ENV_CLASS:
            # Phase 8 (Approach A): the dns-prod-zone unit lives in the FOUNDATION tier at
            # bootstrap/<role>/dns-prod-zone (a sibling of the env subtree), NOT inside the
            # copied env subtree. Every env-subtree consumer derives its bootstrap role from
            # environment.hcl as "<env-class>_role" (local.bootstrap_role) and points its
            # dns_prod_zone dependency config_path at bootstrap/<env-class>_role/dns-prod-zone.
            # A copy to the synthetic env-class therefore points at bootstrap/<dest>_role/,
            # which does not exist. Copy the SOURCE env-class's bootstrap role dir (which holds
            # account.hcl + environment_instance.hcl + the dns-prod-zone foundation unit) to the
            # synthetic role name so every dns_prod_zone config_path + active.hcl read resolves
            # with zero edits. (Region copies already include bootstrap/; env-index and
            # service-instance copies keep the env-class unchanged so they resolve to the real
            # bootstrap/<env>_role.)
            bootstrap_dir = source.parent / _BOOTSTRAP_SEGMENT
            src_role = bootstrap_dir / f"{_ENV_CLASS_SEGMENT}{_BOOTSTRAP_ROLE_SUFFIX}"
            dst_role = bootstrap_dir / f"{dest_name}{_BOOTSTRAP_ROLE_SUFFIX}"
            return [(src_role, dst_role)]

        if level != LEVEL_SERVICE_INSTANCE:
            return []

        closure = _service_instance_closure(source)
        # closure[0] is the primary leaf (copied as the primary scope); the remaining
        # entries are the dependency instances that must accompany it.
        return [(dep, dep.parent / dest_name) for dep in closure[1:]]

    def run_generalized_scope(self) -> None:
        """Run the G6 generalized-scope assertion (arbitrary future region name).

        The destination is a real AWS region not present in the live tree. It must be
        a valid AWS region (not a cpytst-prefixed synthetic name) because region.hcl
        makes the region directory basename the backend region the S3 backend validates
        at init time. A different real region still isolates state via the changed
        namespace and bucket name.

        Raises:
            CopyabilityError: If any precondition, assertion, or teardown fails.
        """
        source = self._source_for_level(LEVEL_REGION)
        dest_name = _GENERALIZED_REGION_NAME
        destination = source.parent / dest_name
        # Region copy needs no seeding (account + env-class from source are unchanged)
        self._run_and_assert(LEVEL_REGION, source, destination, [])


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def _build_default_config() -> RunnerConfig:
    """Build a RunnerConfig from environment variables or repo-relative defaults.

    Environment variables (all optional):
      TG_LIVE_ROOT  -- absolute path to the live tree root
      TG_COMMON_DIR -- absolute path to the common/ directory
      TG_BIN        -- terragrunt binary name or path

    Raises:
        CopyabilityError: If a specified directory does not exist.
    """
    repo_root = pathlib.Path(__file__).parent.parent

    raw_live = os.environ.get("TG_LIVE_ROOT")
    raw_common = os.environ.get("TG_COMMON_DIR")
    tg_bin = os.environ.get("TG_BIN", "terragrunt")

    live_root = pathlib.Path(raw_live) if raw_live else repo_root / "terragrunt" / "live"
    common_dir = pathlib.Path(raw_common) if raw_common else repo_root / "terragrunt" / "common"

    if not live_root.is_dir():
        raise CopyabilityError(
            f"ERROR: TG_LIVE_ROOT '{live_root}' does not exist or is not a directory. "
            "Set TG_LIVE_ROOT to the correct path or run from the repository root."
        )
    if not common_dir.is_dir():
        raise CopyabilityError(
            f"ERROR: TG_COMMON_DIR '{common_dir}' does not exist or is not a directory. "
            "Set TG_COMMON_DIR to the correct path or run from the repository root."
        )

    return RunnerConfig(live_root=live_root, common_dir=common_dir, tg_bin=tg_bin)


# ---------------------------------------------------------------------------
# Runner factory (allows test injection)
# ---------------------------------------------------------------------------


def _make_runner(cfg: RunnerConfig) -> CopyabilityRunner:
    """Construct a CopyabilityRunner from a RunnerConfig."""
    return CopyabilityRunner(cfg)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_VALID_LEVEL_VALUES: list[str] = ["all"] + ALL_LEVELS


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for tg_copyability_test.

    Args:
        argv: Argument list (uses sys.argv[1:] when None).

    Returns:
        0 on success, non-zero on failure.
    """
    parser = argparse.ArgumentParser(
        description="Prove the copy-at-any-level property for the Terragrunt live tree.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--level",
        required=True,
        help=(f"Hierarchy level to exercise. Valid values: {', '.join(_VALID_LEVEL_VALUES)}"),
    )
    args = parser.parse_args(argv)
    level_arg: str = args.level

    if level_arg not in _VALID_LEVEL_VALUES:
        print(
            f"ERROR: invalid --level '{level_arg}'. Valid values: {', '.join(_VALID_LEVEL_VALUES)}",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = _build_default_config()
    except CopyabilityError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Validate that the terragrunt binary exists
    if not shutil.which(cfg.tg_bin) and not pathlib.Path(cfg.tg_bin).is_file():
        print(
            f"ERROR: terragrunt binary '{cfg.tg_bin}' not found in PATH or as an absolute path. "
            "Install terragrunt or set the TG_BIN environment variable to the correct path.",
            file=sys.stderr,
        )
        return 1

    runner = _make_runner(cfg)

    levels_to_run = ALL_LEVELS if level_arg == "all" else [level_arg]

    try:
        for level in levels_to_run:
            print(f"[tg-copyability-test] Running level: {level}", flush=True)
            runner.run_level(level)
            print(f"[tg-copyability-test] PASS: {level}", flush=True)

        if level_arg == "all":
            print("[tg-copyability-test] Running generalized scope (G6)", flush=True)
            runner.run_generalized_scope()
            print("[tg-copyability-test] PASS: generalized scope", flush=True)

    except CopyabilityError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("[tg-copyability-test] All assertions passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
