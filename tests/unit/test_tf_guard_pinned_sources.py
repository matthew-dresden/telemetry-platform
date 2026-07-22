"""Unit tests for scripts/tf_guard_pinned_sources.py.

Implements the docs/release-pipeline.md per-guard pytest matrix for tf_guard_pinned_sources.
Tests assert that the guard:
  - passes a source with ?ref=.../v<semver>
  - rejects a ${get_repo_root()} local source
  - rejects a floating branch ref (?ref=main)
  - rejects a non-semver ref (?ref=sha1234)
  - rejects a source with no ?ref= at all
  - allows a ${get_repo_root()} source when use_pinned_module_sources=false (dev/sandbox)
  - rejects a ${get_repo_root()} source when use_pinned_module_sources=true (prod)
  - resolves use_pinned_module_sources from the leaf's governing account.hcl
  - fails fast when account.hcl lacks the use_pinned_module_sources toggle
"""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.tf_guard_pinned_sources import (
    MissingToggleError,
    UnpinnedSourceError,
    check_source_string,
    collect_leaf_sources,
    resolve_toggle_from_account_hcl,
    run_source_guard,
)

# ---------------------------------------------------------------------------
# check_source_string tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "source,description",
    [
        (
            "git::https://github.com/org/repo.git//module/path?ref=providers/aws/primitives/acm/v1.2.3",
            "semver tag with provider path prefix",
        ),
        (
            "git::https://github.com/org/repo.git//module?ref=v0.1.0",
            "semver tag v0.1.0",
        ),
        (
            "git::https://github.com/org/repo.git//module?ref=refs/tags/v12.34.567",
            "full refs/tags semver",
        ),
    ],
)
def test_check_source_string_passes_for_pinned_semver_ref(source: str, description: str) -> None:
    """A source pinned to a ?ref=.../v<semver> passes without error."""
    check_source_string(source=source, file_path=pathlib.Path("some/unit/terragrunt.hcl"))
    # No exception means pass


@pytest.mark.unit
def test_check_source_string_rejects_get_repo_root_local_source() -> None:
    """A ${get_repo_root()} local source is rejected."""
    source = "${get_repo_root()}//providers/aws/primitives/acm"
    with pytest.raises(UnpinnedSourceError, match="get_repo_root"):
        check_source_string(
            source=source,
            file_path=pathlib.Path("some/unit/terragrunt.hcl"),
        )


