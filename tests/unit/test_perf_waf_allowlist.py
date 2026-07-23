"""Unit tests for scripts/perf_waf_allowlist.py.

The helper adds/removes a terminating ALLOW rule + IPSet on the collector CloudFront WAFv2
WebACL so a perf load test bypasses the per-source-IP rate cap. These tests drive a fake WAFv2
client that models the LockToken (optimistic-lock) semantics, and assert:
  * apply creates the IPSet + ALLOW rule and is idempotent (re-apply merges IPs, no duplicate rule);
  * the WebACL round-trip preserves every pre-existing rule + the DefaultAction/VisibilityConfig;
  * fail-fast on an invalid/colliding priority and on exhausting the optimistic-lock retry budget;
  * a stale LockToken is transparently retried;
  * remove deletes the rule then the IPSet and is a clean no-op when neither exists.
"""

from __future__ import annotations

import argparse
import re

import pytest

from scripts import perf_waf_allowlist as waf


class _LockError(Exception):
    """Stand-in for botocore's WAFOptimisticLockException (only .response is inspected)."""

    def __init__(self) -> None:
        super().__init__("stale lock token")
        self.response = {"Error": {"Code": "WAFOptimisticLockException"}}


class _NonexistentError(Exception):
    def __init__(self) -> None:
        super().__init__("no such item")
        self.response = {"Error": {"Code": "WAFNonexistentItemException"}}


class _UnavailableEntityError(Exception):
    """Stand-in for botocore's WAFUnavailableEntityException (transient consistency error)."""

    def __init__(self) -> None:
        super().__init__("couldn't retrieve the resource; retry")
        self.response = {"Error": {"Code": "WAFUnavailableEntityException"}}


@pytest.mark.unit
def test_is_retryable_waf_error_matches_lock_and_unavailable() -> None:
    assert waf._is_retryable_waf_error(_LockError()) is True
    assert waf._is_retryable_waf_error(_UnavailableEntityError()) is True
    # Non-retryable: a different WAF error, and a plain exception with no response.
    assert waf._is_retryable_waf_error(_NonexistentError()) is False
    assert waf._is_retryable_waf_error(RuntimeError("boom")) is False


@pytest.mark.unit
def test_with_lock_retry_retries_on_unavailable_entity() -> None:
    calls = {"n": 0}

    def attempt() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _UnavailableEntityError()
        return "ok"

    result = waf.with_lock_retry(attempt, retries=5, interval=0, what="x", waiter=lambda _s: None)
    assert result == "ok"
    assert calls["n"] == 3


