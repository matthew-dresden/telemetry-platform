"""Unit tests for scripts/filter_ci_deployable_units.py (apply-scope local-only filter).

The filter splits a changed-unit apply scope into the CI-deployable units (resolved account
ci_deploy=true) and the local-only units (ci_deploy=false, e.g. the sandbox account, D33) so the
terragrunt-apply workflow deploys only the CI-deployable units and validly SKIPS the local-only
ones instead of failing fast on a scope that spans a local-only account. It reuses
scripts.resolve_deploy_role.resolve_account_id + is_account_ci_deployable for account resolution +
the ci_deploy flag (DRY), so these tests focus on the split, the emitted GitHub outputs, the
transparent skip log, and the fail-fast behaviour for genuine configuration errors.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from scripts.filter_ci_deployable_units import (
    SKIP_LOG_PREFIX,
    main,
    partition_by_ci_deploy,
    run,
)
from scripts.resolve_deploy_role import (
    AccountNotFoundError,
    AccountResolutionError,
    PathParseError,
)

# ---------------------------------------------------------------------------
# Fixtures (test-double account ids; mirror the real env-keyed tier rules)
# ---------------------------------------------------------------------------

ENV_ACCOUNTS = {
    "envs": {
        "sandbox": {"account_id": "100000000001", "aws_profile": "t-sandbox"},
        "prod": {"account_id": "100000000002", "aws_profile": "t-prod"},
        "qa": {"account_id": "100000000003", "aws_profile": "t-qa"},
    },
    "dns_owner": {"account_id": "100000000009", "aws_profile": "t-dns"},
}
# prod pretty served from the ROOT zone (apex asymmetry) -> _pretty resolves to dns_owner;
# sandbox pretty served from the env's OWN zone -> _pretty resolves to the sandbox account.
DOMAINS = {
    "sandbox": {
        "dns_service_apex": "sandbox.x.example.com",
        "dns_pretty_apex": "sandbox.x.example.com",
    },
    "prod": {"dns_service_apex": "prod.x.example.com", "dns_pretty_apex": "x.example.com"},
}
ACCOUNTS = {
    "100000000001": {
        "account_role": "sandbox",
        "ci_deploy": False,
        # On-demand ephemeral-apply eligibility (Phase 0), mirroring the real accounts.json.
        "ci_deploy_on_demand": True,
        "deploy_role_name": "tt-apply",
    },
    "100000000002": {
        "account_role": "prod-infra",
        "ci_deploy": True,
        "deploy_role_name": "tt-apply",
    },
    "100000000003": {
        "account_role": "qa-infra",
        "ci_deploy": True,
        "deploy_role_name": "tt-terratest",
    },
    "100000000009": {
        "account_role": "dns-owner",
        "ci_deploy": True,
        "deploy_role_name": "tt-dns-writer",
    },
}

_BASE = "terragrunt/live/telemetry/us-east-1"


@pytest.fixture()
def common_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A tmp common/ dir holding accounts.json + env_accounts.json + domains.json siblings."""
    (tmp_path / "accounts.json").write_text(json.dumps(ACCOUNTS), encoding="utf-8")
    (tmp_path / "env_accounts.json").write_text(json.dumps(ENV_ACCOUNTS), encoding="utf-8")
    (tmp_path / "domains.json").write_text(json.dumps(DOMAINS), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def output_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "github_output.txt"


def _flags(*paths: str) -> str:
    parts: list[str] = []
    for p in paths:
        parts.extend(["--queue-include-dir", p])
    return " ".join(parts)


def _read_outputs(output_file: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# partition_by_ci_deploy -- the split itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_mixed_prod_sandbox_keeps_prod_skips_sandbox(common_dir: pathlib.Path) -> None:
    """A scope spanning prod (ci_deploy=true) + sandbox (ci_deploy=false): the prod unit is
    deployable, the sandbox unit is skipped -- this is the #133-class scope that used to fail."""
    prod_unit = f"{_BASE}/prod/000/collector-ingestion/000"
    sandbox_unit = f"{_BASE}/sandbox/000/collector-ingestion/000"
    deployable, skipped = partition_by_ci_deploy(
        _flags(prod_unit, sandbox_unit), common_dir / "accounts.json"
    )
    assert deployable == [prod_unit]
    assert skipped == [(sandbox_unit, "100000000001")]


@pytest.mark.unit
def test_partition_sandbox_only_yields_empty_deployable(common_dir: pathlib.Path) -> None:
    """A sandbox-only (entirely ci_deploy=false) scope yields zero deployable units (no-op)."""
    sandbox_unit = f"{_BASE}/sandbox/000/portal/000"
    deployable, skipped = partition_by_ci_deploy(_flags(sandbox_unit), common_dir / "accounts.json")
    assert deployable == []
    assert skipped == [(sandbox_unit, "100000000001")]


@pytest.mark.unit
def test_partition_prod_only_is_unchanged(common_dir: pathlib.Path) -> None:
    """A prod-only scope is deployable in full with nothing skipped (the common case)."""
    units = [f"{_BASE}/prod/000/portal/000", f"{_BASE}/prod/000/identity/000"]
    deployable, skipped = partition_by_ci_deploy(_flags(*units), common_dir / "accounts.json")
    assert deployable == units
    assert skipped == []


@pytest.mark.unit
def test_partition_keeps_dns_owner_units_even_under_sandbox_folder(
    common_dir: pathlib.Path,
) -> None:
    """ci_deploy is keyed on the RESOLVED account, not the env folder: a sandbox-folder
    _singletons/dns_owner unit runs in the shared dns-owner (ci_deploy=true) account and is KEPT,
    while the sandbox SERVICE unit (ci_deploy=false) is skipped."""
    prod_unit = f"{_BASE}/prod/000/portal/000"  # prod service -> deployable
    prod_dns = f"{_BASE}/prod/_singletons/dns_owner/dns-delegation/000"  # dns-owner -> deployable
    sandbox_dns = f"{_BASE}/sandbox/_singletons/dns_owner/dns-delegation/000"  # dns-owner -> keep
    sandbox_svc = f"{_BASE}/sandbox/000/portal/000"  # sandbox account -> skip
    sandbox_pretty = f"{_BASE}/sandbox/_singletons/pretty/portal/000"  # sandbox env zone -> skip
    deployable, skipped = partition_by_ci_deploy(
        _flags(prod_unit, prod_dns, sandbox_dns, sandbox_svc, sandbox_pretty),
        common_dir / "accounts.json",
    )
    assert deployable == [prod_unit, prod_dns, sandbox_dns]
    assert sorted(skipped) == sorted(
        [(sandbox_svc, "100000000001"), (sandbox_pretty, "100000000001")]
    )


# ---------------------------------------------------------------------------
# On-demand ephemeral-apply lane (Phase 0)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_on_demand_keeps_eligible_sandbox(common_dir: pathlib.Path) -> None:
    """With on_demand=True the eligible sandbox units are KEPT (deployable), not skipped, so an
    on-demand run applies the sandbox stack instead of filtering it out."""
    prod_unit = f"{_BASE}/prod/000/collector-ingestion/000"
    sandbox_unit = f"{_BASE}/sandbox/000/collector-ingestion/000"
    deployable, skipped = partition_by_ci_deploy(
        _flags(prod_unit, sandbox_unit), common_dir / "accounts.json", on_demand=True
    )
    assert deployable == [prod_unit, sandbox_unit]
    assert skipped == []


@pytest.mark.unit
def test_partition_on_demand_ineligible_account_still_skipped(common_dir: pathlib.Path) -> None:
    """on_demand=True must NOT keep a ci_deploy=false account that lacks ci_deploy_on_demand."""
    bad = json.loads(json.dumps(ACCOUNTS))
    del bad["100000000001"]["ci_deploy_on_demand"]  # eligible -> ineligible
    (common_dir / "accounts.json").write_text(json.dumps(bad), encoding="utf-8")
    sandbox_unit = f"{_BASE}/sandbox/000/portal/000"
    deployable, skipped = partition_by_ci_deploy(
        _flags(sandbox_unit), common_dir / "accounts.json", on_demand=True
    )
    assert deployable == []
    assert skipped == [(sandbox_unit, "100000000001")]


@pytest.mark.unit
def test_main_on_demand_env_keeps_sandbox(
    common_dir: pathlib.Path,
    output_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() with TT_ON_DEMAND_APPLY set keeps the sandbox units (has_units=true)."""
    monkeypatch.setenv("TT_ON_DEMAND_APPLY", "yes")
    sandbox_unit = f"{_BASE}/sandbox/000/collector-ingestion/000"
    monkeypatch.setattr(sys, "argv", _argv(common_dir, output_file, _flags(sandbox_unit)))
    assert main() == 0
    out = _read_outputs(output_file)
    assert out["has_units"] == "true"
    assert out["include_dir_flags"] == f"--queue-include-dir {sandbox_unit}"


@pytest.mark.unit
def test_main_without_on_demand_env_skips_sandbox(
    common_dir: pathlib.Path,
    output_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() without the env opt-in still filters sandbox units out (no-op, has_units=false)."""
    monkeypatch.delenv("TT_ON_DEMAND_APPLY", raising=False)
    sandbox_unit = f"{_BASE}/sandbox/000/collector-ingestion/000"
    monkeypatch.setattr(sys, "argv", _argv(common_dir, output_file, _flags(sandbox_unit)))
    assert main() == 0
    out = _read_outputs(output_file)
    assert out["has_units"] == "false"
    assert out["include_dir_flags"] == ""


# ---------------------------------------------------------------------------
# Fail-fast (genuine configuration errors are never silently skipped)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_unknown_account_fails_fast(
    common_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A unit whose resolved account is not registered in accounts.json fails fast (not skipped)."""
    accounts_no_prod = {k: v for k, v in ACCOUNTS.items() if k != "100000000002"}
    (common_dir / "accounts.json").write_text(json.dumps(accounts_no_prod), encoding="utf-8")
    with pytest.raises(AccountNotFoundError):
        partition_by_ci_deploy(_flags(f"{_BASE}/prod/000/portal/000"), common_dir / "accounts.json")


@pytest.mark.unit
def test_partition_missing_ci_deploy_key_fails_fast(common_dir: pathlib.Path) -> None:
    """An account row missing the ci_deploy key fails fast (no default; never assumed skippable)."""
    bad = json.loads(json.dumps(ACCOUNTS))
    del bad["100000000002"]["ci_deploy"]
    (common_dir / "accounts.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(AccountNotFoundError, match="ci_deploy"):
        partition_by_ci_deploy(_flags(f"{_BASE}/prod/000/portal/000"), common_dir / "accounts.json")


@pytest.mark.unit
def test_partition_unknown_env_fails_fast(common_dir: pathlib.Path) -> None:
    """An env not present in env_accounts.json fails fast (no silent skip)."""
    with pytest.raises(AccountResolutionError):
        partition_by_ci_deploy(
            _flags(f"{_BASE}/staging/000/portal/000"), common_dir / "accounts.json"
        )


@pytest.mark.unit
def test_partition_empty_flags_fails_fast(common_dir: pathlib.Path) -> None:
    """An empty flags string fails fast (the workflow only invokes the filter on a non-empty
    scope; an empty scope reaching the filter is a contract violation)."""
    with pytest.raises(PathParseError):
        partition_by_ci_deploy("", common_dir / "accounts.json")


# ---------------------------------------------------------------------------
# run() -- GitHub Actions output contract + transparent skip log
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_mixed_writes_prod_flags_has_units_true_and_logs_skip(
    common_dir: pathlib.Path, output_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() on a mixed scope writes the prod-only include_dir_flags + has_units=true and logs the
    skipped sandbox unit on a visible line (never silent)."""
    prod_unit = f"{_BASE}/prod/000/portal/000"
    sandbox_unit = f"{_BASE}/sandbox/000/portal/000"
    run(_flags(prod_unit, sandbox_unit), common_dir / "accounts.json", str(output_file))

    out = _read_outputs(output_file)
    assert out["has_units"] == "true"
    assert out["include_dir_flags"] == f"--queue-include-dir {prod_unit}"
    assert "sandbox" not in out["include_dir_flags"]

    captured = capsys.readouterr().out
    assert SKIP_LOG_PREFIX in captured
    assert sandbox_unit in captured


@pytest.mark.unit
def test_run_sandbox_only_writes_has_units_false_no_op(
    common_dir: pathlib.Path, output_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() on a fully local-only scope writes has_units=false + empty flags (clean CI no-op)."""
    sandbox_unit = f"{_BASE}/sandbox/000/portal/000"
    run(_flags(sandbox_unit), common_dir / "accounts.json", str(output_file))

    out = _read_outputs(output_file)
    assert out["has_units"] == "false"
    assert out["include_dir_flags"] == ""

    captured = capsys.readouterr().out
    assert SKIP_LOG_PREFIX in captured
    assert "no-op" in captured


@pytest.mark.unit
def test_run_prod_only_writes_all_flags_no_skip_log(
    common_dir: pathlib.Path, output_file: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() on a prod-only scope keeps every unit, has_units=true, and emits NO skip line."""
    units = [f"{_BASE}/prod/000/portal/000", f"{_BASE}/prod/000/identity/000"]
    run(_flags(*units), common_dir / "accounts.json", str(output_file))

    out = _read_outputs(output_file)
    assert out["has_units"] == "true"
    assert out["include_dir_flags"].count("--queue-include-dir") == 2

    assert SKIP_LOG_PREFIX not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main() -- the CLI entrypoint the Makefile/workflow actually invokes
# ---------------------------------------------------------------------------


def _argv(common_dir: pathlib.Path, output_file: pathlib.Path, flags: str) -> list[str]:
    return [
        "filter_ci_deployable_units",
        "--include-dir-flags",
        flags,
        "--accounts-json",
        str(common_dir / "accounts.json"),
        "--output",
        str(output_file),
    ]


@pytest.mark.unit
def test_main_mixed_scope_returns_zero_and_writes_outputs(
    common_dir: pathlib.Path,
    output_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() on a prod+sandbox scope exits 0 and writes prod-only flags (the #133-class case)."""
    prod_unit = f"{_BASE}/prod/000/portal/000"
    flags = _flags(prod_unit, f"{_BASE}/sandbox/000/portal/000")
    monkeypatch.setattr(sys, "argv", _argv(common_dir, output_file, flags))
    assert main() == 0
    out = _read_outputs(output_file)
    assert out["has_units"] == "true"
    assert out["include_dir_flags"] == f"--queue-include-dir {prod_unit}"


@pytest.mark.unit
def test_main_missing_config_returns_one(
    tmp_path: pathlib.Path,
    output_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() fails fast (exit 1) when the accounts.json config is absent."""
    monkeypatch.setattr(
        sys, "argv", _argv(tmp_path, output_file, _flags(f"{_BASE}/prod/000/portal/000"))
    )
    assert main() == 1


@pytest.mark.unit
def test_main_genuine_error_returns_one(
    common_dir: pathlib.Path,
    output_file: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() returns 1 (does NOT silently skip) on a genuine error -- here a resolved account that
    is absent from accounts.json."""
    accounts_no_prod = {k: v for k, v in ACCOUNTS.items() if k != "100000000002"}
    (common_dir / "accounts.json").write_text(json.dumps(accounts_no_prod), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", _argv(common_dir, output_file, _flags(f"{_BASE}/prod/000/portal/000"))
    )
    assert main() == 1


@pytest.mark.unit
def test_run_output_lines_have_no_embedded_newline(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    """Each GITHUB_OUTPUT key=value line must be single-line (the key=value contract)."""
    run(
        _flags(f"{_BASE}/prod/000/portal/000"),
        common_dir / "accounts.json",
        str(output_file),
    )
    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("include_dir_flags=") for line in lines)
    assert any(line == "has_units=true" for line in lines)