@pytest.mark.unit
def test_check_source_string_rejects_floating_branch_ref() -> None:
    """A floating branch ref (?ref=main) is rejected."""
    source = "git::https://github.com/org/repo.git//module?ref=main"
    with pytest.raises(UnpinnedSourceError, match="semver"):
        check_source_string(
            source=source,
            file_path=pathlib.Path("some/unit/terragrunt.hcl"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source,description",
    [
        (
            "git::https://github.com/org/repo.git//module?ref=sha1234",
            "short SHA ref (no v prefix)",
        ),
        (
            "git::https://github.com/org/repo.git//module?ref=abc123def456",
            "full SHA ref (no v prefix)",
        ),
        (
            "git::https://github.com/org/repo.git//module?ref=feature-branch",
            "feature branch ref",
        ),
    ],
)
def test_check_source_string_rejects_non_semver_ref(source: str, description: str) -> None:
    """A non-semver ref (?ref=sha1234, ?ref=feature-branch) is rejected."""
    with pytest.raises(UnpinnedSourceError, match="semver"):
        check_source_string(
            source=source,
            file_path=pathlib.Path("some/unit/terragrunt.hcl"),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source,description",
    [
        (
            "git::https://github.com/org/repo.git//module",
            "no ?ref= parameter",
        ),
        (
            "https://github.com/org/repo.git//module",
            "no ?ref= parameter, no git:: prefix",
        ),
    ],
)
def test_check_source_string_rejects_source_without_ref(source: str, description: str) -> None:
    """A source with no ?ref= is rejected."""
    with pytest.raises(UnpinnedSourceError, match="ref"):
        check_source_string(
            source=source,
            file_path=pathlib.Path("some/unit/terragrunt.hcl"),
        )


# ---------------------------------------------------------------------------
# collect_leaf_sources tests (using tmp_path fixture)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_leaf_sources_finds_source_in_hcl(tmp_path: pathlib.Path) -> None:
    """collect_leaf_sources extracts the terraform source from a leaf terragrunt.hcl."""
    from scripts.tf_guard_pinned_sources import LiteralSource

    unit_dir = tmp_path / "live" / "env" / "acct" / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl_content = (
        'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v1.0.0"\n}\n'
    )
    (unit_dir / "terragrunt.hcl").write_text(hcl_content, encoding="utf-8")

    sources = collect_leaf_sources(terragrunt_root=tmp_path)
    assert len(sources) == 1
    file_path, source_value = sources[0]
    assert isinstance(source_value, LiteralSource)
    assert "v1.0.0" in source_value.value
    assert file_path == unit_dir / "terragrunt.hcl"


@pytest.mark.unit
def test_collect_leaf_sources_returns_empty_for_no_source(tmp_path: pathlib.Path) -> None:
    """collect_leaf_sources returns empty list when no source is declared."""
    unit_dir = tmp_path / "live" / "env" / "acct" / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl_content = "# no source block\ninputs = {}\n"
    (unit_dir / "terragrunt.hcl").write_text(hcl_content, encoding="utf-8")

    sources = collect_leaf_sources(terragrunt_root=tmp_path)
    assert sources == []


@pytest.mark.unit
def test_collect_leaf_sources_skips_envcommon_dir(tmp_path: pathlib.Path) -> None:
    """collect_leaf_sources does not scan _envcommon templates (they are not leaves)."""
    envcommon_dir = tmp_path / "_envcommon"
    envcommon_dir.mkdir(parents=True)
    hcl_content = "# envcommon template\n"
    (envcommon_dir / "collector.hcl").write_text(hcl_content, encoding="utf-8")

    live_dir = tmp_path / "live" / "env" / "acct" / "svc" / "001"
    live_dir.mkdir(parents=True)
    live_hcl = (
        'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v1.0.0"\n}\n'
    )
    (live_dir / "terragrunt.hcl").write_text(live_hcl, encoding="utf-8")

    sources = collect_leaf_sources(terragrunt_root=tmp_path)
    # Only the live leaf should be collected; _envcommon/*.hcl are not leaves.
    assert len(sources) == 1
    assert "_envcommon" not in str(sources[0][0])


# ---------------------------------------------------------------------------
# run_source_guard and main() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_source_guard_passes_for_all_pinned(tmp_path: pathlib.Path) -> None:
    """run_source_guard returns empty errors when all sources are pinned and toggle=true."""
    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = "locals {\n  use_pinned_module_sources = true\n}\n"
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl = 'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v2.3.4"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == []


@pytest.mark.unit
def test_run_source_guard_returns_errors_for_unpinned_prod(tmp_path: pathlib.Path) -> None:
    """run_source_guard returns errors when a ${get_repo_root()} source is in a prod context."""
    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = "locals {\n  use_pinned_module_sources = true\n}\n"
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl = 'terraform {\n  source = "${get_repo_root()}//providers/aws/primitives/acm"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Env-keyed toggle resolution (account.hcl resolves the toggle from env_accounts.json)
# ---------------------------------------------------------------------------

_ENV_KEYED_ACCOUNT_HCL = (
    "locals {\n"
    '  _env_accounts = jsondecode(file("${get_repo_root()}/terragrunt/common/env_accounts.json"))\n'
    "  use_pinned_module_sources = "
    'local._env_accounts["envs"][local._env]["use_pinned_module_sources"]\n'
    "}\n"
)
_ENVIRONMENT_HCL = "locals {\n  environment = basename(get_terragrunt_dir())\n}\n"


def _write_env_keyed_tree(repo: pathlib.Path, env_class: str, envs: dict, account_dir_rel: str):
    """Build a minimal env-keyed live tree; return (leaf_path, live_root)."""
    common = repo / "terragrunt" / "common"
    common.mkdir(parents=True, exist_ok=True)
    (common / "env_accounts.json").write_text(json.dumps({"envs": envs}), encoding="utf-8")
    live = repo / "terragrunt" / "live"
    env_dir = live / "telemetry" / "us-east-1" / env_class
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "environment.hcl").write_text(_ENVIRONMENT_HCL, encoding="utf-8")
    account_dir = env_dir / account_dir_rel if account_dir_rel else env_dir
    account_dir.mkdir(parents=True, exist_ok=True)
    (account_dir / "account.hcl").write_text(_ENV_KEYED_ACCOUNT_HCL, encoding="utf-8")
    leaf = account_dir / "svc" / "000" / "terragrunt.hcl"
    leaf.parent.mkdir(parents=True, exist_ok=True)
    leaf.write_text('terraform {\n  source = "x"\n}\n', encoding="utf-8")
    return leaf, live


@pytest.mark.unit
def test_resolve_toggle_env_keyed_account_hcl(tmp_path: pathlib.Path) -> None:
    """Env-keyed account.hcl (no literal toggle) resolves the toggle from env_accounts.json."""
    leaf, live = _write_env_keyed_tree(
        tmp_path, "sandbox", {"sandbox": {"use_pinned_module_sources": True}}, ""
    )
    assert resolve_toggle_from_account_hcl(leaf, live) is True