class _FakeWafv2:
    """Minimal in-memory WAFv2 double with LockToken + optimistic-lock behaviour."""

    def __init__(self) -> None:
        self.web_acls: dict[str, dict] = {}
        self.web_acl_locks: dict[str, int] = {}
        self.ip_sets: dict[str, dict] = {}
        self.ip_set_locks: dict[str, int] = {}
        self.fail_next_web_acl_updates = 0  # inject N optimistic-lock failures on update_web_acl
        self.update_web_acl_calls = 0

    # --- seeding -----------------------------------------------------------
    def seed_web_acl(self, name: str, rules: list[dict] | None = None) -> None:
        wid = f"id-{name}"
        self.web_acls[wid] = {
            "Name": name,
            "Id": wid,
            "ARN": f"arn:aws:wafv2:us-east-1:123:global/webacl/{name}/{wid}",
            "DefaultAction": {"Allow": {}},
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": name,
            },
            "Rules": list(rules or []),
            "CustomResponseBodies": {"body1": {"ContentType": "TEXT_PLAIN", "Content": "no"}},
        }
        self.web_acl_locks[wid] = 1

    # --- WebACL API (boto3-cased kwargs accepted via **kwargs, so no PascalCase params) ------
    def list_web_acls(self, **_kwargs) -> dict:
        return {
            "WebACLs": [
                {"Name": a["Name"], "Id": a["Id"], "ARN": a["ARN"]} for a in self.web_acls.values()
            ]
        }

    def get_web_acl(self, **kwargs) -> dict:
        acl = self.web_acls[kwargs["Id"]]
        return {
            "WebACL": {k: (list(v) if k == "Rules" else v) for k, v in acl.items()},
            "LockToken": str(self.web_acl_locks[kwargs["Id"]]),
        }

    def update_web_acl(self, **kwargs) -> dict:
        self.update_web_acl_calls += 1
        if self.fail_next_web_acl_updates > 0:
            self.fail_next_web_acl_updates -= 1
            raise _LockError()
        wid = kwargs["Id"]
        if kwargs["LockToken"] != str(self.web_acl_locks[wid]):
            raise _LockError()
        self.web_acls[wid]["Rules"] = list(kwargs["Rules"])
        # pass-through fields must be echoed back by the caller.
        assert kwargs.get("CustomResponseBodies") == self.web_acls[wid]["CustomResponseBodies"]
        assert kwargs["DefaultAction"] == self.web_acls[wid]["DefaultAction"]
        self.web_acl_locks[wid] += 1
        return {"NextLockToken": str(self.web_acl_locks[wid])}

    # --- IPSet API ---------------------------------------------------------
    def list_ip_sets(self, **_kwargs) -> dict:
        return {
            "IPSets": [
                {"Name": s["Name"], "Id": s["Id"], "ARN": s["ARN"]} for s in self.ip_sets.values()
            ]
        }

    def create_ip_set(self, **kwargs) -> dict:
        name = kwargs["Name"]
        sid = f"id-{name}"
        arn = f"arn:aws:wafv2:us-east-1:123:global/ipset/{name}/{sid}"
        self.ip_sets[sid] = {
            "Name": name,
            "Id": sid,
            "ARN": arn,
            "Addresses": list(kwargs["Addresses"]),
        }
        self.ip_set_locks[sid] = 1
        return {"Summary": {"Name": name, "Id": sid, "ARN": arn}}

    def get_ip_set(self, **kwargs) -> dict:
        sid = kwargs["Id"]
        return {
            "IPSet": {"Addresses": list(self.ip_sets[sid]["Addresses"])},
            "LockToken": str(self.ip_set_locks[sid]),
        }

    def update_ip_set(self, **kwargs) -> dict:
        sid = kwargs["Id"]
        if kwargs["LockToken"] != str(self.ip_set_locks[sid]):
            raise _LockError()
        self.ip_sets[sid]["Addresses"] = list(kwargs["Addresses"])
        self.ip_set_locks[sid] += 1
        return {}

    def delete_ip_set(self, **kwargs) -> dict:
        sid = kwargs["Id"]
        if kwargs["LockToken"] != str(self.ip_set_locks[sid]):
            raise _LockError()
        del self.ip_sets[sid]
        del self.ip_set_locks[sid]
        return {}


_RATE_RULE = {
    "Name": "RateLimitPerIP",
    "Priority": 100,
    "Statement": {"RateBasedStatement": {"Limit": 2000, "AggregateKeyType": "IP"}},
    "Action": {"Block": {}},
    "VisibilityConfig": {
        "SampledRequestsEnabled": True,
        "CloudWatchMetricsEnabled": True,
        "MetricName": "RateLimitPerIP",
    },
}


@pytest.fixture()
def client() -> _FakeWafv2:
    c = _FakeWafv2()
    c.seed_web_acl("sandbox-adot-waf", rules=[dict(_RATE_RULE)])
    return c


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_creates_ipset_and_allow_rule(client: _FakeWafv2) -> None:
    ev = waf.apply_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        priority=0,
        add_cidrs=["203.0.113.7/32"],
        retries=0,
        interval=0,
    )
    assert ev["rule_added"] is True
    assert ev["ip_set_arn"].endswith("perf-ipset/id-perf-ipset")
    # the IPSet holds the CIDR.
    assert client.ip_sets["id-perf-ipset"]["Addresses"] == ["203.0.113.7/32"]
    # the WebACL now has the ALLOW rule AND still has the pre-existing rate rule (preserved).
    rules = client.web_acls["id-sandbox-adot-waf"]["Rules"]
    names = {r["Name"] for r in rules}
    assert names == {"RateLimitPerIP", "perf-allow"}
    allow = next(r for r in rules if r["Name"] == "perf-allow")
    assert allow["Action"] == {"Allow": {}}
    assert allow["Priority"] == 0
    assert allow["Statement"]["IPSetReferenceStatement"]["ARN"] == ev["ip_set_arn"]


