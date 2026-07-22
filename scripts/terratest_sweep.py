"""terratest_sweep -- FR-4 tag-scoped orphan sweep tool.

Run via: make terratest-sweep SWEEP_MODE=<check|delete> [SWEEP_RUN_ID=<id>]
     or: uv run python -m scripts.terratest_sweep --mode <check|delete> [--run-id <id>]

Enumerates AWS resources tagged with BOTH Project=telemetry-platform AND
terratest-run (optionally narrowed to one run id) via the Resource Groups
Tagging API. Never consults Terraform state; the tag pair is the sole authority.

Caller account is verified against an allowlist derived from
terragrunt/common/accounts.json entries whose account_role is in
{sandbox, qa-infra}. Any other account is refused with exit 1 BEFORE any
enumeration (spec section 3.6, G7 worked example).

Check mode: prints the inventory and exits 0 iff zero matches.
Delete mode: dispatches each matched resource through the per-service handler
  table from spec section 4.4. A matched resource with NO handler is reported
  and left untouched; the run exits non-zero (fail-safe invariant).

A JSON report per spec section 5.4 is written to --report (default under
.sweep-reports/).

Environment variables consumed (sourced from scripts/constants.py):
  TT_SWEEP_POLL_TIMEOUT   -- max seconds budget for retries (default 300);
                             used to derive botocore max_attempts for the Tagging API
                             and WaiterConfig MaxAttempts for CloudFront polling
  TT_SWEEP_POLL_INTERVAL  -- desired seconds between retry attempts (default 10);
                             used to derive botocore max_attempts and CloudFront waiter Delay
  TT_SWEEP_MAX_DELETE_PASSES -- max loop-until-stable delete passes (default 6);
                             see the "Delete mode pass model" note below
  SWEEP_MODE              -- check|delete (Makefile pass-through)
  SWEEP_RUN_ID            -- optional run id to narrow the filter

Delete mode pass model:
  SWEEP_MODE=delete loops until STABLE. Each outer pass RE-ENUMERATES the
  tag-scoped inventory (a fresh Resource Groups Tagging API describe) and then
  runs one full reverse-dependency-tier delete over it (non-ec2 resources, then
  the ec2/VPC family in tier order: tier 1 natgateway/EIP/flow-log -> tier 2
  subnet/route-table/SG/vpc-endpoint/IGW -> tier 3 VPC). VPC-family deletion is
  eventually consistent -- a VPC cannot be deleted until its ENIs / subnets / IGW
  / NAT gateway have finished detaching, which can take longer than one pass -- so
  a resource that hits a transient DependencyViolation is left PENDING and retried
  on the NEXT pass, after re-enumeration reflects the drained dependents. The loop
  stops as soon as a pass leaves zero pending DependencyViolations (every resource
  is deleted, already-gone, spared-as-in-flight <TT_SWEEP_MIN_AGE_MINUTES, an
  unsupported orphan, or a genuine failure). The ceiling is
  TT_SWEEP_MAX_DELETE_PASSES; the FINAL pass surfaces any still-stuck
  DependencyViolation as a real failure so a genuine leak is never masked
  (fail-safe invariant). This makes the delete step fully drain multi-pass
  VPC-dependency deletions BEFORE the fail-on-residue check step runs.

All AWS readiness detection uses SDK-managed retry (botocore.config.Config adaptive
retry for the Tagging API) and boto3 built-in waiters (CloudFront distribution_deployed).
No raw time.sleep calls are used for synchronization.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import pathlib
import sys
from typing import Any

import botocore.config
import botocore.exceptions

import scripts.constants as _constants

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_TAG_KEY: str = "Project"
_PROJECT_TAG_VALUE: str = "telemetry-platform"
_RUN_ID_TAG_KEY: str = "terratest-run"
_ALLOWED_ROLES: frozenset[str] = frozenset({"sandbox", "qa-infra"})
_DEFAULT_SWEEP_REPORTS_DIR: str = ".sweep-reports"

# KMS aliases are NOT taggable and never appear in the Resource Groups Tagging
# API, so a tag-scoped sweep that schedules a tagged CMK for deletion would
# otherwise leave the customer alias pointing at it dangling for the full
# pending-deletion window -- and an account-global alias (e.g. alias/telemetry-data)
# blocks the next run's CreateAlias with AlreadyExistsException. When deleting a
# tagged key the handler therefore enumerates and deletes the customer aliases
# that target it. AWS-managed aliases (alias/aws/*) cannot be deleted and are skipped.
_AWS_MANAGED_ALIAS_PREFIX: str = "alias/aws/"

# Maximum number of loop-until-stable delete passes for a single delete-mode run.
# Each pass RE-ENUMERATES the tag-scoped inventory (fresh Tagging API describe)
# and runs one full reverse-dependency-tier delete over it; a resource that hits a
# transient DependencyViolation (its dependents have not yet finished deleting) is
# left PENDING and retried on the NEXT pass, after re-enumeration reflects the
# drained dependents. The loop stops as soon as a pass leaves zero pending
# DependencyViolations; the ceiling below guarantees termination (no infinite
# loop) and the FINAL pass surfaces any still-stuck DependencyViolation as a real
# failure (fail-safe invariant: a genuine leak is never masked). The default is
# defined once in scripts/constants.py and read from the environment at call time
# (via _get_max_delete_passes) so it stays env-driven and test-overridable.
_DEFAULT_MAX_DELETE_PASSES: int = _constants.TT_SWEEP_MAX_DELETE_PASSES

# Error codes that prove a resource is ALREADY GONE. A delete that hits one of
# these is idempotent success, not a failure: the target was already deleted
# (by this run's own earlier pass, by Terraform destroy, or by a concurrent
# actor), and the Tagging API simply has not de-indexed it yet. Matching is also
# done by the generic substring rule (_is_benign_not_found) so any future
# "*NotFound*" / "*NotFoundException" code is covered without enumerating it.
_BENIGN_ALREADY_GONE_CODES: frozenset[str] = frozenset(
    {
        "InvalidSubnetID.NotFound",
        "InvalidRouteTableID.NotFound",
        "InvalidVpcID.NotFound",
        "InvalidVpcEndpointId.NotFound",
        "InvalidInternetGatewayID.NotFound",
        "InvalidGroup.NotFound",
        "InvalidAllocationID.NotFound",
        "InvalidNatGatewayID.NotFound",
        "NatGatewayNotFound",
        "InvalidFlowLogId.NotFound",
        "ClusterNotFoundException",
        "ServiceNotFoundException",
        "ServiceNotActiveException",
        "ResourceNotFoundException",
        "NoSuchEntity",
        "NoSuchBucket",
        # CloudFront already-gone codes. CloudFront's ResourceGroupsTaggingAPI tag
        # index lags significantly behind deletion (it can keep returning a
        # distribution ARN for many minutes after the distribution is gone), so a
        # run-scoped sweep frequently re-discovers a just-destroyed distribution and
        # its get_distribution_config / delete_distribution call raises one of these.
        # They prove the resource is already gone (idempotent success), exactly like
        # the EC2 *.NotFound codes; none of them contain the "NotFound" substring, so
        # they must be enumerated explicitly.
        "NoSuchDistribution",
        "NoSuchCloudFrontOriginAccessIdentity",
        "NoSuchOriginAccessControl",
        "NoSuchResponseHeadersPolicy",
        "WAFNonexistentItemException",
        "NotFoundException",
        "ParameterNotFound",
    }
)

# Error codes that mean a resource cannot be deleted YET because something else
# still depends on it. These are transient during a multi-pass run-scoped sweep
# (the dependent is deleted on an earlier/same pass and the parent succeeds on a
# later pass). They are deferred and retried, and only surface as a real failure
# if they persist into the FINAL pass.
_DEPENDENCY_VIOLATION_CODES: frozenset[str] = frozenset(
    {
        "DependencyViolation",
        "ResourceInUseException",
        "ResourceInUse",
    }
)

# KMS minimum schedule-deletion window in days (AWS minimum is 7).
_KMS_PENDING_WINDOW_DAYS: int = 7

# Aliases for direct reference (spec section 7: single constants site)
TT_SWEEP_POLL_TIMEOUT = _constants.TT_SWEEP_POLL_TIMEOUT
TT_SWEEP_POLL_INTERVAL = _constants.TT_SWEEP_POLL_INTERVAL

# EC2/VPC family reverse-dependency tier ordering (spec section 4.4 FR-4).
# Resources within each tier may be deleted in any order relative to each other,
# but all tier-1 resources must be deleted before any tier-2 resource, and all
# tier-2 resources must be deleted before any tier-3 resource.
#
# Tier 1: highest-level dependents that must go first.
# vpc-flow-log is included here: it is a VPC-level attachment that must be removed
# before subnets and route tables can be deleted (tier 2) and the VPC itself (tier 3).
_EC2_TIER1_TYPES: frozenset[str] = frozenset(
    {"natgateway", "elastic-ip", "eip-allocation", "vpc-flow-log"}
)
# Tier 2: in-VPC resources that depend on nothing except the VPC itself.
_EC2_TIER2_TYPES: frozenset[str] = frozenset(
    {"subnet", "route-table", "security-group", "vpc-endpoint", "internet-gateway"}
)
# Tier 3: the VPC itself -- deleted last.
_EC2_TIER3_TYPES: frozenset[str] = frozenset({"vpc"})


def _ec2_resource_type(arn: str) -> str:
    """Extract the ec2 resource type token from an ARN resource segment.

    ARN shape: arn:aws:ec2:<region>:<account>:<type>/<id>
    Returns the <type> portion (e.g. 'natgateway', 'vpc', 'subnet').
    """
    resource_segment = arn.split(":", 5)[-1] if arn.count(":") >= 5 else arn
    resource_type, _, _ = resource_segment.partition("/")
    return resource_type


def _partition_ec2_by_tier(
    resources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition ec2:: resources into reverse-dependency tiers plus non-ec2.

    Returns a 4-tuple: (non_ec2, tier1, tier2, tier3).
    Resources with ec2 service prefix whose type does not match any declared
    tier fall into tier2 by default (conservative: delete before the VPC).
    Non-ec2 resources are returned unchanged in non_ec2.
    """
    non_ec2: list[dict[str, Any]] = []
    tier1: list[dict[str, Any]] = []
    tier2: list[dict[str, Any]] = []
    tier3: list[dict[str, Any]] = []

    for resource in resources:
        arn = resource.get("ResourceARN", "")
        service = _service_prefix_from_arn(arn)
        if service != "ec2":
            non_ec2.append(resource)
            continue
        rtype = _ec2_resource_type(arn)
        if rtype in _EC2_TIER1_TYPES:
            tier1.append(resource)
        elif rtype in _EC2_TIER3_TYPES:
            tier3.append(resource)
        else:
            # Includes known tier-2 types and any unrecognised ec2 resource types.
            tier2.append(resource)

    return non_ec2, tier1, tier2, tier3


def _get_poll_timeout() -> float:
    """Read TT_SWEEP_POLL_TIMEOUT from env at call time (enables test overrides)."""
    return float(os.environ.get("TT_SWEEP_POLL_TIMEOUT", str(_constants.TT_SWEEP_POLL_TIMEOUT)))


def _get_poll_interval() -> float:
    """Read TT_SWEEP_POLL_INTERVAL from env at call time (enables test overrides)."""
    return float(os.environ.get("TT_SWEEP_POLL_INTERVAL", str(_constants.TT_SWEEP_POLL_INTERVAL)))


def _get_max_delete_passes() -> int:
    """Read TT_SWEEP_MAX_DELETE_PASSES from env at call time (enables test overrides).

    The ceiling on loop-until-stable delete passes. Must be at least 1 (a single
    pass is always attempted); a non-positive or unparseable value falls back to
    the documented default rather than skipping the delete entirely (fail-safe).
    """
    raw = os.environ.get("TT_SWEEP_MAX_DELETE_PASSES", str(_DEFAULT_MAX_DELETE_PASSES))
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_MAX_DELETE_PASSES
    return max(1, value)


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


def _load_allowlist(accounts_json_path: str) -> frozenset[str]:
    """Return the set of account ids whose account_role is in {sandbox, qa-infra}.

    Args:
        accounts_json_path: Absolute or relative path to accounts.json.

    Returns:
        frozenset of account id strings permitted to run the sweep.

    Raises:
        SystemExit: If the file cannot be read or parsed.
    """
    path = pathlib.Path(accounts_json_path)
    if not path.exists():
        print(
            f"ERROR: accounts.json not found at {accounts_json_path}. "
            "Ensure the file exists at terragrunt/common/accounts.json.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        data: dict[str, dict[str, Any]] = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: Failed to parse {accounts_json_path}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    return frozenset(
        acct_id for acct_id, row in data.items() if row.get("account_role") in _ALLOWED_ROLES
    )