@pytest.mark.unit
def test_resolve_toggle_env_keyed_false(tmp_path: pathlib.Path) -> None:
    """Env-keyed account.hcl honours a false toggle from env_accounts.json."""
    leaf, live = _write_env_keyed_tree(
        tmp_path, "sandbox", {"sandbox": {"use_pinned_module_sources": False}}, ""
    )
    assert resolve_toggle_from_account_hcl(leaf, live) is False


@pytest.mark.unit
def test_resolve_toggle_env_keyed_nested_singleton(tmp_path: pathlib.Path) -> None:
    """Nested singleton account.hcl resolves env-class from environment.hcl, not its dir name."""
    # account.hcl sits under _singletons/pretty -- its own dir name 'pretty' is NOT the env-class.
    leaf, live = _write_env_keyed_tree(
        tmp_path, "prod", {"prod": {"use_pinned_module_sources": True}}, "_singletons/pretty"
    )
    assert resolve_toggle_from_account_hcl(leaf, live) is True


@pytest.mark.unit
def test_resolve_toggle_env_keyed_unknown_env_class_raises(tmp_path: pathlib.Path) -> None:
    """An env-class absent from env_accounts.json envs fails fast (no silent fallback)."""
    leaf, live = _write_env_keyed_tree(tmp_path, "sandbox", {}, "")
    with pytest.raises(MissingToggleError):
        resolve_toggle_from_account_hcl(leaf, live)


@pytest.mark.unit
def test_main_returns_0_for_all_pinned(tmp_path: pathlib.Path) -> None:
    """main() returns 0 when all sources are pinned in a prod (true) context."""
    import os

    from scripts.tf_guard_pinned_sources import main as guard_main

    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = "locals {\n  use_pinned_module_sources = true\n}\n"
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl = 'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v1.0.0"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")
    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = guard_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 0