@pytest.mark.unit
def test_apply_is_idempotent_and_merges_ips(client: _FakeWafv2) -> None:
    waf.apply_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        priority=0,
        add_cidrs=["203.0.113.7/32"],
        retries=0,
        interval=0,
    )
    # second apply from a different shard IP: merges, does NOT duplicate the rule.
    ev = waf.apply_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        priority=0,
        add_cidrs=["198.51.100.9/32"],
        retries=0,
        interval=0,
    )
    assert ev["rule_added"] is False
    assert client.ip_sets["id-perf-ipset"]["Addresses"] == ["203.0.113.7/32", "198.51.100.9/32"]
    rules = client.web_acls["id-sandbox-adot-waf"]["Rules"]
    assert sum(1 for r in rules if r["Name"] == "perf-allow") == 1


@pytest.mark.unit
def test_apply_rejects_priority_at_or_above_rate_rule(client: _FakeWafv2) -> None:
    with pytest.raises(waf.WafUsageError, match="must be <"):
        waf.apply_allowlist(
            client,
            web_acl_name="sandbox-adot-waf",
            ipset_name="perf-ipset",
            rule_name="perf-allow",
            priority=100,
            add_cidrs=[],
            retries=0,
            interval=0,
        )


@pytest.mark.unit
def test_apply_rejects_priority_collision(client: _FakeWafv2) -> None:
    # seed a rule already occupying priority 5.
    client.web_acls["id-sandbox-adot-waf"]["Rules"].append(
        {
            "Name": "other",
            "Priority": 5,
            "Statement": {},
            "Action": {"Block": {}},
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "other",
            },
        }
    )
    with pytest.raises(waf.WafUsageError, match="already used by rule"):
        waf.apply_allowlist(
            client,
            web_acl_name="sandbox-adot-waf",
            ipset_name="perf-ipset",
            rule_name="perf-allow",
            priority=5,
            add_cidrs=[],
            retries=0,
            interval=0,
        )


@pytest.mark.unit
def test_apply_retries_on_optimistic_lock(client: _FakeWafv2) -> None:
    client.fail_next_web_acl_updates = 1  # first update_web_acl raises a stale-lock error
    ev = waf.apply_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        priority=0,
        add_cidrs=[],
        retries=2,
        interval=0,
    )
    assert ev["rule_added"] is True
    assert client.update_web_acl_calls == 2  # one failed, one succeeded


@pytest.mark.unit
def test_apply_fails_fast_when_lock_budget_exhausted(client: _FakeWafv2) -> None:
    client.fail_next_web_acl_updates = 5  # more failures than the retry budget
    with pytest.raises(waf.WafAllowlistError, match="retry budget"):
        waf.apply_allowlist(
            client,
            web_acl_name="sandbox-adot-waf",
            ipset_name="perf-ipset",
            rule_name="perf-allow",
            priority=0,
            add_cidrs=[],
            retries=1,
            interval=0,
        )


# ---------------------------------------------------------------------------
# resolution / discovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_web_acl_discovers_by_convention(client: _FakeWafv2) -> None:
    resolved = waf.resolve_web_acl(client, None)  # discovers the '*-adot-waf' ACL
    assert resolved == {"Name": "sandbox-adot-waf", "Id": "id-sandbox-adot-waf"}


@pytest.mark.unit
def test_resolve_web_acl_unresolvable_fails_fast() -> None:
    empty = _FakeWafv2()  # no web acls at all
    with pytest.raises(waf.WafUsageError, match="could not resolve"):
        waf.resolve_web_acl(empty, None)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_remove_deletes_rule_then_ipset(client: _FakeWafv2) -> None:
    waf.apply_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        priority=0,
        add_cidrs=["203.0.113.7/32"],
        retries=0,
        interval=0,
    )
    ev = waf.remove_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        retries=0,
        interval=0,
    )
    assert ev["rule_removed"] is True
    assert ev["ip_set_deleted"] is True
    # the rate rule survives; the allow rule + IPSet are gone.
    assert {r["Name"] for r in client.web_acls["id-sandbox-adot-waf"]["Rules"]} == {
        "RateLimitPerIP"
    }
    assert "id-perf-ipset" not in client.ip_sets


