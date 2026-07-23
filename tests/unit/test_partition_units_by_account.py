"""Unit tests for scripts/partition_units_by_account.py (per-account plan/apply matrix).

The partitioner groups a changed-unit scope by resolved AWS account so a cross-account
Terragrunt plan/apply can run each account's units under that account's own OIDC role
(multi-account CI walls 1 and 2). It reuses scripts.resolve_deploy_role.resolve_account_id for
the account resolution (DRY), so these tests focus on the grouping, role-name selection per
mode, the dns-owner role-chaining flag, and fail-fast behaviour.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from scripts.partition_units_by_account import (
    MODE_APPLY,
    MODE_PLAN,
    PartitionError,
    main,
    partition,
    run,
)
from scripts.resolve_deploy_role import (
    AccountNotFoundError,
    CIDeployForbiddenError,
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
# prod pretty served from the ROOT zone (apex asymmetry) -> _pretty resolves to dns_owner.
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
        "plan_role_name": "tt-plan",
    },
    "100000000002": {
        "account_role": "prod-infra",
        "ci_deploy": True,
        "deploy_role_name": "tt-apply",
        "plan_role_name": "tt-plan",
    },
    "100000000003": {
        "account_role": "qa-infra",
        "ci_deploy": True,
        "deploy_role_name": "tt-terratest",
        "plan_role_name": "tt-terratest",
    },
    "100000000009": {
        "account_role": "dns-owner",
        "ci_deploy": True,
        "deploy_role_name": "tt-dns-writer",
        "plan_role_name": "tt-plan",
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


def _flags(*paths: str) -> str:
    parts: list[str] = []
    for p in paths:
        parts.extend(["--queue-include-dir", p])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Grouping + role selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_groups_cross_env_pr_into_one_job_per_account(common_dir: pathlib.Path) -> None:
    """A cross-env PR touching prod-service, sandbox-service and a prod _pretty (dns-owner)
    unit must produce exactly one matrix row per resolved account, each with the right plan
    role and only that account's units (multi-account CI WALL 1)."""
    flags = _flags(
        f"{_BASE}/prod/000/acm-collector/000",
        f"{_BASE}/sandbox/000/acm-collector/000",
        f"{_BASE}/prod/_singletons/pretty/portal/000",  # prod pretty -> dns-owner account
    )
    rows = partition(flags, common_dir / "accounts.json", MODE_PLAN)
    by_account = {r["account_id"]: r for r in rows}

    assert set(by_account) == {"100000000001", "100000000002", "100000000009"}
    # prod-service + sandbox use the plan role; dns-owner pretty unit uses the dns-owner plan role
    assert by_account["100000000002"]["role_arn"] == "arn:aws:iam::100000000002:role/tt-plan"
    assert by_account["100000000001"]["role_arn"] == "arn:aws:iam::100000000001:role/tt-plan"
    assert by_account["100000000009"]["role_arn"] == "arn:aws:iam::100000000009:role/tt-plan"
    # Each row carries ONLY its account's units.
    assert by_account["100000000002"]["include_dir_flags"].count("--queue-include-dir") == 1
    assert "prod/000/acm-collector/000" in by_account["100000000002"]["include_dir_flags"]
    assert "pretty/portal/000" in by_account["100000000009"]["include_dir_flags"]
    # plan mode never chains to dns-writer.
    assert all(r["needs_dns_writer"] == "false" for r in rows)


@pytest.mark.unit
def test_partition_apply_uses_deploy_role_and_chains_dns_owner(common_dir: pathlib.Path) -> None:
    """In apply mode, the dns-owner account's units select the deploy role (dns-writer) and set
    needs_dns_writer=true; the prod-service units select the prod apply role (WALL 2)."""
    flags = _flags(
        f"{_BASE}/prod/000/dns-portal/000",
        f"{_BASE}/prod/_singletons/dns_owner/dns-delegation/000",  # dns-owner account
    )
    rows = partition(flags, common_dir / "accounts.json", MODE_APPLY)
    by_account = {r["account_id"]: r for r in rows}

    assert by_account["100000000002"]["role_arn"] == "arn:aws:iam::100000000002:role/tt-apply"
    assert by_account["100000000002"]["needs_dns_writer"] == "false"
    assert by_account["100000000009"]["role_arn"] == "arn:aws:iam::100000000009:role/tt-dns-writer"
    assert by_account["100000000009"]["needs_dns_writer"] == "true"