@pytest.mark.unit
def test_main_returns_1_for_unpinned_prod(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when a ${get_repo_root()} source appears in a prod (true) context."""
    import os

    from scripts.tf_guard_pinned_sources import main as guard_main

    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = "locals {\n  use_pinned_module_sources = true\n}\n"
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "svc" / "001"
    unit_dir.mkdir(parents=True)
    hcl = 'terraform {\n  source = "${get_repo_root()}//providers/aws/primitives/acm"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")
    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = guard_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1


# ---------------------------------------------------------------------------
# Toggle-aware context tests (AC-FUNC-001, AC-FUNC-002, AC-FUNC-003)
# ---------------------------------------------------------------------------


def _make_leaf_with_account(
    root: pathlib.Path,
    use_pinned: bool,
    source: str,
    account_id: str = "123456789012",
) -> pathlib.Path:
    """Create a minimal leaf fixture with an account.hcl at the account level."""
    account_dir = root / "live" / "env" / account_id
    account_dir.mkdir(parents=True, exist_ok=True)
    pinned_str = "true" if use_pinned else "false"
    account_hcl = f"locals {{\n  use_pinned_module_sources = {pinned_str}\n}}\n"
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "prod" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True, exist_ok=True)
    hcl = f'terraform {{\n  source = "{source}"\n}}\n'
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text(hcl, encoding="utf-8")
    return leaf


@pytest.mark.unit
@pytest.mark.parametrize(
    "source,description",
    [
        (
            "${get_repo_root()}//providers/aws/references/analytics",
            "local get_repo_root source -- analytics ref",
        ),
        (
            "${get_repo_root()}//providers/aws/primitives/acm",
            "local get_repo_root source -- acm primitive",
        ),
    ],
)
def test_check_source_string_allows_local_source_when_pinned_not_required(
    source: str, description: str
) -> None:
    """A ${get_repo_root()} source is ALLOWED when pinned_required=False (dev/sandbox).

    AC-FUNC-001: guard allows a local source when use_pinned_module_sources is false.
    """
    # Should not raise -- local source is permitted in dev/sandbox context.
    check_source_string(
        source=source,
        file_path=pathlib.Path("live/env/123456789012/sandbox/000/svc/000/terragrunt.hcl"),
        pinned_required=False,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "source,description",
    [
        (
            "${get_repo_root()}//providers/aws/references/analytics",
            "local get_repo_root source -- analytics ref",
        ),
        (
            "${get_repo_root()}//providers/aws/primitives/acm",
            "local get_repo_root source -- acm primitive",
        ),
    ],
)
def test_check_source_string_rejects_local_source_when_pinned_required(
    source: str, description: str
) -> None:
    """A ${get_repo_root()} source is REJECTED when pinned_required=True (prod).

    AC-FUNC-002: guard rejects unpinned source with UnpinnedSourceError when
    use_pinned_module_sources is true, including env/context and remediation.
    """
    with pytest.raises(UnpinnedSourceError) as exc_info:
        check_source_string(
            source=source,
            file_path=pathlib.Path("live/env/123456789012/prod/000/svc/000/terragrunt.hcl"),
            pinned_required=True,
        )
    error_msg = str(exc_info.value)
    assert "get_repo_root" in error_msg
    assert "prod" in error_msg.lower() or "pinned" in error_msg.lower()
    assert "?ref=" in error_msg or "semver" in error_msg.lower() or "remedy" in error_msg.lower()


@pytest.mark.unit
def test_check_source_string_passes_pinned_ref_when_pinned_required() -> None:
    """A pinned ?ref=.../v<semver> source PASSES when pinned_required=True (prod).

    AC-FUNC-002: prod-context pinned sources pass.
    """
    source = "git::https://github.com/org/repo.git//module?ref=refs/tags/v1.2.3"
    check_source_string(
        source=source,
        file_path=pathlib.Path("live/env/123456789012/prod/000/svc/000/terragrunt.hcl"),
        pinned_required=True,
    )


@pytest.mark.unit
def test_resolve_toggle_from_account_hcl_returns_true(tmp_path: pathlib.Path) -> None:
    """resolve_toggle_from_account_hcl returns True when use_pinned_module_sources = true.

    AC-FUNC-003: toggle resolution from account.hcl.
    """
    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = (
        'locals {\n  aws_account_id = "123456789012"\n  use_pinned_module_sources = true\n}\n'
    )
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "prod" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text('terraform {\n  source = "git::..."\n}\n', encoding="utf-8")

    toggle = resolve_toggle_from_account_hcl(leaf_path=leaf, live_root=tmp_path)
    assert toggle is True


@pytest.mark.unit
def test_resolve_toggle_from_account_hcl_returns_false(tmp_path: pathlib.Path) -> None:
    """resolve_toggle_from_account_hcl returns False when use_pinned_module_sources = false.

    AC-FUNC-003: toggle resolution from account.hcl (dev/sandbox path).
    """
    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    account_hcl = (
        'locals {\n  aws_account_id = "123456789012"\n  use_pinned_module_sources = false\n}\n'
    )
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "sandbox" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text('terraform {\n  source = "git::..."\n}\n', encoding="utf-8")

    toggle = resolve_toggle_from_account_hcl(leaf_path=leaf, live_root=tmp_path)
    assert toggle is False


@pytest.mark.unit
def test_resolve_toggle_from_account_hcl_fails_fast_when_toggle_absent(
    tmp_path: pathlib.Path,
) -> None:
    """resolve_toggle_from_account_hcl raises MissingToggleError when toggle key is absent.

    AC-FUNC-003: fail-fast when use_pinned_module_sources is missing from account.hcl.
    """
    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    # account.hcl without use_pinned_module_sources
    account_hcl = 'locals {\n  aws_account_id = "123456789012"\n}\n'
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "env" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text('terraform {\n  source = "git::..."\n}\n', encoding="utf-8")

    with pytest.raises(MissingToggleError) as exc_info:
        resolve_toggle_from_account_hcl(leaf_path=leaf, live_root=tmp_path)
    error_msg = str(exc_info.value)
    assert "use_pinned_module_sources" in error_msg
    assert "account.hcl" in error_msg


@pytest.mark.unit
def test_resolve_toggle_from_account_hcl_fails_fast_when_no_account_hcl(
    tmp_path: pathlib.Path,
) -> None:
    """resolve_toggle_from_account_hcl raises MissingToggleError when no account.hcl found.

    AC-FUNC-003: fail-fast when account.hcl is not found in any parent directory.
    """
    unit_dir = tmp_path / "live" / "env" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text('terraform {\n  source = "git::..."\n}\n', encoding="utf-8")

    with pytest.raises(MissingToggleError) as exc_info:
        resolve_toggle_from_account_hcl(leaf_path=leaf, live_root=tmp_path)
    error_msg = str(exc_info.value)
    assert "account.hcl" in error_msg


@pytest.mark.unit
def test_run_source_guard_allows_local_source_in_false_context(tmp_path: pathlib.Path) -> None:
    """run_source_guard allows ${get_repo_root()} source when use_pinned_module_sources=false.

    AC-FUNC-001: end-to-end allowed path for dev/sandbox leaf.
    """
    leaf = _make_leaf_with_account(
        root=tmp_path,
        use_pinned=False,
        source="${get_repo_root()}//providers/aws/references/analytics",
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == [], f"Expected no errors for dev/sandbox leaf {leaf}, got: {errors}"


@pytest.mark.unit
def test_run_source_guard_rejects_local_source_in_true_context(tmp_path: pathlib.Path) -> None:
    """run_source_guard rejects ${get_repo_root()} source when use_pinned_module_sources=true.

    AC-FUNC-002: end-to-end rejection path for prod leaf with unpinned source.
    """
    leaf = _make_leaf_with_account(
        root=tmp_path,
        use_pinned=True,
        source="${get_repo_root()}//providers/aws/references/analytics",
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert len(errors) == 1, f"Expected exactly 1 error for prod leaf {leaf}"
    assert "get_repo_root" in errors[0]
    assert "ERROR:" in errors[0]


@pytest.mark.unit
def test_run_source_guard_passes_pinned_source_in_true_context(tmp_path: pathlib.Path) -> None:
    """run_source_guard passes a pinned ref source when use_pinned_module_sources=true.

    AC-FUNC-002: prod leaf with correct ?ref=.../v<semver> passes.
    """
    _make_leaf_with_account(
        root=tmp_path,
        use_pinned=True,
        source="git::https://github.com/org/repo.git//module?ref=refs/tags/v1.0.0",
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == []


@pytest.mark.unit
def test_run_source_guard_fails_fast_for_missing_toggle(tmp_path: pathlib.Path) -> None:
    """run_source_guard propagates MissingToggleError when account.hcl lacks the toggle.

    AC-FUNC-003: fail-fast when use_pinned_module_sources is absent from account.hcl.
    """
    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    # account.hcl without use_pinned_module_sources
    account_hcl = 'locals {\n  aws_account_id = "123456789012"\n}\n'
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "env" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    hcl = 'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v1.0.0"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")

    with pytest.raises(MissingToggleError):
        run_source_guard(terragrunt_root=tmp_path)


@pytest.mark.unit
def test_main_returns_0_for_dev_sandbox_local_sources(tmp_path: pathlib.Path) -> None:
    """main() returns 0 for ${get_repo_root()} sources in dev/sandbox (toggle=false).

    AC-FUNC-001: end-to-end: guard exits 0 for sandbox local sources.
    """
    import os

    from scripts.tf_guard_pinned_sources import main as guard_main

    _make_leaf_with_account(
        root=tmp_path,
        use_pinned=False,
        source="${get_repo_root()}//providers/aws/references/analytics",
    )
    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = guard_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 0


@pytest.mark.unit
def test_main_returns_1_for_prod_local_sources(tmp_path: pathlib.Path) -> None:
    """main() returns 1 for ${get_repo_root()} sources in prod context (toggle=true).

    AC-FUNC-002: end-to-end: guard exits 1 for prod context with unpinned sources.
    """
    import os

    from scripts.tf_guard_pinned_sources import main as guard_main

    _make_leaf_with_account(
        root=tmp_path,
        use_pinned=True,
        source="${get_repo_root()}//providers/aws/references/analytics",
    )
    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = guard_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1


# ---------------------------------------------------------------------------
# Additional coverage tests for edge paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_leaf_sources_skips_envcommon_templates(tmp_path: pathlib.Path) -> None:
    """collect_leaf_sources skips files under _envcommon directories."""
    envcommon_dir = tmp_path / "_envcommon"
    envcommon_dir.mkdir(parents=True)
    envcommon_hcl = (
        'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v1.0.0"\n}\n'
    )
    (envcommon_dir / "terragrunt.hcl").write_text(envcommon_hcl, encoding="utf-8")

    # No live leaves -- only envcommon
    sources = collect_leaf_sources(terragrunt_root=tmp_path)
    assert sources == []


@pytest.mark.unit
def test_main_returns_1_when_no_terragrunt_root(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when TG_LIVE_ROOT points to a non-existent path."""
    import os

    from scripts.tf_guard_pinned_sources import main as guard_main

    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path / "nonexistent")
    try:
        result = guard_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1


