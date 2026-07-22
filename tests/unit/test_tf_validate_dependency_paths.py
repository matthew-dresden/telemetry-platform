"""Unit tests for scripts/tf_validate_dependency_paths.py.

Implements the docs/release-pipeline.md per-guard pytest matrix:
  - all paths resolve -> pass
  - one unresolvable path -> exit non-zero
  - nested ../../ relative paths resolve
"""

from __future__ import annotations

import os
import pathlib

import pytest

from scripts.tf_validate_dependency_paths import (
    UnresolvableDependencyError,
    validate_all_dependencies,
    validate_dependency_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_unit(base: pathlib.Path, *parts: str) -> pathlib.Path:
    """Create a unit directory with a terragrunt.hcl file."""
    unit_dir = base.joinpath(*parts)
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "terragrunt.hcl").write_text("# unit\n", encoding="utf-8")
    return unit_dir


def _write_dep_hcl(unit_dir: pathlib.Path, dep_path: str) -> None:
    """Write a terragrunt.hcl with a single dependency config_path."""
    hcl_content = f'dependency "dep" {{\n  config_path = "{dep_path}"\n}}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# validate_dependency_path tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_dependency_path_passes_for_existing_path(tmp_path: pathlib.Path) -> None:
    """A dependency config_path that resolves to an existing directory passes."""
    dep_unit = _make_unit(tmp_path, "dep", "unit")
    consumer_unit = _make_unit(tmp_path, "consumer", "unit")
    rel_path = os.path.relpath(dep_unit, consumer_unit)

    validate_dependency_path(
        hcl_file=consumer_unit / "terragrunt.hcl",
        raw_config_path=rel_path,
    )
    # No exception raised means pass


@pytest.mark.unit
def test_validate_dependency_path_fails_for_nonexistent_path(tmp_path: pathlib.Path) -> None:
    """A config_path pointing to a nonexistent directory raises UnresolvableDependencyError.

    Validates fail-fast behavior when a dependency target does not exist on disk.
    """
    consumer_unit = _make_unit(tmp_path, "consumer", "unit")
    nonexistent_rel = "../../does/not/exist"

    with pytest.raises(UnresolvableDependencyError, match="does/not/exist"):
        validate_dependency_path(
            hcl_file=consumer_unit / "terragrunt.hcl",
            raw_config_path=nonexistent_rel,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "dep_depth,consumer_depth",
    [
        (("a", "b", "networking", "001"), ("a", "b", "app", "001")),
        (("x", "y", "z", "base", "001"), ("x", "y", "z", "overlay", "002")),
    ],
)
def test_validate_dependency_path_resolves_nested_relative_paths(
    tmp_path: pathlib.Path,
    dep_depth: tuple,
    consumer_depth: tuple,
) -> None:
    """Nested ../../ relative dependency paths are resolved correctly."""
    dep_unit = _make_unit(tmp_path, *dep_depth)
    consumer_unit = _make_unit(tmp_path, *consumer_depth)
    rel_path = os.path.relpath(dep_unit, consumer_unit)

    validate_dependency_path(
        hcl_file=consumer_unit / "terragrunt.hcl",
        raw_config_path=rel_path,
    )
    # No exception raised means pass


# ---------------------------------------------------------------------------
# Interpolated config_path tests (instance-relative tree, spec section 4.4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "token",
    [
        "${local.svc_instance}",
        "${local.env_active}",
        "${local.dns_prod_zone_active}",
        "${local.acm_portal_active}",
        "${local.acm_collector_active}",
    ],
)
def test_validate_dependency_path_passes_for_interpolated_token_with_matching_instance(
    tmp_path: pathlib.Path,
    token: str,
) -> None:
    """An interpolated config_path passes when a concrete sibling instance dir exists.

    The token (resolved to an instance basename at Terragrunt parse time) is replaced
    with a single-segment glob wildcard; the check passes because the "000" sibling
    instance directory matches.
    """
    # A real sibling instance directory the interpolation would resolve to.
    _make_unit(tmp_path, "live", "env", "acct", "dep-svc", "000")
    consumer_unit = _make_unit(tmp_path, "live", "env", "acct", "consumer-svc", "000")

    validate_dependency_path(
        hcl_file=consumer_unit / "terragrunt.hcl",
        raw_config_path=f"../../dep-svc/{token}",
    )
    # No exception raised means pass (glob matched the 000 instance).


