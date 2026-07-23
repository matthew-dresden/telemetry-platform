"""Unit tests for scripts/detect_terragrunt_units.py.

Implements the docs/release-pipeline.md per-guard pytest matrix for detect_terragrunt_units.
Tests assert that the script:
  (a) maps a changed file to its enclosing unit dir,
  (b) de-duplicates multiple changed files that resolve to the same unit,
  (c) includes dependent units from the dependency DAG,
  (d) emits the exact '--queue-include-dir <unit>' flag string (never --terragrunt-include-dir),
  (e) aborts non-zero on empty unit scope (D3, AC-5).
"""

from __future__ import annotations

import pathlib

import pytest

from scripts.detect_terragrunt_units import (
    EmptyUnitScopeError,
    ForbiddenTokenError,
    _is_shared_parent_config,
    _resolve_in_parent_folders,
    build_include_dir_flags,
    detect_unit_dir,
    find_dependent_units,
    find_units_affected_by_parent_config,
    parse_dependency_paths,
    resolve_parent_config_references,
)

# ---------------------------------------------------------------------------
# Helpers to build fake terragrunt directory trees in tmp_path
# ---------------------------------------------------------------------------


def _make_unit(base: pathlib.Path, *parts: str) -> pathlib.Path:
    """Create a unit directory containing a terragrunt.hcl file."""
    unit_dir = base.joinpath(*parts)
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "terragrunt.hcl").write_text("# unit\n", encoding="utf-8")
    return unit_dir


