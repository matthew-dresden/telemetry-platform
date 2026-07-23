"""Ephemeral perf-test WAF allowlist -- add/remove a terminating ALLOW rule + IPSet on the
collector's CloudFront WAFv2 WebACL so a heavy single-origin load test bypasses the per-source-IP
rate cap (``rate_limit_per_ip``, D44 -- default 2000 requests / 5-minute window / IP).

Invoked as::

    uv run python -m scripts.perf_waf_allowlist apply  --env sandbox --ambient-credentials \\
        --add-cidr 203.0.113.7/32
    uv run python -m scripts.perf_waf_allowlist remove --env sandbox --ambient-credentials
    make perf-waf-allowlist-apply  ENV=sandbox PERF_WAF_ARGS="--ambient-credentials --add-cidr ..."
    make perf-waf-allowlist-remove ENV=sandbox PERF_WAF_ARGS="--ambient-credentials"

Why this exists:
  The collector WebACL (providers/aws/references/collector-ingestion: ``${service_name}-waf``,
  scope CLOUDFRONT) enforces a rate-based BLOCK rule (``RateLimitPerIP``, priority 100) that a
  ~2000-session load driver run from one runner IP trips almost immediately. A WAFv2 ``allow``
  action is TERMINATING, so a rule that ALLOWs the load-runner egress CIDR at a priority LOWER
  than the rate rule (default 0, evaluated first) lets that traffic through before the rate rule
  can block it -- without changing the deployed IaC. The ``waf_allow_cidrs_ssm_param_name`` input
  on the module is declared but not wired into any rule, so a runtime IPSet+rule is the only path.

Design:
  * ``apply``   -- ensure the IPSet exists (CLOUDFRONT scope, us-east-1), MERGE the given
                   ``--add-cidr`` value(s) into it, and ensure the WebACL carries the ALLOW rule
                   referencing that IPSet. Idempotent: re-running merges IPs and leaves an existing
                   rule untouched. Parallel load shards each call ``apply`` with their own egress
                   IP; concurrent IPSet/WebACL updates are serialized by WAFv2 optimistic locking,
                   so every mutation is retried on a stale ``LockToken`` (bounded, env-driven).
  * ``remove``  -- delete the ALLOW rule from the WebACL and delete the IPSet. Idempotent: a
                   missing rule / IPSet is a clean no-op (safe to call from an ``always()`` cleanup
                   even if ``apply`` never ran).

Fail-fast (CLAUDE.md, no fallback): an unresolvable WebACL, an ALLOW-rule priority already taken by
a DIFFERENT rule, or exhausting the optimistic-lock retry budget aborts non-zero with an actionable
message. The WebACL round-trip preserves the DefaultAction, VisibilityConfig, every existing rule
(managed rule groups + the rate rule), and the pass-through optional fields verbatim -- only the
single perf ALLOW rule is ever added or removed.

Exit codes::

    0 -- the requested apply/remove completed
    2 -- usage error (bad ENV / CLI value / unresolvable WebACL)
    1 -- an AWS operation failed (including exhausting the lock-retry budget)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import threading
from typing import Any

from scripts import e2e_common
from scripts.perf_report import discover_waf_web_acl

# CLOUDFRONT-scope WAFv2 resources are only visible/mutable via the us-east-1 endpoint,
# regardless of where the rest of the stack lives (AWS global-service convention).
WAF_CLOUDFRONT_REGION = "us-east-1"
WAF_SCOPE = "CLOUDFRONT"

# Env-driven optimistic-lock retry budget (no hard-coded literals; overridable for CI/tests).
LOCK_RETRIES_ENV = "TT_WAF_LOCK_RETRIES"
LOCK_RETRY_INTERVAL_ENV = "TT_WAF_LOCK_RETRY_INTERVAL"
DEFAULT_LOCK_RETRIES = 6
DEFAULT_LOCK_RETRY_INTERVAL = 3.0

# WAFv2 optimistic-lock error code (raised when a stale LockToken is submitted).
_OPTIMISTIC_LOCK_CODE = "WAFOptimisticLockException"
_NONEXISTENT_ITEM_CODE = "WAFNonexistentItemException"
# Transient WAFv2 consistency error: a just-created entity (e.g. the IPSet we create
# immediately before referencing its ARN in UpdateWebACL) is not yet retrievable. AWS
# explicitly documents retrying this ("AWS WAF couldn't retrieve the resource... Retry").
_UNAVAILABLE_ENTITY_CODE = "WAFUnavailableEntityException"

# IPSet Description. WAFv2 restricts this to letters, digits, whitespace, and a small
# punctuation set (- _ = : # @ / , .); parentheses and most symbols are rejected by
# CreateIPSet with a ValidationException. Kept as a constant so a regression test can
# assert it against the allowed charset (test_perf_waf_allowlist).
IPSET_DESCRIPTION = "Ephemeral perf-test load-runner allowlist - auto-managed."

# The rate BLOCK rule this allowlist must out-prioritise (waf-webacl primitive: priority 100).
# The ALLOW rule's priority MUST be strictly lower so its terminating allow is evaluated first.
_RATE_RULE_PRIORITY = 100

# WebACL fields that update_web_acl accepts and must be echoed back verbatim so nothing the
# deployed WebACL declares is silently dropped by the round-trip.
_WEBACL_PASSTHROUGH_KEYS = (
    "Description",
    "CustomResponseBodies",
    "CaptchaConfig",
    "ChallengeConfig",
    "TokenDomains",
    "AssociationConfig",
)


class WafAllowlistError(RuntimeError):
    """Raised when an allowlist apply/remove cannot complete (fail-fast, exit 1)."""


class WafUsageError(ValueError):
    """Raised on a bad CLI/ENV value or an unresolvable WebACL (exit 2)."""


# ---------------------------------------------------------------------------
# Optimistic-lock retry wrapper
# ---------------------------------------------------------------------------


def _is_optimistic_lock_error(exc: Exception) -> bool:
    """True when ``exc`` is a WAFv2 stale-LockToken (optimistic-lock) error."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    return bool(response.get("Error", {}).get("Code") == _OPTIMISTIC_LOCK_CODE)