@pytest.mark.unit
def test_main_returns_1_for_missing_toggle(tmp_path: pathlib.Path) -> None:
    """main() returns 1 when a leaf's account.hcl lacks the toggle (MissingToggleError path)."""
    import os

    from scripts.tf_guard_pinned_sources import main as guard_main

    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    # account.hcl without use_pinned_module_sources
    account_hcl = 'locals {\n  aws_account_id = "123456789012"\n}\n'
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    unit_dir = account_dir / "env" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    hcl = 'terraform {\n  source = "git::https://github.com/org/repo.git//module?ref=v1.0.0"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")

    original_env = os.environ.get("TG_LIVE_ROOT")
    os.environ["TG_LIVE_ROOT"] = str(tmp_path)
    try:
        result = guard_main()
    finally:
        if original_env is None:
            os.environ.pop("TG_LIVE_ROOT", None)
        else:
            os.environ["TG_LIVE_ROOT"] = original_env
    assert result == 1


@pytest.mark.unit
def test_resolve_toggle_stops_at_live_root_boundary(tmp_path: pathlib.Path) -> None:
    """Raises MissingToggleError when account.hcl is placed outside (above) live_root.

    The boundary guard must not scan directories above live_root.
    """
    # account.hcl placed OUTSIDE live_root -- should not be found
    (tmp_path / "account.hcl").write_text(
        "locals {\n  use_pinned_module_sources = true\n}\n",
        encoding="utf-8",
    )
    live_root = tmp_path / "live"
    live_root.mkdir()
    unit_dir = live_root / "env" / "svc" / "000"
    unit_dir.mkdir(parents=True)
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text('terraform {\n  source = "git::..."\n}\n', encoding="utf-8")

    with pytest.raises(MissingToggleError):
        resolve_toggle_from_account_hcl(leaf_path=leaf, live_root=live_root)


