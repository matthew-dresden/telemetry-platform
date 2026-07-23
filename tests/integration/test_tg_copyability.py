"""Integration tests for the tg-copyability-test suite.

Each parametrized case copies a representative scope at the given level,
seeds the minimal common/ rows, runs `terragrunt run --all -- validate`
(via the production runner, which forces TG_OFFLINE_BACKEND) over the copied
scope, and asserts:
  1. The copied files are byte-for-byte identical to their sources (zero edits).
  2. The derived namespace, S3 bucket name, and state CMK alias differ from the
     source scope (state isolation).

The dns-owner account's zone-id guard (common.hcl) is temporarily patched by
seeding a synthetic but valid-looking zone id into accounts.json; this is
restored after each test.

The validate runs fully offline: TG_OFFLINE_BACKEND switches root.hcl to a local
backend, so there is no S3 state to read and dependency-output resolution falls
back to the dependency blocks' mock_outputs. No live AWS apply, plan, or state
access is performed -- mocks only (spec section 10, AC-23).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from contextlib import suppress

import pytest

from scripts.tg_copyability_test import (
    _CPYTST_OIDC_ROLES_VALUE,
    _DEST_NAMES,
    _GENERALIZED_REGION_NAME,
    LEVEL_ACCOUNT,
    LEVEL_ENV_CLASS,
    LEVEL_ENV_INDEX,
    LEVEL_REGION,
    LEVEL_SERVICE_INSTANCE,
    CopyabilityError,
    _assert_byte_identical,
    _assert_state_isolation,
    _networks_seed_specs,
    _patch_dns_owner_zone_id,
    _restore_common_row,
    _restore_dns_owner_zone_id,
    _run_tg_command,
    _seed_common_row,
    _service_instance_closure,
    derive_namespace,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
LIVE_ROOT = REPO_ROOT / "terragrunt" / "live"
COMMON_DIR = REPO_ROOT / "terragrunt" / "common"

# Source scopes -- one representative scope per level
REGION_SOURCE = LIVE_ROOT / "telemetry" / "us-east-1"
ACCOUNT_SOURCE = LIVE_ROOT / "telemetry" / "us-east-1" / "111111111111"
ENV_CLASS_SOURCE = LIVE_ROOT / "telemetry" / "us-east-1" / "111111111111" / "prod"
ENV_INDEX_SOURCE = LIVE_ROOT / "telemetry" / "us-east-1" / "111111111111" / "prod" / "000"
SERVICE_INSTANCE_SOURCE = (
    LIVE_ROOT / "telemetry" / "us-east-1" / "111111111111" / "prod" / "000" / "data-lake" / "000"
)

# Generalized scope (G6) -- a real AWS region not present in the live tree. It must be
# a valid AWS region (sourced from the script constant, DRY) because region.hcl makes the
# region directory basename the backend region the S3 backend validates at init time.
GENERALIZED_REGION_NAME = _GENERALIZED_REGION_NAME

# ---------------------------------------------------------------------------
# Parametrize levels
# ---------------------------------------------------------------------------

LEVEL_PARAMS = [
    pytest.param(LEVEL_REGION, id="region"),
    pytest.param(LEVEL_ACCOUNT, id="account"),
    pytest.param(LEVEL_ENV_CLASS, id="env-class"),
    pytest.param(LEVEL_ENV_INDEX, id="env-index"),
    pytest.param(LEVEL_SERVICE_INSTANCE, id="service-instance"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_source_for_level(level: str) -> pathlib.Path:
    """Return the representative source scope for the given level."""
    mapping = {
        LEVEL_REGION: REGION_SOURCE,
        LEVEL_ACCOUNT: ACCOUNT_SOURCE,
        LEVEL_ENV_CLASS: ENV_CLASS_SOURCE,
        LEVEL_ENV_INDEX: ENV_INDEX_SOURCE,
        LEVEL_SERVICE_INSTANCE: SERVICE_INSTANCE_SOURCE,
    }
    if level not in mapping:
        raise CopyabilityError(f"ERROR: unknown level '{level}'")
    return mapping[level]


def _dest_name_for_level(level: str) -> str:
    """Return a synthetic destination name for the given level."""
    if level not in _DEST_NAMES:
        raise CopyabilityError(f"ERROR: unknown level '{level}'")
    return _DEST_NAMES[level]


def _run_tg_validate(
    scope_path: pathlib.Path, tg_bin: str = "terragrunt"
) -> subprocess.CompletedProcess[str]:
    """Run `terragrunt run --all -- validate` in the given scope.

    Delegates to the production runner (scripts.tg_copyability_test._run_tg_command)
    so the integration test exercises the exact invocation `make tg-copyability-test`
    uses -- including TG_OFFLINE_BACKEND, which keeps the structural validate offline
    (local backend, dependency mock_outputs) rather than re-deriving the command and
    env here (DRY).
    """
    return _run_tg_command(tg_bin, scope_path, "validate")


def _assert_state_isolation_explicit(src: pathlib.Path, dst: pathlib.Path, level: str) -> None:
    """Assert the copied scope isolates state (distinct namespace/bucket/alias).

    Delegates to the production assertion (scripts.tg_copyability_test._assert_state_isolation)
    so the integration test enforces the exact isolation contract the runner does --
    leaf-derived namespace, account (positional, copy-safe), bucket, and per-account CMK
    alias -- rather than re-deriving it here (DRY). It raises CopyabilityError on any
    collision, which fails the test.
    """
    _assert_state_isolation(src, dst, level)


# ---------------------------------------------------------------------------
# Integration parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("level", LEVEL_PARAMS)
def test_copyability_at_level(level: str) -> None:
    """Copying a representative scope at 'level' validates with zero edits and state isolation."""
    src = _get_source_for_level(level)
    dest_name = _dest_name_for_level(level)
    dst = src.parent / dest_name

    accounts_json = COMMON_DIR / "accounts.json"
    original_zone_id = _patch_dns_owner_zone_id(accounts_json)

    # Seed common/ rows needed for this level's scope
    seeded_keys: list[tuple[pathlib.Path, str]] = []
    # Sibling-closure copies for a single-leaf (service-instance) copy, as
    # (source, destination) pairs. Each destination must be removed on teardown
    # alongside the primary copy.
    extra_copied: list[tuple[pathlib.Path, pathlib.Path]] = []

    try:
        # Copy the scope
        if dst.exists():
            shutil.rmtree(str(dst))
        shutil.copytree(str(src), str(dst))

        # A single service-instance leaf's instance-relative dependency config_paths
        # point at sibling instances at the new index, which must exist for validate
        # (spec B24, G5). Copy the leaf's transitive sibling closure to the new index
        # too, exactly as the production runner does. Higher levels copy a whole subtree
        # in which the siblings already exist, so they need no extra copies.
        if level == LEVEL_SERVICE_INSTANCE:
            closure = _service_instance_closure(src)
            for sibling in closure[1:]:
                sibling_dst = sibling.parent / dest_name
                if sibling_dst.exists():
                    shutil.rmtree(str(sibling_dst))
                shutil.copytree(str(sibling), str(sibling_dst))
                extra_copied.append((sibling, sibling_dst))

        # Seed required common/ rows based on level
        if level == LEVEL_ACCOUNT:
            # Seed a new account id row in accounts.json
            account_key = dest_name  # the fake account id IS the dest_name
            _seed_common_row(
                accounts_json,
                account_key,
                {
                    "account_role": "cpytst-infra",
                    "aws_profile": "cpytst",
                    # ci_deploy is a required accounts.json field (gates CI-deploy-role
                    # KMS principals in several _envcommon units); false = non-CI test
                    # account.
                    "ci_deploy": False,
                    "deploy_role_name": "telemetry-platform-gha-tg-apply",
                    "is_dns_owner": False,
                },
            )
            seeded_keys.append((accounts_json, account_key))
            # The oidc-bootstrap unit fails fast on an account id absent from
            # oidc-roles.json (spec section 4.9); seed a minimal roles row for the copy.
            _seed_common_row(
                COMMON_DIR / "oidc-roles.json",
                account_key,
                _CPYTST_OIDC_ROLES_VALUE,
            )
            seeded_keys.append((COMMON_DIR / "oidc-roles.json", account_key))

        elif level == LEVEL_ENV_CLASS:
            # Seed env-class rows in domains.json and contacts.json
            env_key = dest_name
            _seed_common_row(
                COMMON_DIR / "domains.json",
                env_key,
                {
                    "dns_service_apex": f"{env_key}.telemetry.example.com",
                    "dns_pretty_apex": f"{env_key}.telemetry.example.com",
                    "enable_custom_domain": False,
                },
            )
            seeded_keys.append((COMMON_DIR / "domains.json", env_key))
            _seed_common_row(
                COMMON_DIR / "contacts.json",
                env_key,
                {
                    "alert_emails": ["cpytst-alerts@example.com"],
                    "budget_emails": ["cpytst-alerts@example.com"],
                },
            )
            seeded_keys.append((COMMON_DIR / "contacts.json", env_key))

        # Seed a CIDR row in networks.json for every VPC-creating (collector-ingestion)
        # unit in the copied scope -- the primary scope and any sibling-closure copies.
        # The copied scope derives a new namespace per such unit, which has no
        # networks.json row, and the fail-fast lookup aborts validate without it (same
        # as the production runner does -- spec S4.1/S3.6).
        network_specs = list(_networks_seed_specs(src, dst, COMMON_DIR))
        for sibling_src, sibling_dst in extra_copied:
            network_specs.extend(_networks_seed_specs(sibling_src, sibling_dst, COMMON_DIR))
        for spec in network_specs:
            _seed_common_row(spec.json_file, spec.key, spec.value)
            seeded_keys.append((spec.json_file, spec.key))

        # Assert zero edits
        _assert_byte_identical(src, dst)

        # Run terragrunt validate
        result = _run_tg_validate(dst)
        assert result.returncode == 0, (
            f"terragrunt run --all -- validate failed for level '{level}' "
            f"with exit code {result.returncode}.\n"
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
        )

        # Assert state isolation
        _assert_state_isolation_explicit(src, dst, level)

    finally:
        _restore_dns_owner_zone_id(accounts_json, original_zone_id)
        for json_file, key in seeded_keys:
            with suppress(CopyabilityError):
                _restore_common_row(json_file, key)
        if dst.exists():
            shutil.rmtree(str(dst))
        for _sibling_src, sibling_dst in extra_copied:
            if sibling_dst.exists():
                shutil.rmtree(str(sibling_dst))


# ---------------------------------------------------------------------------
# Generalized scope test (G6)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_copyability_generalized_scope() -> None:
    """G6: copying to a real future region validates with zero edits and isolation."""
    src = REGION_SOURCE
    dst = src.parent / GENERALIZED_REGION_NAME

    accounts_json = COMMON_DIR / "accounts.json"
    original_zone_id = _patch_dns_owner_zone_id(accounts_json)
    seeded_keys: list[tuple[pathlib.Path, str]] = []

    try:
        if dst.exists():
            shutil.rmtree(str(dst))
        shutil.copytree(str(src), str(dst))

        # Seed networks.json CIDR rows for every VPC-creating unit in the copied region
        # (spec S4.1/S3.6), as the production runner does.
        for spec in _networks_seed_specs(src, dst, COMMON_DIR):
            _seed_common_row(spec.json_file, spec.key, spec.value)
            seeded_keys.append((spec.json_file, spec.key))

        # Assert zero edits
        _assert_byte_identical(src, dst)

        # Run validate
        result = _run_tg_validate(dst)
        assert result.returncode == 0, (
            f"terragrunt run --all -- validate failed for generalized scope "
            f"'{GENERALIZED_REGION_NAME}' with exit code {result.returncode}.\n"
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
        )

        # State isolation: namespace must differ (different region name)
        src_ns = derive_namespace(SERVICE_INSTANCE_SOURCE)
        dst_ns = derive_namespace(dst / "111111111111" / "prod" / "000" / "data-lake" / "000")
        assert src_ns != dst_ns, f"Generalized scope namespace collision: '{src_ns}' == '{dst_ns}'"

    finally:
        _restore_dns_owner_zone_id(accounts_json, original_zone_id)
        for json_file, key in seeded_keys:
            with suppress(CopyabilityError):
                _restore_common_row(json_file, key)
        if dst.exists():
            shutil.rmtree(str(dst))
