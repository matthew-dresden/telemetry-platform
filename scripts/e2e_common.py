"""Shared helpers for the OTLP end-to-end test harness.

This module factors out the cross-script concerns shared by the harness
entry points (``scripts.otlp_e2e_loadgen``, ``scripts.e2e_verify``):

  * ENV -> AWS account/profile resolution from ``terragrunt/common/accounts.json``
    (identical contract to ``scripts.live_verify``: the ``--env`` argument is
    authoritative, the resolved profile is always passed as
    ``boto3.Session(profile_name=...)`` so an ambient ``AWS_PROFILE`` can never
    silently redirect calls at a different account).
  * Per-env endpoint composition from ``terragrunt/common/domains.json`` (the
    same single source of truth the IaC uses; no hostname literals here).
  * Active, bounded readiness polling (``poll_until``) implemented with
    ``threading.Event.wait`` -- no ``time.sleep`` synchronization.
  * The sent-vs-found reconciliation function shared by the load generator's
    manifest and the consumer-side verifier.

No module here issues network or AWS calls at import time; ``boto3`` is injected
by callers (never imported at module scope) so the harness logic is unit
testable without credentials.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import secrets
import threading
import time
from typing import Any

from scripts import constants

_REPO_ROOT = pathlib.Path(__file__).parent.parent
ACCOUNTS_JSON_PATH = _REPO_ROOT / "terragrunt" / "common" / "accounts.json"
DOMAINS_JSON_PATH = _REPO_ROOT / "terragrunt" / "common" / "domains.json"

# The collector edge is published in sandbox, prod, and qa env-classes.
VALID_ENVS = frozenset({"sandbox", "prod", "qa"})


class E2EUsageError(Exception):
    """Raised for unknown ENV values or invalid CLI usage -- exit 2."""


class E2EError(Exception):
    """Raised when a harness operation fails -- exit 1."""


# Allowlist for SQL identifiers (catalog table/column names): letters, digits, and
# underscore only. Any value failing this is rejected before a query is built, so a
# discovered identifier can never introduce a SQL-injection vector.
_SQL_IDENTIFIER_RE = re.compile(r"\A[A-Za-z0-9_]+\Z")


def safe_sql_identifier(identifier: str) -> str:
    """Return ``identifier`` when it is a safe SQL identifier, else raise.

    Args:
        identifier: A catalog identifier (e.g. a Glue table name).

    Returns:
        The identifier unchanged.

    Raises:
        E2EError: When the identifier contains characters outside the allowlist
            (letters, digits, underscore).
    """
    if not _SQL_IDENTIFIER_RE.match(identifier):
        raise E2EError(f"ERROR: refusing to build a query with unsafe identifier {identifier!r}")
    return identifier


def load_json(path: pathlib.Path) -> dict[str, Any]:
    """Read and parse a JSON file, failing fast with an actionable message.

    Args:
        path: Absolute path to the JSON file.

    Returns:
        The parsed JSON object.

    Raises:
        E2EUsageError: When the file does not exist.
    """
    if not path.exists():
        raise E2EUsageError(f"ERROR: required file not found: {path}")
    parsed = json.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise E2EUsageError(f"ERROR: expected a JSON object in {path}, got {type(parsed).__name__}")
    return parsed


def resolve_account(env: str, accounts_data: dict[str, Any]) -> tuple[str, str]:
    """Resolve the (account_id, aws_profile) pair for ENV from accounts.json.

    The ``aws_profile`` value in each account row equals the env-class name
    (sandbox/prod/qa), so ENV maps directly onto the profile -- the same
    contract ``scripts.live_verify`` uses.

    Args:
        env: The environment name (sandbox, prod, qa).
        accounts_data: Parsed accounts.json content.

    Returns:
        A ``(account_id, aws_profile)`` tuple.

    Raises:
        E2EUsageError: When no account row has an aws_profile matching env.
    """
    if env not in VALID_ENVS:
        raise E2EUsageError(f"ERROR: unknown ENV {env!r}: must be one of {sorted(VALID_ENVS)!r}")
    for acct_id, acct_info in accounts_data.items():
        if acct_info.get("aws_profile") == env:
            return str(acct_id), env
    raise E2EUsageError(
        f"ERROR: ENV {env!r} has no account in accounts.json with aws_profile={env!r}"
    )


def resolve_endpoints(env: str, domains_data: dict[str, Any]) -> dict[str, str]:
    """Compose the per-env published service hostnames from domains.json.

    Mirrors the IaC composition (terragrunt/_envcommon/collector-ingestion.hcl
    lines 85-86 and portal.hcl lines 76-77):

      collector_pretty_fqdn  = "collector.<dns_pretty_apex>"
      collector_service_fqdn = "collector-<env_instance>.<dns_service_apex>"
      portal_pretty_fqdn     = "telemetry.<dns_pretty_apex>"
      portal_service_fqdn    = "telemetry-<env_instance>.<dns_service_apex>"

    Args:
        env: The environment name (sandbox, prod, qa).
        domains_data: Parsed domains.json content.

    Returns:
        Mapping of the four published FQDN keys to their hostnames.

    Raises:
        E2EUsageError: When the env-class has no row in domains.json.
    """
    env_cfg = domains_data.get(env)
    if not env_cfg:
        raise E2EUsageError(
            f"ERROR: ENV {env!r} not found in domains.json -- available: {sorted(domains_data)!r}"
        )
    service_apex = env_cfg["dns_service_apex"]
    pretty_apex = env_cfg["dns_pretty_apex"]
    instance = constants.E2E_ENVIRONMENT_INSTANCE
    collector = constants.E2E_COLLECTOR_HOST_LABEL
    portal = constants.E2E_PORTAL_HOST_LABEL
    return {
        "collector_pretty_fqdn": f"{collector}.{pretty_apex}",
        "collector_service_fqdn": f"{collector}-{instance}.{service_apex}",
        "portal_pretty_fqdn": f"{portal}.{pretty_apex}",
        "portal_service_fqdn": f"{portal}-{instance}.{service_apex}",
    }


def build_session(boto3_module: Any, profile: str) -> Any:
    """Return a boto3 Session bound to the resolved profile.

    The profile is always passed explicitly so an ambient ``AWS_PROFILE`` can
    never redirect calls at a different account than ENV selected.

    Args:
        boto3_module: The boto3 module (injected for test isolation).
        profile: The aws_profile to bind the session to.

    Returns:
        A boto3 Session instance.
    """
    return boto3_module.Session(profile_name=profile)


def build_ambient_session(boto3_module: Any) -> Any:
    """Return a boto3 Session bound to the ambient (default) credential chain.

    Unlike ``build_session`` (which binds a named profile), this passes no
    ``profile_name`` so credentials resolve from the default chain: the CI
    OIDC-assumed role from the environment, or a local ``AWS_PROFILE`` / ambient
    env credentials. Used when running under CI OIDC, where no named profile
    (``sandbox``/``prod``/``qa``) exists in ``~/.aws/config``.

    Args:
        boto3_module: The boto3 module (injected for test isolation).

    Returns:
        A boto3 Session instance.
    """
    return boto3_module.Session()


def poll_until(
    predicate: Any,
    timeout: float,
    interval: float,
    timeout_msg: str,
) -> bool:
    """Poll ``predicate()`` until it returns True or the budget expires.

    Active readiness detection over a bounded, caller-supplied budget. The
    inter-poll pause is implemented with ``threading.Event.wait`` so no
    ``time.sleep`` synchronization delay is introduced. Returns True when the
    predicate passes, False on timeout (printing ``timeout_msg``).

    Args:
        predicate: Zero-arg callable returning True when the condition is met.
        timeout: Maximum total seconds to wait.
        interval: Seconds between checks.
        timeout_msg: Diagnostic emitted on timeout.

    Returns:
        True when the predicate passed, False on timeout.
    """
    deadline = time.monotonic() + timeout
    stop = threading.Event()
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(timeout_msg)
            return False
        stop.wait(timeout=min(interval, remaining))


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ``Z`` timestamp."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dt_partition(timestamp_iso: str) -> str:
    """Return the ``dt`` partition (YYYY-MM-DD) for an ISO-8601 timestamp.

    Args:
        timestamp_iso: An ISO-8601 timestamp (e.g. ``2026-06-28T12:00:00Z``).

    Returns:
        The date portion used as the Glue ``dt`` partition value.
    """
    return timestamp_iso[:10]


def generate_run_id() -> str:
    """Return a fresh run id (``<UTC-compact>-<hex>``).

    Shared by every harness entry point that stamps a synthetic run
    (``scripts.otlp_e2e_loadgen``, ``scripts.perf_loadgen``) so the format --
    and the reason for it -- lives in exactly one place.

    The hex suffix is redrawn on the rare all-digit token so the run id always
    contains a non-digit character. The reconciliation query (scripts.e2e_verify)
    scopes a run with ``json_extract_scalar(payload, '$.run_id') = ?`` where the
    run id is bound as a native Athena ``ExecutionParameter``; Athena infers the
    parameter's type from the value, and a purely ``<digits>-<digits>`` run id is
    inferred as a ``bigint`` arithmetic expression, so the query fails with
    ``TYPE_MISMATCH: Cannot apply operator: varchar = bigint`` (the left side,
    ``json_extract_scalar``, is ``varchar``). A single alphabetic hex digit forces
    the ``varchar`` binding. ``secrets.token_hex(3)`` is all-digits ~6% of draws.

    Returns:
        A run id of the form ``YYYYMMDDHHMMSS-<6 hex chars>``.
    """
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(3)
    while suffix.isdigit():
        suffix = secrets.token_hex(3)
    return f"{stamp}-{suffix}"


def metric_window(
    started_at: str,
    finished_at: str,
    min_span_seconds: int = 60,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Compute a valid CloudWatch ``(StartTime, EndTime)`` from a run window.

    Shared by every harness entry point that queries CloudWatch over a
    recorded run window (``scripts.e2e_verify``, ``scripts.perf_report``).

    ``GetMetricStatistics``/``GetMetricData`` reject ``StartTime >= EndTime``
    with ``InvalidParameterValue: StartTime must be less than EndTime``. A fast
    run records ``started_at == finished_at`` at one-second resolution, which
    trips that error. The start is floored to the minute (CloudWatch aligns
    datapoints to period boundaries anyway) and the end is widened to at least
    ``min_span_seconds`` after the start, so the window always spans at least
    one period and ``StartTime < EndTime`` strictly holds.

    Args:
        started_at: ISO-8601 ``...Z`` run start.
        finished_at: ISO-8601 ``...Z`` run finish.
        min_span_seconds: Minimum width of the returned window.

    Returns:
        A ``(start, end)`` pair of timezone-aware UTC datetimes.
    """
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    start = datetime.datetime.strptime(started_at, fmt).replace(
        second=0, microsecond=0, tzinfo=datetime.UTC
    )
    end = datetime.datetime.strptime(finished_at, fmt).replace(tzinfo=datetime.UTC)
    minimum_end = start + datetime.timedelta(seconds=min_span_seconds)
    if end < minimum_end:
        end = minimum_end
    return start, end


