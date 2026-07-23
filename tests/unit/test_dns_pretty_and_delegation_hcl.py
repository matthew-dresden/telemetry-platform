"""Unit tests for the active-switch DNS units: the config-derived _singletons/pretty tier
(pretty service collector) and the _singletons/dns_owner/dns-delegation unit.

These supersede the old test_prod_dns_delegation_and_pretty_hcl.py, which targeted the
pre-refactor design (account-keyed folder paths and the dns-collector-pretty
unit homed in dns_owner). The env-keyed instance-set layout instead uses:

  <env>/_singletons/pretty/collector   -- the active-set pretty CNAME,
      whose account + zone are resolved from config by the apex asymmetry (D2 decision):
      dns_pretty_apex == dns_service_apex (sandbox) -> env service account + env zone;
      dns_pretty_apex != dns_service_apex (prod)    -> dns-owner account + shared root zone.
  <env>/_singletons/dns_owner/dns-delegation                  -- the NS delegation record.

Every assertion verifies a real contract substring read from the on-disk HCL, in BOTH the
sandbox and prod trees, so a regression in either env (or a drift between them) fails the suite.
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
US_EAST_1 = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1"

ENVS = ["sandbox", "prod"]
PRETTY_SERVICES = ["collector"]

# Pretty hostname noun per pretty service. The pretty CNAME hostname is NOT always the
# service dir basename; the collector keeps the "collector" noun.
PRETTY_HOSTNAME_NOUN = {"collector": "collector"}


def _pretty_dir(env: str) -> pathlib.Path:
    return US_EAST_1 / env / "_singletons" / "pretty"


def _pretty_leaf(env: str, svc: str) -> pathlib.Path:
    return _pretty_dir(env) / svc / "000" / "terragrunt.hcl"


def _delegation_leaf(env: str) -> pathlib.Path:
    return (
        US_EAST_1 / env / "_singletons" / "dns_owner" / "dns-delegation" / "000" / "terragrunt.hcl"
    )


def _read(p: pathlib.Path) -> str:
    assert p.exists(), f"expected DNS unit file not found: {p}"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# pretty tier structure (both envs)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_pretty_tier_scaffolding_present(env: str) -> None:
    """The pretty tier carries account.hcl, environment_instance.hcl, active.hcl in every env."""
    base = _pretty_dir(env)
    for fname in ("account.hcl", "environment_instance.hcl", "active.hcl"):
        assert (base / fname).exists(), (
            f"{env}/_singletons/pretty/{fname} is missing. The pretty tier needs its own "
            "account resolution, environment-instance layer, and active pointer (D2)."
        )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_layer_files_present(env: str, svc: str) -> None:
    """Each pretty unit carries service.hcl, active.hcl, 000/service_instance.hcl,
    000/terragrunt.hcl."""
    udir = _pretty_dir(env) / svc
    for rel in ("service.hcl", "active.hcl", "000/service_instance.hcl", "000/terragrunt.hcl"):
        assert (udir / rel).exists(), (
            f"{env}/_singletons/pretty/{svc}/{rel} is missing; root.hcl reads service.hcl + "
            "service_instance.hcl for the state key and tags."
        )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_pretty_active_pointer_default(env: str) -> None:
    """active.hcl defaults to 000 at the tier and unit levels (no hardcoded number elsewhere)."""
    content = _read(_pretty_dir(env) / "active.hcl")
    assert 'active = "000"' in content, (
        f'{env}/_singletons/pretty/active.hcl must declare locals.active = "000".'
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_pretty_environment_instance_is_basename_derived(env: str) -> None:
    """environment_instance is basename-derived (dir basename 'pretty'), never hardcoded."""
    content = _read(_pretty_dir(env) / "environment_instance.hcl")
    assert "basename(get_terragrunt_dir())" in content, (
        f"{env}/_singletons/pretty/environment_instance.hcl must derive environment_instance "
        "from the directory basename (D2/D47)."
    )


# ---------------------------------------------------------------------------
# pretty/account.hcl -- the config-derived apex asymmetry (the core D2 decision)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_pretty_account_resolves_from_config_not_path(env: str) -> None:
    """account.hcl reads domains.json + env_accounts.json (account NEVER in the folder path, D2)."""
    content = _read(_pretty_dir(env) / "account.hcl")
    assert "common/domains.json" in content, (
        f"{env}/_singletons/pretty/account.hcl must read common/domains.json to decide the "
        "zone/account."
    )
    assert "common/env_accounts.json" in content, (
        f"{env}/_singletons/pretty/account.hcl must read common/env_accounts.json for the "
        "account ids."
    )
    assert 'find_in_parent_folders("environment.hcl")' in content, (
        f"{env}/_singletons/pretty/account.hcl must resolve the env class from environment.hcl, "
        "not from a hardcoded account in the path."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_pretty_account_apex_asymmetry_ternary(env: str) -> None:
    """The account is the env SERVICE account when pretty_apex==service_apex, else the dns-owner
    account -- expressed as a config-derived ternary, identical in both env trees (copy-safe)."""
    content = _read(_pretty_dir(env) / "account.hcl")
    assert 'dns_pretty_apex"] == local._domain_cfg["dns_service_apex"]' in content, (
        f"{env}/_singletons/pretty/account.hcl must compute pretty_in_env_zone as "
        "dns_pretty_apex == dns_service_apex (the apex asymmetry that selects the zone/account)."
    )
    assert (
        '_pretty_in_env_zone ? local._svc_entry["account_id"] : '
        'local._env_accounts["dns_owner"]["account_id"]' in content
    ), (
        f"{env}/_singletons/pretty/account.hcl must select the env SERVICE account when the pretty "
        "name lives in the env zone, else the shared dns-owner account."
    )


# ---------------------------------------------------------------------------
# pretty CNAME unit contract (both envs, both services)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_sources_route53_record_toggle_driven(env: str, svc: str) -> None:
    """The pretty unit sources exactly primitives/route53-record, toggle-driven (local vs
    pinned)."""
    content = _read(_pretty_leaf(env, svc))
    assert "primitives/route53-record" in content, (
        f"{env} {svc} must source the route53-record primitive."
    )
    assert "use_pinned_module_sources" in content and "get_repo_root()" in content, (
        f"{env} {svc} terraform.source must be toggle-driven: local get_repo_root() path when "
        "use_pinned_module_sources is false, pinned ?ref= git URL when true (Section 4.3)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_writes_cname(env: str, svc: str) -> None:
    """The pretty record is a CNAME (the friendly name aliasing the active set's real name)."""
    content = _read(_pretty_leaf(env, svc))
    assert 'type    = "CNAME"' in content or 'type = "CNAME"' in content, (
        f"{env} {svc} must write a CNAME record (the pretty-name switch, D5)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_name_from_pretty_apex(env: str, svc: str) -> None:
    """The record NAME is <svc-noun>.${pretty_apex}, composed from domains.json (never
    hardcoded)."""
    content = _read(_pretty_leaf(env, svc))
    noun = PRETTY_HOSTNAME_NOUN[svc]  # collector -> "collector"
    assert f'pretty_fqdn = "{noun}.${{local.pretty_apex}}"' in content, (
        f"{env} {svc} pretty_fqdn must be {noun}.${{local.pretty_apex}} where pretty_apex comes "
        "from common/domains.json (AC-7, no hardcoded apex)."
    )
    assert 'pretty_apex        = local.domain_cfg["dns_pretty_apex"]' in content, (
        f"{env} {svc} must read pretty_apex from domains.json[dns_pretty_apex]."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_targets_active_set_real_record(env: str, svc: str) -> None:
    """The CNAME target is <svc>-${env_active}.${service_apex} -- the ACTIVE set's real, set-scoped
    record, with env_active read from <env>/active.hcl (no hardcoded instance number)."""
    content = _read(_pretty_leaf(env, svc))
    noun = PRETTY_HOSTNAME_NOUN[svc]  # collector -> "collector"
    assert f'target_fqdn = "{noun}-${{local.env_active}}.${{local.service_apex}}"' in content, (
        f"{env} {svc} must target {noun}-${{local.env_active}}.${{local.service_apex}} (the active "
        "set's real record)."
    )
    assert "active.hcl" in content and "env_active" in content, (
        f"{env} {svc} must read env_active from <env>/active.hcl (the active instance-set pointer, "
        "D4) rather than hardcoding the set number."
    )
    assert "records = [local.target_fqdn]" in content, (
        f"{env} {svc} must set records to the single active-set target fqdn."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_zone_id_asymmetry(env: str, svc: str) -> None:
    """zone_id is the env-zone (dns-prod-zone dependency) when the pretty name lives in the env
    zone, else the shared root zone (dns_owner_zone_id). The dependency is declared
    unconditionally so a single file serves both envs."""
    content = _read(_pretty_leaf(env, svc))
    assert 'dependency "dns_prod_zone"' in content, (
        f"{env} {svc} must declare the dns_prod_zone dependency (the env-zone zone_id source)."
    )
    assert (
        "pretty_in_env_zone ? dependency.dns_prod_zone.outputs.zone_id : local.dns_owner_zone_id"
        in content
    ), (
        f"{env} {svc} zone_id must be the env-zone dependency when pretty_in_env_zone, else "
        "dns_owner_zone_id (the shared root zone)."
    )
    assert "dns_owner_zone_id" in content, (
        f"{env} {svc} must resolve dns_owner_zone_id from common/accounts.json for the "
        "root-zone case."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_includes_root_expose(env: str, svc: str) -> None:
    """The pretty unit includes root.hcl with expose=true (so common_tags etc. are available)."""
    content = _read(_pretty_leaf(env, svc))
    assert 'include "root"' in content and "expose" in content, (
        f"{env} {svc} must include root.hcl with expose=true."
    )
    assert "include.root.locals.common_tags" in content, (
        f"{env} {svc} must apply include.root.locals.common_tags (the per-field metadata tags)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("svc", PRETTY_SERVICES)
def test_pretty_unit_identical_across_envs(svc: str) -> None:
    """The pretty unit terragrunt.hcl is IDENTICAL in sandbox and prod -- all env differences are
    resolved from config (account.hcl / domains.json), proving the copy-folder property (D2)."""
    sandbox = _read(_pretty_leaf("sandbox", svc))
    prod = _read(_pretty_leaf("prod", svc))
    assert sandbox == prod, (
        f"_singletons/pretty/{svc}/000/terragrunt.hcl differs between sandbox and prod; the pretty "
        "unit must be byte-identical across envs (account + zone come from config, not the file)."
    )


# ---------------------------------------------------------------------------
# dns-delegation (the NS delegation record, dns_owner tier)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_dns_delegation_present_and_ns(env: str) -> None:
    """dns-delegation writes an NS record sourcing route53-record, with zone_id from
    dns_owner_zone_id."""
    content = _read(_delegation_leaf(env))
    assert "primitives/route53-record" in content, (
        f"{env} dns-delegation must source the route53-record primitive."
    )
    assert 'type    = "NS"' in content or 'type = "NS"' in content, (
        f"{env} dns-delegation must write an NS delegation record."
    )
    assert "dns_owner_zone_id" in content, (
        f"{env} dns-delegation must land the NS record in the dns-owner zone "
        "(dns_owner_zone_id, D38)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_dns_delegation_records_from_dependency(env: str) -> None:
    """The NS values come from the dns-prod-zone dependency output, never hardcoded (AC-7)."""
    content = _read(_delegation_leaf(env))
    assert "dependency.dns_prod_zone.outputs.name_servers" in content, (
        f"{env} dns-delegation records must come from "
        "dependency.dns_prod_zone.outputs.name_servers, not literal NS values."
    )


# ---------------------------------------------------------------------------
# pretty validate-collector: the pretty-name SAN cert validation,
# placed in the config-derived pretty tier (env zone for sandbox, root zone for prod) so
# the cert's pretty SAN validates in whatever zone owns the pretty apex. Supersedes the old
# dns_owner/acm-validate-collector unit (which always wrote to the root zone).
# ---------------------------------------------------------------------------

# (service dir name, dependency block name, pretty hostname noun)
PRETTY_VALIDATE = [
    ("validate-collector", "acm_collector", "collector"),
]


def _pretty_validate_leaf(env: str, svc: str) -> pathlib.Path:
    return _pretty_dir(env) / svc / "000" / "terragrunt.hcl"


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc,dep,noun", PRETTY_VALIDATE)
def test_pretty_validate_unit_layer_files_present(env: str, svc: str, dep: str, noun: str) -> None:
    """Each pretty-validate unit carries service.hcl, active.hcl, 000/service_instance.hcl,
    000/terragrunt.hcl."""
    udir = _pretty_dir(env) / svc
    for rel in ("service.hcl", "active.hcl", "000/service_instance.hcl", "000/terragrunt.hcl"):
        assert (udir / rel).exists(), f"{env}/_singletons/pretty/{svc}/{rel} is missing."


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc,dep,noun", PRETTY_VALIDATE)
def test_pretty_validate_sources_route53_record_toggle_driven(
    env: str, svc: str, dep: str, noun: str
) -> None:
    """The pretty-validate unit sources route53-record, toggle-driven."""
    content = _read(_pretty_validate_leaf(env, svc))
    assert "primitives/route53-record" in content
    assert "use_pinned_module_sources" in content and "get_repo_root()" in content, (
        f"{env} {svc} terraform.source must be toggle-driven."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc,dep,noun", PRETTY_VALIDATE)
def test_pretty_validate_depends_on_active_set_cert(
    env: str, svc: str, dep: str, noun: str
) -> None:
    """The unit reads the validation CNAME from the ACTIVE set's acm cert
    domain_validation_options."""
    content = _read(_pretty_validate_leaf(env, svc))
    assert f'dependency "{dep}"' in content, (
        f"{env} {svc} must depend on the active set's {dep} cert (for its pretty-SAN DVO)."
    )
    assert "domain_validation_options" in content and "env_active" in content, (
        f"{env} {svc} must select the validation record from the active set's cert DVO "
        "(env_active)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc,dep,noun", PRETTY_VALIDATE)
def test_pretty_validate_selects_pretty_san_dvo(env: str, svc: str, dep: str, noun: str) -> None:
    """It selects the DVO whose domain_name is the pretty fqdn (<noun>.${pretty_apex})."""
    content = _read(_pretty_validate_leaf(env, svc))
    assert f'pretty_fqdn        = "{noun}.${{local.pretty_apex}}"' in content, (
        f"{env} {svc} pretty_fqdn must be {noun}.${{local.pretty_apex}}."
    )
    assert "dvo.domain_name == local.pretty_fqdn" in content, (
        f"{env} {svc} must filter domain_validation_options to the pretty fqdn entry."
    )
    assert 'type = "CNAME"' in content, f"{env} {svc} must write a CNAME validation record."


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("svc,dep,noun", PRETTY_VALIDATE)
def test_pretty_validate_zone_id_asymmetry(env: str, svc: str, dep: str, noun: str) -> None:
    """zone_id is the env-zone (dns-prod-zone dep) when pretty is in the env zone, else the root
    zone."""
    content = _read(_pretty_validate_leaf(env, svc))
    assert (
        "pretty_in_env_zone ? dependency.dns_prod_zone.outputs.zone_id : local.dns_owner_zone_id"
        in content
    ), f"{env} {svc} zone_id must be env-zone when pretty_in_env_zone, else dns_owner_zone_id."


@pytest.mark.unit
@pytest.mark.parametrize("svc,dep,noun", PRETTY_VALIDATE)
def test_pretty_validate_identical_across_envs(svc: str, dep: str, noun: str) -> None:
    """The pretty-validate unit terragrunt.hcl is byte-identical across sandbox and prod."""
    assert _read(_pretty_validate_leaf("sandbox", svc)) == _read(
        _pretty_validate_leaf("prod", svc)
    ), (
        f"pretty/{svc}/000/terragrunt.hcl must be identical across envs "
        "(config-derived account+zone)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("env", ENVS)
def test_dns_owner_no_longer_has_acm_validate(env: str) -> None:
    """The pretty-SAN validation moved to pretty; dns_owner no longer carries acm-validate units."""
    dns_owner = US_EAST_1 / env / "_singletons" / "dns_owner"
    for gone in ("acm-validate-collector", "acm-validate-portal"):
        assert not (dns_owner / gone).exists(), (
            f"{env}/_singletons/dns_owner/{gone} must be removed; the pretty-SAN validation now "
            "lives in pretty/validate-* (config-derived zone)."
        )
