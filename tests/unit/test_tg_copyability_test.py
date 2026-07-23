"""Unit tests for scripts/tg_copyability_test.py.

Covers:
  - derive_namespace: correct namespace composition from a scope path
  - derive_bucket_name: B21 hash-suffix shortening scheme
  - derive_cmk_alias: alias/<account>-tfstate formula
  - _assert_state_isolation: ensures copied scope differs in namespace/bucket/cmk
  - _seed_common_row / _restore_common_row: JSON round-trip, duplicate key detection
  - CopyConfig / RunnerConfig dataclass construction
  - CopyabilityError raised on invalid inputs (fail-fast contract)
  - _build_default_config: env-var overrides read correctly
  - CLI main(): --level argument accepted; missing binary fails closed
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from unittest.mock import MagicMock

import pytest

from scripts.tg_copyability_test import (
    ALL_LEVELS,
    LEVEL_ACCOUNT,
    LEVEL_ENV_CLASS,
    LEVEL_ENV_INDEX,
    LEVEL_REGION,
    LEVEL_SERVICE_INSTANCE,
    CopyabilityError,
    CopyabilityRunner,
    CopyConfig,
    RunnerConfig,
    _assert_state_isolation,
    _build_default_config,
    _restore_common_row,
    _seed_common_row,
    derive_bucket_name,
    derive_cmk_alias,
    derive_namespace,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


def _make_scope_path(
    tmp_path: pathlib.Path,
    product: str = "telemetry",
    region: str = "us-east-1",
    account: str = "111111111111",
    env_class: str = "sandbox",
    env_index: str = "000",
    service: str = "data-lake",
    svc_instance: str = "000",
) -> pathlib.Path:
    """Build a minimal fake scope path with the seven-layer hierarchy."""
    scope = (
        tmp_path
        / "live"
        / product
        / region
        / account
        / env_class
        / env_index
        / service
        / svc_instance
    )
    scope.mkdir(parents=True, exist_ok=True)
    return scope


# ---------------------------------------------------------------------------
# derive_namespace
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_derive_namespace_canonical() -> None:
    """Namespace follows product-regionclean-envclass-envindex-service-svcinstance, with each
    field's internal hyphens replaced by underscores (service data-lake -> data_lake)."""
    scope = pathlib.Path("/repo/terragrunt/live/telemetry/us-east-1/prod/000/data-lake/000")
    ns = derive_namespace(scope)
    assert ns == "telemetry-useast1-prod-000-data_lake-000"


@pytest.mark.unit
def test_derive_namespace_different_region() -> None:
    """Region dashes are stripped in the namespace."""
    scope = pathlib.Path("/repo/terragrunt/live/telemetry/us-west-2/sandbox/001/analytics/001")
    ns = derive_namespace(scope)
    assert ns == "telemetry-uswest2-sandbox-001-analytics-001"


@pytest.mark.unit
def test_derive_namespace_too_short_raises() -> None:
    """A path without enough components raises CopyabilityError."""
    scope = pathlib.Path("/a/b/c")
    with pytest.raises(CopyabilityError, match="ERROR"):
        derive_namespace(scope)


@pytest.mark.unit
@pytest.mark.parametrize(
    "product, region, env_class, env_index, service, svc_instance",
    [
        ("telemetry", "us-east-1", "prod", "000", "identity", "000"),
        ("telemetry", "eu-west-1", "qa", "001", "portal", "002"),
    ],
)
def test_derive_namespace_parametrized(
    product: str,
    region: str,
    env_class: str,
    env_index: str,
    service: str,
    svc_instance: str,
) -> None:
    # Account is abstracted out of the env-keyed path (D2); each field's internal
    # hyphens are replaced with underscores before joining with hyphens.
    region_clean = region.replace("-", "")
    scope = pathlib.Path(
        f"/repo/live/{product}/{region}/{env_class}/{env_index}/{service}/{svc_instance}"
    )
    ns = derive_namespace(scope)
    fields = [product, region_clean, env_class, env_index, service, svc_instance]
    expected = "-".join(f.replace("-", "_") for f in fields)
    assert ns == expected


# ---------------------------------------------------------------------------
# derive_bucket_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_derive_bucket_name_short() -> None:
    """Short names are returned unchanged."""
    account = "111111111111"
    # 12 + 1 + 22 + 1 + 7 = 43 chars (well under 63)
    ns = "telemetry-useast1-prod-000"
    name = derive_bucket_name(account, ns)
    raw = f"{account}-{ns}-tfstate"
    assert name == raw.lower()
    assert len(name) <= 63


@pytest.mark.unit
def test_derive_bucket_name_shortened_long() -> None:
    """Names exceeding 63 chars are shortened with a hash suffix."""
    account = "111111111111"
    ns = "telemetry-useast1-sandbox-000-acm-validate-collector-000"
    name = derive_bucket_name(account, ns)
    assert len(name) <= 63
    # Must contain the account id prefix
    assert name.startswith(account)
    # Must end with the hash-tfstate pattern
    assert "-tfstate" in name
    # Hash suffix must match md5 of namespace
    ns_hash = hashlib.md5(ns.encode(), usedforsecurity=False).hexdigest()[:8]
    assert ns_hash in name


@pytest.mark.unit
def test_derive_bucket_name_is_lowercase() -> None:
    """Bucket names are always lowercased."""
    account = "111111111111"
    ns = "Telemetry-USEast1-PROD-000-DataLake-000"
    name = derive_bucket_name(account, ns)
    assert name == name.lower()


# ---------------------------------------------------------------------------
# derive_cmk_alias
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_derive_cmk_alias_formula() -> None:
    """CMK alias follows alias/<account>-tfstate."""
    alias = derive_cmk_alias("123456789012")
    assert alias == "alias/123456789012-tfstate"


@pytest.mark.unit
def test_derive_cmk_alias_different_account() -> None:
    alias = derive_cmk_alias("999888777666")
    assert alias == "alias/999888777666-tfstate"


# ---------------------------------------------------------------------------
# _assert_state_isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_state_isolation_passes_for_distinct_scopes(tmp_path: pathlib.Path) -> None:
    """Different scopes produce distinct namespace/bucket/alias."""
    src = _make_scope_path(tmp_path, account="111111111111", svc_instance="000")
    dst = _make_scope_path(tmp_path, account="222222222222", svc_instance="001")
    # Should not raise
    _assert_state_isolation(src, dst)