@pytest.mark.unit
def test_partition_single_account_pr_is_one_job(common_dir: pathlib.Path) -> None:
    """A prod-only PR partitions to a single prod job (the common case is unchanged)."""
    flags = _flags(
        f"{_BASE}/prod/000/acm-collector/000",
        f"{_BASE}/prod/000/acm-portal/000",
    )
    rows = partition(flags, common_dir / "accounts.json", MODE_PLAN)
    assert len(rows) == 1
    assert rows[0]["account_id"] == "100000000002"
    assert rows[0]["include_dir_flags"].count("--queue-include-dir") == 2


# ---------------------------------------------------------------------------
# Empty scope (deletion-only no-op)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("empty_flags", ["", "   ", "\t\n"])
def test_partition_empty_scope_is_noop_empty_matrix(
    common_dir: pathlib.Path, empty_flags: str
) -> None:
    """An EMPTY include_dir_flags (deletion-only changeset -> has_units=false from the detect
    step) partitions to ZERO account rows (an empty matrix) rather than raising -- so the
    downstream per-account plan matrix runs zero jobs and the lane is a clean no-op."""
    rows = partition(empty_flags, common_dir / "accounts.json", MODE_PLAN)
    assert rows == []


@pytest.mark.unit
def test_partition_empty_scope_apply_mode_is_noop(common_dir: pathlib.Path) -> None:
    """The empty-scope no-op holds in apply mode too (no account resolution, no ci_deploy check)."""
    rows = partition("", common_dir / "accounts.json", MODE_APPLY)
    assert rows == []


@pytest.mark.unit
def test_run_writes_empty_matrix_for_empty_scope(common_dir: pathlib.Path) -> None:
    """run() must write a valid, fromJSON-parseable ``matrix={"include": []}`` (exit-0 no-op) for
    an empty scope; an empty ``matrix.include`` expands to zero GitHub Actions plan jobs."""
    out = common_dir / "gh_output"
    run("", common_dir / "accounts.json", MODE_PLAN, str(out))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    matrix_line = next(line for line in lines if line.startswith("matrix="))
    payload = json.loads(matrix_line[len("matrix=") :])
    assert payload == {"include": []}
    assert matrix_line.count("\n") == 0


# ---------------------------------------------------------------------------
# Fail-fast
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_apply_rejects_local_only_account(common_dir: pathlib.Path) -> None:
    """Apply mode must fail fast for a ci_deploy=false account (sandbox, D33)."""
    flags = _flags(f"{_BASE}/sandbox/000/acm-collector/000")
    with pytest.raises(CIDeployForbiddenError):
        partition(flags, common_dir / "accounts.json", MODE_APPLY)


@pytest.mark.unit
def test_partition_plan_allows_local_only_account(common_dir: pathlib.Path) -> None:
    """Plan mode (read-only) MUST allow a ci_deploy=false account -- sandbox is plan-only in CI."""
    flags = _flags(f"{_BASE}/sandbox/000/acm-collector/000")
    rows = partition(flags, common_dir / "accounts.json", MODE_PLAN)
    assert len(rows) == 1
    assert rows[0]["account_id"] == "100000000001"
    assert rows[0]["role_arn"] == "arn:aws:iam::100000000001:role/tt-plan"


# ---------------------------------------------------------------------------
# On-demand ephemeral-apply lane (Phase 0)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_partition_apply_on_demand_includes_eligible_sandbox(common_dir: pathlib.Path) -> None:
    """Apply mode with on_demand=True partitions the eligible sandbox account under its DEPLOY role
    (the on-demand ephemeral-apply lane); no dns-writer chaining for a sandbox-service scope."""
    flags = _flags(f"{_BASE}/sandbox/000/acm-collector/000")
    rows = partition(flags, common_dir / "accounts.json", MODE_APPLY, on_demand=True)
    assert len(rows) == 1
    assert rows[0]["account_id"] == "100000000001"
    assert rows[0]["role_arn"] == "arn:aws:iam::100000000001:role/tt-apply"
    assert rows[0]["needs_dns_writer"] == "false"