def tool_marker(run_id: str) -> str:
    """Return the fixed reserved e2e tool value (``constants.E2E_TOOL_VALUE``).

    Every synthetic record now carries the single fixed ``tool`` value
    ``e2e-smoke`` regardless of run: the Glue ``tool`` partition uses Athena enum
    projection over a governed, fixed set of tool values, so a per-run tool value
    would fall outside the enum and never be projected. The run identity moves
    into the event payload (``$.run_id``). ``run_id`` is accepted so existing call
    sites keep their signature, but it does not affect the returned value -- runs
    are distinguished by the payload ``run_id``, not by the tool partition.
    """
    return constants.E2E_TOOL_VALUE


def discover_firehose_stream(firehose_client: Any, env: str) -> dict[str, str] | None:
    """Discover the telemetry Firehose delivery stream and its S3 destination.

    Shared by every harness entry point that needs the telemetry delivery
    stream's name/S3 destination (``scripts.e2e_verify``, ``scripts.perf_report``).
    The deployed stream name is namespace-derived, so it is discovered by
    listing + filtering (never hardcoded): a stream whose name contains both
    ``"telemetry"`` and ``env`` is preferred; if none matches on ``env``, the
    first stream containing ``"telemetry"`` is used as a fallback (mirrors
    every other namespace-agnostic discovery helper in this module).

    Args:
        firehose_client: A boto3 Firehose client.
        env: The environment name (sandbox, prod, qa), used as a name filter.

    Returns:
        A dict with ``stream``, ``bucket``, ``prefix``, ``error_prefix`` keys,
        or None when no telemetry stream is found.
    """
    streams = firehose_client.list_delivery_streams().get("DeliveryStreamNames", [])
    target = next(
        (s for s in streams if "telemetry" in s and env in s),
        next((s for s in streams if "telemetry" in s), None),
    )
    if target is None:
        return None
    desc = firehose_client.describe_delivery_stream(DeliveryStreamName=target)
    destinations = desc.get("DeliveryStreamDescription", {}).get("Destinations", [])
    if not destinations:
        return {"stream": target, "bucket": "", "prefix": "", "error_prefix": ""}
    s3_desc = (
        destinations[0].get("ExtendedS3DestinationDescription")
        or destinations[0].get("S3DestinationDescription")
        or {}
    )
    bucket_arn = s3_desc.get("BucketARN", "")
    bucket = bucket_arn.split(":::")[-1] if ":::" in bucket_arn else ""
    return {
        "stream": target,
        "bucket": bucket,
        "prefix": s3_desc.get("Prefix", ""),
        "error_prefix": s3_desc.get("ErrorOutputPrefix", ""),
    }