@pytest.mark.unit
def test_assert_state_isolation_fails_for_same_scope(tmp_path: pathlib.Path) -> None:
    """Same source and destination raises CopyabilityError (state collision)."""
    scope = _make_scope_path(tmp_path, account="111111111111", svc_instance="000")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_state_isolation(scope, scope)


@pytest.mark.unit
def test_assert_state_isolation_account_level_skips_namespace(tmp_path: pathlib.Path) -> None:
    """Account-level copy: namespace without account id, so bucket/alias prove isolation."""
    src = _make_scope_path(tmp_path, account="111111111111")
    dst = _make_scope_path(tmp_path, account="222222222222")
    # Should not raise even though svc_instance/service are same
    _assert_state_isolation(src, dst, level=LEVEL_ACCOUNT)


@pytest.mark.unit
def test_assert_state_isolation_fails_same_bucket(tmp_path: pathlib.Path) -> None:
    """Identical accounts at service-instance level raises (namespace and bucket collision)."""
    scope = _make_scope_path(tmp_path, account="111111111111", env_class="prod", svc_instance="000")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_state_isolation(scope, scope, level=LEVEL_SERVICE_INSTANCE)


# ---------------------------------------------------------------------------
# _seed_common_row / _restore_common_row
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_seed_common_row_adds_key(tmp_path: pathlib.Path) -> None:
    """Seeding adds a new key to the JSON file."""
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps({"existing": {"val": 1}}, indent=2), encoding="utf-8")
    _seed_common_row(json_file, "new_key", {"val": 2})
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["new_key"] == {"val": 2}
    assert data["existing"] == {"val": 1}


@pytest.mark.unit
def test_seed_common_row_duplicate_raises(tmp_path: pathlib.Path) -> None:
    """Seeding a key that already exists raises CopyabilityError."""
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps({"existing": {"val": 1}}, indent=2), encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _seed_common_row(json_file, "existing", {"val": 2})


@pytest.mark.unit
def test_seed_common_row_missing_file_raises(tmp_path: pathlib.Path) -> None:
    """Seeding into a non-existent file raises CopyabilityError."""
    json_file = tmp_path / "nonexistent.json"
    with pytest.raises(CopyabilityError, match="ERROR"):
        _seed_common_row(json_file, "new_key", {"val": 1})


@pytest.mark.unit
def test_restore_common_row_removes_key(tmp_path: pathlib.Path) -> None:
    """Restoring removes the seeded key, leaving original content intact."""
    json_file = tmp_path / "test.json"
    original = {"existing": {"val": 1}, "seeded": {"val": 2}}
    json_file.write_text(json.dumps(original, indent=2), encoding="utf-8")
    _restore_common_row(json_file, "seeded")
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert "seeded" not in data
    assert data["existing"] == {"val": 1}