@pytest.mark.unit
def test_validate_dependency_path_fails_for_interpolated_token_with_no_matching_dir(
    tmp_path: pathlib.Path,
) -> None:
    """An interpolated config_path fails when the target directory does not exist.

    The glob substitution must still fail closed when the dependency target directory
    (the segment BEFORE the interpolated instance) is absent entirely.
    """
    consumer_unit = _make_unit(tmp_path, "live", "env", "acct", "consumer-svc", "000")

    with pytest.raises(UnresolvableDependencyError, match="No sibling instance directory"):
        validate_dependency_path(
            hcl_file=consumer_unit / "terragrunt.hcl",
            raw_config_path="../../missing-dep-svc/${local.svc_instance}",
        )


# ---------------------------------------------------------------------------
# validate_all_dependencies tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_all_dependencies_passes_when_all_resolve(tmp_path: pathlib.Path) -> None:
    """validate_all_dependencies passes when all dependency paths resolve."""
    dep_unit = _make_unit(tmp_path, "live", "env", "acct", "vpc", "001")
    consumer_unit = _make_unit(tmp_path, "live", "env", "acct", "app", "001")
    rel_path = os.path.relpath(dep_unit, consumer_unit)
    _write_dep_hcl(consumer_unit, rel_path)

    validate_all_dependencies(terragrunt_root=tmp_path)
    # No exception raised means pass


@pytest.mark.unit
def test_validate_all_dependencies_fails_for_one_unresolvable(tmp_path: pathlib.Path) -> None:
    """validate_all_dependencies raises when one dependency path is unresolvable."""
    consumer_unit = _make_unit(tmp_path, "live", "env", "acct", "app", "001")
    _write_dep_hcl(consumer_unit, "../../nonexistent/unit")

    with pytest.raises(UnresolvableDependencyError):
        validate_all_dependencies(terragrunt_root=tmp_path)


@pytest.mark.unit
def test_validate_all_dependencies_passes_with_no_dependencies(tmp_path: pathlib.Path) -> None:
    """validate_all_dependencies passes when no unit has any dependency."""
    unit = _make_unit(tmp_path, "live", "env", "acct", "svc", "001")
    (unit / "terragrunt.hcl").write_text("# no deps\n", encoding="utf-8")

    validate_all_dependencies(terragrunt_root=tmp_path)
    # No exception raised means pass


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_0_for_valid_tree(tmp_path: pathlib.Path) -> None:
    """main() returns 0 when all dependency paths resolve."""
    import os

    from scripts.tf_validate_dependency_paths import main as dep_main

    dep_unit = _make_unit(tmp_path, "live", "env", "acct", "vpc", "001")
    consumer_unit = _make_unit(tmp_path, "live", "env", "acct", "app", "001")
    rel_path = os.path.relpath(dep_unit, consumer_unit)
    _write_dep_hcl(consumer_unit, rel_path)

    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = dep_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 0


@pytest.mark.unit
def test_main_returns_1_for_invalid_dependency(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when a dependency path does not resolve."""
    import os

    from scripts.tf_validate_dependency_paths import main as dep_main

    consumer_unit = _make_unit(tmp_path, "live", "env", "acct", "app", "001")
    _write_dep_hcl(consumer_unit, "../../nonexistent/unit")

    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = dep_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1


@pytest.mark.unit
def test_main_returns_1_when_no_terragrunt_root_set() -> None:
    """main() returns 1 when TG_LIVE_ROOT is not set and no default root exists."""
    import os

    import scripts.tf_validate_dependency_paths as mod

    original_env = os.environ.pop("TG_LIVE_ROOT", None)
    original_default_fn = mod._get_default_terragrunt_root

    def mock_get_root():
        return None

    mod._get_default_terragrunt_root = mock_get_root
    try:
        result = mod.main()
    finally:
        mod._get_default_terragrunt_root = original_default_fn
        if original_env is not None:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1