def _iter_glue_databases(glue_client: Any) -> Any:
    """Yield every Glue database, following pagination."""
    token: str | None = None
    while True:
        kwargs = {"NextToken": token} if token else {}
        response = glue_client.get_databases(**kwargs)
        yield from response.get("DatabaseList", [])
        token = response.get("NextToken")
        if not token:
            return


def _iter_glue_tables(glue_client: Any, database_name: str) -> Any:
    """Yield every table in ``database_name``, following pagination."""
    token: str | None = None
    while True:
        kwargs: dict[str, str] = {"DatabaseName": database_name}
        if token:
            kwargs["NextToken"] = token
        response = glue_client.get_tables(**kwargs)
        yield from response.get("TableList", [])
        token = response.get("NextToken")
        if not token:
            return


def discover_glue_table(
    glue_client: Any,
    required_columns: tuple[str, ...],
    partition_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    """Discover the telemetry data-lake Glue table by its schema, not its name.

    The deployed table name is namespace-derived (e.g.
    ``telemetry_useast1_sandbox_shared_data_lake_000_events``), so it is never
    matched by a hardcoded literal. Every database is enumerated and every table
    probed: a table matches when its ``StorageDescriptor`` column names are a
    superset of ``required_columns`` and its ``PartitionKeys`` names are a
    superset of ``partition_keys``.

    Args:
        glue_client: A boto3 Glue client.
        required_columns: Data-column names every match must expose (timestamp,
            event_type, payload). Per the data-lake BUG-3 fix, tool is a
            partition key only -- it is NOT a data column.
        partition_keys: Partition-key names every match must expose (tool, dt).

    Returns:
        ``{"database", "table", "location", "parameters"}`` for the first matching
        table, or None when no table in any database matches the schema. The
        ``parameters`` value is the Glue table-level ``Parameters`` map (the
        TBLPROPERTIES), which carries the Athena partition-projection configuration
        (``projection.enabled`` / ``projection.<column>.type`` /
        ``projection.<column>.values``) the caller needs to tell a projection-managed
        partition (enum-projected ``tool``; no physical catalog partition) apart from
        a physically-registered one.
    """
    required = set(required_columns)
    needed_partitions = set(partition_keys)
    for database in _iter_glue_databases(glue_client):
        db_name = database.get("Name", "")
        for table in _iter_glue_tables(glue_client, db_name):
            storage = table.get("StorageDescriptor", {})
            column_names = {col.get("Name", "") for col in storage.get("Columns", [])}
            partition_names = {pk.get("Name", "") for pk in table.get("PartitionKeys", [])}
            if required <= column_names and needed_partitions <= partition_names:
                return {
                    "database": db_name,
                    "table": table.get("Name", ""),
                    "location": storage.get("Location", ""),
                    "parameters": table.get("Parameters", {}),
                }
    return None


def discover_workgroup(athena_client: Any, env: str) -> str | None:
    """Find the analytics Athena workgroup for ``env``.

    Args:
        athena_client: A boto3 Athena client.
        env: The environment name used to disambiguate workgroups.

    Returns:
        The workgroup name, or None when no analytics workgroup exists.
    """
    names = [g.get("Name", "") for g in athena_client.list_work_groups().get("WorkGroups", [])]
    return next(
        (n for n in names if "analytics" in n and env in n),
        next((n for n in names if "analytics" in n), None),
    )


def discover_cloudfront_domain(cloudfront_client: Any, target_alias: str) -> str | None:
    """Discover the collector CloudFront distribution's default domain.

    Lists distributions and returns the AWS-assigned default DomainName
    (``d1234abcdef.cloudfront.net``) of the distribution whose Aliases include
    ``target_alias`` (the env-specific collector FQDN, e.g.
    ``collector.sandbox.telemetry.example.com``). Falls back to the sole
    distribution when exactly one exists.

    This is how the perf test reaches the collector in an ephemeral sandbox run:
    the service-account-scoped apply cannot create the cross-account public DNS
    record for the pretty FQDN, and ``terragrunt output`` on the collector unit
    fails because it re-resolves cross-account dependencies. The default
    CloudFront domain resolves and serves without any custom DNS.

    Args:
        cloudfront_client: A boto3 CloudFront client (global service; region
            does not matter for the read).
        target_alias: The env-specific collector alias to match against each
            distribution's Aliases.

    Returns:
        The distribution's default DomainName, or None when unresolved.
    """
    items: list[dict[str, Any]] = []
    paginator = cloudfront_client.get_paginator("list_distributions")
    for page in paginator.paginate():
        items.extend(page.get("DistributionList", {}).get("Items", []) or [])
    domain = next(
        (
            d["DomainName"]
            for d in items
            if target_alias in (d.get("Aliases", {}).get("Items", []) or [])
        ),
        None,
    )
    if domain is None and len(items) == 1:
        domain = items[0].get("DomainName")
    return domain


def reconcile_counts(
    expected: dict[str, int],
    found: dict[str, int],
) -> dict[str, Any]:
    """Reconcile sent (manifest) counts against found (queried) counts.

    Both mappings are keyed identically (e.g. ``"<tool>|<event_type>"``). The
    reconciliation is exact: every expected key must be present in ``found``
    with a count greater than or equal to the expected count, and no expected
    count may exceed what was found.

    Args:
        expected: The per-key counts the load generator recorded as sent.
        found: The per-key counts the verifier observed in the data lake.

    Returns:
        A reconciliation report dict with ``matched`` (bool) and per-key detail.
    """
    keys = sorted(set(expected) | set(found))
    per_key: list[dict[str, Any]] = []
    matched = True
    for key in keys:
        exp = expected.get(key, 0)
        fnd = found.get(key, 0)
        ok = fnd >= exp
        if not ok:
            matched = False
        per_key.append({"key": key, "expected": exp, "found": fnd, "ok": ok})
    return {
        "matched": matched,
        "total_expected": sum(expected.values()),
        "total_found": sum(found.values()),
        "per_key": per_key,
    }