@pytest.mark.unit
def test_restore_common_row_missing_key_raises(tmp_path: pathlib.Path) -> None:
    """Restoring a key that doesn't exist raises CopyabilityError."""
    json_file = tmp_path / "test.json"
    json_file.write_text(json.dumps({"existing": {"val": 1}}, indent=2), encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _restore_common_row(json_file, "nonexistent")


@pytest.mark.unit
def test_restore_common_row_preserves_other_keys(tmp_path: pathlib.Path) -> None:
    """Restoring one key leaves all other keys unchanged."""
    json_file = tmp_path / "test.json"
    original = {"a": 1, "b": 2, "c": 3}
    json_file.write_text(json.dumps(original, indent=2), encoding="utf-8")
    _restore_common_row(json_file, "b")
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data == {"a": 1, "c": 3}


# ---------------------------------------------------------------------------
# CopyConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_copy_config_construction(tmp_path: pathlib.Path) -> None:
    """CopyConfig holds source path, dest path, and level."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    cfg = CopyConfig(source=src, destination=dst, level=LEVEL_REGION)
    assert cfg.source == src
    assert cfg.destination == dst
    assert cfg.level == LEVEL_REGION


@pytest.mark.unit
@pytest.mark.parametrize(
    "level", [LEVEL_REGION, LEVEL_ACCOUNT, LEVEL_ENV_CLASS, LEVEL_ENV_INDEX, LEVEL_SERVICE_INSTANCE]
)
def test_copy_config_valid_levels(level: str, tmp_path: pathlib.Path) -> None:
    cfg = CopyConfig(source=tmp_path, destination=tmp_path / "dst", level=level)
    assert cfg.level == level


# ---------------------------------------------------------------------------
# RunnerConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_runner_config_defaults(tmp_path: pathlib.Path) -> None:
    """RunnerConfig carries live_root, common_dir, and tg_bin."""
    cfg = RunnerConfig(
        live_root=tmp_path / "live", common_dir=tmp_path / "common", tg_bin="terragrunt"
    )
    assert cfg.tg_bin == "terragrunt"
    assert cfg.live_root == tmp_path / "live"
    assert cfg.common_dir == tmp_path / "common"


# ---------------------------------------------------------------------------
# _build_default_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_default_config_uses_repo_root() -> None:
    """Default config derives live_root and common_dir from the repo root."""
    cfg = _build_default_config()
    assert cfg.live_root.is_dir(), f"live_root must exist: {cfg.live_root}"
    assert cfg.common_dir.is_dir(), f"common_dir must exist: {cfg.common_dir}"
    assert cfg.tg_bin == "terragrunt"


@pytest.mark.unit
def test_build_default_config_env_override(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TG_LIVE_ROOT, TG_COMMON_DIR, and TG_BIN env vars override defaults."""
    live = tmp_path / "live"
    live.mkdir()
    common = tmp_path / "common"
    common.mkdir()
    monkeypatch.setenv("TG_LIVE_ROOT", str(live))
    monkeypatch.setenv("TG_COMMON_DIR", str(common))
    monkeypatch.setenv("TG_BIN", "/usr/local/bin/terragrunt")
    cfg = _build_default_config()
    assert cfg.live_root == live
    assert cfg.common_dir == common
    assert cfg.tg_bin == "/usr/local/bin/terragrunt"


@pytest.mark.unit
def test_build_default_config_missing_live_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing TG_LIVE_ROOT path raises CopyabilityError."""
    monkeypatch.setenv("TG_LIVE_ROOT", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("TG_COMMON_DIR", str(tmp_path))
    with pytest.raises(CopyabilityError, match="ERROR"):
        _build_default_config()


@pytest.mark.unit
def test_build_default_config_missing_common_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing TG_COMMON_DIR path raises CopyabilityError."""
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setenv("TG_LIVE_ROOT", str(live))
    monkeypatch.setenv("TG_COMMON_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(CopyabilityError, match="ERROR"):
        _build_default_config()


# ---------------------------------------------------------------------------
# ALL_LEVELS constant
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_all_levels_contains_expected() -> None:
    """ALL_LEVELS includes the live-tree copy levels (account excluded: env-keyed layout, D2)."""
    assert LEVEL_REGION in ALL_LEVELS
    assert LEVEL_ENV_CLASS in ALL_LEVELS
    assert LEVEL_ENV_INDEX in ALL_LEVELS
    assert LEVEL_SERVICE_INSTANCE in ALL_LEVELS
    assert LEVEL_ACCOUNT not in ALL_LEVELS


@pytest.mark.unit
def test_all_levels_length() -> None:
    """ALL_LEVELS has exactly 4 entries (account excluded: env-keyed layout, D2)."""
    assert len(ALL_LEVELS) == 4


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_all_levels_invokes_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with --level all calls run_level for each level and run_generalized_scope."""
    runner = MagicMock()
    runner.run_level.return_value = None
    runner.run_generalized_scope.return_value = None

    import scripts.tg_copyability_test as mod

    monkeypatch.setattr(mod, "_make_runner", lambda cfg: runner)
    result = main(["--level", "all"])
    assert result == 0
    assert runner.run_level.call_count == len(ALL_LEVELS)
    assert runner.run_generalized_scope.call_count == 1


@pytest.mark.unit
def test_main_single_level_invokes_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with --level region calls run_level once for region."""
    runner = MagicMock()
    runner.run_level.return_value = None
    runner.run_generalized_scope.return_value = None

    import scripts.tg_copyability_test as mod

    monkeypatch.setattr(mod, "_make_runner", lambda cfg: runner)
    result = main(["--level", "region"])
    assert result == 0
    runner.run_level.assert_called_once_with(LEVEL_REGION)
    runner.run_generalized_scope.assert_not_called()


@pytest.mark.unit
def test_main_invalid_level_exits_nonzero() -> None:
    """main() with an invalid --level value exits non-zero."""
    result = main(["--level", "invalid-level"])
    assert result != 0


@pytest.mark.unit
def test_main_copyability_error_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """CopyabilityError raised by the runner causes main() to return non-zero."""
    runner = MagicMock()
    runner.run_level.side_effect = CopyabilityError("ERROR: something failed")

    import scripts.tg_copyability_test as mod

    monkeypatch.setattr(mod, "_make_runner", lambda cfg: runner)
    result = main(["--level", "region"])
    assert result != 0


@pytest.mark.unit
def test_main_missing_tg_binary_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A missing terragrunt binary causes main() to return non-zero."""
    live = tmp_path / "live"
    live.mkdir()
    common = tmp_path / "common"
    common.mkdir()
    monkeypatch.setenv("TG_LIVE_ROOT", str(live))
    monkeypatch.setenv("TG_COMMON_DIR", str(common))
    monkeypatch.setenv("TG_BIN", str(tmp_path / "no-such-binary"))
    result = main(["--level", "region"])
    assert result != 0


# ---------------------------------------------------------------------------
# Error path coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_derive_namespace_path_without_live_segment_raises() -> None:
    """A path that has fewer than 7 components after 'live/' raises CopyabilityError."""
    scope = pathlib.Path("/a/live/telemetry/us-east-1/123456789012")
    with pytest.raises(CopyabilityError, match="ERROR"):
        derive_namespace(scope)


@pytest.mark.unit
def test_seed_common_row_invalid_json_raises(tmp_path: pathlib.Path) -> None:
    """A malformed JSON file raises CopyabilityError on seed."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _seed_common_row(bad_json, "key", {"val": 1})


@pytest.mark.unit
def test_restore_common_row_missing_file_raises(tmp_path: pathlib.Path) -> None:
    """Restoring from a non-existent file raises CopyabilityError."""
    json_file = tmp_path / "nonexistent.json"
    with pytest.raises(CopyabilityError, match="ERROR"):
        _restore_common_row(json_file, "key")


# ---------------------------------------------------------------------------
# Byte-for-byte identity helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_seed_restore_preserves_byte_identity(tmp_path: pathlib.Path) -> None:
    """After seed + restore the JSON file content is byte-for-byte identical."""
    json_file = tmp_path / "test.json"
    original_content = json.dumps({"a": 1, "b": 2}, indent=2) + "\n"
    json_file.write_bytes(original_content.encode("utf-8"))
    original_bytes = json_file.read_bytes()

    _seed_common_row(json_file, "cpytst-new", {"val": 99})
    _restore_common_row(json_file, "cpytst-new")

    restored_bytes = json_file.read_bytes()
    assert restored_bytes == original_bytes, (
        "After seed + restore the file content must be byte-for-byte identical. "
        f"Expected {len(original_bytes)} bytes, got {len(restored_bytes)} bytes."
    )


# ---------------------------------------------------------------------------
# Additional coverage: _leaf_scope_under
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_leaf_scope_under_with_deep_scope(tmp_path: pathlib.Path) -> None:
    """_leaf_scope_under finds a leaf under a high-level scope."""
    from scripts.tg_copyability_test import _leaf_scope_under

    # Build a minimal directory tree with live/.../terragrunt.hcl
    leaf = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf.mkdir(parents=True)
    (leaf / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")

    # Use the region level scope -- should find the leaf
    region_scope = tmp_path / "live" / "telemetry" / "us-east-1"
    result = _leaf_scope_under(region_scope)
    assert result == leaf


@pytest.mark.unit
def test_leaf_scope_under_no_leaf_raises(tmp_path: pathlib.Path) -> None:
    """_leaf_scope_under raises when no service-instance leaf exists."""
    from scripts.tg_copyability_test import _leaf_scope_under

    (tmp_path / "live").mkdir()
    with pytest.raises(CopyabilityError, match="ERROR"):
        _leaf_scope_under(tmp_path / "live")


# ---------------------------------------------------------------------------
# Additional coverage: _assert_state_isolation via _leaf_scope_under
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_state_isolation_namespace_collision_raises(tmp_path: pathlib.Path) -> None:
    """Identical namespace at non-account level raises CopyabilityError."""
    # Build two identical scope paths (same namespace will be derived)
    leaf = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf.mkdir(parents=True)
    (leaf / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")
    # src and dst both derive the same namespace since paths are the same
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_state_isolation(leaf, leaf, level=LEVEL_REGION)


@pytest.mark.unit
def test_assert_state_isolation_account_cmk_collision_raises(tmp_path: pathlib.Path) -> None:
    """Same account at LEVEL_ACCOUNT raises CMK alias collision."""
    # Build two leaves with same account id -> same CMK alias
    leaf = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf.mkdir(parents=True)
    (leaf / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")
    # src and dst with same account id -> same bucket AND same CMK alias
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_state_isolation(leaf, leaf, level=LEVEL_ACCOUNT)


@pytest.mark.unit
def test_account_segment_from_leaf_positional_accepts_synthetic_name(
    tmp_path: pathlib.Path,
) -> None:
    """The account segment is read positionally (below_live[2]), not by 12-digit scan.

    A copied account-level scope uses a synthetic account directory name (e.g.
    'cpytst-111111111111') that is not a bare 12-digit string. Positional extraction
    must still return it so state-isolation derivation works for the copy.
    """
    from scripts.tg_copyability_test import _account_segment_from_leaf

    leaf = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "cpytst-111111111111"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    assert _account_segment_from_leaf(leaf) == "cpytst-111111111111"


@pytest.mark.unit
def test_account_segment_from_leaf_too_shallow_raises(tmp_path: pathlib.Path) -> None:
    """A path that does not reach the account level below 'live' raises (fail-fast)."""
    from scripts.tg_copyability_test import _account_segment_from_leaf

    shallow = tmp_path / "live" / "telemetry" / "us-east-1"
    with pytest.raises(CopyabilityError, match="ERROR"):
        _account_segment_from_leaf(shallow)


@pytest.mark.unit
def test_assert_state_isolation_region_synthetic_account_distinct_bucket(
    tmp_path: pathlib.Path,
) -> None:
    """A region-level copy with a non-numeric account derives a distinct bucket, no raise.

    Region copies change the region segment, so the namespace and the derived bucket
    differ even though the account segment (here a non-12-digit name) is unchanged.
    """
    leaf1 = (
        tmp_path
        / "a"
        / "live"
        / "telemetry"
        / "us-east-1"
        / "no-acct"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf2 = (
        tmp_path
        / "b"
        / "live"
        / "telemetry"
        / "us-east-2"
        / "no-acct"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf1.mkdir(parents=True)
    leaf2.mkdir(parents=True)
    (leaf1 / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")
    (leaf2 / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")
    # Distinct region -> distinct namespace and bucket; positional account read does
    # not require a 12-digit segment, so this must NOT raise.
    _assert_state_isolation(leaf1, leaf2, level=LEVEL_REGION)


# ---------------------------------------------------------------------------
# Additional coverage: _read_accounts
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_accounts_missing_file_raises(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _read_accounts

    with pytest.raises(CopyabilityError, match="ERROR"):
        _read_accounts(tmp_path / "nonexistent.json")


@pytest.mark.unit
def test_read_accounts_malformed_json_raises(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _read_accounts

    bad = tmp_path / "accounts.json"
    bad.write_text("{bad json", encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _read_accounts(bad)


@pytest.mark.unit
def test_read_accounts_valid(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _read_accounts

    accounts_file = tmp_path / "accounts.json"
    data = {"111111111111": {"account_role": "test"}}
    accounts_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    result = _read_accounts(accounts_file)
    assert result == data


# ---------------------------------------------------------------------------
# Additional coverage: _patch_dns_owner_zone_id / _restore_dns_owner_zone_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_patch_dns_owner_zone_id_replaces_placeholder(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import (
        _DNS_OWNER_ACCOUNT_ID,
        _SYNTHETIC_ZONE_ID,
        _ZONE_ID_PLACEHOLDER,
        _patch_dns_owner_zone_id,
        _restore_dns_owner_zone_id,
    )

    accounts_file = tmp_path / "accounts.json"
    data = {_DNS_OWNER_ACCOUNT_ID: {"dns_owner_zone_id": _ZONE_ID_PLACEHOLDER}}
    accounts_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    original = _patch_dns_owner_zone_id(accounts_file)
    assert original == _ZONE_ID_PLACEHOLDER

    patched = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert patched[_DNS_OWNER_ACCOUNT_ID]["dns_owner_zone_id"] == _SYNTHETIC_ZONE_ID

    _restore_dns_owner_zone_id(accounts_file, original)
    restored = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert restored[_DNS_OWNER_ACCOUNT_ID]["dns_owner_zone_id"] == _ZONE_ID_PLACEHOLDER


@pytest.mark.unit
def test_patch_dns_owner_zone_id_missing_account_raises(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _patch_dns_owner_zone_id

    accounts_file = tmp_path / "accounts.json"
    accounts_file.write_text(json.dumps({"other_acct": {}}), encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _patch_dns_owner_zone_id(accounts_file)


# ---------------------------------------------------------------------------
# Additional coverage: _assert_byte_identical
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_byte_identical_raises_on_no_hcl(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _assert_byte_identical

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_byte_identical(src, dst)


@pytest.mark.unit
def test_assert_byte_identical_raises_on_missing_dst_file(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _assert_byte_identical

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "test.hcl").write_text("# test", encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_byte_identical(src, dst)


@pytest.mark.unit
def test_assert_byte_identical_raises_on_different_content(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _assert_byte_identical

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "test.hcl").write_text("# original", encoding="utf-8")
    (dst / "test.hcl").write_text("# modified", encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_byte_identical(src, dst)


@pytest.mark.unit
def test_assert_byte_identical_passes_on_identical(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _assert_byte_identical

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    content = b"# hcl content"
    (src / "test.hcl").write_bytes(content)
    (dst / "test.hcl").write_bytes(content)
    _assert_byte_identical(src, dst)  # should not raise


# ---------------------------------------------------------------------------
# Additional coverage: _run_tg_command
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_tg_command_missing_binary_raises(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _run_tg_command

    with pytest.raises(CopyabilityError, match="ERROR"):
        _run_tg_command("/no/such/binary", tmp_path, "validate")


@pytest.mark.unit
def test_run_tg_command_builds_run_all_command_and_offline_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner must invoke `run --all --parallelism 1 -- <cmd>` with the offline
    backend forced and the shared-plugin-cache env wired.

    Verifies the actual subprocess invocation: the terragrunt subcommand is
    `run --all --parallelism 1 -- validate` (parallelism 1 makes the shared plugin
    cache single-writer), runs in the copied scope, and the child environment carries
    TG_OFFLINE_BACKEND=true (so root.hcl emits a local backend and the structural
    validate stays offline), a TF_PLUGIN_CACHE_DIR (so providers download once, not
    per-unit -> no runner disk exhaustion), TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE
    (so the lockfile-less copied modules accept the cache copy), alongside the get_env()
    test inputs.
    """
    import scripts.tg_copyability_test as mod

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr("scripts.tg_copyability_test.subprocess.run", fake_run)

    mod._run_tg_command("terragrunt", tmp_path, "validate")

    assert captured["cmd"] == [
        "terragrunt",
        "run",
        "--all",
        "--parallelism",
        "1",
        "--",
        "validate",
    ]
    assert captured["cwd"] == str(tmp_path)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env[mod._TG_OFFLINE_BACKEND_ENV] == "true"
    # Shared plugin cache wiring: providers download once (disk O(1)) and the
    # lockfile-less copied modules accept the cache copy.
    assert env["TF_PLUGIN_CACHE_DIR"]
    assert env["TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE"] == "true"
    # The operator-supplied get_env() test inputs are still layered in.
    for key, value in mod._TG_ENV_INPUTS.items():
        assert env[key] == value


# ---------------------------------------------------------------------------
# Additional coverage: _seed_specs_for_level
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_seed_specs_for_level_account(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _seed_specs_for_level

    specs = _seed_specs_for_level(LEVEL_ACCOUNT, "cpytst-111111111111", tmp_path)
    # The account level seeds both an accounts.json row and an oidc-roles.json row,
    # each keyed by the synthetic account id (the oidc-bootstrap unit fails fast on a
    # missing oidc-roles row, spec section 4.9).
    assert len(specs) == 2
    assert all(spec.key == "cpytst-111111111111" for spec in specs)

    by_file = {spec.json_file.name: spec for spec in specs}
    assert "accounts.json" in by_file
    assert "oidc-roles.json" in by_file
    assert "account_role" in by_file["accounts.json"].value
    # The oidc-roles row carries a structurally valid roles map.
    assert "roles" in by_file["oidc-roles.json"].value
    assert by_file["oidc-roles.json"].value["roles"]


@pytest.mark.unit
def test_seed_specs_for_level_env_class(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _seed_specs_for_level

    specs = _seed_specs_for_level(LEVEL_ENV_CLASS, "cpytst-envclass", tmp_path)
    assert len(specs) == 3
    # domains.json + contacts.json (top-level), then the nested env_accounts.json "envs" row
    # that account.hcl needs to resolve the new env-class to an account.
    assert "domains.json" in str(specs[0].json_file)
    assert "contacts.json" in str(specs[1].json_file)
    assert "env_accounts.json" in str(specs[2].json_file)
    assert specs[2].parent_key == "envs"


@pytest.mark.unit
def test_seed_specs_for_level_region_empty(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _seed_specs_for_level

    specs = _seed_specs_for_level(LEVEL_REGION, "cpytst-eu-west-1", tmp_path)
    assert specs == []


@pytest.mark.unit
def test_seed_specs_for_level_env_index_empty(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _seed_specs_for_level

    specs = _seed_specs_for_level(LEVEL_ENV_INDEX, "cpytst-001", tmp_path)
    assert specs == []


@pytest.mark.unit
def test_seed_specs_for_level_service_instance_empty(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _seed_specs_for_level

    specs = _seed_specs_for_level(LEVEL_SERVICE_INSTANCE, "cpytst-001", tmp_path)
    assert specs == []


# ---------------------------------------------------------------------------
# Additional coverage: _copy_and_seed_scope context manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_copy_and_seed_scope_creates_and_cleans_up(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import (
        _DNS_OWNER_ACCOUNT_ID,
        _ZONE_ID_PLACEHOLDER,
        _copy_and_seed_scope,
        _SeedSpec,
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "test.hcl").write_text("# content", encoding="utf-8")

    # Set up a minimal accounts.json with dns-owner
    accounts_file = tmp_path / "accounts.json"
    data = {_DNS_OWNER_ACCOUNT_ID: {"dns_owner_zone_id": _ZONE_ID_PLACEHOLDER}}
    accounts_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # A seed file
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps({}, indent=2) + "\n", encoding="utf-8")
    specs = [_SeedSpec(json_file=seed_file, key="cpytst-key", value={"v": 1})]

    with _copy_and_seed_scope(src, dst, specs, accounts_file) as yielded:
        assert yielded == dst
        assert dst.exists()
        # Seed should be in seed.json
        seeded_data = json.loads(seed_file.read_text(encoding="utf-8"))
        assert "cpytst-key" in seeded_data

    # After context: dst removed and seed.json restored
    assert not dst.exists()
    restored_data = json.loads(seed_file.read_text(encoding="utf-8"))
    assert "cpytst-key" not in restored_data
    # accounts.json zone id restored
    accounts_data = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert accounts_data[_DNS_OWNER_ACCOUNT_ID]["dns_owner_zone_id"] == _ZONE_ID_PLACEHOLDER


# ---------------------------------------------------------------------------
# Additional coverage: CopyabilityRunner._source_for_level
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_runner_source_for_unknown_level_raises(tmp_path: pathlib.Path) -> None:
    """CopyabilityRunner._source_for_level raises on unknown level."""
    live = tmp_path / "live"
    live.mkdir()
    common = tmp_path / "common"
    common.mkdir()
    cfg = RunnerConfig(live_root=live, common_dir=common, tg_bin="terragrunt")
    runner = CopyabilityRunner(cfg)
    with pytest.raises(CopyabilityError, match="ERROR"):
        runner._source_for_level("unknown-level")


@pytest.mark.unit
def test_runner_source_for_missing_source_raises(tmp_path: pathlib.Path) -> None:
    """CopyabilityRunner._source_for_level raises when source path doesn't exist."""
    live = tmp_path / "live"
    live.mkdir()
    common = tmp_path / "common"
    common.mkdir()
    cfg = RunnerConfig(live_root=live, common_dir=common, tg_bin="terragrunt")
    runner = CopyabilityRunner(cfg)
    with pytest.raises(CopyabilityError, match="ERROR"):
        runner._source_for_level(LEVEL_REGION)


@pytest.mark.unit
def test_runner_source_for_existing_level_returns_path(tmp_path: pathlib.Path) -> None:
    """CopyabilityRunner._source_for_level returns the source path when it exists."""
    from scripts.tg_copyability_test import (
        _LIVE_ROOT_SEGMENT,
        _REGION_SEGMENT,
    )

    live = tmp_path / "live"
    region_path = live / _LIVE_ROOT_SEGMENT / _REGION_SEGMENT
    region_path.mkdir(parents=True)
    common = tmp_path / "common"
    common.mkdir()
    cfg = RunnerConfig(live_root=live, common_dir=common, tg_bin="terragrunt")
    runner = CopyabilityRunner(cfg)
    result = runner._source_for_level(LEVEL_REGION)
    assert result == region_path


# ---------------------------------------------------------------------------
# Additional coverage: _make_runner
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_runner_returns_copyability_runner(tmp_path: pathlib.Path) -> None:
    from scripts.tg_copyability_test import _make_runner

    cfg = RunnerConfig(live_root=tmp_path, common_dir=tmp_path, tg_bin="terragrunt")
    runner = _make_runner(cfg)
    assert isinstance(runner, CopyabilityRunner)


# ---------------------------------------------------------------------------
# Additional coverage: main() edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_build_config_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """main() exits non-zero when _build_default_config raises."""
    monkeypatch.setenv("TG_LIVE_ROOT", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("TG_COMMON_DIR", str(tmp_path))
    result = main(["--level", "region"])
    assert result != 0


@pytest.mark.unit
def test_main_runs_generalized_scope_when_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with --level all calls run_generalized_scope once."""
    runner = MagicMock()
    runner.run_level.return_value = None
    runner.run_generalized_scope.return_value = None

    import scripts.tg_copyability_test as mod

    monkeypatch.setattr(mod, "_make_runner", lambda cfg: runner)
    result = main(["--level", "all"])
    assert result == 0
    assert runner.run_generalized_scope.call_count == 1


# ---------------------------------------------------------------------------
# Additional coverage: restore_common_row with invalid JSON
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_restore_common_row_invalid_json_raises(tmp_path: pathlib.Path) -> None:
    """_restore_common_row on a malformed JSON file raises CopyabilityError."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CopyabilityError, match="ERROR"):
        _restore_common_row(bad_json, "key")


@pytest.mark.unit
def test_leaf_scope_under_skips_shallow_hcl(tmp_path: pathlib.Path) -> None:
    """_leaf_scope_under skips terragrunt.hcl files that are not at the service-instance depth."""
    from scripts.tg_copyability_test import _leaf_scope_under

    # Build a tree with a shallow terragrunt.hcl (not enough components) and a deep leaf
    shallow = tmp_path / "live" / "telemetry"
    shallow.mkdir(parents=True)
    # This terragrunt.hcl is too shallow -- it will be tried by _leaf_scope_under but
    # derive_namespace will raise (only 1 component below 'live'), triggering the continue
    (shallow / "terragrunt.hcl").write_text("# shallow hcl", encoding="utf-8")

    leaf = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf.mkdir(parents=True)
    (leaf / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")

    result = _leaf_scope_under(tmp_path / "live" / "telemetry")
    assert result == leaf


@pytest.mark.unit
def test_assert_state_isolation_bucket_collision_raises(tmp_path: pathlib.Path) -> None:
    """Identical account + namespace (same scope) raises bucket collision at LEVEL_ACCOUNT."""
    # Two paths with the same account id and same environment but different regions
    # would not typically collide -- we force it by using same leaf
    leaf = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "000"
        / "svc"
        / "000"
    )
    leaf.mkdir(parents=True)
    (leaf / "terragrunt.hcl").write_text("# leaf", encoding="utf-8")
    # At LEVEL_ACCOUNT with same scope -> same bucket AND same CMK alias
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_state_isolation(leaf, leaf, level=LEVEL_ACCOUNT)


@pytest.mark.unit
def test_assert_state_isolation_cmk_alias_collision_raises(tmp_path: pathlib.Path) -> None:
    """Same account id at LEVEL_ACCOUNT with different service raises CMK alias collision."""
    # Two different service-instance leaves under the SAME account -- different namespaces
    # (so buckets differ) but same account id (so CMK alias is the same)
    src = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "000"
        / "svc-a"
        / "000"
    )
    dst = (
        tmp_path
        / "live"
        / "telemetry"
        / "us-east-1"
        / "111111111111"
        / "prod"
        / "001"
        / "svc-b"
        / "001"
    )
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "terragrunt.hcl").write_text("# src", encoding="utf-8")
    (dst / "terragrunt.hcl").write_text("# dst", encoding="utf-8")
    # Different namespaces -> different buckets, same account -> same CMK alias
    with pytest.raises(CopyabilityError, match="ERROR"):
        _assert_state_isolation(src, dst, level=LEVEL_ACCOUNT)


@pytest.mark.unit
def test_copy_and_seed_scope_pre_existing_dst_removed(tmp_path: pathlib.Path) -> None:
    """_copy_and_seed_scope removes a pre-existing destination before copying."""
    from scripts.tg_copyability_test import (
        _DNS_OWNER_ACCOUNT_ID,
        _ZONE_ID_PLACEHOLDER,
        _copy_and_seed_scope,
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    # Pre-populate dst with a stale file
    (dst / "stale.hcl").write_text("# stale", encoding="utf-8")
    (src / "fresh.hcl").write_text("# fresh", encoding="utf-8")

    accounts_file = tmp_path / "accounts.json"
    data = {_DNS_OWNER_ACCOUNT_ID: {"dns_owner_zone_id": _ZONE_ID_PLACEHOLDER}}
    accounts_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    with _copy_and_seed_scope(src, dst, [], accounts_file) as yielded:
        assert yielded == dst
        # Stale file should be gone; fresh file should be present
        assert not (dst / "stale.hcl").exists()
        assert (dst / "fresh.hcl").exists()


@pytest.mark.unit
def test_copy_and_seed_scope_teardown_on_exception(tmp_path: pathlib.Path) -> None:
    """_copy_and_seed_scope cleans up even when the body raises."""
    from scripts.tg_copyability_test import (
        _DNS_OWNER_ACCOUNT_ID,
        _ZONE_ID_PLACEHOLDER,
        _copy_and_seed_scope,
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "test.hcl").write_text("# test", encoding="utf-8")

    accounts_file = tmp_path / "accounts.json"
    data = {_DNS_OWNER_ACCOUNT_ID: {"dns_owner_zone_id": _ZONE_ID_PLACEHOLDER}}
    accounts_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(CopyabilityError), _copy_and_seed_scope(src, dst, [], accounts_file):
        raise CopyabilityError("ERROR: simulated failure")

    # Destination must be cleaned up after the exception
    assert not dst.exists()


@pytest.mark.unit
def test_copy_and_seed_scope_suppress_restore_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_copy_and_seed_scope suppresses CopyabilityError during teardown restore."""
    import scripts.tg_copyability_test as mod
    from scripts.tg_copyability_test import (
        _DNS_OWNER_ACCOUNT_ID,
        _ZONE_ID_PLACEHOLDER,
        _copy_and_seed_scope,
        _SeedSpec,
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "test.hcl").write_text("# test", encoding="utf-8")

    accounts_file = tmp_path / "accounts.json"
    data = {_DNS_OWNER_ACCOUNT_ID: {"dns_owner_zone_id": _ZONE_ID_PLACEHOLDER}}
    accounts_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps({}, indent=2) + "\n", encoding="utf-8")
    specs = [_SeedSpec(json_file=seed_file, key="cpytst-key2", value={"v": 1})]

    # Patch _restore_common_row to raise CopyabilityError on teardown
    call_count = {"n": 0}

    def patched_restore(json_file: pathlib.Path, key: str, parent_key: str | None = None) -> None:
        call_count["n"] += 1
        raise CopyabilityError("ERROR: simulated restore failure")

    monkeypatch.setattr(mod, "_restore_common_row", patched_restore)

    # Should not propagate the restore error
    with _copy_and_seed_scope(src, dst, specs, accounts_file):
        pass

    # Confirm the restore was attempted
    assert call_count["n"] == 1


@pytest.mark.unit
def test_restore_dns_owner_zone_id_noop_when_account_absent(tmp_path: pathlib.Path) -> None:
    """_restore_dns_owner_zone_id does nothing when dns-owner account is not in the file."""
    from scripts.tg_copyability_test import _restore_dns_owner_zone_id

    accounts_file = tmp_path / "accounts.json"
    original_data = {"other_account": {"role": "test"}}
    accounts_file.write_text(json.dumps(original_data, indent=2), encoding="utf-8")

    # Should not raise even though the DNS owner account is missing
    _restore_dns_owner_zone_id(accounts_file, "<REAL_Z_ID>")

    # Content should be unchanged
    result = json.loads(accounts_file.read_text(encoding="utf-8"))
    assert result == original_data


# ---------------------------------------------------------------------------
# cloudfront.json region-keyed seeding (collector-ingestion v1.0.4 input)
# ---------------------------------------------------------------------------


def _make_collector_leaf(
    root: pathlib.Path,
    region: str,
    env_class: str = "prod",
    env_index: str = "000",
    svc_instance: str = "000",
) -> pathlib.Path:
    """Create a minimal collector-ingestion service-instance leaf (no-account layout).

    Returns the region directory (live/<product>/<region>) so it can be used as a copy
    source scope, mirroring the real live-tree layout the seeders walk.
    """
    region_dir = root / "live" / "telemetry" / region
    leaf = region_dir / env_class / env_index / "collector-ingestion" / svc_instance
    leaf.mkdir(parents=True, exist_ok=True)
    (leaf / "terragrunt.hcl").write_text("# collector-ingestion leaf\n", encoding="utf-8")
    return region_dir


@pytest.mark.unit
def test_region_segment_from_leaf_positional() -> None:
    """_region_segment_from_leaf returns the region (second component below 'live')."""
    from scripts.tg_copyability_test import _region_segment_from_leaf

    leaf = pathlib.Path(
        "/repo/terragrunt/live/telemetry/eu-west-1/prod/000/collector-ingestion/000"
    )
    assert _region_segment_from_leaf(leaf) == "eu-west-1"


@pytest.mark.unit
def test_region_segment_from_leaf_no_live_raises() -> None:
    """A path without a 'live' segment raises CopyabilityError."""
    from scripts.tg_copyability_test import _region_segment_from_leaf

    with pytest.raises(CopyabilityError, match="ERROR"):
        _region_segment_from_leaf(pathlib.Path("/a/b/c"))


@pytest.mark.unit
def test_region_segment_from_leaf_too_short_raises() -> None:
    """A path with too few components below 'live' raises CopyabilityError."""
    from scripts.tg_copyability_test import _region_segment_from_leaf

    with pytest.raises(CopyabilityError, match="ERROR"):
        _region_segment_from_leaf(pathlib.Path("/x/live/telemetry"))


@pytest.mark.unit
def test_existing_top_level_keys_absent_and_present(tmp_path: pathlib.Path) -> None:
    """_existing_top_level_keys returns empty for an absent file and the key set otherwise."""
    from scripts.tg_copyability_test import _existing_top_level_keys

    missing = tmp_path / "nope.json"
    assert _existing_top_level_keys(missing) == set()

    present = tmp_path / "cloudfront.json"
    present.write_text(json.dumps({"us-east-1": "pl-3b927c52"}, indent=2), encoding="utf-8")
    assert _existing_top_level_keys(present) == {"us-east-1"}


@pytest.mark.unit
def test_existing_top_level_keys_malformed_raises(tmp_path: pathlib.Path) -> None:
    """Malformed JSON raises CopyabilityError (fail fast, no silent fallback)."""
    from scripts.tg_copyability_test import _existing_top_level_keys

    bad = tmp_path / "cloudfront.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(CopyabilityError, match="malformed JSON"):
        _existing_top_level_keys(bad)


@pytest.mark.unit
def test_cloudfront_seed_specs_seeds_changed_region(tmp_path: pathlib.Path) -> None:
    """A copy that changes the region seeds a cloudfront.json row at the new region key."""
    from scripts.tg_copyability_test import (
        _CLOUDFRONT_JSON_NAME,
        _SYNTHETIC_CLOUDFRONT_PREFIX_LIST_ID,
        _cloudfront_seed_specs,
    )

    src_region_dir = _make_collector_leaf(tmp_path / "src", "us-east-1")
    dst_region_dir = tmp_path / "src" / "live" / "telemetry" / "eu-west-1"

    common_dir = tmp_path / "common"
    common_dir.mkdir()
    (common_dir / _CLOUDFRONT_JSON_NAME).write_text(
        json.dumps({"us-east-1": "pl-3b927c52"}, indent=2), encoding="utf-8"
    )

    specs = _cloudfront_seed_specs(src_region_dir, dst_region_dir, common_dir)

    assert len(specs) == 1, f"expected exactly one seed row, got {specs}"
    spec = specs[0]
    assert spec.key == "eu-west-1"
    assert spec.value == _SYNTHETIC_CLOUDFRONT_PREFIX_LIST_ID
    assert spec.json_file == common_dir / _CLOUDFRONT_JSON_NAME


@pytest.mark.unit
def test_cloudfront_seed_specs_skips_unchanged_region(tmp_path: pathlib.Path) -> None:
    """A copy that keeps the source region seeds nothing (the region row already exists)."""
    from scripts.tg_copyability_test import _CLOUDFRONT_JSON_NAME, _cloudfront_seed_specs

    src_region_dir = _make_collector_leaf(tmp_path / "src", "us-east-1")
    # Destination keeps the same region (e.g. an env-index / service-instance level copy).
    dst_region_dir = src_region_dir

    common_dir = tmp_path / "common"
    common_dir.mkdir()
    (common_dir / _CLOUDFRONT_JSON_NAME).write_text(
        json.dumps({"us-east-1": "pl-3b927c52"}, indent=2), encoding="utf-8"
    )

    specs = _cloudfront_seed_specs(src_region_dir, dst_region_dir, common_dir)
    assert specs == [], "no row should be seeded when the region is already present"


@pytest.mark.unit
def test_cloudfront_seed_specs_no_collector_unit(tmp_path: pathlib.Path) -> None:
    """A scope with no collector-ingestion unit seeds nothing."""
    from scripts.tg_copyability_test import _CLOUDFRONT_JSON_NAME, _cloudfront_seed_specs

    # Build a non-collector leaf under a region dir.
    region_dir = tmp_path / "src" / "live" / "telemetry" / "us-east-1"
    other_leaf = region_dir / "prod" / "000" / "data-lake" / "000"
    other_leaf.mkdir(parents=True)
    (other_leaf / "terragrunt.hcl").write_text("# data-lake\n", encoding="utf-8")

    dst_region_dir = tmp_path / "src" / "live" / "telemetry" / "eu-west-1"
    common_dir = tmp_path / "common"
    common_dir.mkdir()
    (common_dir / _CLOUDFRONT_JSON_NAME).write_text(
        json.dumps({"us-east-1": "pl-3b927c52"}, indent=2), encoding="utf-8"
    )

    specs = _cloudfront_seed_specs(region_dir, dst_region_dir, common_dir)
    assert specs == [], "non-VPC unit must not trigger a cloudfront.json seed"


@pytest.mark.unit
def test_cloudfront_seed_specs_dedupes_multiple_collector_leaves(tmp_path: pathlib.Path) -> None:
    """Multiple collector-ingestion leaves mapping to the same new region seed one row."""
    from scripts.tg_copyability_test import _CLOUDFRONT_JSON_NAME, _cloudfront_seed_specs

    src_region_dir = _make_collector_leaf(tmp_path / "src", "us-east-1", env_class="prod")
    # A second collector-ingestion leaf in the same region (e.g. sandbox subtree).
    _make_collector_leaf(tmp_path / "src", "us-east-1", env_class="sandbox")
    dst_region_dir = tmp_path / "src" / "live" / "telemetry" / "ap-southeast-2"

    common_dir = tmp_path / "common"
    common_dir.mkdir()
    (common_dir / _CLOUDFRONT_JSON_NAME).write_text(
        json.dumps({"us-east-1": "pl-3b927c52"}, indent=2), encoding="utf-8"
    )

    specs = _cloudfront_seed_specs(src_region_dir, dst_region_dir, common_dir)
    assert len(specs) == 1, f"two leaves -> one region -> one seed, got {specs}"
    assert specs[0].key == "ap-southeast-2"


@pytest.mark.unit
def test_seed_common_row_accepts_scalar_string(tmp_path: pathlib.Path) -> None:
    """_seed_common_row writes a scalar string value (cloudfront.json region mapping)."""
    json_file = tmp_path / "cloudfront.json"
    json_file.write_text(json.dumps({"us-east-1": "pl-3b927c52"}, indent=2), encoding="utf-8")

    _seed_common_row(json_file, "eu-west-1", "pl-0a0a0a0a")

    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["eu-west-1"] == "pl-0a0a0a0a"
    assert data["us-east-1"] == "pl-3b927c52"