@pytest.mark.unit
def test_partition_apply_on_demand_ineligible_still_fails_fast(common_dir: pathlib.Path) -> None:
    """on_demand=True must NOT unlock a ci_deploy=false account that lacks ci_deploy_on_demand."""
    bad = json.loads(json.dumps(ACCOUNTS))
    del bad["100000000001"]["ci_deploy_on_demand"]  # eligible -> ineligible
    (common_dir / "accounts.json").write_text(json.dumps(bad), encoding="utf-8")
    flags = _flags(f"{_BASE}/sandbox/000/acm-collector/000")
    with pytest.raises(CIDeployForbiddenError):
        partition(flags, common_dir / "accounts.json", MODE_APPLY, on_demand=True)


@pytest.mark.unit
def test_partition_missing_plan_role_name_fails_fast(
    common_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A plan-mode account row missing plan_role_name aborts with an actionable error."""
    bad_accounts = json.loads(json.dumps(ACCOUNTS))
    del bad_accounts["100000000002"]["plan_role_name"]
    (common_dir / "accounts.json").write_text(json.dumps(bad_accounts), encoding="utf-8")
    flags = _flags(f"{_BASE}/prod/000/acm-collector/000")
    with pytest.raises(AccountNotFoundError, match="plan_role_name"):
        partition(flags, common_dir / "accounts.json", MODE_PLAN)


@pytest.mark.unit
def test_partition_unregistered_account_fails_fast(
    common_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A unit resolving to an account id absent from accounts.json aborts fast."""
    bad_accounts = json.loads(json.dumps(ACCOUNTS))
    del bad_accounts["100000000003"]  # qa account resolved below, now unregistered
    (common_dir / "accounts.json").write_text(json.dumps(bad_accounts), encoding="utf-8")
    flags = _flags(f"{_BASE}/qa/000/acm-collector/000")
    with pytest.raises(AccountNotFoundError, match="not registered"):
        partition(flags, common_dir / "accounts.json", MODE_PLAN)


@pytest.mark.unit
def test_partition_missing_ci_deploy_key_fails_fast(common_dir: pathlib.Path) -> None:
    """An account row missing the required ci_deploy key aborts fast."""
    bad_accounts = json.loads(json.dumps(ACCOUNTS))
    del bad_accounts["100000000002"]["ci_deploy"]
    (common_dir / "accounts.json").write_text(json.dumps(bad_accounts), encoding="utf-8")
    flags = _flags(f"{_BASE}/prod/000/acm-collector/000")
    with pytest.raises(AccountNotFoundError, match="ci_deploy"):
        partition(flags, common_dir / "accounts.json", MODE_PLAN)


@pytest.mark.unit
def test_partition_nonempty_tokenless_scope_still_fails_fast(common_dir: pathlib.Path) -> None:
    """A NON-empty but tokenless include_dir_flags is genuinely malformed (not an empty scope) and
    must still fail fast via extract_all_unit_paths -- the empty-scope no-op never masks it."""
    with pytest.raises(PathParseError):
        partition("garbage-no-token", common_dir / "accounts.json", MODE_PLAN)


@pytest.mark.unit
def test_partition_unknown_mode_fails_fast(common_dir: pathlib.Path) -> None:
    """An unknown mode aborts fast."""
    flags = _flags(f"{_BASE}/prod/000/acm-collector/000")
    with pytest.raises(PartitionError, match="unknown mode"):
        partition(flags, common_dir / "accounts.json", "destroy")


# ---------------------------------------------------------------------------
# run() output contract (GitHub Actions matrix JSON)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_writes_fromjson_compatible_matrix(common_dir: pathlib.Path) -> None:
    """run() must write a single-line ``matrix=<json>`` output parseable by fromJSON with an
    'include' list."""
    flags = _flags(
        f"{_BASE}/prod/000/acm-collector/000",
        f"{_BASE}/sandbox/000/acm-collector/000",
    )
    out = common_dir / "gh_output"
    run(flags, common_dir / "accounts.json", MODE_PLAN, str(out))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    matrix_line = next(line for line in lines if line.startswith("matrix="))
    payload = json.loads(matrix_line[len("matrix=") :])
    assert "include" in payload
    assert {row["account_id"] for row in payload["include"]} == {"100000000001", "100000000002"}
    # single-line JSON (no embedded newline would break the GITHUB_OUTPUT key=value contract)
    assert matrix_line.count("\n") == 0


# ---------------------------------------------------------------------------
# CLI entrypoint (main) -- the boundary the tg-partition-units workflow step invokes
# ---------------------------------------------------------------------------


def _run_main(
    common_dir: pathlib.Path,
    include_dir_flags: str,
    mode: str,
    out: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    accounts_json: pathlib.Path | None = None,
) -> int:
    """Invoke main() with a synthesised argv (mirrors the Makefile tg-partition-units call)."""
    accounts_path = accounts_json if accounts_json is not None else common_dir / "accounts.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "partition_units_by_account",
            "--include-dir-flags",
            include_dir_flags,
            "--accounts-json",
            str(accounts_path),
            "--mode",
            mode,
            "--output",
            str(out),
        ],
    )
    return main()