def _is_retryable_waf_error(exc: Exception) -> bool:
    """True when ``exc`` is a WAFv2 error worth retrying from fresh server state.

    Covers both the stale-LockToken (optimistic-lock) error and the transient
    UnavailableEntity error (a just-created IPSet not yet retrievable when
    UpdateWebACL references its ARN). Both clear on a re-fetch-and-retry cycle.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code")
    return code in (_OPTIMISTIC_LOCK_CODE, _UNAVAILABLE_ENTITY_CODE)


def _is_nonexistent_item_error(exc: Exception) -> bool:
    """True when ``exc`` reports a WAFv2 resource that does not exist (idempotent no-op)."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    return bool(response.get("Error", {}).get("Code") == _NONEXISTENT_ITEM_CODE)


def with_lock_retry(
    attempt: Any,
    *,
    retries: int,
    interval: float,
    what: str,
    waiter: Any = None,
) -> Any:
    """Call ``attempt()`` retrying ONLY on a WAFv2 optimistic-lock error.

    Each ``attempt`` must re-fetch the fresh ``LockToken`` and re-apply its mutation, so a retry
    starts from the current server state (that is why a callable, not a pre-built request, is
    passed). Any non-lock error surfaces immediately (fail-fast). Exhausting the budget raises
    WafAllowlistError. The inter-attempt pause uses ``threading.Event().wait`` (active wait, never
    ``time.sleep``); ``waiter`` is injected in tests to avoid real waiting.

    Args:
        attempt: Zero-arg callable performing the fetch-token + mutate cycle; returns any value.
        retries: Number of RETRIES after the first attempt (total tries = retries + 1, >= 0).
        interval: Seconds to wait between attempts.
        what: Short description of the mutation for error messages.
        waiter: Optional ``(seconds) -> None`` override for the inter-attempt pause.

    Returns:
        Whatever ``attempt()`` returns on success.

    Raises:
        WafAllowlistError: the optimistic-lock retry budget was exhausted.
    """
    pause = waiter if waiter is not None else (lambda s: threading.Event().wait(timeout=s))
    last_exc: Exception | None = None
    for tries_left in range(retries, -1, -1):
        try:
            return attempt()
        except Exception as exc:
            # Re-raise anything that is NOT a retryable WAFv2 error (stale LockToken or a
            # transient just-created-entity-not-yet-retrievable): fail-fast, no retry.
            if not _is_retryable_waf_error(exc):
                raise
            last_exc = exc
            if tries_left > 0 and interval > 0:
                pause(interval)
    raise WafAllowlistError(
        f"ERROR: {what} failed: exhausted the WAFv2 retry budget "
        f"({retries} retries) for lock/consistency errors. Last error: {last_exc}. "
        "Remedy: raise TT_WAF_LOCK_RETRIES / TT_WAF_LOCK_RETRY_INTERVAL or reduce concurrent "
        "WebACL mutators."
    )


