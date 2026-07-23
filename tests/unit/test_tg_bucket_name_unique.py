"""Unit tests for scripts/tg_bucket_name_unique.py.

Implements the docs/release-pipeline.md per-guard pytest matrix:
  - all unique -> pass
  - collision -> non-zero
  - > 63 chars -> non-zero
  - hash-suffix scheme stays at or under 63 chars
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from scripts.tg_bucket_name_unique import (
    MAX_BUCKET_NAME_LENGTH,
    BucketNameCollisionError,
    BucketNameTooLongError,
    assert_bucket_names_unique,
    shorten_bucket_name,
)

# ---------------------------------------------------------------------------
# assert_bucket_names_unique tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_bucket_names_unique_passes_for_distinct_names() -> None:
    """A list of distinct bucket names passes without error."""
    names = [
        "123456789012-telemetry-useast1-prod-000-svc-001-tfstate",
        "123456789012-telemetry-useast1-prod-000-svc-002-tfstate",
        "999999999999-telemetry-useast1-prod-000-svc-001-tfstate",
    ]
    assert_bucket_names_unique(names)
    # No exception means pass


@pytest.mark.unit
def test_assert_bucket_names_unique_raises_for_collision() -> None:
    """Duplicate bucket names raise BucketNameCollisionError."""
    duplicate = "123456789012-telemetry-useast1-prod-000-svc-001-tfstate"
    names = [duplicate, duplicate]
    with pytest.raises(BucketNameCollisionError, match=duplicate):
        assert_bucket_names_unique(names)


@pytest.mark.unit
def test_assert_bucket_names_unique_raises_for_name_over_63_chars() -> None:
    """A bucket name longer than 63 characters raises BucketNameTooLongError."""
    long_name = "a" * 64  # 64 chars -- exceeds the S3 limit
    with pytest.raises(BucketNameTooLongError, match="63"):
        assert_bucket_names_unique([long_name])


@pytest.mark.unit
def test_assert_bucket_names_unique_passes_for_exactly_63_chars() -> None:
    """A bucket name of exactly 63 characters passes."""
    name_63 = "a" * 63
    assert_bucket_names_unique([name_63])
    # No exception means pass


@pytest.mark.unit
def test_assert_bucket_names_unique_raises_for_both_collision_and_length() -> None:
    """Both collision and length errors are caught (length check is first per spec)."""
    long_name = "b" * 64
    names = [long_name, long_name]
    with pytest.raises(BucketNameTooLongError):
        assert_bucket_names_unique(names)


# ---------------------------------------------------------------------------
# shorten_bucket_name tests (hash-suffix scheme stays at or under 63 chars)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_shorten_bucket_name_returns_original_when_under_limit() -> None:
    """shorten_bucket_name returns the raw name unchanged when it is within 63 chars."""
    short_name = "123456789012-short-tfstate"
    result = shorten_bucket_name(
        account_id="123456789012",
        namespace="short",
        raw_name=short_name,
    )
    assert result == short_name


@pytest.mark.unit
def test_shorten_bucket_name_produces_name_at_or_under_63_chars() -> None:
    """shorten_bucket_name always produces a name at or under 63 characters."""
    account_id = "123456789012"
    namespace = "telemetry-useast1-prod-000-collector-ingestion-001"
    raw_name = f"{account_id}-{namespace}-tfstate"
    assert len(raw_name) > MAX_BUCKET_NAME_LENGTH

    result = shorten_bucket_name(
        account_id=account_id,
        namespace=namespace,
        raw_name=raw_name,
    )
    assert len(result) <= MAX_BUCKET_NAME_LENGTH, (
        f"shorten_bucket_name produced a name of {len(result)} chars, "
        f"exceeding the {MAX_BUCKET_NAME_LENGTH}-char S3 limit: {result}"
    )


@pytest.mark.unit
def test_shorten_bucket_name_includes_hash_suffix_for_long_names() -> None:
    """shorten_bucket_name includes an md5-derived hash suffix for long names."""
    account_id = "123456789012"
    namespace = "telemetry-useast1-prod-000-collector-ingestion-long-suffix"
    raw_name = f"{account_id}-{namespace}-tfstate"
    assert len(raw_name) > MAX_BUCKET_NAME_LENGTH

    result = shorten_bucket_name(
        account_id=account_id,
        namespace=namespace,
        raw_name=raw_name,
    )
    expected_hash = hashlib.md5(namespace.encode()).hexdigest()[:8]
    assert expected_hash in result, (
        f"Expected md5 hash suffix '{expected_hash}' in shortened name: {result}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "namespace",
    [
        "telemetry-useast1-prod-000-acm-validate-collector",
        "telemetry-useast1-prod-000-acm-validate-portal",
        "telemetry-useast1-prod-000-collector-ingestion-001",
        "telemetry-useast1-staging-000-analytics-portal-webapp",
    ],
)
def test_shorten_bucket_name_stays_under_63_for_known_namespaces(namespace: str) -> None:
    """shorten_bucket_name stays at or under 63 chars for known long namespace patterns."""
    account_id = "123456789012"
    raw_name = f"{account_id}-{namespace}-tfstate"
    result = shorten_bucket_name(
        account_id=account_id,
        namespace=namespace,
        raw_name=raw_name,
    )
    assert len(result) <= MAX_BUCKET_NAME_LENGTH, (
        f"Name too long ({len(result)} chars) for namespace '{namespace}': {result}"
    )


# ---------------------------------------------------------------------------
# collect_bucket_names and run_bucket_name_guard tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_bucket_names_derives_names_for_leaf_units(tmp_path: pathlib.Path) -> None:
    """collect_bucket_names returns (hcl_path, bucket_name) for each leaf unit."""
    from scripts.tg_bucket_name_unique import collect_bucket_names

    unit_dir = tmp_path / "live" / "env" / "123456789012" / "svc" / "001"
    unit_dir.mkdir(parents=True)
    account_hcl = unit_dir.parent.parent / "account.hcl"
    account_hcl.write_text('locals {\n  aws_account_id = "123456789012"\n}\n', encoding="utf-8")
    (unit_dir / "terragrunt.hcl").write_text("# unit\n", encoding="utf-8")

    results = collect_bucket_names(terragrunt_root=tmp_path)
    assert len(results) >= 1
    hcl_paths = [str(r[0]) for r in results]
    assert any("terragrunt.hcl" in p for p in hcl_paths)


@pytest.mark.unit
def test_run_bucket_name_guard_returns_empty_for_unique_names(tmp_path: pathlib.Path) -> None:
    """run_bucket_name_guard returns empty list when all names are unique."""
    from scripts.tg_bucket_name_unique import run_bucket_name_guard

    for i in range(3):
        unit_dir = tmp_path / "live" / "env" / f"acct{i}" / "svc" / "001"
        unit_dir.mkdir(parents=True)
        account_hcl = unit_dir.parent.parent / "account.hcl"
        account_hcl.write_text(
            f'locals {{\n  aws_account_id = "12345678901{i}"\n}}\n', encoding="utf-8"
        )
        (unit_dir / "terragrunt.hcl").write_text("# unit\n", encoding="utf-8")

    errors = run_bucket_name_guard(terragrunt_root=tmp_path)
    assert errors == []


@pytest.mark.unit
def test_main_returns_0_for_unique_names(tmp_path: pathlib.Path) -> None:
    """main() returns 0 when all bucket names are unique and within limit."""
    import os

    from scripts.tg_bucket_name_unique import main as bucket_main

    unit_dir = tmp_path / "live" / "env" / "acct" / "svc" / "001"
    unit_dir.mkdir(parents=True)
    (unit_dir / "terragrunt.hcl").write_text("# unit\n", encoding="utf-8")

    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = bucket_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 0


@pytest.mark.unit
def test_find_account_id_in_ancestors_finds_parent_account_hcl(tmp_path: pathlib.Path) -> None:
    """_find_account_id_in_ancestors walks up to find account.hcl."""
    from scripts.tg_bucket_name_unique import _find_account_id_in_ancestors

    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = account_dir / "account.hcl"
    account_hcl.write_text('locals {\n  aws_account_id = "123456789012"\n}\n', encoding="utf-8")
    unit_dir = account_dir / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl_file = unit_dir / "terragrunt.hcl"
    hcl_file.write_text("# unit\n", encoding="utf-8")

    account_id = _find_account_id_in_ancestors(hcl_file)
    assert account_id == "123456789012"


@pytest.mark.unit
def test_find_account_id_in_ancestors_returns_none_when_not_found(tmp_path: pathlib.Path) -> None:
    """_find_account_id_in_ancestors returns None when no account.hcl is in the tree."""
    from scripts.tg_bucket_name_unique import _find_account_id_in_ancestors

    unit_dir = tmp_path / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl_file = unit_dir / "terragrunt.hcl"
    hcl_file.write_text("# unit\n", encoding="utf-8")

    account_id = _find_account_id_in_ancestors(hcl_file)
    assert account_id is None


@pytest.mark.unit
def test_main_returns_1_for_collision(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when two units produce the same bucket name."""
    import os

    import scripts.tg_bucket_name_unique as mod

    # Mock collect_bucket_names to return a collision.
    colliding_name = "123456789012-same-name-tfstate"

    def mock_collect(terragrunt_root: pathlib.Path) -> list:
        return [
            (tmp_path / "unit1" / "terragrunt.hcl", colliding_name),
            (tmp_path / "unit2" / "terragrunt.hcl", colliding_name),
        ]

    monkeypatch.setattr(mod, "collect_bucket_names", mock_collect)

    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    (tmp_path / "dummy").mkdir(parents=True, exist_ok=True)
    try:
        result = mod.main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1