# ---------------------------------------------------------------------------
# Tagging API enumeration with botocore adaptive retry
# ---------------------------------------------------------------------------


def _compute_max_attempts(poll_timeout: float, poll_interval: float) -> int:
    """Compute botocore max_attempts from the configured polling budget.

    Args:
        poll_timeout: Maximum seconds allowed for retries.
        poll_interval: Desired seconds between retry attempts.

    Returns:
        max_attempts value suitable for botocore.config.Config.
    """
    if poll_interval <= 0:
        # No delay between attempts; use timeout in seconds as a generous upper bound.
        return max(1, int(poll_timeout) + 1)
    return max(1, math.ceil(poll_timeout / poll_interval) + 1)


def _adaptive_retry_config(max_attempts: int) -> botocore.config.Config:
    """Return a botocore Config that retries throttled calls with adaptive backoff.

    Adaptive mode adds a client-side rate limiter on top of exponential backoff,
    so a burst of low-TPS control-plane calls (e.g. ECS DeleteCluster) is
    self-throttled to stay under the service's rate limit instead of hammering it
    until the default 4-attempt ceiling is exhausted. ``max_attempts`` is the total
    attempt ceiling (initial call + retries).

    Args:
        max_attempts: Total attempt ceiling for a single API call.

    Returns:
        A botocore.config.Config with retries={'mode': 'adaptive', ...}.
    """
    return botocore.config.Config(retries={"mode": "adaptive", "max_attempts": max_attempts})


def _client_retry_config() -> botocore.config.Config:
    """Return the shared adaptive-retry Config for every AWS client the sweep builds.

    Every deletion-path client (ECS, EC2, KMS, IAM, CloudFront, Route53, S3, Logs,
    Firehose, ...) must survive AWS API throttling exactly the way the Tagging-API
    client does. botocore's DEFAULT retry (standard mode, max 4 attempts) is too
    shallow for low-TPS control-plane APIs: a backlog sweep throttles ECS
    DeleteCluster with 'ThrottlingException ... (reached max retries: 4)' and tallies
    the throttled deletes as failures, failing the whole run. The attempt ceiling is
    derived from the env-driven poll budget (TT_SWEEP_POLL_TIMEOUT /
    TT_SWEEP_POLL_INTERVAL), so throttling is retried with adaptive backoff up to that
    ceiling; a delete that STILL fails after the full budget surfaces as a real
    failure (fail-fast) rather than being masked.

    Returns:
        The shared botocore.config.Config to apply to every sweep client.
    """
    max_attempts = _compute_max_attempts(_get_poll_timeout(), _get_poll_interval())
    return _adaptive_retry_config(max_attempts)


def _make_client(boto3_mod: Any, service_name: str, **kwargs: Any) -> Any:
    """Construct a boto3 client with the shared adaptive-retry Config applied.

    Single construction site for every AWS client the sweep builds (DRY), so no
    deletion path can accidentally fall back to botocore's shallow default retry.
    A caller that supplies its own ``config`` kwarg (the Tagging-API enumeration,
    which derives max_attempts from explicit poll args for its diagnostic message)
    is honoured as-is; every other client inherits the shared adaptive-retry Config.

    Args:
        boto3_mod: The boto3 module (injected for testability).
        service_name: The AWS service name (e.g. "ecs", "ec2", "kms").
        **kwargs: Extra client kwargs (e.g. region_name); an explicit ``config`` wins.

    Returns:
        A boto3 client constructed with adaptive-retry Config.
    """
    if "config" not in kwargs:
        kwargs["config"] = _client_retry_config()
    return boto3_mod.client(service_name, **kwargs)