# ---------------------------------------------------------------------------
# WebACL / IPSet resolution
# ---------------------------------------------------------------------------


def resolve_web_acl(wafv2: Any, web_acl_name: str | None) -> dict[str, str]:
    """Resolve the target CLOUDFRONT WebACL's Name + Id (given, or discovered).

    Args:
        wafv2: A boto3 WAFV2 client built for us-east-1.
        web_acl_name: Explicit WebACL name, or None to auto-discover the collector WebACL.

    Returns:
        ``{"Name": ..., "Id": ...}`` for the resolved WebACL.

    Raises:
        WafUsageError: the WebACL cannot be resolved (no override, no unambiguous discovery).
    """
    name = web_acl_name or discover_waf_web_acl(wafv2)
    if not name:
        raise WafUsageError(
            "ERROR: could not resolve the collector CLOUDFRONT WebACL. "
            "Remedy: pass --web-acl-name explicitly (auto-discovery found no unambiguous match)."
        )
    acls = wafv2.list_web_acls(Scope=WAF_SCOPE).get("WebACLs", [])
    match = next((a for a in acls if a.get("Name") == name), None)
    if match is None:
        raise WafUsageError(
            f"ERROR: CLOUDFRONT WebACL {name!r} not found in scope {WAF_SCOPE}. "
            "Remedy: verify the WebACL name and that the stack is deployed."
        )
    return {"Name": name, "Id": str(match["Id"])}


def _find_ip_set(wafv2: Any, ipset_name: str) -> dict[str, str] | None:
    """Return the ``{Name, Id, ARN}`` summary of ``ipset_name``, or None when absent."""
    summaries = wafv2.list_ip_sets(Scope=WAF_SCOPE).get("IPSets", [])
    match = next((s for s in summaries if s.get("Name") == ipset_name), None)
    if match is None:
        return None
    return {"Name": ipset_name, "Id": str(match["Id"]), "ARN": str(match["ARN"])}


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def ensure_ip_set(
    wafv2: Any, ipset_name: str, add_cidrs: list[str], *, retries: int, interval: float
) -> dict[str, str]:
    """Create-or-merge the IPSet, adding ``add_cidrs`` to it. Returns its ``{Name, Id, ARN}``.

    Idempotent: creates the IPSet when absent, otherwise merges the new CIDRs into the existing
    address set (deduplicated, order-stable). The merge is optimistic-lock retried so parallel
    shards adding their own IP never clobber each other.
    """
    existing = _find_ip_set(wafv2, ipset_name)
    if existing is None:
        created = wafv2.create_ip_set(
            Name=ipset_name,
            Scope=WAF_SCOPE,
            IPAddressVersion="IPV4",
            Addresses=_dedupe(add_cidrs),
            Description=IPSET_DESCRIPTION,
        )["Summary"]
        return {"Name": ipset_name, "Id": str(created["Id"]), "ARN": str(created["ARN"])}

    if add_cidrs:

        def _merge() -> None:
            current = wafv2.get_ip_set(Name=ipset_name, Scope=WAF_SCOPE, Id=existing["Id"])
            addresses = list(current["IPSet"].get("Addresses", []))
            merged = _dedupe(addresses + add_cidrs)
            if merged == addresses:
                return  # every requested CIDR is already present -- no update needed.
            wafv2.update_ip_set(
                Name=ipset_name,
                Scope=WAF_SCOPE,
                Id=existing["Id"],
                Addresses=merged,
                LockToken=current["LockToken"],
            )

        with_lock_retry(
            _merge, retries=retries, interval=interval, what=f"IPSet {ipset_name!r} address merge"
        )
    return existing


