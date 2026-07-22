"""Unit tests for scripts/resolve_deploy_role.py (env-keyed account resolution).

The resolver mirrors the runtime account.hcl tier rules: it parses the env (or bootstrap role)
from the env-keyed live-tree path and resolves the AWS account from common/env_accounts.json +
common/domains.json (account abstracted OUT of the folder path, D2), then looks up
deploy_role_name + ci_deploy in common/accounts.json (still account-id-keyed).
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from scripts.resolve_deploy_role import (
    AccountNotFoundError,
    AccountResolutionError,
    CIDeployForbiddenError,
    MixedAccountScopeError,
    PathParseError,
    account_allows_ci_apply,
    build_role_arn,
    extract_all_unit_paths,
    is_account_ci_deployable,
    load_account_entry,
    main,
    on_demand_opt_in,
    parse_unit_path,
    resolve_account_id,
    resolve_deploy_role,
)

# ---------------------------------------------------------------------------
# Fixtures (account ids are arbitrary test doubles, not the real accounts)
# ---------------------------------------------------------------------------

ENV_ACCOUNTS = {
    "envs": {
        "sandbox": {
            "account_id": "100000000001",
            "aws_profile": "t-sandbox",
            "use_pinned_module_sources": False,
        },
        "prod": {
            "account_id": "100000000002",
            "aws_profile": "t-prod",
            "use_pinned_module_sources": True,
        },
        "qa": {
            "account_id": "100000000003",
            "aws_profile": "t-qa",
            "use_pinned_module_sources": False,
        },
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
        "deploy_role_name": "tt-apply",
        "ci_deploy": False,
        # On-demand ephemeral-apply eligibility (Phase 0): sandbox is applyable by CI ONLY when a
        # run also opts in via TT_ON_DEMAND_APPLY (mirrors the real accounts.json sandbox row).
        "ci_deploy_on_demand": True,
    },
    "100000000002": {
        "account_role": "prod-infra",
        "deploy_role_name": "tt-apply",
        "ci_deploy": True,
    },
    "100000000003": {
        "account_role": "qa-infra",
        "deploy_role_name": "tt-terratest",
        "ci_deploy": True,
    },
    "100000000009": {
        "account_role": "dns-owner",
        "deploy_role_name": "tt-dns-writer",
        "ci_deploy": True,
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


# ---------------------------------------------------------------------------
# resolve_account_id -- tier resolution (the env-keyed contract)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "unit_path,expected_account_id",
    [
        (f"{_BASE}/prod/000/collector-ingestion/000", "100000000002"),  # service tier
        (f"{_BASE}/sandbox/000/collector-ingestion/000", "100000000001"),  # service tier
        (f"{_BASE}/qa/000/identity/000", "100000000003"),  # service tier
        (f"{_BASE}/bootstrap/qa_role/oidc-bootstrap/000", "100000000003"),  # bootstrap role
        (f"{_BASE}/bootstrap/prod_role/state-bootstrap/000", "100000000002"),  # bootstrap role
        (
            f"{_BASE}/bootstrap/dns_owner_role/state-bootstrap/000",
            "100000000009",
        ),  # bootstrap dns_owner
        (
            f"{_BASE}/sandbox/_singletons/dns_owner/dns-delegation/000",
            "100000000009",
        ),  # dns_owner tier
        (f"{_BASE}/prod/_singletons/pretty/collector/000", "100000000009"),  # pretty -> root zone
        (f"{_BASE}/sandbox/_singletons/pretty/collector/000", "100000000001"),  # pretty -> env zone
    ],
)
def test_resolve_account_id_per_tier(unit_path: str, expected_account_id: str) -> None:
    """resolve_account_id maps each path tier to the correct account via env_accounts + domains."""
    region, account_id = resolve_account_id(unit_path, ENV_ACCOUNTS, DOMAINS)
    assert region == "us-east-1"
    assert account_id == expected_account_id, (
        f"{unit_path} -> {account_id}, expected {expected_account_id}"
    )


@pytest.mark.unit
def test_resolve_account_id_unknown_env_fails_fast() -> None:
    """An env not present in env_accounts.json fails fast (no silent default)."""
    with pytest.raises(AccountResolutionError):
        resolve_account_id(f"{_BASE}/staging/000/portal/000", ENV_ACCOUNTS, DOMAINS)


@pytest.mark.unit
def test_resolve_account_id_bootstrap_without_role_fails_fast() -> None:
    with pytest.raises(PathParseError):
        resolve_account_id(f"{_BASE}/bootstrap", ENV_ACCOUNTS, DOMAINS)


@pytest.mark.unit
@pytest.mark.parametrize("bad_path", ["", "not/a/live/path", "terragrunt/live/telemetry/us-east-1"])
def test_parse_unit_path_rejects_non_grammar(bad_path: str) -> None:
    with pytest.raises(PathParseError):
        parse_unit_path(bad_path)


# ---------------------------------------------------------------------------
# build_role_arn + accounts.json lookup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_role_arn_formats_correctly() -> None:
    assert build_role_arn("100000000002", "tt-apply") == "arn:aws:iam::100000000002:role/tt-apply"


@pytest.mark.unit
def test_load_account_entry_unknown_account_fails_fast(common_dir: pathlib.Path) -> None:
    with pytest.raises(AccountNotFoundError):
        load_account_entry("999999999999", common_dir / "accounts.json")


@pytest.mark.unit
def test_load_account_entry_ci_deploy_false_fails_fast(common_dir: pathlib.Path) -> None:
    """A local-only account (ci_deploy=false, e.g. sandbox) cannot be applied via CI (D33)."""
    with pytest.raises(CIDeployForbiddenError):
        load_account_entry("100000000001", common_dir / "accounts.json")


@pytest.mark.unit
@pytest.mark.parametrize("missing_key", ["deploy_role_name", "ci_deploy"])
def test_load_account_entry_missing_required_key_fails_fast(
    tmp_path: pathlib.Path, missing_key: str
) -> None:
    entry = {"account_role": "prod-infra", "deploy_role_name": "tt-apply", "ci_deploy": True}
    del entry[missing_key]
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps({"100000000002": entry}), encoding="utf-8")
    with pytest.raises(AccountNotFoundError):
        load_account_entry("100000000002", p)


# ---------------------------------------------------------------------------
# is_account_ci_deployable -- returns the flag (does NOT raise on ci_deploy=false)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "account_id,expected",
    [
        ("100000000002", True),  # prod-infra
        ("100000000009", True),  # dns-owner
        ("100000000001", False),  # sandbox (local-only, D33) -- returned, NOT raised
    ],
)
def test_is_account_ci_deployable_returns_flag(account_id: str, expected: bool) -> None:
    """The flag is returned for both true and false ci_deploy (the false case lets a caller SKIP
    a local-only unit instead of aborting -- unlike load_account_entry which raises)."""
    assert is_account_ci_deployable(account_id, ACCOUNTS, pathlib.Path("accounts.json")) is expected


@pytest.mark.unit
def test_is_account_ci_deployable_unknown_account_fails_fast() -> None:
    with pytest.raises(AccountNotFoundError):
        is_account_ci_deployable("999999999999", ACCOUNTS, pathlib.Path("accounts.json"))


@pytest.mark.unit
def test_is_account_ci_deployable_missing_key_fails_fast() -> None:
    accounts = {"100000000002": {"account_role": "prod-infra", "deploy_role_name": "tt-apply"}}
    with pytest.raises(AccountNotFoundError, match="ci_deploy"):
        is_account_ci_deployable("100000000002", accounts, pathlib.Path("accounts.json"))


# ---------------------------------------------------------------------------
# On-demand ephemeral-apply opt-in (Phase 0)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("  Yes  ", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
        ("maybe", False),
    ],
)
def test_on_demand_opt_in_reads_truthy_env(env_value: str, expected: bool) -> None:
    """The opt-in is TT_ON_DEMAND_APPLY set to a truthy value (case-insensitive, trimmed)."""
    assert on_demand_opt_in({"TT_ON_DEMAND_APPLY": env_value}) is expected


@pytest.mark.unit
def test_on_demand_opt_in_absent_env_is_false() -> None:
    """An absent TT_ON_DEMAND_APPLY means the normal (non-on-demand) lane."""
    assert on_demand_opt_in({}) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry,on_demand,expected",
    [
        # ci_deploy=true is always applyable, on_demand irrelevant.
        ({"ci_deploy": True}, False, True),
        ({"ci_deploy": True}, True, True),
        # ci_deploy=false + eligible: applyable ONLY when the run opts in.
        ({"ci_deploy": False, "ci_deploy_on_demand": True}, True, True),
        ({"ci_deploy": False, "ci_deploy_on_demand": True}, False, False),
        # ci_deploy=false + NOT eligible: opting in alone must NOT unlock it.
        ({"ci_deploy": False, "ci_deploy_on_demand": False}, True, False),
        ({"ci_deploy": False}, True, False),  # key absent -> not eligible
        ({"ci_deploy": False}, False, False),
    ],
)
def test_account_allows_ci_apply_predicate(
    entry: dict[str, object], on_demand: bool, expected: bool
) -> None:
    """Both keys must hold to apply a ci_deploy=false account: the per-run env opt-in AND the
    per-account ci_deploy_on_demand eligibility (defense in depth)."""
    assert account_allows_ci_apply(entry, on_demand) is expected


@pytest.mark.unit
def test_is_account_ci_deployable_on_demand_keeps_eligible_sandbox() -> None:
    """With on_demand=True the eligible sandbox account reports applyable (filter KEEPS its units);
    without it, sandbox is still local-only (skipped)."""
    p = pathlib.Path("accounts.json")
    assert is_account_ci_deployable("100000000001", ACCOUNTS, p, on_demand=True) is True
    assert is_account_ci_deployable("100000000001", ACCOUNTS, p, on_demand=False) is False


@pytest.mark.unit
def test_load_account_entry_on_demand_permits_eligible_sandbox(common_dir: pathlib.Path) -> None:
    """load_account_entry(on_demand=True) returns the eligible sandbox entry instead of raising."""
    entry = load_account_entry("100000000001", common_dir / "accounts.json", on_demand=True)
    assert entry["deploy_role_name"] == "tt-apply"


@pytest.mark.unit
def test_load_account_entry_on_demand_ineligible_still_fails_fast(
    common_dir: pathlib.Path,
) -> None:
    """on_demand=True must NOT unlock a ci_deploy=false account that lacks ci_deploy_on_demand."""
    bad = json.loads((common_dir / "accounts.json").read_text(encoding="utf-8"))
    del bad["100000000001"]["ci_deploy_on_demand"]  # eligible -> ineligible
    p = common_dir / "accounts.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(CIDeployForbiddenError):
        load_account_entry("100000000001", p, on_demand=True)


@pytest.mark.unit
def test_resolve_deploy_role_on_demand_sandbox_writes_outputs(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    """A sandbox service unit resolves its deploy role under the on-demand lane (no fail-fast)."""
    resolve_deploy_role(
        unit_path=f"{_BASE}/sandbox/000/collector-ingestion/000",
        accounts_json_path=common_dir / "accounts.json",
        output_path=str(output_file),
        on_demand=True,
    )
    out = _read_outputs(output_file)
    assert out["target_account_id"] == "100000000001"
    assert out["role_arn"] == "arn:aws:iam::100000000001:role/tt-apply"
    assert out["needs_dns_writer"] == "false"


@pytest.mark.unit
def test_resolve_deploy_role_sandbox_without_on_demand_fails_fast(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    """Absent the on-demand opt-in, a sandbox service unit still fails fast (sandbox stays
    NOT always-on in the normal lane)."""
    with pytest.raises(CIDeployForbiddenError):
        resolve_deploy_role(
            unit_path=f"{_BASE}/sandbox/000/collector-ingestion/000",
            accounts_json_path=common_dir / "accounts.json",
            output_path=str(output_file),
        )


# ---------------------------------------------------------------------------
# extract_all_unit_paths + mixed-scope guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_all_unit_paths() -> None:
    flags = (
        f"--queue-include-dir {_BASE}/prod/000/portal/000 "
        f"--queue-include-dir {_BASE}/prod/000/identity/000"
    )
    assert extract_all_unit_paths(flags) == [
        f"{_BASE}/prod/000/portal/000",
        f"{_BASE}/prod/000/identity/000",
    ]


@pytest.mark.unit
def test_extract_all_unit_paths_empty_fails_fast() -> None:
    with pytest.raises(PathParseError):
        extract_all_unit_paths("")


# ---------------------------------------------------------------------------
# resolve_deploy_role -- end to end (writes GH outputs)
# ---------------------------------------------------------------------------


def _read_outputs(output_file: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


@pytest.mark.unit
def test_resolve_deploy_role_writes_outputs_for_prod_service(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    resolve_deploy_role(
        unit_path=f"{_BASE}/prod/000/collector-ingestion/000",
        accounts_json_path=common_dir / "accounts.json",
        output_path=str(output_file),
    )
    out = _read_outputs(output_file)
    assert out["role_arn"] == "arn:aws:iam::100000000002:role/tt-apply"
    assert out["aws_region"] == "us-east-1"
    assert out["target_account_id"] == "100000000002"
    assert out["needs_dns_writer"] == "false"
    assert out["dns_writer_arn"] == ""


@pytest.mark.unit
def test_resolve_deploy_role_service_plus_dns_owner_scope_chains_dns_writer(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    """An env apply spanning the service account + its dns_owner units does NOT fail: the primary
    role is the service account and needs_dns_writer/dns_writer_arn surface for the chain step."""
    flags = (
        f"--queue-include-dir {_BASE}/prod/000/portal/000 "
        f"--queue-include-dir {_BASE}/prod/_singletons/dns_owner/dns-delegation/000"
    )
    resolve_deploy_role(
        unit_path=None,
        accounts_json_path=common_dir / "accounts.json",
        output_path=str(output_file),
        include_dir_flags=flags,
    )
    out = _read_outputs(output_file)
    assert out["target_account_id"] == "100000000002"  # the prod service account is primary
    assert out["needs_dns_writer"] == "true"
    assert out["dns_writer_arn"] == "arn:aws:iam::100000000009:role/tt-dns-writer"


@pytest.mark.unit
def test_resolve_deploy_role_dns_owner_only_scope_no_chain(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    """A scope of ONLY dns_owner units (e.g. bootstrap/dns_owner_role) uses the dns_owner role
    directly, with no chaining (the primary role is already in the dns_owner account)."""
    resolve_deploy_role(
        unit_path=f"{_BASE}/bootstrap/dns_owner_role/state-bootstrap/000",
        accounts_json_path=common_dir / "accounts.json",
        output_path=str(output_file),
    )
    out = _read_outputs(output_file)
    assert out["target_account_id"] == "100000000009"
    assert out["needs_dns_writer"] == "false"
    assert out["dns_writer_arn"] == ""


@pytest.mark.unit
def test_resolve_deploy_role_mixed_account_scope_fails_fast(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    flags = (
        f"--queue-include-dir {_BASE}/prod/000/portal/000 "
        f"--queue-include-dir {_BASE}/qa/000/identity/000"
    )
    with pytest.raises(MixedAccountScopeError):
        resolve_deploy_role(
            unit_path=None,
            accounts_json_path=common_dir / "accounts.json",
            output_path=str(output_file),
            include_dir_flags=flags,
        )


@pytest.mark.unit
def test_resolve_deploy_role_single_account_multi_unit_scope_ok(
    common_dir: pathlib.Path, output_file: pathlib.Path
) -> None:
    flags = (
        f"--queue-include-dir {_BASE}/prod/000/portal/000 "
        f"--queue-include-dir {_BASE}/prod/000/identity/000"
    )
    resolve_deploy_role(
        unit_path=None,
        accounts_json_path=common_dir / "accounts.json",
        output_path=str(output_file),
        include_dir_flags=flags,
    )
    assert _read_outputs(output_file)["target_account_id"] == "100000000002"


# ---------------------------------------------------------------------------
# main() CLI boundary -- reads TT_ON_DEMAND_APPLY from the process environment
# ---------------------------------------------------------------------------


def _run_main(
    common_dir: pathlib.Path,
    unit_path: str,
    out: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_deploy_role",
            "--unit-path",
            unit_path,
            "--accounts-json",
            str(common_dir / "accounts.json"),
            "--output",
            str(out),
        ],
    )
    return main()


@pytest.mark.unit
def test_main_sandbox_on_demand_env_exits_zero(
    common_dir: pathlib.Path, output_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With TT_ON_DEMAND_APPLY set, main() resolves the sandbox deploy role and exits 0."""
    monkeypatch.setenv("TT_ON_DEMAND_APPLY", "1")
    rc = _run_main(
        common_dir, f"{_BASE}/sandbox/000/collector-ingestion/000", output_file, monkeypatch
    )
    assert rc == 0
    assert _read_outputs(output_file)["target_account_id"] == "100000000001"


@pytest.mark.unit
def test_main_sandbox_without_on_demand_env_exits_one(
    common_dir: pathlib.Path, output_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent the env opt-in, main() fails fast (exit 1) for a sandbox unit (unchanged lane)."""
    monkeypatch.delenv("TT_ON_DEMAND_APPLY", raising=False)
    rc = _run_main(
        common_dir, f"{_BASE}/sandbox/000/collector-ingestion/000", output_file, monkeypatch
    )
    assert rc == 1