@pytest.mark.unit
def test_remove_is_noop_when_nothing_present(client: _FakeWafv2) -> None:
    ev = waf.remove_allowlist(
        client,
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        rule_name="perf-allow",
        retries=0,
        interval=0,
    )
    assert ev["rule_removed"] is False
    assert ev["ip_set_deleted"] is False


# ---------------------------------------------------------------------------
# retry wrapper + CLI plumbing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_with_lock_retry_reraises_non_lock_errors() -> None:
    def boom() -> None:
        raise ValueError("not a lock error")

    with pytest.raises(ValueError, match="not a lock error"):
        waf.with_lock_retry(boom, retries=3, interval=0, what="x", waiter=lambda _s: None)


@pytest.mark.unit
def test_delete_ip_set_tolerates_nonexistent(client: _FakeWafv2) -> None:
    client.create_ip_set(
        Name="perf-ipset",
        Scope="CLOUDFRONT",
        IPAddressVersion="IPV4",
        Addresses=[],
        Description="x",
    )

    # get_ip_set then delete raises Nonexistent (deleted concurrently) -> treated as no-op.
    def _raise_nonexistent(*_a, **_k):
        raise _NonexistentError()

    client.get_ip_set = _raise_nonexistent
    assert waf.delete_ip_set(client, "perf-ipset", retries=0, interval=0) is False


@pytest.mark.unit
def test_run_dispatches_apply_and_remove(client: _FakeWafv2) -> None:
    apply_ns = argparse.Namespace(
        action="apply",
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        allow_rule_name="perf-allow",
        allow_rule_priority=0,
        add_cidrs=["203.0.113.7/32"],
        lock_retries=0,
        lock_retry_interval=0,
    )
    ev = waf.run(apply_ns, client)
    assert ev["action"] == "apply" and ev["rule_added"] is True

    remove_ns = argparse.Namespace(
        action="remove",
        web_acl_name="sandbox-adot-waf",
        ipset_name="perf-ipset",
        allow_rule_name="perf-allow",
        allow_rule_priority=0,
        add_cidrs=[],
        lock_retries=0,
        lock_retry_interval=0,
    )
    ev2 = waf.run(remove_ns, client)
    assert ev2["action"] == "remove" and ev2["rule_removed"] is True


@pytest.mark.unit
def test_default_ipset_name_derivation() -> None:
    assert waf._default_ipset_name("sandbox-adot-waf", "perf-allow") == "sandbox-adot-waf-ipset"
    assert waf._default_ipset_name(None, "perf-allow") == "perf-allow-ipset"


# WAFv2 IPSet Description allowed charset (AWS docs): letters, digits, whitespace, and
# the punctuation set + = : # @ / - , . ; must start and end with a non-space allowed char.
# Parentheses and other symbols are rejected by CreateIPSet with a ValidationException.
_WAFV2_DESCRIPTION_RE = re.compile(
    r"\A[a-zA-Z0-9=:#@/\-,.][a-zA-Z0-9+=:#@/\-,.\s]+[a-zA-Z0-9+=:#@/\-,.]\Z"
)


@pytest.mark.unit
def test_ipset_description_matches_wafv2_charset() -> None:
    """The IPSet description must satisfy WAFv2's Description charset (no parentheses)."""
    assert _WAFV2_DESCRIPTION_RE.match(waf.IPSET_DESCRIPTION), (
        f"IPSET_DESCRIPTION {waf.IPSET_DESCRIPTION!r} contains characters WAFv2 CreateIPSet "
        "rejects; keep to letters, digits, whitespace, and - _ = : # @ / , . only."
    )


@pytest.mark.unit
def test_ipset_description_has_no_parentheses() -> None:
    assert "(" not in waf.IPSET_DESCRIPTION and ")" not in waf.IPSET_DESCRIPTION