def build_allow_rule(rule_name: str, priority: int, ipset_arn: str) -> dict[str, Any]:
    """Build the terminating ALLOW rule referencing the perf IPSet."""
    return {
        "Name": rule_name,
        "Priority": priority,
        "Statement": {"IPSetReferenceStatement": {"ARN": ipset_arn}},
        "Action": {"Allow": {}},
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": rule_name,
        },
    }


def _webacl_update_kwargs(
    web_acl: dict[str, Any], name: str, web_acl_id: str, rules: list[dict[str, Any]], lock: str
) -> dict[str, Any]:
    """Assemble update_web_acl kwargs, echoing pass-through fields verbatim (nothing dropped)."""
    kwargs: dict[str, Any] = {
        "Name": name,
        "Scope": WAF_SCOPE,
        "Id": web_acl_id,
        "DefaultAction": web_acl["DefaultAction"],
        "Rules": rules,
        "VisibilityConfig": web_acl["VisibilityConfig"],
        "LockToken": lock,
    }
    for key in _WEBACL_PASSTHROUGH_KEYS:
        if key in web_acl and web_acl[key]:
            kwargs[key] = web_acl[key]
    return kwargs


def ensure_allow_rule(
    wafv2: Any,
    web_acl: dict[str, str],
    rule_name: str,
    priority: int,
    ipset_arn: str,
    *,
    retries: int,
    interval: float,
) -> bool:
    """Ensure the WebACL carries the perf ALLOW rule referencing ``ipset_arn``. Idempotent.

    Returns True when the rule was added, False when it was already present. Fail-fast when the
    requested ``priority`` is already occupied by a DIFFERENT rule, or when it is not below the
    rate rule (so its terminating allow could never pre-empt the rate block).

    Raises:
        WafUsageError: the priority is invalid or collides with a different rule.
        WafAllowlistError: the optimistic-lock retry budget was exhausted.
    """
    if priority >= _RATE_RULE_PRIORITY:
        raise WafUsageError(
            f"ERROR: --allow-rule-priority {priority} must be < {_RATE_RULE_PRIORITY} (the rate "
            "rule's priority) so the terminating ALLOW is evaluated before the rate BLOCK."
        )

    added = [False]

    def _attempt() -> None:
        resp = wafv2.get_web_acl(Name=web_acl["Name"], Scope=WAF_SCOPE, Id=web_acl["Id"])
        acl = resp["WebACL"]
        rules = list(acl.get("Rules", []))
        for existing in rules:
            if existing.get("Name") == rule_name:
                added[0] = False
                return  # already present -- idempotent no-op.
            if existing.get("Priority") == priority:
                raise WafUsageError(
                    f"ERROR: priority {priority} is already used by rule "
                    f"{existing.get('Name')!r}. Remedy: pass a different --allow-rule-priority."
                )
        rules.append(build_allow_rule(rule_name, priority, ipset_arn))
        wafv2.update_web_acl(
            **_webacl_update_kwargs(acl, web_acl["Name"], web_acl["Id"], rules, resp["LockToken"])
        )
        added[0] = True

    with_lock_retry(
        _attempt, retries=retries, interval=interval, what=f"WebACL ALLOW-rule {rule_name!r} add"
    )
    return added[0]