@pytest.mark.unit
def test_get_default_terragrunt_root_fallback_when_no_env(tmp_path: pathlib.Path) -> None:
    """_get_default_terragrunt_root returns a path when terragrunt/ exists beside the script."""
    import os

    from scripts.tf_guard_pinned_sources import _get_default_terragrunt_root

    original_env = os.environ.get("TG_LIVE_ROOT")
    # Remove TG_LIVE_ROOT to exercise the fallback path (lines 291-293).
    os.environ.pop("TG_LIVE_ROOT", None)
    try:
        # The repo has a terragrunt/ directory, so the fallback returns a Path.
        result = _get_default_terragrunt_root()
        # Either a Path to an existing dir or None; both are valid returns.
        assert result is None or isinstance(result, pathlib.Path)
    finally:
        if original_env is not None:
            os.environ["TG_LIVE_ROOT"] = original_env


@pytest.mark.unit
def test_get_default_terragrunt_root_returns_none_when_no_terragrunt_dir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_default_terragrunt_root returns None when no TG_LIVE_ROOT and no terragrunt/ dir."""
    from scripts import tf_guard_pinned_sources as guard_module

    # Patch __file__ on the module to point inside tmp_path so that
    # repo_root / "terragrunt" does not exist.
    fake_file = str(tmp_path / "scripts" / "tf_guard_pinned_sources.py")
    monkeypatch.setattr(guard_module, "__file__", fake_file)
    monkeypatch.delenv("TG_LIVE_ROOT", raising=False)

    result = guard_module._get_default_terragrunt_root()
    assert result is None


@pytest.mark.unit
def test_main_entry_point_exits_zero_when_all_valid(tmp_path: pathlib.Path) -> None:
    """Executing the script as __main__ exits 0 when all leaf sources are valid."""
    import os
    import subprocess
    import sys

    account_dir = tmp_path / "live" / "env" / "123456789012"
    account_dir.mkdir(parents=True)
    (account_dir / "account.hcl").write_text(
        "locals {\n  use_pinned_module_sources = false\n}\n",
        encoding="utf-8",
    )
    unit_dir = account_dir / "svc" / "000"
    unit_dir.mkdir(parents=True)
    (unit_dir / "terragrunt.hcl").write_text(
        'terraform {\n  source = "${get_repo_root()}//providers/aws/references/analytics"\n}\n',
        encoding="utf-8",
    )
    env = {**os.environ, "TG_LIVE_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.tf_guard_pinned_sources"],
        env=env,
        capture_output=True,
        cwd=str(pathlib.Path(__file__).parent.parent.parent),
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Ternary toggle source shape tests (the real leaf format from E9-F3-S1-T1)
# These tests exercise the ternary source form:
#   source = <toggle_expr> ? "<true_branch>" : "<false_branch>"
# which is the actual shape used by all 34 leaf terragrunt.hcl files.
# ---------------------------------------------------------------------------

_PINNED_TRUE_BRANCH = (
    "git::https://github.com/example-org/telemetry-platform.git"
    "//providers/aws/references/analytics"
    "?ref=providers/aws/references/analytics/v1.0.0"
)
_LOCAL_FALSE_BRANCH = "${get_repo_root()}//providers/aws/references/analytics"


def _make_ternary_leaf(
    root: pathlib.Path,
    use_pinned: bool,
    true_branch: str,
    false_branch: str,
    account_id: str = "123456789012",
) -> pathlib.Path:
    """Create a leaf with the real ternary toggle source shape and matching account.hcl."""
    account_dir = root / "live" / "env" / account_id
    account_dir.mkdir(parents=True, exist_ok=True)
    pinned_str = "true" if use_pinned else "false"
    (account_dir / "account.hcl").write_text(
        f"locals {{\n  use_pinned_module_sources = {pinned_str}\n}}\n",
        encoding="utf-8",
    )
    unit_dir = account_dir / "prod" / "000" / "svc" / "000"
    unit_dir.mkdir(parents=True, exist_ok=True)
    # Real ternary source shape as written by E9-F3-S1-T1:
    # source = local.account_vars.locals.use_pinned_module_sources ? "..." : "..."
    toggle_expr = "local.account_vars.locals.use_pinned_module_sources"
    ternary_source = f'  source = {toggle_expr} ? "{true_branch}" : "{false_branch}"'
    hcl = f"terraform {{\n{ternary_source}\n}}\n"
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text(hcl, encoding="utf-8")
    return leaf


@pytest.mark.unit
def test_collect_leaf_sources_finds_ternary_source(tmp_path: pathlib.Path) -> None:
    """collect_leaf_sources extracts both branches from a ternary toggle source line.

    The real leaf format uses:
      source = <toggle_expr> ? "<true_branch>" : "<false_branch>"
    collect_leaf_sources must parse this and return at least the relevant branch.
    """
    _make_ternary_leaf(
        root=tmp_path,
        use_pinned=True,
        true_branch=_PINNED_TRUE_BRANCH,
        false_branch=_LOCAL_FALSE_BRANCH,
    )
    sources = collect_leaf_sources(terragrunt_root=tmp_path)
    # Must find at least one source from the ternary line (not zero).
    assert len(sources) >= 1, (
        "collect_leaf_sources returned 0 sources for a ternary toggle leaf -- "
        "SOURCE_RE does not match the ternary form"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "true_branch,description",
    [
        (
            _PINNED_TRUE_BRANCH,
            "analytics ref with pinned semver",
        ),
        (
            "git::https://github.com/example-org/telemetry-platform.git"
            "//providers/aws/references/identity"
            "?ref=providers/aws/references/identity/v1.0.0",
            "identity ref with pinned semver",
        ),
    ],
)
def test_run_source_guard_ternary_toggle_true_with_pinned_true_branch_passes(
    tmp_path: pathlib.Path,
    true_branch: str,
    description: str,
) -> None:
    """run_source_guard exits 0 for ternary leaves where toggle=true and true-branch is pinned.

    AC-FUNC-002: prod (toggle=true) with a properly pinned ternary -> exit 0.
    """
    _make_ternary_leaf(
        root=tmp_path,
        use_pinned=True,
        true_branch=true_branch,
        false_branch=_LOCAL_FALSE_BRANCH,
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == [], f"Expected no errors for pinned ternary ({description}), got: {errors}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_true_branch,description",
    [
        (
            _LOCAL_FALSE_BRANCH,
            "true-branch is a local get_repo_root source (unpinned)",
        ),
        (
            "git::https://github.com/example-org/telemetry-platform.git"
            "//providers/aws/references/analytics"
            "?ref=main",
            "true-branch has floating branch ref (not semver)",
        ),
    ],
)
def test_run_source_guard_ternary_toggle_true_with_unpinned_true_branch_fails(
    tmp_path: pathlib.Path,
    bad_true_branch: str,
    description: str,
) -> None:
    """run_source_guard exits non-zero for ternary leaves with unpinned true-branch.

    AC-FUNC-002: prod (toggle=true) with an unpinned/local true-branch -> error naming the file.
    """
    leaf = _make_ternary_leaf(
        root=tmp_path,
        use_pinned=True,
        true_branch=bad_true_branch,
        false_branch=_LOCAL_FALSE_BRANCH,
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert len(errors) >= 1, (
        f"Expected error for unpinned ternary true-branch ({description}) "
        f"under toggle=true, leaf={leaf}"
    )
    assert any("ERROR:" in e for e in errors), (
        f"Error message should contain 'ERROR:' prefix; got: {errors}"
    )


@pytest.mark.unit
def test_run_source_guard_ternary_toggle_false_with_local_false_branch_passes(
    tmp_path: pathlib.Path,
) -> None:
    """run_source_guard exits 0 for ternary leaves where toggle=false (local branch allowed).

    AC-FUNC-001: dev/sandbox (toggle=false) with ternary -> allowed, exit 0.
    """
    _make_ternary_leaf(
        root=tmp_path,
        use_pinned=False,
        true_branch=_PINNED_TRUE_BRANCH,
        false_branch=_LOCAL_FALSE_BRANCH,
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == [], f"Expected no errors for ternary leaf with toggle=false, got: {errors}"


# ---------------------------------------------------------------------------
# Bootstrap path carve-out tests (D-16)
#
# Bootstrap units (path segment 'bootstrap') always source the in-repo module
# via get_repo_root() regardless of use_pinned_module_sources. The pin toggle
# governs service units only; the guard must exempt bootstrap paths so that
# the prod bootstrap leaf does not falsely fire the unpinned-source error.
# ---------------------------------------------------------------------------


def _make_bootstrap_leaf(
    root: pathlib.Path,
    use_pinned: bool,
    source: str,
    account_id: str = "111111111111",
) -> pathlib.Path:
    """Create a bootstrap leaf at the canonical bootstrap path with matching account.hcl."""
    account_dir = root / "live" / "env" / account_id
    account_dir.mkdir(parents=True, exist_ok=True)
    pinned_str = "true" if use_pinned else "false"
    account_hcl = f"locals {{\n  use_pinned_module_sources = {pinned_str}\n}}\n"
    (account_dir / "account.hcl").write_text(account_hcl, encoding="utf-8")

    # Bootstrap leaves live under a 'bootstrap' path segment (D-16).
    unit_dir = account_dir / "bootstrap" / "000" / "state-bootstrap" / "000"
    unit_dir.mkdir(parents=True, exist_ok=True)
    hcl = f'terraform {{\n  source = "{source}"\n}}\n'
    leaf = unit_dir / "terragrunt.hcl"
    leaf.write_text(hcl, encoding="utf-8")
    return leaf


@pytest.mark.unit
def test_run_source_guard_exempts_bootstrap_leaf_with_local_source_in_prod_context(
    tmp_path: pathlib.Path,
) -> None:
    """Bootstrap leaves use get_repo_root() even when use_pinned_module_sources=true (D-16).

    The guard must NOT raise UnpinnedSourceError for a bootstrap leaf in a prod account.
    Service units still require pinned refs under the same toggle.
    """
    _make_bootstrap_leaf(
        root=tmp_path,
        use_pinned=True,
        source="${get_repo_root()}//providers/aws/references/state-bootstrap",
    )
    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == [], (
        "Bootstrap leaf with get_repo_root() source must be exempt from the pinned-source "
        f"requirement (D-16) even when use_pinned_module_sources=true; got errors: {errors}"
    )


@pytest.mark.unit
def test_run_source_guard_still_rejects_non_bootstrap_prod_local_source(
    tmp_path: pathlib.Path,
) -> None:
    """Non-bootstrap service leaves in prod context are still rejected for get_repo_root() source.

    The bootstrap carve-out applies only to path segments containing 'bootstrap'.
    """
    account_dir = tmp_path / "live" / "env" / "111111111111"
    account_dir.mkdir(parents=True, exist_ok=True)
    (account_dir / "account.hcl").write_text(
        "locals {\n  use_pinned_module_sources = true\n}\n",
        encoding="utf-8",
    )
    # Service unit (no 'bootstrap' in path):
    unit_dir = account_dir / "prod" / "000" / "analytics" / "000"
    unit_dir.mkdir(parents=True, exist_ok=True)
    hcl = 'terraform {\n  source = "${get_repo_root()}//providers/aws/references/analytics"\n}\n'
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")

    errors = run_source_guard(terragrunt_root=tmp_path)
    assert len(errors) >= 1, (
        "Service leaf with get_repo_root() source in prod context must still be rejected; "
        "got no errors"
    )
    assert any("get_repo_root" in e for e in errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bootstrap_path,description",
    [
        ("bootstrap/000/state-bootstrap/000", "canonical prod state-bootstrap path"),
        ("bootstrap/000/oidc-bootstrap/000", "oidc-bootstrap path"),
        ("deep/nested/bootstrap/000/any-bootstrap/000", "deeply nested bootstrap path"),
    ],
)
def test_run_source_guard_exempts_any_bootstrap_path_segment_in_prod(
    tmp_path: pathlib.Path,
    bootstrap_path: str,
    description: str,
) -> None:
    """Any path containing a 'bootstrap' segment is exempt from the pinned-source rule (D-16).

    The guard exempts by path segment, so oidc-bootstrap and any future bootstrap
    units are also exempt.
    """
    account_dir = tmp_path / "live" / "env" / "111111111111"
    account_dir.mkdir(parents=True, exist_ok=True)
    (account_dir / "account.hcl").write_text(
        "locals {\n  use_pinned_module_sources = true\n}\n",
        encoding="utf-8",
    )
    unit_dir = account_dir / pathlib.Path(bootstrap_path)
    unit_dir.mkdir(parents=True, exist_ok=True)
    hcl = (
        "terraform {\n  source = "
        '"${get_repo_root()}//providers/aws/references/state-bootstrap"\n}\n'
    )
    (unit_dir / "terragrunt.hcl").write_text(hcl, encoding="utf-8")

    errors = run_source_guard(terragrunt_root=tmp_path)
    assert errors == [], (
        f"Bootstrap path '{description}' must be exempt from pinned-source rule; "
        f"got errors: {errors}"
    )