def _enumerate_resources(
    boto3_mod: Any,
    run_id: str | None,
    poll_timeout: float,
    poll_interval: float,
) -> list[dict[str, Any]]:
    """Enumerate resources via the Resource Groups Tagging API.

    Filters on BOTH Project=telemetry-platform AND the presence of the
    terratest-run tag (optionally narrowed to one run id).

    Throttle retries are handled by botocore's built-in adaptive retry
    mechanism (botocore.config.Config retries). The env-driven
    TT_SWEEP_POLL_TIMEOUT and TT_SWEEP_POLL_INTERVAL budgets are used to
    derive the max_attempts ceiling.

    Args:
        boto3_mod: The boto3 module (injected for testability).
        run_id: Optional run id to narrow the filter.
        poll_timeout: Maximum seconds to spend retrying throttled calls.
        poll_interval: Seconds between retry attempts (used to compute max_attempts).

    Returns:
        List of resource tag mapping dicts from the Tagging API.

    Raises:
        SystemExit: If botocore retries are exhausted.
    """
    tag_filters: list[dict[str, Any]] = [
        {"Key": _PROJECT_TAG_KEY, "Values": [_PROJECT_TAG_VALUE]},
    ]
    if run_id is not None:
        tag_filters.append({"Key": _RUN_ID_TAG_KEY, "Values": [run_id]})
    else:
        # Filter on presence of the tag (any value) by providing an empty Values list.
        tag_filters.append({"Key": _RUN_ID_TAG_KEY, "Values": []})

    max_attempts = _compute_max_attempts(poll_timeout, poll_interval)
    retry_config = _adaptive_retry_config(max_attempts)
    tagging_client = _make_client(boto3_mod, "resourcegroupstaggingapi", config=retry_config)
    try:
        paginator = tagging_client.get_paginator("get_resources")
        resources: list[dict[str, Any]] = []
        for page in paginator.paginate(TagFilters=tag_filters):
            resources.extend(page.get("ResourceTagMappingList", []))
        return resources
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ThrottlingException", "RequestThrottled", "Throttling"):
            print(
                f"ERROR: Tagging API throttling budget exhausted "
                f"(max_attempts={max_attempts}). "
                f"Last error: {exc}. "
                "Increase TT_SWEEP_POLL_TIMEOUT or retry later.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


# ---------------------------------------------------------------------------
# Min-age guard for the GLOBAL (cross-run) sweep
#
# The daily scheduled global sweep runs SWEEP_MODE=delete with no run id and
# would otherwise delete EVERY terratest-tagged resource in the account --
# including the just-created resources of any PR scope=all matrix job that
# happens to be running concurrently (GitHub cron can fire 30-90 min late, so it
# overlaps PR runs). That race manifests as "KMS Key Id is not in active state",
# "ClusterNotFoundException: cluster was inactive", "S3 bucket ... doesn't exist"
# and "CertificateNotFound" mid-apply in the victim job.
#
# The auto-generated run id encodes its own UTC creation timestamp
# (tt-<yyyymmddHHMMSS>-<rand>), carried on every resource as the terratest-run
# tag value, so the global sweep can compute each resource's age WITHOUT any
# per-resource API call and skip ones younger than TT_SWEEP_MIN_AGE_MINUTES.
# ---------------------------------------------------------------------------


def _get_min_age_minutes() -> int:
    """Read TT_SWEEP_MIN_AGE_MINUTES from env at call time (enables test overrides)."""
    return int(os.environ.get("TT_SWEEP_MIN_AGE_MINUTES", str(_constants.TT_SWEEP_MIN_AGE_MINUTES)))


def _run_id_tag_value(resource: dict[str, Any]) -> str | None:
    """Return the terratest-run tag value of a Tagging-API resource, or None."""
    for tag in resource.get("Tags", []):
        if tag.get("Key") == _RUN_ID_TAG_KEY:
            value = tag.get("Value")
            return None if value is None else str(value)
    return None


def _run_id_age_minutes(run_id: str, now: datetime.datetime) -> float | None:
    """Compute the age in minutes of an auto-generated run id.

    Parses the UTC timestamp embedded in a ``tt-<yyyymmddHHMMSS>-<rand>`` run id
    and returns ``now - timestamp`` in minutes.

    Args:
        run_id: The terratest-run tag value.
        now: Current UTC time (injected for testability).

    Returns:
        Age in minutes, or None when the run id does not match the
        auto-generated ``tt-<14-digit-UTC>-<rand>`` shape (an externally-supplied
        or malformed id whose age cannot be derived from the value alone).
    """
    prefix = f"{_constants.TERRATEST_RUN_ID_PREFIX}-"
    if not run_id.startswith(prefix):
        return None
    parts = run_id.split("-")
    # tt-<yyyymmddHHMMSS>-<rand> -> ["tt", "<14 digits>", "<rand>"]
    if len(parts) < 3:
        return None
    ts_token = parts[1]
    if len(ts_token) != 14 or not ts_token.isdigit():
        return None
    try:
        ts = datetime.datetime.strptime(
            ts_token, _constants.TERRATEST_RUN_ID_TIMESTAMP_FORMAT
        ).replace(tzinfo=datetime.UTC)
    except ValueError:
        return None
    return (now - ts).total_seconds() / 60.0


def _partition_by_min_age(
    resources: list[dict[str, Any]],
    min_age_minutes: int,
    now: datetime.datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split resources into (old-enough-to-sweep, too-young-to-sweep).

    A resource is too young when its terratest-run tag value is an
    auto-generated run id whose embedded UTC timestamp is newer than
    ``min_age_minutes`` ago. Resources whose run id age cannot be parsed
    (externally-supplied ids) are treated as old enough to sweep, preserving the
    global sweep's orphan-cleanup behaviour for non-standard tags.

    Args:
        resources: Tagging-API resource dicts (each with Tags + ResourceARN).
        min_age_minutes: Age floor below which a resource is spared.
        now: Current UTC time (injected for testability).

    Returns:
        (sweepable, spared) lists.
    """
    sweepable: list[dict[str, Any]] = []
    spared: list[dict[str, Any]] = []
    for resource in resources:
        value = _run_id_tag_value(resource)
        age = _run_id_age_minutes(value, now) if value is not None else None
        if age is not None and age < min_age_minutes:
            spared.append(resource)
        else:
            sweepable.append(resource)
    return sweepable, spared


def _enumerate_and_spare(
    boto3_mod: Any,
    run_id: str | None,
    poll_timeout: float,
    poll_interval: float,
    now: datetime.datetime,
) -> list[dict[str, Any]]:
    """Enumerate the tag-scoped inventory and apply the GLOBAL-sweep min-age guard.

    Single site (DRY) for the enumerate-then-spare step shared by the initial
    inventory pass and every re-enumeration inside the loop-until-stable delete
    loop. On the GLOBAL cross-run path (run_id is None) it spares resources whose
    terratest-run tag encodes a UTC timestamp younger than TT_SWEEP_MIN_AGE_MINUTES
    (an in-flight PR matrix job's resources) and logs how many were spared; the
    run-scoped path (run_id set) sweeps everything it enumerated.

    Args:
        boto3_mod: The boto3 module (injected for testability).
        run_id: Optional run id to narrow the filter (None = global sweep).
        poll_timeout: Max seconds to retry throttled Tagging API calls.
        poll_interval: Seconds between retry attempts (derives max_attempts).
        now: Current UTC time used to compute resource age (injected for tests).

    Returns:
        The list of sweepable resources (spared in-flight resources removed).
    """
    resources = _enumerate_resources(boto3_mod, run_id, poll_timeout, poll_interval)
    if run_id is None:
        min_age = _get_min_age_minutes()
        resources, spared = _partition_by_min_age(resources, min_age, now)
        if spared:
            print(
                f"INFO: sparing {len(spared)} in-flight resource(s) younger than "
                f"{min_age} minute(s) from the global sweep.",
                file=sys.stderr,
            )
    return resources


# ---------------------------------------------------------------------------
# Cost Explorer anomaly-monitor sweep (untaggable -- absent from Tagging API)
#
# CE anomaly monitors and subscriptions do not appear in the Resource Groups
# Tagging API, so the tag-scoped sweep never sees them and they orphan. This
# direct CE-API sweep enumerates them and deletes the ones whose name matches a
# terratest fixture pattern (test-monitor-<unix-ts>, *-monitor for the
# subscription's <name>-<suffix>-monitor / -subscription). It runs only in the
# GLOBAL path (run_id=None): a run-scoped name-pattern delete would race other
# concurrent runs' identically-patterned monitors. Subscriptions are deleted
# before monitors (a monitor cannot be deleted while a subscription references
# it). test-*-<unix-ts> names carry a parseable creation epoch used for the same
# min-age guard as the tag-based path.
# ---------------------------------------------------------------------------


def _ce_name_matches(name: str, prefixes: tuple[str, ...], suffixes: tuple[str, ...]) -> bool:
    """Return True when name starts with any prefix or ends with any suffix."""
    return any(name.startswith(p) for p in prefixes) or any(name.endswith(s) for s in suffixes)


def _ce_epoch_age_minutes(name: str, now: datetime.datetime) -> float | None:
    """Age in minutes derived from a trailing unix-epoch token, else None.

    Fixture names of the form ``test-monitor-<unix-seconds>`` /
    ``test-sub-<unix-seconds>`` embed the creation epoch; this parses the
    trailing token so the global CE sweep can spare an in-flight test's monitor.
    Names without a numeric trailing token (e.g. ``<name>-<suffix>-monitor``)
    return None (age unknown).
    """
    token = name.rsplit("-", 1)[-1]
    if token.isdigit() and len(token) >= 9:
        try:
            ts = datetime.datetime.fromtimestamp(int(token), tz=datetime.UTC)
        except ValueError, OverflowError, OSError:
            return None
        return (now - ts).total_seconds() / 60.0
    return None


def _ce_paginate(client: Any, method_name: str, result_key: str) -> list[dict[str, Any]]:
    """Collect all items from a NextPageToken-paginated CE list call.

    The CE API returns NextPageToken as a non-empty string when more pages exist
    and omits it otherwise. The loop continues ONLY while the token is a non-empty
    string, so a malformed/missing token terminates the sweep deterministically
    rather than looping forever (fail-safe: never spin indefinitely on an
    unexpected response shape).
    """
    method = getattr(client, method_name)
    items: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs = {"NextPageToken": next_token} if next_token else {}
        response = method(**kwargs)
        page_items = response.get(result_key, [])
        if isinstance(page_items, list):
            items.extend(page_items)
        token = response.get("NextPageToken")
        if not isinstance(token, str) or not token:
            break
        next_token = token
    return items


def _sweep_ce_anomaly_monitors(
    boto3_mod: Any,
    mode: str,
    min_age_minutes: int,
    now: datetime.datetime,
) -> list[dict[str, Any]]:
    """Enumerate and (in delete mode) delete terratest CE monitors/subscriptions.

    Args:
        boto3_mod: The boto3 module (injected for testability).
        mode: "check" (report matches as found) or "delete".
        min_age_minutes: Spare matches whose name encodes an epoch younger than this.
        now: Current UTC time (injected for testability).

    Returns:
        Report entries: {"arn", "type": "ce", "action": "found"|"deleted"|
        "already-gone"|"failed", "name"}.
    """
    client = _make_client(boto3_mod, "ce")
    entries: list[dict[str, Any]] = []

    # Subscriptions first: a monitor cannot be deleted while a subscription
    # references it.
    for sub in _ce_paginate(client, "get_anomaly_subscriptions", "AnomalySubscriptions"):
        name = sub.get("SubscriptionName", "")
        arn = sub.get("SubscriptionArn", "")
        if not _ce_name_matches(
            name, _constants.CE_SUBSCRIPTION_NAME_PREFIXES, _constants.CE_SUBSCRIPTION_NAME_SUFFIXES
        ):
            continue
        age = _ce_epoch_age_minutes(name, now)
        if age is not None and age < min_age_minutes:
            continue
        entries.append(
            _ce_act(client, mode, arn, name, "delete_anomaly_subscription", "SubscriptionArn")
        )

    for mon in _ce_paginate(client, "get_anomaly_monitors", "AnomalyMonitors"):
        name = mon.get("MonitorName", "")
        arn = mon.get("MonitorArn", "")
        if not _ce_name_matches(
            name, _constants.CE_MONITOR_NAME_PREFIXES, _constants.CE_MONITOR_NAME_SUFFIXES
        ):
            continue
        age = _ce_epoch_age_minutes(name, now)
        if age is not None and age < min_age_minutes:
            continue
        entries.append(_ce_act(client, mode, arn, name, "delete_anomaly_monitor", "MonitorArn"))

    return entries


def _ce_act(
    client: Any,
    mode: str,
    arn: str,
    name: str,
    delete_method: str,
    arn_kwarg: str,
) -> dict[str, Any]:
    """Report (check) or delete (delete mode) a single matched CE resource."""
    if mode == "check":
        return {"arn": arn, "type": "ce", "action": "found", "name": name}
    try:
        getattr(client, delete_method)(**{arn_kwarg: arn})
    except botocore.exceptions.ClientError as exc:
        if _is_benign_not_found(exc):
            return {"arn": arn, "type": "ce", "action": "already-gone", "name": name}
        return {"arn": arn, "type": "ce", "action": "failed", "name": name}
    return {"arn": arn, "type": "ce", "action": "deleted", "name": name}


# ---------------------------------------------------------------------------
# Service handlers -- common interface
# ---------------------------------------------------------------------------


class ResourceHandler:
    """Abstract base for per-service deletion handlers.

    Each subclass handles one Tagging-API service prefix and implements
    ``delete`` to perform dependency-safe deletion of a single matched
    resource ARN.
    """

    service_prefix: str

    def delete(self, arn: str, boto3_mod: Any) -> str:
        """Delete the resource identified by ARN.

        Args:
            arn: The full resource ARN from the Tagging API.
            boto3_mod: The boto3 module (injected for testability).

        Returns:
            action string: one of "deleted", "scheduled", "failed".

        Raises:
            Exception: On unrecoverable deletion errors; the caller records
                the failure and sets the overall exit code to non-zero.
        """
        raise NotImplementedError

    def is_live(self, arn: str, boto3_mod: Any) -> bool:
        """Return True iff the tagged resource is still genuinely live.

        Check mode counts a tagged resource as an orphan ONLY when this returns
        True. The default is True: most services either drop the tag promptly on
        delete or have no terminal-but-still-tagged state, so a tag match alone
        is authoritative.

        Subclasses override this for services whose resources remain visible in
        the Resource Groups Tagging API after deletion / in a terminal non-live
        state (e.g. ECS clusters/services/task-definitions go INACTIVE on delete
        but keep their tags). For those, an authoritative describe-API call is the
        only way to distinguish a real orphan from a Tagging-API lag phantom.

        Args:
            arn: The full resource ARN from the Tagging API.
            boto3_mod: The boto3 module (injected for testability).

        Returns:
            True if the resource is live and must be counted as an orphan;
            False if it is provably non-live (deleted / terminal) and must be
            excluded from the orphan count.
        """
        return True


# ---------------------------------------------------------------------------
# S3 handler: purge versioned objects + delete-markers, then delete bucket
# ---------------------------------------------------------------------------


class S3Handler(ResourceHandler):
    """Handle s3:: ARNs -- versioned object purge then bucket deletion."""

    service_prefix = "s3"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        # ARN shape: arn:aws:s3:::<bucket-name>
        bucket = arn.split(":::")[-1].split("/")[0]
        client = _make_client(boto3_mod, "s3")
        # Purge all versions and delete markers first using the paginator so that
        # every page of versions and delete-markers is removed (not just the first page).
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
            if versions:
                client.delete_objects(
                    Bucket=bucket,
                    Delete={
                        "Objects": [
                            {"Key": v["Key"], "VersionId": v["VersionId"]} for v in versions
                        ],
                        "Quiet": True,
                    },
                )
        client.delete_bucket(Bucket=bucket)
        return "deleted"


# ---------------------------------------------------------------------------
# KMS handler: schedule-key-deletion
# ---------------------------------------------------------------------------


class KMSHandler(ResourceHandler):
    """Handle kms:: ARNs -- alias deletion + schedule-key-deletion."""

    service_prefix = "kms"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "kms")
        # If it's an alias ARN, delete the alias first.
        if ":alias/" in arn:
            alias_name = "alias/" + arn.split(":alias/", 1)[1]
            client.delete_alias(AliasName=alias_name)
            return "deleted"
        # It's a key ARN.
        key_id = arn.split("/")[-1]
        # Delete the customer aliases that target this key FIRST. KMS aliases are
        # untaggable (never returned by the Tagging API), so scheduling the key for
        # deletion alone would strand the alias for the whole pending window -- and an
        # account-global alias (alias/telemetry-data) then blocks the next run's
        # CreateAlias. Deleting the alias now frees the name immediately.
        self._delete_key_aliases(key_id, client)
        # Check the current key state before scheduling deletion.
        # If the key is already in PendingDeletion / PendingReplicaDeletion,
        # schedule_key_deletion would raise KMSInvalidStateException -- treat
        # the key as already handled and return "scheduled" without calling AWS.
        try:
            key_state = client.describe_key(KeyId=key_id).get("KeyMetadata", {}).get("KeyState", "")
        except botocore.exceptions.ClientError:
            # Cannot determine key state; attempt deletion and let it fail-fast
            # if the state is genuinely invalid.
            key_state = ""
        if key_state in _constants.KMS_PENDING_DELETION_STATES:
            # Already scheduled; nothing more to do.
            return "scheduled"
        client.schedule_key_deletion(
            KeyId=key_id,
            PendingWindowInDays=_KMS_PENDING_WINDOW_DAYS,
        )
        # Report as 'scheduled' per spec (key is not immediately gone).
        return "scheduled"

    @staticmethod
    def _delete_key_aliases(key_id: str, client: Any) -> None:
        """Delete every customer alias that targets the given KMS key id.

        KMS aliases are not taggable, so they never appear in the Tagging API and
        a tag-scoped sweep cannot reach them directly. Enumerating the aliases of a
        tagged key being swept is the only way to remove the dangling customer
        alias (which otherwise blocks the next run's CreateAlias on an
        account-global name). AWS-managed aliases (alias/aws/*) cannot be deleted
        and are skipped. An already-gone alias (NotFoundException) is benign.

        Args:
            key_id: The KMS key id whose aliases should be deleted.
            client: The KMS boto3 client.
        """
        paginator = client.get_paginator("list_aliases")
        for page in paginator.paginate(KeyId=key_id):
            for alias in page.get("Aliases", []):
                alias_name = alias.get("AliasName", "")
                if not alias_name or alias_name.startswith(_AWS_MANAGED_ALIAS_PREFIX):
                    continue
                try:
                    client.delete_alias(AliasName=alias_name)
                except botocore.exceptions.ClientError as exc:
                    # An alias already removed by Terraform destroy / a prior pass is
                    # benign; any other error is surfaced (fail-fast).
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code == "NotFoundException":
                        continue
                    raise


# ---------------------------------------------------------------------------
# IAM handler: detach/delete inline + managed attachments, then delete
# ---------------------------------------------------------------------------


class IAMHandler(ResourceHandler):
    """Handle iam:: ARNs -- roles, policies, instance profiles."""

    service_prefix = "iam"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "iam")
        if ":role/" in arn:
            role_name = arn.split(":role/", 1)[1]
            # Remove from instance profiles.
            profiles = client.list_instance_profiles_for_role(RoleName=role_name).get(
                "InstanceProfiles", []
            )
            for profile in profiles:
                client.remove_role_from_instance_profile(
                    InstanceProfileName=profile["InstanceProfileName"],
                    RoleName=role_name,
                )
            # Detach managed policies.
            for policy in client.list_attached_role_policies(RoleName=role_name).get(
                "AttachedPolicies", []
            ):
                client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
            # Delete inline policies.
            for policy_name in client.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            client.delete_role(RoleName=role_name)
        elif ":policy/" in arn:
            # Detach from all entities first.
            for entity in client.list_entities_for_policy(PolicyArn=arn).get("PolicyRoles", []):
                client.detach_role_policy(RoleName=entity["RoleName"], PolicyArn=arn)
            client.delete_policy(PolicyArn=arn)
        elif ":instance-profile/" in arn:
            profile_name = arn.split(":instance-profile/", 1)[1]
            profile = client.get_instance_profile(InstanceProfileName=profile_name).get(
                "InstanceProfile", {}
            )
            for role in profile.get("Roles", []):
                client.remove_role_from_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role["RoleName"],
                )
            client.delete_instance_profile(InstanceProfileName=profile_name)
        return "deleted"


# ---------------------------------------------------------------------------
# CloudFront handler: disable-waiter-Deployed, then delete
# ---------------------------------------------------------------------------


class CloudFrontHandler(ResourceHandler):
    """Handle cloudfront:: ARNs -- disable distribution, wait Deployed via waiter, then delete."""

    service_prefix = "cloudfront"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "cloudfront")
        dist_id = arn.split("/")[-1]
        # Get current config and ETag.
        resp = client.get_distribution_config(Id=dist_id)
        etag = resp["ETag"]
        config = resp["DistributionConfig"]
        if config.get("Enabled", True):
            config["Enabled"] = False
            update_resp = client.update_distribution(
                Id=dist_id, DistributionConfig=config, IfMatch=etag
            )
            etag = update_resp["ETag"]
            # Use the SDK-managed waiter for distribution_deployed -- eliminates
            # hand-rolled sleep-as-synchronization (CLAUDE.md Code Standards).
            poll_timeout = _get_poll_timeout()
            poll_interval = _get_poll_interval()
            delay = max(1, int(poll_interval)) if poll_interval > 0 else 1
            max_attempts = _compute_max_attempts(poll_timeout, poll_interval)
            try:
                waiter = client.get_waiter("distribution_deployed")
                waiter.wait(
                    Id=dist_id,
                    WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts},
                )
            except botocore.exceptions.WaiterError as exc:
                raise TimeoutError(
                    f"CloudFront distribution {dist_id} did not reach Deployed "
                    f"within {poll_timeout}s after disabling."
                ) from exc
            # Refresh ETag after waiter confirms Deployed state.
            etag = client.get_distribution(Id=dist_id)["ETag"]
        client.delete_distribution(Id=dist_id, IfMatch=etag)
        return "deleted"

    def is_live(self, arn: str, boto3_mod: Any) -> bool:
        """Return True iff the CloudFront distribution genuinely still exists.

        CloudFront's Resource Groups Tagging API index lags significantly behind
        deletion: it keeps returning a distribution ARN for many minutes (and the
        tag association persists) AFTER the distribution itself is gone. Counting
        such a phantom as an orphan makes the portal / collector-ingestion
        zero-orphan proof false-fail right after its own test destroyed the
        distribution. The authoritative GetDistribution API is the only signal
        that distinguishes a real orphan from a Tagging-API lag phantom.

        A distribution is live (counted as an orphan) ONLY when GetDistribution
        succeeds. A NoSuchDistribution (or any benign already-gone) ClientError
        proves it is gone and must be excluded. Mirrors the ECS is_live override
        and the EC2 eventual-consistency handling.

        Args:
            arn: The full CloudFront distribution ARN from the Tagging API.
            boto3_mod: The boto3 module (injected for testability).

        Returns:
            True if GetDistribution confirms the distribution exists; False if a
            NotFound/already-gone error proves it has been deleted.
        """
        client = _make_client(boto3_mod, "cloudfront")
        dist_id = arn.split("/")[-1]
        try:
            client.get_distribution(Id=dist_id)
            return True
        except botocore.exceptions.ClientError as exc:
            if _is_benign_not_found(exc):
                return False
            # Any other error cannot prove the distribution is gone, so keep it in
            # the orphan count (the delete-mode fail-safe will surface a real leak).
            raise


# ---------------------------------------------------------------------------
# ACM handler
# ---------------------------------------------------------------------------


class ACMHandler(ResourceHandler):
    """Handle acm:: ARNs -- certificate deletion."""

    service_prefix = "acm"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "acm")
        client.delete_certificate(CertificateArn=arn)
        return "deleted"


# ---------------------------------------------------------------------------
# Route53 handler: delete record sets then zone
# ---------------------------------------------------------------------------


class Route53Handler(ResourceHandler):
    """Handle route53:: ARNs -- record sets then hosted zone deletion."""

    service_prefix = "route53"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "route53")
        if ":hostedzone/" in arn:
            zone_id = arn.split(":hostedzone/", 1)[1]
            # Resolve the sub-zone apex name so the dangling NS delegation that
            # points at it (written into a SHARED parent zone the fixture does not
            # own, and therefore neither tagged nor returned by the Tagging API) can
            # be removed once the sub-zone is gone. A clean terraform destroy removes
            # both; a cancelled run strands the parent NS record forever otherwise.
            zone_name = self._zone_name(zone_id, client)
            # Delete all non-SOA/NS records first.
            response = client.list_resource_record_sets(HostedZoneId=zone_id)
            changes = [
                {"Action": "DELETE", "ResourceRecordSet": rrs}
                for rrs in response.get("ResourceRecordSets", [])
                if rrs["Type"] not in ("SOA", "NS")
            ]
            if changes:
                client.change_resource_record_sets(
                    HostedZoneId=zone_id,
                    ChangeBatch={"Changes": changes},
                )
            client.delete_hosted_zone(Id=zone_id)
            # Now that the sub-zone is deleted, remove its dangling NS delegation
            # record from whichever parent zone holds it (scoped EXACTLY to the
            # deleted sub-zone's name, so a parent's apex NS and unrelated
            # delegations are never touched).
            if zone_name:
                self._delete_parent_delegation(zone_id, zone_name, client)
        else:
            # rrset, change, healthcheck, and any other sub-type ARNs cannot be
            # independently deleted via this handler -- they require the hosted zone
            # context or are lifecycle artefacts managed implicitly by Route53.
            # Report as unactioned so the caller records a failure and exits non-zero
            # (fail-safe invariant: matched-but-unactioned != deleted).
            raise ValueError(
                f"Route53Handler: no deletion path for ARN sub-type (ARN: {arn}). "
                "Only ':hostedzone/' ARNs are supported. "
                "Resource left untouched."
            )
        return "deleted"

    @staticmethod
    def _zone_name(zone_id: str, client: Any) -> str:
        """Return the apex name (with trailing dot) of a hosted zone, or '' if unknown.

        A missing/inaccessible zone yields '' so the caller simply skips the
        parent-delegation cleanup rather than failing the whole sweep.

        Args:
            zone_id: The hosted zone id.
            client: The Route53 boto3 client.
        """
        try:
            return str(client.get_hosted_zone(Id=zone_id).get("HostedZone", {}).get("Name", ""))
        except botocore.exceptions.ClientError:
            return ""

    @staticmethod
    def _delete_parent_delegation(child_zone_id: str, zone_name: str, client: Any) -> None:
        """Delete the NS delegation record for ``zone_name`` from its parent zone.

        The terratest collector/portal fixtures create a per-run sub-zone and write
        its name servers as an NS record INTO a shared parent zone (the live qa
        public zone). That parent zone is persistent and never swept, and its NS
        delegation record is not taggable, so a cancelled run strands it. This scans
        the account's hosted zones for the one parent that holds an NS record whose
        name equals the just-deleted sub-zone apex and deletes ONLY that record.

        Matching is exact on the record name (== the deleted sub-zone apex) AND type
        NS, so a parent zone's own apex NS (a different name) and every unrelated
        delegation are left untouched. The parent hosted zone itself is never deleted.

        Args:
            child_zone_id: The id of the sub-zone just deleted (skipped while scanning).
            zone_name: The deleted sub-zone apex name (with trailing dot).
            client: The Route53 boto3 client.
        """
        child_zone_id_suffix = child_zone_id.split("/")[-1]
        paginator = client.get_paginator("list_hosted_zones")
        for page in paginator.paginate():
            for zone in page.get("HostedZones", []):
                # Route53 ids are returned as "/hostedzone/<id>"; normalise to the
                # bare id (the form change_resource_record_sets / the child arn use).
                parent_id = zone.get("Id", "").split("/")[-1]
                # Never treat the deleted sub-zone as its own parent.
                if parent_id == child_zone_id_suffix:
                    continue
                # The delegation lives at the child apex name inside the parent; a
                # parent only holds it when it is a strict ancestor of zone_name.
                parent_name = str(zone.get("Name", ""))
                if not zone_name.endswith("." + parent_name) and zone_name != parent_name:
                    continue
                rrsets = client.list_resource_record_sets(
                    HostedZoneId=parent_id,
                    StartRecordName=zone_name,
                    StartRecordType="NS",
                    MaxItems="1",
                ).get("ResourceRecordSets", [])
                for rrs in rrsets:
                    if rrs.get("Name") == zone_name and rrs.get("Type") == "NS":
                        client.change_resource_record_sets(
                            HostedZoneId=parent_id,
                            ChangeBatch={
                                "Changes": [{"Action": "DELETE", "ResourceRecordSet": rrs}]
                            },
                        )
                        return


# ---------------------------------------------------------------------------
# EC2 handler: dispatches a single ec2:: ARN to the appropriate delete call.
# Reverse-dependency tier ordering across the VPC family is enforced by
# _partition_ec2_by_tier() in main(), NOT inside this per-ARN handler.
# ---------------------------------------------------------------------------


class EC2Handler(ResourceHandler):
    """Dispatch a single ec2:: ARN to its delete call.

    Handles natgateway, elastic-ip/eip-allocation, subnet, route-table,
    internet-gateway, security-group, vpc-endpoint, and vpc resource types.
    Cross-resource ordering (tier 1 -> tier 2 -> tier 3) is imposed by the
    caller (_partition_ec2_by_tier in main()), not by this class.
    """

    service_prefix = "ec2"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "ec2")
        # Determine resource type from ARN resource segment.
        # ARN shape: arn:aws:ec2:<region>:<account>:<type>/<id>
        resource_segment = arn.split(":", 5)[-1] if arn.count(":") >= 5 else arn
        resource_type, _, resource_id = resource_segment.partition("/")

        if resource_type == "natgateway":
            # NAT Gateway first (depends on EIP and subnet).
            # Skip gateways already in a terminal deleted/deleting state to avoid
            # spurious API errors from the tagging-API eventual-consistency window.
            ngw_resp = client.describe_nat_gateways(NatGatewayIds=[resource_id])
            ngw_state = ""
            for ngw in ngw_resp.get("NatGateways", []):
                ngw_state = ngw.get("State", "")
            if ngw_state not in ("deleted", "deleting"):
                client.delete_nat_gateway(NatGatewayId=resource_id)
        elif resource_type == "vpc-flow-log":
            # VPC flow logs are deleted via delete_flow_logs.
            # If the flow log no longer exists (already deleted), AWS raises
            # InvalidFlowLogId.NotFound -- treat as already-deleted (not an error).
            try:
                client.delete_flow_logs(FlowLogIds=[resource_id])
            except botocore.exceptions.ClientError as _fl_exc:
                if _fl_exc.response.get("Error", {}).get("Code", "") == "InvalidFlowLogId.NotFound":
                    pass  # Already gone -- idempotent delete is acceptable.
                else:
                    raise
        elif resource_type in ("elastic-ip", "eip-allocation"):
            # EIP second (released after NAT GW is deleted)
            client.release_address(AllocationId=resource_id)
        elif resource_type == "subnet":
            client.delete_subnet(SubnetId=resource_id)
        elif resource_type == "route-table":
            client.delete_route_table(RouteTableId=resource_id)
        elif resource_type == "internet-gateway":
            # Detach first.
            igw_resp = client.describe_internet_gateways(InternetGatewayIds=[resource_id])
            for igw in igw_resp.get("InternetGateways", []):
                for attach in igw.get("Attachments", []):
                    client.detach_internet_gateway(
                        InternetGatewayId=resource_id,
                        VpcId=attach["VpcId"],
                    )
            client.delete_internet_gateway(InternetGatewayId=resource_id)
        elif resource_type == "security-group":
            # A VPC's default security group (GroupName == "default") cannot be
            # deleted independently -- AWS rejects DeleteSecurityGroup for it
            # ("cannot be deleted by a user"); it is removed implicitly when the
            # VPC is deleted. Skip it (the caller records "skipped-default-sg",
            # a success outcome) instead of failing the sweep.
            if _is_default_security_group(resource_id, boto3_mod):
                raise _DefaultSecurityGroupSkipError(resource_id)
            client.delete_security_group(GroupId=resource_id)
        elif resource_type == "vpc-endpoint":
            # Before calling delete_vpc_endpoints, check the current endpoint state.
            # If the endpoint is already in a terminal deleted/deleting state (or no
            # longer exists at all), treat it as already handled to avoid spurious
            # errors from the Tagging-API eventual-consistency window.
            try:
                vpce_resp = client.describe_vpc_endpoints(VpcEndpointIds=[resource_id])
                vpce_endpoints = vpce_resp.get("VpcEndpoints", [])
                vpce_state = vpce_endpoints[0].get("State", "") if vpce_endpoints else "deleted"
                if vpce_state not in ("deleted", "deleting"):
                    client.delete_vpc_endpoints(VpcEndpointIds=[resource_id])
            except botocore.exceptions.ClientError as _vpce_exc:
                if (
                    _vpce_exc.response.get("Error", {}).get("Code", "")
                    == "InvalidVpcEndpointId.NotFound"
                ):
                    pass  # Already gone -- idempotent delete is acceptable.
                else:
                    raise
        elif resource_type == "vpc":
            client.delete_vpc(VpcId=resource_id)
        else:
            # Unrecognised EC2 resource type -- no deletion was performed.
            # Raise so the caller records a failure and exits non-zero
            # (fail-safe invariant: matched-but-unactioned != deleted).
            raise ValueError(
                f"EC2Handler: unrecognised resource type {resource_type!r} (ARN: {arn}). "
                "Resource left untouched."
            )
        return "deleted"


# ---------------------------------------------------------------------------
# ECS handler
# ---------------------------------------------------------------------------


class ECSHandler(ResourceHandler):
    """Handle ecs:: ARNs -- scale service to 0, delete, deregister task defs."""

    service_prefix = "ecs"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "ecs")
        if ":cluster/" in arn:
            cluster = arn.split(":cluster/", 1)[1]
            client.delete_cluster(cluster=cluster)
        elif ":service/" in arn:
            parts = arn.split(":")
            # arn:aws:ecs:<region>:<account>:service/<cluster>/<service>
            service_part = parts[-1].split("/")
            cluster = service_part[1] if len(service_part) >= 3 else service_part[0]
            service = service_part[-1]
            client.update_service(cluster=cluster, service=service, desiredCount=0)
            client.delete_service(cluster=cluster, service=service, force=True)
        elif ":task-definition/" in arn:
            task_def = arn.split(":task-definition/", 1)[1]
            client.deregister_task_definition(taskDefinition=task_def)
        else:
            # Unrecognised ECS resource sub-type -- no deletion was performed.
            # Raise so the caller records a failure and exits non-zero
            # (fail-safe invariant: matched-but-unactioned != deleted).
            raise ValueError(
                f"ECSHandler: unrecognised resource sub-type (ARN: {arn}). Resource left untouched."
            )
        return "deleted"

    def is_live(self, arn: str, boto3_mod: Any) -> bool:
        """Return True iff the ECS resource is genuinely live (status ACTIVE).

        ECS keeps tagged shells around after deletion: clusters and services go
        INACTIVE on delete and task-definitions go INACTIVE / DELETE_IN_PROGRESS
        on deregister, yet all remain visible in the Resource Groups Tagging API
        for a while. Counting those as orphans makes every ECS module's zero-orphan
        proof false-fail right after its own test. The authoritative ECS describe
        API is the only signal that distinguishes a real orphan from a phantom.

        A resource is live (counted as an orphan) ONLY when its describe call
        reports status == "ACTIVE". Any non-ACTIVE status (INACTIVE,
        DELETE_IN_PROGRESS, DRAINING, etc.), a missing resource, or a ClientError
        (ClusterNotFound, ServiceNotFound, ClientException for a missing
        task-definition, etc.) means the resource is NOT live -- a missing or
        terminal resource is not an orphan. Mirrors the EC2 eventual-consistency
        handling (see _is_nat_gateway_deleted / _is_vpc_endpoint_deleted): only
        an authoritative live signal counts; everything else is excluded.

        Args:
            arn: The full ECS resource ARN from the Tagging API.
            boto3_mod: The boto3 module (injected for testability).

        Returns:
            True if the ECS resource status is ACTIVE; False otherwise.
        """
        client = _make_client(boto3_mod, "ecs")
        try:
            if ":cluster/" in arn:
                cluster = arn.split(":cluster/", 1)[1]
                response = client.describe_clusters(clusters=[cluster])
                clusters = response.get("clusters", [])
                if not clusters:
                    return False
                return str(clusters[0].get("status", "")) == "ACTIVE"
            if ":service/" in arn:
                # arn:aws:ecs:<region>:<account>:service/<cluster>/<service>
                service_part = arn.split(":")[-1].split("/")
                cluster = service_part[1] if len(service_part) >= 3 else service_part[0]
                service = service_part[-1]
                response = client.describe_services(cluster=cluster, services=[service])
                services = response.get("services", [])
                if not services:
                    return False
                return str(services[0].get("status", "")) == "ACTIVE"
            if ":task-definition/" in arn:
                task_def = arn.split(":task-definition/", 1)[1]
                response = client.describe_task_definition(taskDefinition=task_def)
                return str(response.get("taskDefinition", {}).get("status", "")) == "ACTIVE"
        except botocore.exceptions.ClientError:
            # ClusterNotFound / ServiceNotFound / ClientException (missing
            # task-definition) all mean the resource is gone -- not an orphan.
            return False
        # Unrecognised ECS sub-type: cannot prove liveness, so do not exclude it
        # from the orphan count (delete mode will flag it via the fail-safe).
        return True


# ---------------------------------------------------------------------------
# ELB handler
# ---------------------------------------------------------------------------


class ELBHandler(ResourceHandler):
    """Handle elasticloadbalancing:: ARNs -- listeners, ALBs, target groups."""

    service_prefix = "elasticloadbalancing"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "elbv2")
        if ":loadbalancer/" in arn:
            # Delete listeners first.
            listeners = client.describe_listeners(LoadBalancerArn=arn).get("Listeners", [])
            for listener in listeners:
                client.delete_listener(ListenerArn=listener["ListenerArn"])
            client.delete_load_balancer(LoadBalancerArn=arn)
        elif ":targetgroup/" in arn:
            client.delete_target_group(TargetGroupArn=arn)
        return "deleted"


# ---------------------------------------------------------------------------
# Firehose handler
# ---------------------------------------------------------------------------


class FirehoseHandler(ResourceHandler):
    """Handle firehose:: ARNs -- delete delivery stream and poll until gone."""

    service_prefix = "firehose"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "firehose")
        stream_name = arn.split("/", 1)[-1]
        client.delete_delivery_stream(
            DeliveryStreamName=stream_name,
            AllowForceDelete=True,
        )
        return "deleted"


# ---------------------------------------------------------------------------
# Glue handler
# ---------------------------------------------------------------------------


class GlueHandler(ResourceHandler):
    """Handle glue:: ARNs -- tables then database."""

    service_prefix = "glue"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "glue")
        if ":database/" in arn:
            db_name = arn.split(":database/", 1)[1]
            # Delete all tables first.
            tables = client.get_tables(DatabaseName=db_name).get("TableList", [])
            for table in tables:
                client.delete_table(DatabaseName=db_name, Name=table["Name"])
            client.delete_database(Name=db_name)
        elif ":table/" in arn:
            parts = arn.split(":table/", 1)[1].split("/")
            db_name, table_name = parts[0], parts[1] if len(parts) > 1 else parts[0]
            client.delete_table(DatabaseName=db_name, Name=table_name)
        else:
            # Unrecognised Glue resource sub-type -- no deletion was performed.
            # Raise so the caller records a failure and exits non-zero
            # (fail-safe invariant: matched-but-unactioned != deleted).
            raise ValueError(
                f"GlueHandler: unrecognised resource sub-type (ARN: {arn}). "
                "Resource left untouched."
            )
        return "deleted"


# ---------------------------------------------------------------------------
# Athena handler
# ---------------------------------------------------------------------------


class AthenaHandler(ResourceHandler):
    """Handle athena:: ARNs -- workgroup with recursive delete."""

    service_prefix = "athena"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "athena")
        if ":workgroup/" in arn:
            workgroup = arn.split(":workgroup/", 1)[1]
            client.delete_work_group(WorkGroup=workgroup, RecursiveDeleteOption=True)
        return "deleted"


# ---------------------------------------------------------------------------
# SNS handler
# ---------------------------------------------------------------------------


class SNSHandler(ResourceHandler):
    """Handle sns:: ARNs -- delete topic (subscriptions cascade)."""

    service_prefix = "sns"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "sns")
        client.delete_topic(TopicArn=arn)
        return "deleted"


# ---------------------------------------------------------------------------
# SSM handler
# ---------------------------------------------------------------------------


class SSMHandler(ResourceHandler):
    """Handle ssm:: ARNs -- delete parameter."""

    service_prefix = "ssm"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "ssm")
        # ARN shape: arn:aws:ssm:<region>:<account>:parameter/<name>
        if ":parameter/" in arn:
            param_name = "/" + arn.split(":parameter/", 1)[1]
        else:
            param_name = arn.split("/", 1)[-1]
        client.delete_parameter(Name=param_name)
        return "deleted"


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


class LambdaHandler(ResourceHandler):
    """Handle lambda:: ARNs -- delete function."""

    service_prefix = "lambda"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "lambda")
        client.delete_function(FunctionName=arn)
        return "deleted"


# ---------------------------------------------------------------------------
# CloudWatch / Logs handler
# ---------------------------------------------------------------------------


class LogsHandler(ResourceHandler):
    """Handle logs:: ARNs -- delete CloudWatch log groups."""

    service_prefix = "logs"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "logs")
        # ARN shape: arn:aws:logs:<region>:<account>:log-group:<name>
        log_group = arn.split(":log-group:", 1)[-1].split(":")[0]
        client.delete_log_group(logGroupName=log_group)
        return "deleted"


class CloudWatchHandler(ResourceHandler):
    """Handle cloudwatch:: ARNs -- alarms and metrics."""

    service_prefix = "cloudwatch"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "cloudwatch")
        alarm_name = arn.split(":")[-1]
        client.delete_alarms(AlarmNames=[alarm_name])
        return "deleted"


# ---------------------------------------------------------------------------
# QuickSight handler
# ---------------------------------------------------------------------------


class QuickSightHandler(ResourceHandler):
    """Handle quicksight:: ARNs -- data sources and datasets."""

    service_prefix = "quicksight"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "quicksight")
        # ARN shape: arn:aws:quicksight:<region>:<account>:<type>/<account-id>/<id>
        parts = arn.split(":")
        account_id = parts[4]
        resource_segment = parts[5] if len(parts) > 5 else ""
        if resource_segment.startswith("dataset/"):
            ds_parts = resource_segment.split("/")
            data_set_id = ds_parts[-1]
            client.delete_data_set(AwsAccountId=account_id, DataSetId=data_set_id)
        elif resource_segment.startswith("datasource/"):
            ds_parts = resource_segment.split("/")
            data_source_id = ds_parts[-1]
            client.delete_data_source(AwsAccountId=account_id, DataSourceId=data_source_id)
        else:
            # Unrecognised QuickSight resource sub-type -- no deletion was performed.
            # Raise so the caller records a failure and exits non-zero
            # (fail-safe invariant: matched-but-unactioned != deleted).
            raise ValueError(
                f"QuickSightHandler: unrecognised resource sub-type "
                f"{resource_segment!r} (ARN: {arn}). "
                "Resource left untouched."
            )
        return "deleted"


# ---------------------------------------------------------------------------
# Budgets / CE handler
# ---------------------------------------------------------------------------


class BudgetsHandler(ResourceHandler):
    """Handle budgets:: and ce:: ARNs -- budgets, anomaly monitors+subscriptions."""

    service_prefix = "budgets"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        if ":budget/" in arn:
            client = _make_client(boto3_mod, "budgets")
            parts = arn.split(":")
            account_id = parts[4]
            budget_name = arn.split(":budget/", 1)[1]
            client.delete_budget(AccountId=account_id, BudgetName=budget_name)
        else:
            client = _make_client(boto3_mod, "ce")
            monitor_arn = arn
            client.delete_anomaly_monitor(MonitorArn=monitor_arn)
        return "deleted"


class CEHandler(ResourceHandler):
    """Handle ce:: ARNs -- cost anomaly monitors and subscriptions."""

    service_prefix = "ce"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        client = _make_client(boto3_mod, "ce")
        if ":anomalysubscription/" in arn:
            client.delete_anomaly_subscription(SubscriptionArn=arn)
        else:
            client.delete_anomaly_monitor(MonitorArn=arn)
        return "deleted"


# ---------------------------------------------------------------------------
# WAFv2 handler
# ---------------------------------------------------------------------------


def _wafv2_parse_arn(arn: str) -> tuple[str, str, str]:
    """Parse a WAFv2 web ACL ARN into (name, scope, id).

    ARN shapes:
      REGIONAL: arn:aws:wafv2:<region>:<account>:regional/webacl/<name>/<id>
      CLOUDFRONT: arn:aws:wafv2:us-east-1:<account>:global/webacl/<name>/<id>

    Returns:
        Tuple of (name, scope, id) where scope is the AWS API value
        (REGIONAL or CLOUDFRONT).

    Raises:
        ValueError: When the ARN does not match the expected webacl shape.
    """
    # arn:aws:wafv2:<region>:<account>:<scope_segment>/webacl/<name>/<id>
    parts = arn.split(":", 5)
    if len(parts) < 6:
        raise ValueError(
            f"WAFv2Handler: ARN too short to parse (ARN: {arn}). "
            "Expected arn:aws:wafv2:<region>:<account>:<scope>/webacl/<name>/<id>."
        )
    resource_segment = parts[5]
    # resource_segment: regional/webacl/<name>/<id> or global/webacl/<name>/<id>
    seg_parts = resource_segment.split("/")
    if len(seg_parts) < 4 or seg_parts[1] != "webacl":
        raise ValueError(
            f"WAFv2Handler: unsupported WAFv2 ARN resource segment {resource_segment!r} "
            f"(ARN: {arn}). Only regional/webacl/<name>/<id> and "
            "global/webacl/<name>/<id> are supported."
        )
    scope_segment = seg_parts[0]
    name = seg_parts[2]
    web_acl_id = seg_parts[3]
    if scope_segment == "regional":
        scope = "REGIONAL"
    elif scope_segment == "global":
        scope = "CLOUDFRONT"
    else:
        raise ValueError(
            f"WAFv2Handler: unrecognised scope segment {scope_segment!r} in ARN {arn}. "
            "Expected 'regional' or 'global'."
        )
    return name, scope, web_acl_id


class WAFv2Handler(ResourceHandler):
    """Handle wafv2:: ARNs -- delete WAFv2 web ACLs.

    The WAFv2 delete_web_acl API requires a LockToken retrieved via get_web_acl.
    WAFNonexistentItemException is treated as already-deleted (eventual consistency
    after Terraform destroy runs but before the Tagging API de-indexes the resource).
    Only webacl ARNs are supported; other WAFv2 resource types raise ValueError
    (fail-safe invariant: matched-but-unactioned != deleted).
    """

    service_prefix = "wafv2"

    def delete(self, arn: str, boto3_mod: Any) -> str:
        name, scope, web_acl_id = _wafv2_parse_arn(arn)
        # CLOUDFRONT-scope WAFv2 resources are always in us-east-1.
        region = "us-east-1" if scope == "CLOUDFRONT" else arn.split(":")[3]
        client = _make_client(boto3_mod, "wafv2", region_name=region)
        try:
            response = client.get_web_acl(Name=name, Scope=scope, Id=web_acl_id)
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "WAFNonexistentItemException":
                # Already deleted -- treat as success (eventual consistency).
                return "already-deleted"
            raise
        lock_token = response["LockToken"]
        try:
            client.delete_web_acl(Name=name, Scope=scope, Id=web_acl_id, LockToken=lock_token)
        except botocore.exceptions.ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "WAFNonexistentItemException":
                return "already-deleted"
            raise
        return "deleted"


# ---------------------------------------------------------------------------
# Cognito user pool handler: clear deletion protection + Hosted-UI domain, then
# delete the user pool.
# ---------------------------------------------------------------------------


class CognitoIdpHandler(ResourceHandler):
    """Handle cognito-idp:: ARNs -- Cognito user pools.

    A Cognito user pool deleted by terraform destroy lingers in the Resource Groups
    Tagging API for a while (eventual consistency), so a tag match alone is NOT
    authoritative -- is_live() consults the DescribeUserPool API, mirroring the ECS
    Tagging-API-lag handling. Without this handler the post-test zero-orphan proof of
    every Cognito module false-fails right after its own terratest destroys the pool
    (the Tagging API still lists the just-deleted pool and the sweeper reports it as a
    leaked, unhandled resource).

    delete() handles a genuinely-leaked pool: it clears deletion protection, deletes the
    Hosted-UI prefix domain (a pool that still owns a domain cannot be deleted), then
    deletes the pool. ResourceNotFoundException at any step means the pool/domain is
    already gone and is treated as success (eventual consistency), mirroring WAFv2Handler.
    """

    service_prefix = "cognito-idp"

    @staticmethod
    def _pool_id_and_region(arn: str) -> tuple[str, str]:
        # ARN shape: arn:aws:cognito-idp:<region>:<account>:userpool/<pool-id>
        region = arn.split(":")[3]
        pool_id = arn.split(":userpool/", 1)[1]
        return pool_id, region

    def is_live(self, arn: str, boto3_mod: Any) -> bool:
        """Return True iff the user pool still genuinely exists (DescribeUserPool).

        A ResourceNotFoundException means the pool is gone (deleted by the module's own
        terraform destroy and still lingering in the Tagging API) -- a phantom, not an
        orphan. Any other ClientError propagates so a real failure is not masked.
        """
        pool_id, region = self._pool_id_and_region(arn)
        client = _make_client(boto3_mod, "cognito-idp", region_name=region)
        try:
            client.describe_user_pool(UserPoolId=pool_id)
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return False
            raise
        return True

    def delete(self, arn: str, boto3_mod: Any) -> str:
        pool_id, region = self._pool_id_and_region(arn)
        client = _make_client(boto3_mod, "cognito-idp", region_name=region)
        try:
            pool = client.describe_user_pool(UserPoolId=pool_id).get("UserPool", {})
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return "already-deleted"
            raise

        # A pool with deletion protection ACTIVE cannot be deleted; clear it first.
        if pool.get("DeletionProtection") == "ACTIVE":
            client.update_user_pool(UserPoolId=pool_id, DeletionProtection="INACTIVE")

        # A pool that still owns a Hosted-UI prefix domain cannot be deleted; remove it first.
        domain = pool.get("Domain")
        if domain:
            try:
                client.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id)
            except botocore.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code", "") != "ResourceNotFoundException":
                    raise

        try:
            client.delete_user_pool(UserPoolId=pool_id)
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return "already-deleted"
            raise
        return "deleted"


# ---------------------------------------------------------------------------
# Handler dispatch table
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, ResourceHandler] = {
    handler.service_prefix: handler()
    for handler in [
        S3Handler,
        KMSHandler,
        IAMHandler,
        CloudFrontHandler,
        ACMHandler,
        Route53Handler,
        EC2Handler,
        ECSHandler,
        ELBHandler,
        FirehoseHandler,
        GlueHandler,
        AthenaHandler,
        SNSHandler,
        SSMHandler,
        LambdaHandler,
        LogsHandler,
        CloudWatchHandler,
        QuickSightHandler,
        BudgetsHandler,
        CEHandler,
        WAFv2Handler,
        CognitoIdpHandler,
    ]
}


def _is_kms_key_in_pending_deletion(arn: str, boto3_mod: Any) -> bool:
    """Return True if the KMS key ARN is in a pending-deletion state.

    KMS keys whose KeyState is in KMS_PENDING_DELETION_STATES are excluded from
    the orphan-residue count because schedule_key_deletion() keeps them visible
    in the Tagging API for the mandatory 7-day minimum pending-deletion window.
    Only applies to key ARNs (:key/); alias ARNs (:alias/) return False.

    Args:
        arn: The KMS resource ARN from the Tagging API.
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True if the key state is in KMS_PENDING_DELETION_STATES, False otherwise.
    """
    if ":alias/" in arn:
        # Alias ARNs have no key state; they are always counted
        return False
    try:
        key_id = arn.split("/")[-1]
        kms_client = _make_client(boto3_mod, "kms")
        response = kms_client.describe_key(KeyId=key_id)
        key_state = response.get("KeyMetadata", {}).get("KeyState", "")
        return key_state in _constants.KMS_PENDING_DELETION_STATES
    except botocore.exceptions.ClientError as exc:
        # Cannot determine key state; conservatively include it in the residue count.
        print(
            f"WARNING: describe_key failed for {arn} -- key state unknown, "
            f"including in residue count. Error: {exc}",
            file=sys.stderr,
        )
        return False


def _is_nat_gateway_deleted(arn: str, boto3_mod: Any) -> bool:
    """Return True if the NAT gateway is genuinely gone and must be excluded from residue.

    AWS's Resource Groups Tagging API exhibits eventual consistency: a NAT gateway
    that was destroyed via terraform destroy remains visible in tag searches for
    several minutes. Two authoritative signals from the EC2 describe API prove
    the gateway is gone and must be excluded from the orphan-residue count:

    1. describe_nat_gateways returns a gateway in the 'deleted' or 'deleting' state.
    2. describe_nat_gateways raises NatGatewayNotFound -- the gateway no longer exists
       in EC2 at all; the Tagging-API result is a lag phantom.

    Any other ClientError is ambiguous; the gateway is conservatively counted as
    residue (fail-closed) to avoid masking real orphans.

    Args:
        arn: The EC2 NAT gateway resource ARN from the Tagging API.
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True if the NAT gateway is provably gone (excluded from residue), False otherwise.
    """
    resource_id = arn.split("/")[-1]
    try:
        client = _make_client(boto3_mod, "ec2")
        response = client.describe_nat_gateways(NatGatewayIds=[resource_id])
        for ngw in response.get("NatGateways", []):
            state = ngw.get("State", "")
            if state in ("deleted", "deleting"):
                return True
        return False
    except botocore.exceptions.ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "NatGatewayNotFound":
            # The authoritative EC2 describe API confirms the gateway does not exist.
            # The Tagging API is still showing it due to eventual-consistency lag.
            # This is a provably-gone phantom, not a real orphan -- exclude from residue.
            return True
        # For any other ClientError the state is unknown; fail-closed and count as residue.
        print(
            f"WARNING: describe_nat_gateways failed for {arn} -- state unknown, "
            f"including in residue count. Error: {exc}",
            file=sys.stderr,
        )
        return False


def _is_vpc_flow_log_deleted(arn: str, boto3_mod: Any) -> bool:
    """Return True if the VPC flow log no longer exists in EC2.

    AWS's Resource Groups Tagging API exhibits eventual consistency: a flow log
    that was destroyed via terraform destroy may remain visible in tag searches
    for several minutes. These are not leaked resources and must be excluded from
    the orphan-residue count.

    Args:
        arn: The EC2 vpc-flow-log resource ARN from the Tagging API.
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True if the flow log no longer exists (describe returns empty list).
    """
    resource_id = arn.split("/")[-1]
    try:
        client = _make_client(boto3_mod, "ec2")
        response = client.describe_flow_logs(FlowLogIds=[resource_id])
        return len(response.get("FlowLogs", [])) == 0
    except botocore.exceptions.ClientError as exc:
        # Cannot determine flow log existence; conservatively include in residue count.
        print(
            f"WARNING: describe_flow_logs failed for {arn} -- existence unknown, "
            f"including in residue count. Error: {exc}",
            file=sys.stderr,
        )
        return False


def _is_vpc_endpoint_deleted(arn: str, boto3_mod: Any) -> bool:
    """Return True if the VPC endpoint no longer exists in EC2.

    AWS's Resource Groups Tagging API exhibits eventual consistency: a VPC endpoint
    that was destroyed via terraform destroy or the sweep delete handler may remain
    visible in tag searches for an extended period (potentially hours). These are
    not leaked resources and must be excluded from the orphan-residue count.

    Two authoritative signals from the EC2 describe API prove the endpoint is gone:
    1. describe_vpc_endpoints raises InvalidVpcEndpointId.NotFound -- the endpoint
       no longer exists in EC2 at all; the Tagging-API result is a lag phantom.
    2. describe_vpc_endpoints returns the endpoint in 'deleted' or 'deleting' state.

    Any other ClientError is ambiguous; the endpoint is conservatively counted as
    residue (fail-closed) to avoid masking real orphans.

    Args:
        arn: The EC2 vpc-endpoint resource ARN from the Tagging API.
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True if the endpoint is provably gone (excluded from residue), False otherwise.
    """
    resource_id = arn.split("/")[-1]
    try:
        client = _make_client(boto3_mod, "ec2")
        response = client.describe_vpc_endpoints(VpcEndpointIds=[resource_id])
        for endpoint in response.get("VpcEndpoints", []):
            state = endpoint.get("State", "")
            if state in ("deleted", "deleting"):
                return True
        return len(response.get("VpcEndpoints", [])) == 0
    except botocore.exceptions.ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "InvalidVpcEndpointId.NotFound":
            # The authoritative EC2 describe API confirms the endpoint does not exist.
            # The Tagging API is still showing it due to eventual-consistency lag.
            # This is a provably-gone phantom, not a real orphan -- exclude from residue.
            return True
        # For any other ClientError the state is unknown; fail-closed and count as residue.
        print(
            f"WARNING: describe_vpc_endpoints failed for {arn} -- state unknown, "
            f"including in residue count. Error: {exc}",
            file=sys.stderr,
        )
        return False


def _is_security_group_deleted(arn: str, boto3_mod: Any) -> bool:
    """Return True if the security group no longer exists in EC2 and must be excluded from residue.

    AWS's Resource Groups Tagging API exhibits eventual consistency: a VPC's default
    security group is deleted atomically when the VPC is destroyed, but may remain
    visible in tag searches for a period after deletion. The authoritative signal is
    InvalidGroup.NotFound from describe_security_groups -- proof the SG is genuinely
    gone and not a real orphan.

    Any other ClientError is ambiguous; the security group is conservatively counted
    as residue (fail-closed) to avoid masking real orphan security groups.

    Args:
        arn: The EC2 security-group resource ARN from the Tagging API.
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True if the security group is provably gone (excluded from residue), False otherwise.
    """
    resource_id = arn.split("/")[-1]
    try:
        client = _make_client(boto3_mod, "ec2")
        client.describe_security_groups(GroupIds=[resource_id])
        return False
    except botocore.exceptions.ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "InvalidGroup.NotFound":
            # The authoritative EC2 describe API confirms the security group does not exist.
            # The Tagging API is still showing it due to eventual-consistency lag.
            # This is a provably-gone phantom, not a real orphan -- exclude from residue.
            return True
        # For any other ClientError the state is unknown; fail-closed and count as residue.
        print(
            f"WARNING: describe_security_groups failed for {arn} -- state unknown, "
            f"including in residue count. Error: {exc}",
            file=sys.stderr,
        )
        return False


def _service_prefix_from_arn(arn: str) -> str:
    """Extract the service prefix from a resource ARN.

    ARN shape: arn:partition:service:region:account-id:resource
    """
    parts = arn.split(":")
    if len(parts) < 6:
        return ""
    return parts[2]


def _error_code(exc: botocore.exceptions.ClientError) -> str:
    """Extract the AWS error code from a botocore ClientError."""
    return str(exc.response.get("Error", {}).get("Code", ""))


def _is_benign_not_found(exc: BaseException) -> bool:
    """Return True if the exception proves the target is already gone.

    A delete that hits an "already-gone" condition is idempotent SUCCESS, not a
    failure: the resource was already deleted (by this run's earlier pass, by
    Terraform destroy, or by a concurrent actor) and the Tagging API has not yet
    de-indexed it. Two matching rules:

    1. The error code is in the explicit _BENIGN_ALREADY_GONE_CODES allowlist.
    2. The generic rule: the code contains the substring "NotFound" (covers
       InvalidSubnetID.NotFound, ClusterNotFoundException, and any future
       "*NotFound*" / "*NotFoundException" code without enumerating it).

    Only botocore ClientErrors carry an error code; any other exception type
    returns False (it is a genuine failure, not an already-gone signal).

    Args:
        exc: The exception raised by a delete call.

    Returns:
        True if the exception means the resource is already deleted.
    """
    if not isinstance(exc, botocore.exceptions.ClientError):
        return False
    code = _error_code(exc)
    if code in _BENIGN_ALREADY_GONE_CODES:
        return True
    return "NotFound" in code


def _is_dependency_violation(exc: BaseException) -> bool:
    """Return True if the exception is a (transient) dependency violation.

    A dependency violation means the resource cannot be deleted YET because
    something else still depends on it (e.g. DeleteVpc while a subnet remains,
    DeleteSubnet while an ENI remains). During a multi-pass run-scoped sweep the
    dependent is deleted on an earlier/same pass, so the parent succeeds on a
    later pass. These are deferred and retried; they only surface as a real
    failure if they persist into the FINAL pass.

    Args:
        exc: The exception raised by a delete call.

    Returns:
        True if the exception is a deferrable dependency violation.
    """
    if not isinstance(exc, botocore.exceptions.ClientError):
        return False
    return _error_code(exc) in _DEPENDENCY_VIOLATION_CODES


class _DefaultSecurityGroupSkipError(Exception):
    """Raised by EC2Handler when asked to delete a VPC's default security group.

    A VPC's default security group cannot be deleted independently (AWS rejects
    DeleteSecurityGroup for the default SG with "cannot be deleted by a user");
    it is removed automatically when its VPC is deleted. The sweep must SKIP it
    rather than fail. The run-scoped delete loop catches this and records the
    resource as "skipped-default-sg" (a success outcome, not residue).
    """


def _is_default_security_group(resource_id: str, boto3_mod: Any) -> bool:
    """Return True if the EC2 security group id is a VPC's default security group.

    A default security group has GroupName == "default" and cannot be deleted on
    its own; it is destroyed implicitly with its VPC. Detected authoritatively via
    describe_security_groups. If the group no longer exists (InvalidGroup.NotFound)
    it is not a default-SG concern (the caller's delete will itself be a benign
    not-found); any other describe error is treated as "not default" so the delete
    proceeds and any genuine problem surfaces there rather than being masked here.

    Args:
        resource_id: The security group id (sg-...).
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True iff the group exists and its GroupName is "default".
    """
    try:
        client = _make_client(boto3_mod, "ec2")
        response = client.describe_security_groups(GroupIds=[resource_id])
        for group in response.get("SecurityGroups", []):
            if group.get("GroupName", "") == "default":
                return True
        return False
    except botocore.exceptions.ClientError:
        return False


def _handler_reports_live(arn: str, boto3_mod: Any) -> bool:
    """Return whether the per-service handler reports the resource as live.

    Dispatches to the matched ResourceHandler.is_live() for check-mode residue
    filtering. A resource whose handler reports it as NOT live (e.g. an INACTIVE
    or DELETE_IN_PROGRESS ECS shell that the Tagging API still returns) is a lag
    phantom, not an orphan, and must be excluded from the residue count.

    When no handler is registered for the service prefix, the resource is treated
    as live (counted): a tagged resource with no handler is a genuine, unhandled
    orphan and must surface (the delete-mode fail-safe relies on the same).

    Args:
        arn: The full resource ARN from the Tagging API.
        boto3_mod: The boto3 module (injected for testability).

    Returns:
        True if the resource is live and must be counted as residue; False if the
        handler proves it is non-live and it must be excluded.
    """
    handler = _HANDLERS.get(_service_prefix_from_arn(arn))
    if handler is None:
        return True
    return handler.is_live(arn, boto3_mod)


def _dispatch_delete(
    resource: dict[str, Any],
    boto3_mod: Any,
    is_final_pass: bool,
    pass_index: int,
) -> tuple[dict[str, Any], bool]:
    """Dispatch one resource to its handler and classify the outcome.

    Single source of truth for the per-resource delete classification used by the
    multi-pass delete loop (DRY). Classifies the result into one of:

      * reported-unsupported -- no handler registered (terminal, fail-safe).
      * <handler action>     -- handler returned an action (deleted/scheduled/...).
      * skipped-default-sg   -- a VPC default security group (cannot be deleted
                                independently; removed with its VPC). Success.
      * already-deleted      -- a benign NotFound/already-inactive error proves the
                                target is already gone (idempotent success).
      * failed               -- a genuine error, OR a dependency violation that
                                persists into the final pass.

    A DependencyViolation on a NON-final pass is DEFERRED (returned with
    defer=True and a placeholder entry the caller discards) so the resource is
    retried on the next pass after its dependents are removed.

    Args:
        resource: The Tagging-API resource mapping (has "ResourceARN").
        boto3_mod: The boto3 module (injected for testability).
        is_final_pass: True when a dependency violation must surface as a failure
            rather than being deferred for another retry.
        pass_index: The current 1-based pass number (for diagnostics only).

    Returns:
        Tuple of (entry, defer). When defer is True the entry is a placeholder
        the caller must NOT record (the resource is retried next pass); otherwise
        entry is the terminal found-entry dict to record.
    """
    arn = resource.get("ResourceARN", "")
    service = _service_prefix_from_arn(arn)
    handler = _HANDLERS.get(service)

    if handler is None:
        print(
            f"WARNING: No handler for service {service!r} (ARN: {arn}). "
            "Resource left untouched (fail-safe invariant).",
            file=sys.stderr,
        )
        return {"arn": arn, "type": service, "action": "reported-unsupported"}, False

    try:
        action = handler.delete(arn, boto3_mod)
        print(f"  {action}: {arn}")
        return {"arn": arn, "type": service, "action": action}, False
    except _DefaultSecurityGroupSkipError:
        # A VPC's default SG cannot be deleted independently; it goes away with
        # the VPC. This is a success outcome, not residue.
        print(f"  skipped-default-sg: {arn}")
        return {"arn": arn, "type": service, "action": "skipped-default-sg"}, False
    except botocore.exceptions.ClientError as exc:
        if _is_benign_not_found(exc):
            # Already gone -- idempotent success, not a failure.
            print(f"  already-deleted ({_error_code(exc)}): {arn}")
            return {"arn": arn, "type": service, "action": "already-deleted"}, False
        if _is_dependency_violation(exc) and not is_final_pass:
            # Transient: a dependent is still being removed. Retry next pass.
            print(
                f"  deferred ({_error_code(exc)}, pass {pass_index}): {arn}",
                file=sys.stderr,
            )
            return {"arn": arn, "type": service, "action": "deferred"}, True
        # Genuine failure (or a dependency violation that never cleared by the
        # final pass) -- surface it.
        print(f"ERROR: Failed to delete {arn}: {exc}", file=sys.stderr)
        return {"arn": arn, "type": service, "action": "failed"}, False
    except Exception as exc:
        print(f"ERROR: Failed to delete {arn}: {exc}", file=sys.stderr)
        return {"arn": arn, "type": service, "action": "failed"}, False


def _delete_pass(
    resources: list[dict[str, Any]],
    boto3_mod: Any,
    is_final_pass: bool,
    pass_index: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run ONE full reverse-dependency-tier delete over ``resources``.

    This is a single pass of the loop-until-stable delete model. It deletes in
    strict reverse-dependency-tier order -- all non-ec2 resources first, then the
    ec2/VPC family tier 1 (natgateway, EIP, flow-log) -> tier 2 (subnet,
    route-table, security-group, vpc-endpoint, IGW) -> tier 3 (VPC) -- so a
    within-pass dependent is always attempted before the resource that depends on
    it. Each resource is dispatched to its handler via ``_dispatch_delete`` exactly
    once per pass.

    A resource that hits a transient DependencyViolation is classified as PENDING
    (returned in the ``pending`` list, NOT recorded as a terminal entry) unless
    ``is_final_pass`` is True, in which case a still-unclearable DependencyViolation
    is recorded as a ``failed`` terminal entry (fail-safe: a genuine leak is never
    masked). The OUTER loop in ``main`` re-enumerates the tag-scoped inventory and
    calls this again for each pending resource, so an eventually-consistent
    deletion (a VPC whose ENIs/subnets/IGW detach slowly) is picked up once the
    fresh describe reflects its drained dependents.

    Args:
        resources: Tagging-API resource mappings to delete this pass (already
            enumerated + min-age-spared by the caller).
        boto3_mod: The boto3 module (injected for testability).
        is_final_pass: True when a DependencyViolation must surface as a failure
            rather than being deferred for another re-enumerated pass.
        pass_index: The current 1-based outer pass number (diagnostics only).

    Returns:
        Tuple of (entries, pending):
          entries -- terminal per-resource result dicts (deleted / already-deleted
            / scheduled / skipped-default-sg / reported-unsupported / failed).
          pending -- resources that hit a transient DependencyViolation and were
            NOT finalized (empty when ``is_final_pass`` is True); the outer loop
            re-enumerates and retries them.
    """
    non_ec2_resources, ec2_tier1, ec2_tier2, ec2_tier3 = _partition_ec2_by_tier(resources)
    ordered = non_ec2_resources + ec2_tier1 + ec2_tier2 + ec2_tier3

    entries: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for resource in ordered:
        entry, defer = _dispatch_delete(resource, boto3_mod, is_final_pass, pass_index)
        if defer:
            pending.append(resource)
        else:
            entries.append(entry)
    return entries, pending


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def _write_report(
    report_path: str,
    account_id: str,
    mode: str,
    run_id: str | None,
    found: list[dict[str, Any]],
    remaining: int,
) -> None:
    """Write the JSON report per spec section 5.4.

    Args:
        report_path: File path to write the report to.
        account_id: The verified caller AWS account id.
        mode: "check" or "delete".
        run_id: Optional run id used in the filter.
        found: List of resource entries with arn, type, and action.
        remaining: Number of resources still present after sweep.
    """
    report = {
        "run_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "account_id": account_id,
        "mode": mode,
        "filter": {
            _PROJECT_TAG_KEY: _PROJECT_TAG_VALUE,
            _RUN_ID_TAG_KEY: run_id if run_id is not None else "*",
        },
        "found": found,
        "remaining": remaining,
    }
    out_path = pathlib.Path(report_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))


def _default_report_path() -> str:
    """Return a timestamped default report path under .sweep-reports/."""
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    return str(pathlib.Path(_DEFAULT_SWEEP_REPORTS_DIR) / f"sweep-{ts}.json")


# ---------------------------------------------------------------------------
# Main entry point (importable for tests)
# ---------------------------------------------------------------------------


def main(
    mode: str,
    accounts_json: str,
    report_path: str | None,
    run_id: str | None,
    boto3_mod: Any = None,
) -> None:
    """Run the orphan sweep.

    Args:
        mode: "check" or "delete".
        accounts_json: Path to terragrunt/common/accounts.json.
        report_path: Path to write the JSON report; if None, uses the default.
        run_id: Optional run id to narrow the Tagging API filter.
        boto3_mod: The boto3 module; injected to enable unit testing.

    Raises:
        SystemExit: Always; exit 0 on success (check mode with zero resources),
            non-zero on any error or when resources are found.
    """
    if boto3_mod is None:
        import boto3

        boto3_mod = boto3

    if report_path is None:
        report_path = _default_report_path()

    # ---------------------------------------------------------------------------
    # Step 1: Verify caller account against allowlist BEFORE any enumeration.
    # ---------------------------------------------------------------------------
    allowlist = _load_allowlist(accounts_json)
    sts = _make_client(boto3_mod, "sts")
    identity = sts.get_caller_identity()
    caller_account = identity["Account"]

    if caller_account not in allowlist:
        allowlist_display = ", ".join(sorted(allowlist)) if allowlist else "(empty)"
        print(
            f"ERROR: account {caller_account} is not in the sweep allowlist "
            f"({allowlist_display}). Refusing to sweep.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # Step 2: Enumerate resources via strict two-tag filter, then apply the
    # GLOBAL-sweep min-age guard.
    #
    # When this is the cross-run global sweep (no run id), spare resources whose
    # terratest-run tag encodes a UTC timestamp younger than the min-age floor:
    # they belong to a concurrently-running PR matrix job, and deleting them
    # mid-apply is the documented daily-sweep-vs-PR race. The run-scoped path
    # (run_id set) is exempt -- it deletes only its own just-finished run.
    #
    # Delete mode RE-ENUMERATES + re-spares fresh on every loop-until-stable pass
    # (see Step 4); this initial enumeration is the check-mode inventory and the
    # delete loop's first pass.
    poll_timeout = _get_poll_timeout()
    poll_interval = _get_poll_interval()
    now_utc = datetime.datetime.now(datetime.UTC)
    resources = _enumerate_and_spare(boto3_mod, run_id, poll_timeout, poll_interval, now_utc)

    # ---------------------------------------------------------------------------
    # Step 3: Check mode -- print inventory and exit.
    #
    # KMS keys in PendingDeletion / PendingReplicaDeletion are excluded from the
    # residue count (AC-5): schedule_key_deletion() leaves them visible in the
    # Tagging API for the mandatory 7-day window -- they are not leaked resources.
    #
    # NAT gateways in the 'deleted' or 'deleting' state are excluded for the same
    # reason: the Tagging API exhibits eventual consistency and keeps them visible
    # for several minutes after Terraform destroy completes.
    # ---------------------------------------------------------------------------
    if mode == "check":
        # Filter out resources that are in terminal-but-still-visible states.
        #
        # The Resource Groups Tagging API exhibits eventual consistency and keeps
        # tags on resources that are already deleted / in a terminal non-live
        # state. A tagged resource is residue (a real orphan) ONLY if it is
        # actually live. Two exclusion mechanisms apply:
        #
        # 1. Service-specific eventual-consistency probes (KMS pending-deletion,
        #    EC2 NAT-gateway / flow-log / VPC-endpoint terminal states) that long
        #    predate the generic liveness hook.
        # 2. The per-handler ResourceHandler.is_live() check: a handler whose
        #    is_live() returns False (e.g. an INACTIVE / DELETE_IN_PROGRESS ECS
        #    cluster, service, or task-definition) is provably non-live and is
        #    excluded. The base handler returns True, so services without an
        #    override are unaffected.
        residue_resources = [
            r
            for r in resources
            if not (
                _service_prefix_from_arn(r.get("ResourceARN", "")) == "kms"
                and _is_kms_key_in_pending_deletion(r.get("ResourceARN", ""), boto3_mod)
            )
            and not (
                _service_prefix_from_arn(r.get("ResourceARN", "")) == "ec2"
                and _ec2_resource_type(r.get("ResourceARN", "")) == "natgateway"
                and _is_nat_gateway_deleted(r.get("ResourceARN", ""), boto3_mod)
            )
            and not (
                _service_prefix_from_arn(r.get("ResourceARN", "")) == "ec2"
                and _ec2_resource_type(r.get("ResourceARN", "")) == "vpc-flow-log"
                and _is_vpc_flow_log_deleted(r.get("ResourceARN", ""), boto3_mod)
            )
            and not (
                _service_prefix_from_arn(r.get("ResourceARN", "")) == "ec2"
                and _ec2_resource_type(r.get("ResourceARN", "")) == "vpc-endpoint"
                and _is_vpc_endpoint_deleted(r.get("ResourceARN", ""), boto3_mod)
            )
            and not (
                _service_prefix_from_arn(r.get("ResourceARN", "")) == "ec2"
                and _ec2_resource_type(r.get("ResourceARN", "")) == "security-group"
                and _is_security_group_deleted(r.get("ResourceARN", ""), boto3_mod)
            )
            and _handler_reports_live(r.get("ResourceARN", ""), boto3_mod)
        ]
        # Cost Explorer anomaly monitors/subscriptions are absent from the Tagging
        # API, so they are enumerated directly (global path only) and reported as
        # residue alongside the tagged resources.
        ce_entries = (
            _sweep_ce_anomaly_monitors(boto3_mod, "check", _get_min_age_minutes(), now_utc)
            if run_id is None
            else []
        )
        tagged_entries = [
            {
                "arn": r.get("ResourceARN", ""),
                "type": _service_prefix_from_arn(r.get("ResourceARN", "")),
                "action": "found",
            }
            for r in residue_resources
        ]
        all_found = tagged_entries + ce_entries
        count = len(all_found)
        if count == 0:
            print(f"0 resources tagged {_PROJECT_TAG_KEY}={_PROJECT_TAG_VALUE} + {_RUN_ID_TAG_KEY}")
            _write_report(report_path, caller_account, mode, run_id, [], 0)
            sys.exit(0)
        else:
            for entry in all_found:
                print(entry.get("arn", "<unknown>"))
            _write_report(report_path, caller_account, mode, run_id, all_found, count)
            print(
                f"ERROR: {count} resource(s) tagged "
                f"{_PROJECT_TAG_KEY}={_PROJECT_TAG_VALUE} + {_RUN_ID_TAG_KEY} "
                "(plus untaggable CE anomaly monitors) found. "
                "Run with SWEEP_MODE=delete to clean up.",
                file=sys.stderr,
            )
            sys.exit(count)

    # ---------------------------------------------------------------------------
    # Step 4: Delete mode -- loop-until-stable, re-enumerating every pass.
    #
    # Each pass re-enumerates the tag-scoped inventory (fresh Tagging API describe,
    # + min-age spare) and runs one full reverse-dependency-tier delete over it via
    # _delete_pass: non-ec2 resources first, then the ec2/VPC family tier 1
    # (natgateway, EIP, flow-log) -> tier 2 (subnet, route-table, SG, vpc-endpoint,
    # IGW) -> tier 3 (VPC). Per-resource outcomes:
    #
    #   * No handler           -> reported-unsupported (terminal; fail-safe).
    #   * skipped-default-sg   -> a VPC's default security group cannot be deleted
    #                             on its own; SKIP it (success, removed with VPC).
    #   * already-gone         -> a NotFound/already-inactive error proves the
    #                             target is already deleted (idempotent success).
    #   * deleted / scheduled  -> the delete (or KMS schedule) succeeded.
    #   * DependencyViolation  -> transient: the resource still has a dependent that
    #                             has not finished detaching (a VPC waiting on its
    #                             ENIs/subnets/IGW). Left PENDING and retried on the
    #                             NEXT pass, so re-enumeration picks up the
    #                             eventually-consistent deletion. It surfaces as a
    #                             real failure ONLY if it persists into the FINAL
    #                             pass (fail-safe: a genuine leak is never masked).
    #   * any other exception  -> failed (terminal).
    #
    # The loop stops as soon as a pass leaves zero pending DependencyViolations
    # (STABLE: every resource is deleted / already-gone / spared / unsupported /
    # failed, so re-enumerating cannot delete anything more) or the account is fully
    # drained. The ceiling is TT_SWEEP_MAX_DELETE_PASSES; the final pass finalizes
    # any still-stuck DependencyViolation as a failure. Looping the delete here
    # (instead of relying on a single pass) drains multi-pass VPC-dependency
    # deletions BEFORE the fail-on-residue check step runs.
    # ---------------------------------------------------------------------------
    max_passes = _get_max_delete_passes()
    found_entries: list[dict[str, Any]] = []
    # Pass 1 reuses the inventory already enumerated + min-age-spared above; every
    # subsequent pass re-enumerates fresh so eventually-consistent deletions are
    # picked up.
    pass_resources = resources

    for pass_index in range(1, max_passes + 1):
        if pass_index > 1:
            pass_resources = _enumerate_and_spare(
                boto3_mod,
                run_id,
                poll_timeout,
                poll_interval,
                datetime.datetime.now(datetime.UTC),
            )
        if not pass_resources:
            # Nothing tagged remains -> the account is fully drained (stable).
            found_entries = []
            break

        is_final_pass = pass_index == max_passes
        entries, pending = _delete_pass(pass_resources, boto3_mod, is_final_pass, pass_index)
        # The report reflects the final reconciled pass; entries from superseded
        # passes (a resource that was "deleted" earlier and is gone from the fresh
        # enumeration) are intentionally not carried forward.
        found_entries = entries

        if is_final_pass or not pending:
            # Final pass surfaces any still-stuck DependencyViolation as a failure;
            # a pass that leaves zero pending DependencyViolations is stable.
            break
        # Transient DependencyViolations remain -> loop and re-enumerate so the
        # fresh describe reflects dependents that have since drained.

    # Cost Explorer anomaly monitors/subscriptions are untaggable (absent from
    # the Tagging API), so they are swept by direct CE-API name-pattern match in
    # the global path only. A run-scoped name-pattern delete would race other
    # concurrent runs' identically-patterned monitors, so it is skipped when a
    # run id is set.
    if run_id is None:
        found_entries.extend(
            _sweep_ce_anomaly_monitors(boto3_mod, "delete", _get_min_age_minutes(), now_utc)
        )

    has_unsupported = any(e.get("action") == "reported-unsupported" for e in found_entries)
    has_failure = any(e.get("action") == "failed" for e in found_entries)

    remaining = sum(
        1 for e in found_entries if e.get("action") in ("reported-unsupported", "failed")
    )

    _write_report(report_path, caller_account, mode, run_id, found_entries, remaining)

    if has_unsupported or has_failure:
        sys.exit(1)
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _read_run_id_from_file(run_id_file: str) -> str:
    """Read a run id from a file written by the terratest runner.

    Used by the parallel-safe per-module zero-orphan proof: each module's
    tf-test run writes its run id to a deterministic per-module file, and the
    sweep reads it back to scope the check to that one run (so concurrent
    module checks never cross-contaminate). Fails fast when the file is absent
    or empty.

    Args:
        run_id_file: Path to the per-module run-id file.

    Returns:
        The run id read from the file (whitespace-stripped).

    Raises:
        SystemExit: If the file does not exist, cannot be read, or is empty.
    """
    path = pathlib.Path(run_id_file)
    if not path.is_file():
        print(
            f"ERROR: run-id file {run_id_file} not found; run the terratest "
            "runner (make tf-test MODULE_PATH=...) for this module first to "
            "produce it.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        run_id = path.read_text().strip()
    except OSError as exc:
        print(
            f"ERROR: failed to read run-id file {run_id_file}: {exc}.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not run_id:
        print(
            f"ERROR: run-id file {run_id_file} is empty; expected a run id of "
            "the form tt-<utc>-<rand>.",
            file=sys.stderr,
        )
        sys.exit(1)

    return run_id


def _cli_main() -> None:
    """Parse CLI arguments and invoke main()."""
    parser = argparse.ArgumentParser(description="Tag-scoped orphan sweep for terratest resources.")
    parser.add_argument(
        "--mode",
        choices=["check", "delete"],
        default=os.environ.get("SWEEP_MODE", "check"),
        help="check: inventory only; delete: destroy all matched resources.",
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("SWEEP_RUN_ID"),
        help="Narrow filter to one specific terratest run id.",
    )
    parser.add_argument(
        "--run-id-file",
        default=None,
        help=(
            "Path to a per-module run-id file written by the terratest runner. "
            "When set, the run id is read from this file (fail-fast if missing "
            "or empty); enables the parallel-safe per-module zero-orphan proof. "
            "Mutually exclusive with --run-id."
        ),
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path for the JSON report (default: .sweep-reports/<timestamp>.json).",
    )
    parser.add_argument(
        "--accounts-json",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "terragrunt",
            "common",
            "accounts.json",
        ),
        help="Path to terragrunt/common/accounts.json.",
    )
    args = parser.parse_args()

    if args.run_id_file is not None and args.run_id is not None:
        print(
            "ERROR: --run-id and --run-id-file are mutually exclusive; pass only one.",
            file=sys.stderr,
        )
        sys.exit(1)

    run_id = args.run_id
    if args.run_id_file is not None:
        run_id = _read_run_id_from_file(args.run_id_file)

    main(
        mode=args.mode,
        accounts_json=args.accounts_json,
        report_path=args.report,
        run_id=run_id,
        boto3_mod=None,
    )


if __name__ == "__main__":
    _cli_main()