def apply_allowlist(
    wafv2: Any,
    *,
    web_acl_name: str | None,
    ipset_name: str,
    rule_name: str,
    priority: int,
    add_cidrs: list[str],
    retries: int,
    interval: float,
) -> dict[str, Any]:
    """Ensure the IPSet (with ``add_cidrs``) and the ALLOW rule exist. Returns an evidence dict."""
    web_acl = resolve_web_acl(wafv2, web_acl_name)
    ip_set = ensure_ip_set(wafv2, ipset_name, add_cidrs, retries=retries, interval=interval)
    rule_added = ensure_allow_rule(
        wafv2, web_acl, rule_name, priority, ip_set["ARN"], retries=retries, interval=interval
    )
    return {
        "action": "apply",
        "web_acl_name": web_acl["Name"],
        "web_acl_id": web_acl["Id"],
        "ip_set_name": ipset_name,
        "ip_set_arn": ip_set["ARN"],
        "allow_rule_name": rule_name,
        "allow_rule_priority": priority,
        "added_cidrs": _dedupe(add_cidrs),
        "rule_added": rule_added,
    }


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def remove_allow_rule(
    wafv2: Any, web_acl: dict[str, str], rule_name: str, *, retries: int, interval: float
) -> bool:
    """Remove the perf ALLOW rule from the WebACL if present. Idempotent; True when removed."""
    removed = [False]

    def _attempt() -> None:
        resp = wafv2.get_web_acl(Name=web_acl["Name"], Scope=WAF_SCOPE, Id=web_acl["Id"])
        acl = resp["WebACL"]
        rules = list(acl.get("Rules", []))
        kept = [r for r in rules if r.get("Name") != rule_name]
        if len(kept) == len(rules):
            removed[0] = False
            return  # rule absent -- clean no-op.
        wafv2.update_web_acl(
            **_webacl_update_kwargs(acl, web_acl["Name"], web_acl["Id"], kept, resp["LockToken"])
        )
        removed[0] = True

    with_lock_retry(
        _attempt, retries=retries, interval=interval, what=f"WebACL ALLOW-rule {rule_name!r} remove"
    )
    return removed[0]


def delete_ip_set(wafv2: Any, ipset_name: str, *, retries: int, interval: float) -> bool:
    """Delete the perf IPSet if present. Idempotent. Returns True if deleted."""
    existing = _find_ip_set(wafv2, ipset_name)
    if existing is None:
        return False

    def _attempt() -> None:
        current = wafv2.get_ip_set(Name=ipset_name, Scope=WAF_SCOPE, Id=existing["Id"])
        wafv2.delete_ip_set(
            Name=ipset_name, Scope=WAF_SCOPE, Id=existing["Id"], LockToken=current["LockToken"]
        )

    try:
        with_lock_retry(
            _attempt, retries=retries, interval=interval, what=f"IPSet {ipset_name!r} delete"
        )
    except Exception as exc:
        # A concurrent delete (the item vanished between list and delete) is a clean no-op;
        # anything else is surfaced fail-fast.
        if _is_nonexistent_item_error(exc):
            return False
        raise
    return True


def remove_allowlist(
    wafv2: Any,
    *,
    web_acl_name: str | None,
    ipset_name: str,
    rule_name: str,
    retries: int,
    interval: float,
) -> dict[str, Any]:
    """Remove the ALLOW rule then delete the IPSet. Idempotent. Returns an evidence dict.

    The rule MUST be removed before the IPSet is deleted -- WAFv2 refuses to delete an IPSet that
    a WebACL rule still references.
    """
    web_acl = resolve_web_acl(wafv2, web_acl_name)
    rule_removed = remove_allow_rule(wafv2, web_acl, rule_name, retries=retries, interval=interval)
    ip_set_deleted = delete_ip_set(wafv2, ipset_name, retries=retries, interval=interval)
    return {
        "action": "remove",
        "web_acl_name": web_acl["Name"],
        "web_acl_id": web_acl["Id"],
        "ip_set_name": ipset_name,
        "allow_rule_name": rule_name,
        "rule_removed": rule_removed,
        "ip_set_deleted": ip_set_deleted,
    }


# ---------------------------------------------------------------------------
# helpers / CLI
# ---------------------------------------------------------------------------