@pytest.mark.unit
def test_main_empty_scope_exits_zero_with_empty_matrix(
    common_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end at the CLI boundary: an EMPTY --include-dir-flags (deletion-only no-op) exits 0
    and writes matrix={"include": []} -- zero downstream plan jobs, a clean green lane."""
    out = common_dir / "gh_output"
    rc = _run_main(common_dir, "", MODE_PLAN, out, monkeypatch)
    assert rc == 0
    matrix_line = next(
        line for line in out.read_text(encoding="utf-8").splitlines() if line.startswith("matrix=")
    )
    assert json.loads(matrix_line[len("matrix=") :]) == {"include": []}


@pytest.mark.unit
def test_main_nonempty_scope_exits_zero(
    common_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal non-empty scope exits 0 through main() and writes a populated matrix."""
    out = common_dir / "gh_output"
    flags = _flags(f"{_BASE}/prod/000/acm-collector/000")
    rc = _run_main(common_dir, flags, MODE_PLAN, out, monkeypatch)
    assert rc == 0
    matrix_line = next(
        line for line in out.read_text(encoding="utf-8").splitlines() if line.startswith("matrix=")
    )
    assert {r["account_id"] for r in json.loads(matrix_line[len("matrix=") :])["include"]} == {
        "100000000002"
    }


@pytest.mark.unit
def test_main_missing_config_exits_one(
    common_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns 1 when the accounts.json (or a required sibling) does not exist."""
    rc = _run_main(
        common_dir,
        "",
        MODE_PLAN,
        common_dir / "gh_output",
        monkeypatch,
        accounts_json=tmp_path / "nonexistent" / "accounts.json",
    )
    assert rc == 1


@pytest.mark.unit
def test_main_malformed_flags_exits_one(
    common_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-empty but tokenless --include-dir-flags is malformed: main() catches PathParseError
    and returns 1 (the empty-scope no-op never masks a genuinely broken scope)."""
    rc = _run_main(common_dir, "not-a-flag", MODE_PLAN, common_dir / "gh_output", monkeypatch)
    assert rc == 1


@pytest.mark.unit
def test_main_apply_sandbox_on_demand_env_exits_zero(
    common_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply mode + TT_ON_DEMAND_APPLY set: main() exits 0 and emits a sandbox deploy-role row."""
    monkeypatch.setenv("TT_ON_DEMAND_APPLY", "true")
    out = common_dir / "gh_output"
    flags = _flags(f"{_BASE}/sandbox/000/acm-collector/000")
    rc = _run_main(common_dir, flags, MODE_APPLY, out, monkeypatch)
    assert rc == 0
    matrix_line = next(
        line for line in out.read_text(encoding="utf-8").splitlines() if line.startswith("matrix=")
    )
    rows = json.loads(matrix_line[len("matrix=") :])["include"]
    assert [r["account_id"] for r in rows] == ["100000000001"]
    assert rows[0]["role_arn"] == "arn:aws:iam::100000000001:role/tt-apply"


@pytest.mark.unit
def test_main_apply_sandbox_without_on_demand_env_exits_one(
    common_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply mode without the env opt-in: main() fails fast (exit 1) for a sandbox scope."""
    monkeypatch.delenv("TT_ON_DEMAND_APPLY", raising=False)
    flags = _flags(f"{_BASE}/sandbox/000/acm-collector/000")
    rc = _run_main(common_dir, flags, MODE_APPLY, common_dir / "gh_output", monkeypatch)
    assert rc == 1