def _make_unit_with_dependency(
    base: pathlib.Path, unit_parts: tuple, dep_parts: tuple
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a unit that declares a dependency on another unit.

    The config_path written to the unit's terragrunt.hcl is the relative path
    from the unit directory to the dep directory, computed using os.path.relpath.
    """
    dep_dir = _make_unit(base, *dep_parts)
    unit_dir = _make_unit(base, *unit_parts)
    import os

    rel_dep = os.path.relpath(dep_dir, unit_dir)
    hcl_content = f'dependency "dep" {{\n  config_path = "{rel_dep}"\n}}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl_content, encoding="utf-8")
    return unit_dir, dep_dir


# ---------------------------------------------------------------------------
# detect_unit_dir -- maps a changed file to its enclosing unit dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_unit_dir_maps_file_to_enclosing_unit(tmp_path: pathlib.Path) -> None:
    """A changed file inside a unit dir is mapped to that unit dir."""
    unit_dir = _make_unit(tmp_path, "live", "env", "account", "service", "001")
    changed_file = unit_dir / "some_file.tf"
    changed_file.write_text("# changed\n", encoding="utf-8")

    result = detect_unit_dir(changed_file, terragrunt_root=tmp_path)
    assert result == unit_dir


@pytest.mark.unit
def test_detect_unit_dir_maps_hcl_file_itself(tmp_path: pathlib.Path) -> None:
    """A changed terragrunt.hcl is mapped to its parent (the unit dir)."""
    unit_dir = _make_unit(tmp_path, "live", "env", "account", "service", "001")
    hcl_file = unit_dir / "terragrunt.hcl"

    result = detect_unit_dir(hcl_file, terragrunt_root=tmp_path)
    assert result == unit_dir


@pytest.mark.unit
def test_detect_unit_dir_returns_none_for_file_outside_any_unit(tmp_path: pathlib.Path) -> None:
    """A file in a non-unit directory returns None."""
    non_unit_dir = tmp_path / "not-a-unit"
    non_unit_dir.mkdir(parents=True)
    changed_file = non_unit_dir / "some_file.txt"
    changed_file.write_text("# not a unit\n", encoding="utf-8")

    result = detect_unit_dir(changed_file, terragrunt_root=tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# parse_dependency_paths -- extracts config_path values from terragrunt.hcl
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_dependency_paths_finds_single_dependency(tmp_path: pathlib.Path) -> None:
    """parse_dependency_paths returns the config_path for a single dependency."""
    hcl_content = 'dependency "vpc" {\n  config_path = "../../networking/vpc"\n}\n'
    hcl_file = tmp_path / "terragrunt.hcl"
    hcl_file.write_text(hcl_content, encoding="utf-8")

    paths = parse_dependency_paths(hcl_file)
    assert paths == ["../../networking/vpc"]


@pytest.mark.unit
def test_parse_dependency_paths_finds_multiple_dependencies(tmp_path: pathlib.Path) -> None:
    """parse_dependency_paths returns all config_path values."""
    hcl_content = (
        'dependency "vpc" {\n'
        '  config_path = "../networking/vpc"\n'
        "}\n"
        'dependency "iam" {\n'
        '  config_path = "../iam/roles"\n'
        "}\n"
    )
    hcl_file = tmp_path / "terragrunt.hcl"
    hcl_file.write_text(hcl_content, encoding="utf-8")

    paths = parse_dependency_paths(hcl_file)
    assert set(paths) == {"../networking/vpc", "../iam/roles"}


@pytest.mark.unit
def test_parse_dependency_paths_returns_empty_for_no_dependencies(tmp_path: pathlib.Path) -> None:
    """parse_dependency_paths returns an empty list when there are no dependencies."""
    hcl_content = "# no deps\ninputs = {}\n"
    hcl_file = tmp_path / "terragrunt.hcl"
    hcl_file.write_text(hcl_content, encoding="utf-8")

    paths = parse_dependency_paths(hcl_file)
    assert paths == []


# ---------------------------------------------------------------------------
# find_dependent_units -- includes units that depend on a changed unit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_dependent_units_includes_units_depending_on_changed(tmp_path: pathlib.Path) -> None:
    """find_dependent_units returns units that declare a dep on the changed unit."""
    import os

    changed_unit = _make_unit(tmp_path, "live", "env", "account", "vpc", "001")
    dependent_unit = _make_unit(tmp_path, "live", "env", "account", "app", "001")
    rel_path = os.path.relpath(changed_unit, dependent_unit)
    hcl_content = f'dependency "vpc" {{\n  config_path = "{rel_path}"\n}}\n'
    (dependent_unit / "terragrunt.hcl").write_text(hcl_content, encoding="utf-8")

    dependents = find_dependent_units(
        changed_units={changed_unit},
        terragrunt_root=tmp_path,
    )
    assert dependent_unit in dependents


@pytest.mark.unit
def test_find_dependent_units_returns_empty_when_no_dependents(tmp_path: pathlib.Path) -> None:
    """find_dependent_units returns an empty set when no other unit depends on changed."""
    changed_unit = _make_unit(tmp_path, "live", "env", "account", "vpc", "001")
    dependents = find_dependent_units(
        changed_units={changed_unit},
        terragrunt_root=tmp_path,
    )
    assert dependents == set()


# ---------------------------------------------------------------------------
# build_include_dir_flags -- emits --queue-include-dir tokens (AC-5, D35)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "unit_dirs,expected_tokens",
    [
        (
            [pathlib.Path("/repo/terragrunt/live/env/acct/svc/001")],
            ["--queue-include-dir", "/repo/terragrunt/live/env/acct/svc/001"],
        ),
        (
            [
                pathlib.Path("/repo/terragrunt/live/env/acct/svc/001"),
                pathlib.Path("/repo/terragrunt/live/env/acct/svc/002"),
            ],
            [
                "--queue-include-dir",
                "/repo/terragrunt/live/env/acct/svc/001",
                "--queue-include-dir",
                "/repo/terragrunt/live/env/acct/svc/002",
            ],
        ),
    ],
)
def test_build_include_dir_flags_uses_queue_include_dir(
    unit_dirs: list[pathlib.Path], expected_tokens: list[str]
) -> None:
    """build_include_dir_flags emits --queue-include-dir tokens (never --terragrunt-include-dir)."""
    flags = build_include_dir_flags(unit_dirs)
    assert "--terragrunt-include-dir" not in flags, (
        "build_include_dir_flags must not emit the removed --terragrunt-include-dir token (D35)."
    )
    for token in expected_tokens:
        assert token in flags


@pytest.mark.unit
def test_build_include_dir_flags_never_emits_terragrunt_include_dir(
    tmp_path: pathlib.Path,
) -> None:
    """The removed --terragrunt-include-dir token must never appear in output (D35, AC-5)."""
    unit_dirs = [tmp_path / "live" / "env" / "acct" / "svc" / "001"]
    flags = build_include_dir_flags(unit_dirs)
    assert "--terragrunt-include-dir" not in flags, (
        "The removed --terragrunt-include-dir token was emitted. "
        "Only --queue-include-dir is valid in Terragrunt 1.0.7 (D35, AC-5)."
    )


@pytest.mark.unit
def test_build_include_dir_flags_raises_on_empty_unit_list() -> None:
    """build_include_dir_flags raises EmptyUnitScopeError on empty input (D3)."""
    with pytest.raises(EmptyUnitScopeError):
        build_include_dir_flags([])


@pytest.mark.unit
def test_forbidden_token_error_exists() -> None:
    """ForbiddenTokenError is importable (guards against using removed CLI tokens)."""
    assert issubclass(ForbiddenTokenError, RuntimeError)


# ---------------------------------------------------------------------------
# De-duplication -- multiple changed files in the same unit are counted once
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_unit_dir_deduplication_via_set(tmp_path: pathlib.Path) -> None:
    """Multiple changed files in the same unit map to the same unit dir (de-dup by set)."""
    unit_dir = _make_unit(tmp_path, "live", "env", "account", "service", "001")
    file_a = unit_dir / "a.tf"
    file_b = unit_dir / "b.tf"
    file_a.write_text("# a\n", encoding="utf-8")
    file_b.write_text("# b\n", encoding="utf-8")

    units = {
        detect_unit_dir(f, terragrunt_root=tmp_path)
        for f in [file_a, file_b]
        if detect_unit_dir(f, terragrunt_root=tmp_path) is not None
    }
    assert units == {unit_dir}, (
        "Multiple files in the same unit must resolve to a single unit dir entry."
    )


# ---------------------------------------------------------------------------
# Nested ../../ relative dependency resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_dependent_units_resolves_nested_relative_paths(tmp_path: pathlib.Path) -> None:
    """find_dependent_units resolves ../../ style relative dependency paths correctly."""
    dep_unit = _make_unit(tmp_path, "live", "env", "account", "networking", "001")
    consumer_unit, _ = _make_unit_with_dependency(
        tmp_path,
        ("live", "env", "account", "app", "001"),
        ("live", "env", "account", "networking", "001"),
    )
    # The dependency path in consumer_unit/terragrunt.hcl is relative from the consumer.
    # find_dependent_units must resolve it to dep_unit's absolute path.
    dependents = find_dependent_units(
        changed_units={dep_unit},
        terragrunt_root=tmp_path,
    )
    assert consumer_unit in dependents, (
        f"Expected {consumer_unit} to be a dependent of {dep_unit} via relative path. "
        f"Got: {dependents}"
    )


# ---------------------------------------------------------------------------
# main() entry point tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_1_for_missing_terragrunt_root(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when --terragrunt-root does not exist."""
    import sys

    from scripts.detect_terragrunt_units import main as detect_main

    missing_root = str(tmp_path / "does_not_exist")
    original_argv = sys.argv
    try:
        sys.argv = [
            "detect_terragrunt_units",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--terragrunt-root",
            missing_root,
            "--output",
            str(tmp_path / "out"),
        ]
        result = detect_main()
    finally:
        sys.argv = original_argv
    assert result == 1


@pytest.mark.unit
def test_run_detect_writes_flags_to_output(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_detect writes include_dir_flags to the output file."""
    from scripts.detect_terragrunt_units import run_detect

    unit_dir = tmp_path / "live" / "env" / "acct" / "svc" / "001"
    unit_dir.mkdir(parents=True)
    (unit_dir / "terragrunt.hcl").write_text("# unit\n", encoding="utf-8")

    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    changed_file = unit_dir / "main.tf"
    changed_file.write_text("# changed\n", encoding="utf-8")

    import scripts.detect_terragrunt_units as mod

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [changed_file]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)

    run_detect(
        base_ref="origin/main",
        head_ref="HEAD",
        terragrunt_root=tmp_path,
        output_path=str(output_file),
    )

    content = output_file.read_text(encoding="utf-8")
    assert "include_dir_flags" in content
    assert "--queue-include-dir" in content


@pytest.mark.unit
def test_run_detect_raises_empty_scope_when_no_units(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_detect raises EmptyUnitScopeError when no units match the changed files."""
    from scripts.detect_terragrunt_units import EmptyUnitScopeError, run_detect

    # Create root dir but no units.
    (tmp_path / "not_a_unit.txt").write_text("# not a unit\n", encoding="utf-8")
    output_file = tmp_path / "gh_output"

    import scripts.detect_terragrunt_units as mod

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [tmp_path / "not_a_unit.txt"]

    def mock_list_deleted(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        # The changed file was modified (not deleted) -> NOT a deletion-only changeset,
        # so the empty scope must still fail closed (D3).
        return []

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    monkeypatch.setattr(mod, "list_deleted_files", mock_list_deleted)

    with pytest.raises(EmptyUnitScopeError):
        run_detect(
            base_ref="origin/main",
            head_ref="HEAD",
            terragrunt_root=tmp_path,
            output_path=str(output_file),
        )


@pytest.mark.unit
def test_run_detect_deletion_only_is_clean_noop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DELETION-ONLY changeset with no remaining units is a clean no-op, NOT an
    EmptyUnitScopeError: the deleted units' live resources are already destroyed, so there is
    nothing to plan. run_detect emits empty include_dir_flags + has_units=false and returns None."""
    import scripts.detect_terragrunt_units as mod
    from scripts.detect_terragrunt_units import run_detect

    # Two deleted terragrunt.hcl unit definitions: at HEAD no terragrunt.hcl exists for these
    # paths, so detect_unit_dir maps them to nothing -> empty scope.
    deleted_a = tmp_path / "live" / "env" / "acct" / "svc" / "001" / "terragrunt.hcl"
    deleted_b = tmp_path / "live" / "env" / "acct" / "svc" / "002" / "terragrunt.hcl"
    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [deleted_a, deleted_b]

    def mock_list_deleted(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [deleted_a, deleted_b]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    monkeypatch.setattr(mod, "list_deleted_files", mock_list_deleted)

    result = run_detect(
        base_ref="origin/main",
        head_ref="HEAD",
        terragrunt_root=tmp_path,
        output_path=str(output_file),
    )
    assert result is None
    content = output_file.read_text(encoding="utf-8")
    assert "has_units=false" in content
    # Empty include_dir_flags value (the key is written, but no --queue-include-dir tokens).
    assert "include_dir_flags=" in content
    assert "--queue-include-dir" not in content


@pytest.mark.unit
def test_run_detect_mixed_deletion_and_edit_empty_scope_fails_closed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changeset that DELETES some files but also modifies/adds a non-unit file is NOT
    deletion-only, so an empty scope still fails closed (D3): only pure deletions no-op."""
    import scripts.detect_terragrunt_units as mod
    from scripts.detect_terragrunt_units import run_detect

    deleted_unit = tmp_path / "live" / "env" / "acct" / "svc" / "001" / "terragrunt.hcl"
    edited_non_unit = tmp_path / "not_a_unit.txt"
    edited_non_unit.write_text("# edited, not deleted\n", encoding="utf-8")
    output_file = tmp_path / "gh_output"

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [deleted_unit, edited_non_unit]

    def mock_list_deleted(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        # Only the unit file was deleted; the .txt was modified -> mixed -> not deletion-only.
        return [deleted_unit]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    monkeypatch.setattr(mod, "list_deleted_files", mock_list_deleted)

    with pytest.raises(EmptyUnitScopeError):
        run_detect(
            base_ref="origin/main",
            head_ref="HEAD",
            terragrunt_root=tmp_path,
            output_path=str(output_file),
        )


# ---------------------------------------------------------------------------
# --exclude-bootstrap (CI APPLY path): bootstrap units are operator-applied (D40/D33/D-15)
# ---------------------------------------------------------------------------


def _bootstrap_unit(base: pathlib.Path, role: str) -> pathlib.Path:
    """Create a bootstrap unit under bootstrap/<role>_role/oidc-bootstrap/000."""
    return _make_unit(
        base,
        "live",
        "telemetry",
        "us-east-1",
        "bootstrap",
        f"{role}_role",
        "oidc-bootstrap",
        "000",
    )


def _service_unit(base: pathlib.Path, env: str, svc: str) -> pathlib.Path:
    """Create an env service unit at live/telemetry/us-east-1/<env>/000/<svc>/000."""
    return _make_unit(base, "live", "telemetry", "us-east-1", env, "000", svc, "000")


@pytest.mark.unit
def test_exclude_bootstrap_drops_bootstrap_units(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exclude_bootstrap=True drops bootstrap units but keeps service units, and emits
    has_units=true (CI APPLY path)."""
    from scripts.detect_terragrunt_units import run_detect

    boot = _bootstrap_unit(tmp_path, "sandbox")
    svc = _service_unit(tmp_path, "prod", "portal")
    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    import scripts.detect_terragrunt_units as mod

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [boot / "terragrunt.hcl", svc / "terragrunt.hcl"]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)

    run_detect(
        base_ref="origin/main",
        head_ref="HEAD",
        terragrunt_root=tmp_path,
        output_path=str(output_file),
        exclude_bootstrap=True,
    )
    content = output_file.read_text(encoding="utf-8")
    assert "has_units=true" in content
    assert str(svc) in content
    # The bootstrap unit must NOT appear in the apply scope.
    assert str(boot) not in content


@pytest.mark.unit
def test_exclude_bootstrap_only_scope_is_clean_noop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bootstrap-only change yields has_units=false + empty flags (clean CI no-op), NOT an
    EmptyUnitScopeError (the post-merge apply of a bootstrap-touching PR must stay green)."""
    from scripts.detect_terragrunt_units import run_detect

    boot1 = _bootstrap_unit(tmp_path, "sandbox")
    boot2 = _bootstrap_unit(tmp_path, "dns_owner")
    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    import scripts.detect_terragrunt_units as mod

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [boot1 / "terragrunt.hcl", boot2 / "terragrunt.hcl"]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)

    run_detect(
        base_ref="origin/main",
        head_ref="HEAD",
        terragrunt_root=tmp_path,
        output_path=str(output_file),
        exclude_bootstrap=True,
    )
    content = output_file.read_text(encoding="utf-8")
    assert "has_units=false" in content
    # Empty include_dir_flags (no --queue-include-dir tokens).
    assert "--queue-include-dir" not in content


@pytest.mark.unit
def test_plan_path_still_fails_closed_on_bootstrap_only_without_exclude(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without exclude_bootstrap (the PR PLAN path), bootstrap units are STILL planned: a
    bootstrap-only change is a non-empty scope (no EmptyUnitScopeError), preserving WALL-1's
    cross-env bootstrap planning."""
    from scripts.detect_terragrunt_units import run_detect

    boot = _bootstrap_unit(tmp_path, "sandbox")
    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    import scripts.detect_terragrunt_units as mod

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [boot / "terragrunt.hcl"]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)

    run_detect(
        base_ref="origin/main",
        head_ref="HEAD",
        terragrunt_root=tmp_path,
        output_path=str(output_file),
    )
    content = output_file.read_text(encoding="utf-8")
    assert str(boot) in content  # bootstrap unit IS in the plan scope
    assert "has_units" not in content  # has_units only emitted in exclude-bootstrap mode


@pytest.mark.unit
def test_main_returns_1_for_empty_unit_scope(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when no units are found (empty scope, D3)."""
    import sys

    import scripts.detect_terragrunt_units as mod

    (tmp_path / "not_a_unit.txt").write_text("# not a unit\n", encoding="utf-8")
    output_file = tmp_path / "gh_output"

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [tmp_path / "not_a_unit.txt"]

    def mock_list_deleted(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return []

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    monkeypatch.setattr(mod, "list_deleted_files", mock_list_deleted)
    original_argv = sys.argv
    try:
        sys.argv = [
            "detect_terragrunt_units",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--terragrunt-root",
            str(tmp_path),
            "--output",
            str(output_file),
        ]
        result = mod.main()
    finally:
        sys.argv = original_argv
    assert result == 1


@pytest.mark.unit
def test_main_returns_0_for_deletion_only_empty_scope(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 0 (exit 0) for a deletion-only empty scope -- a clean no-op, not the D3
    fail-closed exit 1. Proves the end-to-end deletion-only PR path exits green."""
    import sys

    import scripts.detect_terragrunt_units as mod

    deleted_unit = tmp_path / "live" / "env" / "acct" / "svc" / "001" / "terragrunt.hcl"
    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [deleted_unit]

    def mock_list_deleted(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [deleted_unit]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    monkeypatch.setattr(mod, "list_deleted_files", mock_list_deleted)
    original_argv = sys.argv
    try:
        sys.argv = [
            "detect_terragrunt_units",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--terragrunt-root",
            str(tmp_path),
            "--output",
            str(output_file),
        ]
        result = mod.main()
    finally:
        sys.argv = original_argv
    assert result == 0
    content = output_file.read_text(encoding="utf-8")
    assert "has_units=false" in content
    assert "--queue-include-dir" not in content


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changed", "deleted", "expected"),
    [
        (["a.tf", "b.tf"], ["a.tf", "b.tf"], True),  # every changed file deleted
        (["a.tf"], ["a.tf"], True),  # single deletion
        (["a.tf", "b.tf"], ["a.tf"], False),  # one file only modified -> mixed
        (["a.tf"], [], False),  # modification, no deletions
        ([], [], False),  # empty changeset is never deletion-only
    ],
)
def test_is_deletion_only_changeset(changed: list[str], deleted: list[str], expected: bool) -> None:
    """is_deletion_only_changeset is True iff the changeset is non-empty and every changed file
    was deleted (pure deletions no-op; empty or mixed changesets fail closed)."""
    from scripts.detect_terragrunt_units import is_deletion_only_changeset

    changed_paths = [pathlib.Path("/repo") / name for name in changed]
    deleted_paths = [pathlib.Path("/repo") / name for name in deleted]
    assert is_deletion_only_changeset(changed_paths, deleted_paths) is expected


@pytest.mark.unit
def test_list_deleted_files_returns_only_deletions(tmp_path: pathlib.Path) -> None:
    """list_deleted_files returns only the files removed in the diff range (--diff-filter=D)."""
    import subprocess

    from scripts.detect_terragrunt_units import list_changed_files, list_deleted_files

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    keep = tmp_path / "keep.txt"
    drop = tmp_path / "drop.txt"
    keep.write_text("keep\n", encoding="utf-8")
    drop.write_text("drop\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True
    )
    base_sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # HEAD commit: delete drop.txt, add a new file.
    drop.unlink()
    (tmp_path / "added.txt").write_text("added\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "delete+add"], check=True, capture_output=True
    )

    changed = list_changed_files(base_ref=base_sha, head_ref="HEAD", repo_root=tmp_path)
    deleted = list_deleted_files(base_ref=base_sha, head_ref="HEAD", repo_root=tmp_path)
    assert drop in deleted
    assert (tmp_path / "added.txt") not in deleted
    assert keep not in deleted
    # The deleted set is a strict subset of the changed set (mixed changeset).
    assert set(deleted).issubset(set(changed))
    assert set(deleted) != set(changed)


@pytest.mark.unit
def test_list_changed_files_raises_on_git_failure(tmp_path: pathlib.Path) -> None:
    """list_changed_files raises RuntimeError when git diff fails."""
    from scripts.detect_terragrunt_units import list_changed_files

    # Use a path that is not a git repo to trigger failure.
    with pytest.raises(RuntimeError, match="git diff failed"):
        list_changed_files(
            base_ref="nonexistent-ref",
            head_ref="HEAD",
            repo_root=tmp_path,
        )


@pytest.mark.unit
def test_list_changed_files_returns_paths_for_valid_diff(tmp_path: pathlib.Path) -> None:
    """list_changed_files returns file paths for a valid git diff range."""
    import subprocess

    from scripts.detect_terragrunt_units import list_changed_files

    # Initialize a real git repo so git diff can work.
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "test.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "initial"], check=True, capture_output=True
    )

    # An empty diff (HEAD...HEAD) returns no files.
    result = list_changed_files(base_ref="HEAD", head_ref="HEAD", repo_root=tmp_path)
    assert result == []


# ---------------------------------------------------------------------------
# Parent-config (shared config) detection -- issue #91 blind spot
# ---------------------------------------------------------------------------


# A leaf terragrunt.hcl that consumes shared parent configs through all three
# real reference forms: include "root" + _envcommon include + find_in_parent_folders
# reads + a ${get_terragrunt_dir()}-relative read.
_LEAF_HCL = (
    'include "root" {\n'
    '  path = find_in_parent_folders("root.hcl")\n'
    "}\n"
    'include "envcommon" {\n'
    '  path = "${dirname(find_in_parent_folders("root.hcl"))}/_envcommon/'
    '__COMPONENT__.hcl"\n'
    "}\n"
    "locals {\n"
    '  account_vars = read_terragrunt_config(find_in_parent_folders("account.hcl"))\n'
    '  service_vars = read_terragrunt_config(find_in_parent_folders("service.hcl"))\n'
    '  env_active   = read_terragrunt_config("${get_terragrunt_dir()}/../active.hcl")'
    ".locals.active\n"
    "}\n"
)


def _build_parent_config_tree(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Build a realistic terragrunt tree with shared parent configs + two units.

    Layout (root == terragrunt_root):
        root/root.hcl
        root/_envcommon/collector-ingestion.hcl
        root/_envcommon/portal.hcl
        root/live/env/account/account.hcl
        root/live/env/account/collector-ingestion/service.hcl
        root/live/env/account/collector-ingestion/active.hcl
        root/live/env/account/collector-ingestion/000/terragrunt.hcl  (reads them all)
        root/live/env/account/portal/service.hcl
        root/live/env/account/portal/active.hcl
        root/live/env/account/portal/000/terragrunt.hcl               (its OWN service.hcl)
    """
    (root / "root.hcl").write_text("# root\n", encoding="utf-8")
    envcommon = root / "_envcommon"
    envcommon.mkdir()
    ci_envcommon = envcommon / "collector-ingestion.hcl"
    ci_envcommon.write_text("# envcommon collector-ingestion\n", encoding="utf-8")
    (envcommon / "portal.hcl").write_text("# envcommon portal\n", encoding="utf-8")

    account_dir = root / "live" / "env" / "account"
    account_dir.mkdir(parents=True)
    account_hcl = account_dir / "account.hcl"
    account_hcl.write_text("# account\n", encoding="utf-8")

    def _component(name: str, component_token: str) -> tuple[pathlib.Path, pathlib.Path]:
        svc_dir = account_dir / name
        svc_dir.mkdir()
        service_hcl = svc_dir / "service.hcl"
        service_hcl.write_text("# service\n", encoding="utf-8")
        (svc_dir / "active.hcl").write_text('locals { active = "000" }\n', encoding="utf-8")
        unit_dir = svc_dir / "000"
        unit_dir.mkdir()
        (unit_dir / "terragrunt.hcl").write_text(
            _LEAF_HCL.replace("__COMPONENT__", component_token), encoding="utf-8"
        )
        return service_hcl, unit_dir

    ci_service_hcl, ci_unit = _component("collector-ingestion", "collector-ingestion")
    portal_service_hcl, portal_unit = _component("portal", "portal")

    return {
        "root_hcl": root / "root.hcl",
        "ci_envcommon": ci_envcommon,
        "account_hcl": account_hcl,
        "ci_service_hcl": ci_service_hcl,
        "ci_unit": ci_unit,
        "ci_active": account_dir / "collector-ingestion" / "active.hcl",
        "portal_service_hcl": portal_service_hcl,
        "portal_unit": portal_unit,
    }


@pytest.mark.unit
def test_resolve_in_parent_folders_finds_nearest_ancestor(tmp_path: pathlib.Path) -> None:
    """_resolve_in_parent_folders returns the NEAREST ancestor file of the given name."""
    tree = _build_parent_config_tree(tmp_path)
    resolved = _resolve_in_parent_folders("service.hcl", tree["ci_unit"], tmp_path)
    assert resolved == tree["ci_service_hcl"].resolve()
    # root.hcl resolves at the tree root (the search includes terragrunt_root).
    assert _resolve_in_parent_folders("root.hcl", tree["ci_unit"], tmp_path) == (
        tree["root_hcl"].resolve()
    )
    # A name that does not exist in any ancestor returns None.
    assert _resolve_in_parent_folders("missing.hcl", tree["ci_unit"], tmp_path) is None


@pytest.mark.unit
def test_resolve_parent_config_references_covers_all_forms(tmp_path: pathlib.Path) -> None:
    """resolve_parent_config_references resolves root/account/service/envcommon/get_tg_dir refs."""
    tree = _build_parent_config_tree(tmp_path)
    refs = resolve_parent_config_references(tree["ci_unit"] / "terragrunt.hcl", tmp_path)
    assert tree["root_hcl"].resolve() in refs
    assert tree["account_hcl"].resolve() in refs
    assert tree["ci_service_hcl"].resolve() in refs
    # _envcommon include resolved via ${dirname(find_in_parent_folders("root.hcl"))}.
    assert tree["ci_envcommon"].resolve() in refs
    # ${get_terragrunt_dir()}/../active.hcl resolved relative to the unit dir.
    assert tree["ci_active"].resolve() in refs


@pytest.mark.unit
def test_find_units_affected_by_service_hcl_change(tmp_path: pathlib.Path) -> None:
    """A service.hcl change maps to the unit that reads it -- and ONLY that unit.

    The portal unit reads its OWN (nearer) service.hcl, so a change to the
    collector-ingestion service.hcl must NOT pull portal into scope.
    """
    tree = _build_parent_config_tree(tmp_path)
    affected = find_units_affected_by_parent_config(tree["ci_service_hcl"], tmp_path)
    assert affected == {tree["ci_unit"]}
    assert tree["portal_unit"] not in affected


@pytest.mark.unit
def test_find_units_affected_by_account_hcl_change_maps_subtree(tmp_path: pathlib.Path) -> None:
    """An account.hcl change maps to EVERY unit in its subtree (both read it)."""
    tree = _build_parent_config_tree(tmp_path)
    affected = find_units_affected_by_parent_config(tree["account_hcl"], tmp_path)
    assert affected == {tree["ci_unit"], tree["portal_unit"]}


@pytest.mark.unit
def test_find_units_affected_by_envcommon_change(tmp_path: pathlib.Path) -> None:
    """An _envcommon/<component>.hcl change maps to the units that include it (not others)."""
    tree = _build_parent_config_tree(tmp_path)
    affected = find_units_affected_by_parent_config(tree["ci_envcommon"], tmp_path)
    assert affected == {tree["ci_unit"]}
    assert tree["portal_unit"] not in affected


@pytest.mark.unit
def test_find_units_affected_by_root_hcl_change_maps_all_units(tmp_path: pathlib.Path) -> None:
    """A root.hcl change maps to every unit (all units include the root)."""
    tree = _build_parent_config_tree(tmp_path)
    affected = find_units_affected_by_parent_config(tree["root_hcl"], tmp_path)
    assert affected == {tree["ci_unit"], tree["portal_unit"]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("service.hcl", True),
        ("account.hcl", True),
        ("_envcommon/portal.hcl", True),
        ("terragrunt.hcl", False),  # a unit definition, not a shared parent config
        ("README.md", False),  # not an .hcl config
    ],
)
def test_is_shared_parent_config(tmp_path: pathlib.Path, name: str, expected: bool) -> None:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# x\n", encoding="utf-8")
    assert _is_shared_parent_config(target, tmp_path) is expected


@pytest.mark.unit
def test_is_shared_parent_config_rejects_file_outside_tree(tmp_path: pathlib.Path) -> None:
    """A .hcl file OUTSIDE the terragrunt tree is not a shared parent config of it."""
    outside = tmp_path.parent / "outside.hcl"
    outside.write_text("# x\n", encoding="utf-8")
    try:
        assert _is_shared_parent_config(outside, tmp_path) is False
    finally:
        outside.unlink()


@pytest.mark.unit
def test_run_detect_maps_parent_config_change_to_units(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_detect: a changed shared service.hcl (no enclosing unit) is mapped to its
    dependent unit in include_dir_flags -- it is NOT silently dropped (issue #91)."""
    import scripts.detect_terragrunt_units as mod
    from scripts.detect_terragrunt_units import run_detect

    tree = _build_parent_config_tree(tmp_path)
    output_file = tmp_path / "gh_output"
    output_file.write_text("", encoding="utf-8")

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [tree["ci_service_hcl"]]

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)

    run_detect(
        base_ref="origin/main",
        head_ref="HEAD",
        terragrunt_root=tmp_path,
        output_path=str(output_file),
    )
    content = output_file.read_text(encoding="utf-8")
    assert "--queue-include-dir" in content
    assert str(tree["ci_unit"]) in content
    # The portal unit (which reads its own nearer service.hcl) is NOT in scope.
    assert str(tree["portal_unit"]) not in content


@pytest.mark.unit
def test_run_detect_parent_config_with_no_dependents_still_fails_closed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared .hcl that no unit references maps to nothing -> EmptyUnitScopeError (D3).

    Fail-closed is preserved: an orphan parent-config change does not silently become
    a no-op apply in the PLAN path.
    """
    import scripts.detect_terragrunt_units as mod
    from scripts.detect_terragrunt_units import run_detect

    _build_parent_config_tree(tmp_path)
    orphan = tmp_path / "_envcommon" / "unreferenced.hcl"
    orphan.write_text("# referenced by no unit\n", encoding="utf-8")
    output_file = tmp_path / "gh_output"

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        return [orphan]

    def mock_list_deleted(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        # The orphan parent-config was modified (not deleted) -> not deletion-only -> fail closed.
        return []

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    monkeypatch.setattr(mod, "list_deleted_files", mock_list_deleted)

    with pytest.raises(EmptyUnitScopeError):
        run_detect(
            base_ref="origin/main",
            head_ref="HEAD",
            terragrunt_root=tmp_path,
            output_path=str(output_file),
        )


@pytest.mark.unit
def test_main_returns_1_when_git_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when list_changed_files raises RuntimeError."""
    import sys

    import scripts.detect_terragrunt_units as mod

    # Create a minimal terragrunt root
    tg_root = tmp_path / "terragrunt"
    tg_root.mkdir()
    output_file = tmp_path / "gh_output"

    def mock_list_changed(base: str, head: str, repo_root: pathlib.Path) -> list[pathlib.Path]:
        raise RuntimeError("git diff failed (exit 128)")

    monkeypatch.setattr(mod, "list_changed_files", mock_list_changed)
    original_argv = sys.argv
    try:
        sys.argv = [
            "detect_terragrunt_units",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--terragrunt-root",
            str(tg_root),
            "--output",
            str(output_file),
        ]
        result = mod.main()
    finally:
        sys.argv = original_argv
    assert result == 1