def _dedupe(items: list[str]) -> list[str]:
    """Order-stable de-duplication."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _default_ipset_name(web_acl_name: str | None, rule_name: str) -> str:
    """Derive a stable IPSet name from the WebACL name (or the rule name when discovering)."""
    base = web_acl_name if web_acl_name else rule_name
    return f"{base}-ipset"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the perf WAF allowlist tool."""
    parser = argparse.ArgumentParser(
        prog="scripts.perf_waf_allowlist",
        description=(
            "Add/remove a terminating ALLOW rule + IPSet on the collector CloudFront WAFv2 WebACL "
            "so a perf load test bypasses the per-source-IP rate cap."
        ),
    )
    parser.add_argument(
        "action", choices=("apply", "remove"), help="apply or remove the allowlist."
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment (used for WebACL discovery + credential context).",
    )
    parser.add_argument(
        "--ambient-credentials",
        dest="ambient_credentials",
        action="store_true",
        help="Build the boto3 session from the ambient/default credential chain (CI OIDC role).",
    )
    parser.add_argument(
        "--aws-profile",
        dest="aws_profile",
        default=None,
        help="Named AWS profile (local dev). Ignored when --ambient-credentials is set.",
    )
    parser.add_argument(
        "--web-acl-name",
        dest="web_acl_name",
        default=None,
        help="Override the CLOUDFRONT WebACL name (default: discovered).",
    )
    parser.add_argument(
        "--ipset-name",
        dest="ipset_name",
        default=None,
        help="IPSet name (default: <web-acl-name>-ipset).",
    )
    parser.add_argument(
        "--allow-rule-name",
        dest="allow_rule_name",
        default="perf-test-allow",
        help="Name of the terminating ALLOW rule (default: perf-test-allow).",
    )
    parser.add_argument(
        "--allow-rule-priority",
        dest="allow_rule_priority",
        type=int,
        default=0,
        help="Priority of the ALLOW rule (default: 0 = evaluated first; must be < the rate rule).",
    )
    parser.add_argument(
        "--add-cidr",
        dest="add_cidrs",
        action="append",
        default=[],
        help="CIDR to add to the IPSet (repeatable). apply only, e.g. 203.0.113.7/32.",
    )
    parser.add_argument(
        "--lock-retries",
        dest="lock_retries",
        type=int,
        default=_env_int(LOCK_RETRIES_ENV, DEFAULT_LOCK_RETRIES),
        help=f"Optimistic-lock retries (default: ${LOCK_RETRIES_ENV} or {DEFAULT_LOCK_RETRIES}).",
    )
    parser.add_argument(
        "--lock-retry-interval",
        dest="lock_retry_interval",
        type=float,
        default=_env_float(LOCK_RETRY_INTERVAL_ENV, DEFAULT_LOCK_RETRY_INTERVAL),
        help=(
            f"Seconds between lock retries (default: ${LOCK_RETRY_INTERVAL_ENV} or "
            f"{DEFAULT_LOCK_RETRY_INTERVAL})."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the JSON evidence summary (default: stdout only).",
    )
    return parser


def _build_wafv2_client(args: argparse.Namespace, boto3_module: Any) -> Any:
    """Build a WAFV2 client for us-east-1 (CLOUDFRONT-scope resources live only there)."""
    if args.ambient_credentials:
        session = e2e_common.build_ambient_session(boto3_module)
    elif args.aws_profile:
        session = boto3_module.Session(profile_name=args.aws_profile)
    else:
        session = boto3_module.Session()
    return session.client("wafv2", region_name=WAF_CLOUDFRONT_REGION)


def run(args: argparse.Namespace, wafv2: Any) -> dict[str, Any]:
    """Dispatch apply/remove against a WAFV2 client and return the evidence dict."""
    ipset_name = args.ipset_name or _default_ipset_name(args.web_acl_name, args.allow_rule_name)
    if args.action == "apply":
        return apply_allowlist(
            wafv2,
            web_acl_name=args.web_acl_name,
            ipset_name=ipset_name,
            rule_name=args.allow_rule_name,
            priority=args.allow_rule_priority,
            add_cidrs=list(args.add_cidrs),
            retries=args.lock_retries,
            interval=args.lock_retry_interval,
        )
    return remove_allowlist(
        wafv2,
        web_acl_name=args.web_acl_name,
        ipset_name=ipset_name,
        rule_name=args.allow_rule_name,
        retries=args.lock_retries,
        interval=args.lock_retry_interval,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    import boto3

    try:
        wafv2 = _build_wafv2_client(args, boto3)
        evidence = run(args, wafv2)
    except WafUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        # Every AWS/lock failure is surfaced fail-fast (exit 1) -- never silently swallowed.
        print(f"ERROR: perf WAF allowlist {args.action} failed: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(evidence, indent=2)
    print(rendered)
    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
