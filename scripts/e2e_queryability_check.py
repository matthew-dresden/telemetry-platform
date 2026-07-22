"""e2e_queryability_check -- post-deploy prod queryability gate (no collector emit).

Asserts the telemetry data-lake Glue table is queryable with NO tool filter: a
bare ``SELECT count(*)`` over the whole table MUST reach ``SUCCEEDED``. The
previous ``injected`` tool projection rejected any query lacking a static
``WHERE tool='...'`` equality with ``CONSTRAINT_VIOLATION``; the ``enum``
projection keeps the table queryable without a tool filter (``SELECT *`` works).
This gate is the post-deploy regression guard for that fix.

Why this and not the full emit-through-collector e2e in CI: the prod collector
is fronted by a WAF whose ``AWSManagedRulesAnonymousIpList`` /
``AWSManagedRulesAmazonIpReputationList`` rules block hosting/cloud source IPs --
i.e. GitHub Actions runners -- so ``scripts.otlp_e2e_loadgen`` cannot POST to the
public OTLP endpoint from CI (it gets 403). This check never touches the
collector: it uses the ambient (OIDC) credential chain to run one Athena query
directly, so it validates the deployed table from CI. The full synthetic
emit-and-reconcile e2e (``make e2e-all``) remains a developer/operator tool to be
run from a non-WAF-blocked source IP.

Usage:
    uv run python -m scripts.e2e_queryability_check --env prod \\
        --output e2e-evidence/queryability-prod.json

Exit codes:
    0 -- the table is enum-projected AND the no-tool-filter query SUCCEEDED
    1 -- an assertion failed (still injected, query did not succeed, no table)
    2 -- usage error (unknown ENV / missing region)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from collections.abc import Callable
from typing import Any

from scripts import constants, e2e_common

# The tool partition key the data-lake table declares with Athena enum projection
# (projection.tool.type=enum over projection.tool.values). Named once so the
# projection-parameter keys below can never drift from the partition name.
_TOOL_PARTITION_KEY = "tool"
_PROJECTION_TYPE_PARAM = f"projection.{_TOOL_PARTITION_KEY}.type"
_PROJECTION_VALUES_PARAM = f"projection.{_TOOL_PARTITION_KEY}.values"
_PROJECTION_TYPE_ENUM = "enum"

_ATHENA_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

# Queryability-guard query. A plain SELECT count(*) with NO tool filter over the
# whole table. The {table} slot is filled only with an identifier validated by
# e2e_common.safe_sql_identifier; the database is selected out-of-band via
# QueryExecutionContext, never string-interpolated -- so there is no SQL-injection
# vector. Kept as a module-level constant filled via str.format (not an inline
# f-string) so the SQL text is a single auditable literal.
_ATHENA_QUERYABLE_QUERY = "SELECT count(*) AS n FROM {table}"


def build_default_session(boto3_module: Any) -> Any:
    """Return a boto3 Session bound to the ambient credential chain.

    Thin wrapper over the shared ``e2e_common.build_ambient_session`` (single source
    of truth for the ambient/OIDC session), kept as a module-local name for existing
    call sites and tests. Unlike ``e2e_common.build_session`` (which binds a named
    profile), this uses the default chain so it resolves the CI OIDC-assumed role
    credentials from the environment; locally it honours ``AWS_PROFILE`` / ambient
    env creds.

    Args:
        boto3_module: The boto3 module (injected for test isolation).

    Returns:
        A boto3 Session instance.
    """
    return e2e_common.build_ambient_session(boto3_module)


def resolve_region(explicit_region: str | None) -> str:
    """Resolve the AWS region, failing fast when neither source provides one.

    Args:
        explicit_region: A region passed on the CLI, or None.

    Returns:
        The resolved region string.

    Raises:
        e2e_common.E2EUsageError: When no region is available.
    """
    region = (explicit_region or os.environ.get("AWS_DEFAULT_REGION", "")).strip()
    if not region:
        raise e2e_common.E2EUsageError(
            "ERROR: AWS_DEFAULT_REGION is not set. Set it to the target region "
            "(e.g. us-east-1) before running e2e-queryability-check."
        )
    return region


def build_queryability_query(table: str) -> str:
    """Build the no-tool-filter queryability-guard query for ``table``.

    A bare ``SELECT count(*)`` over the whole table with NO ``WHERE`` clause: it
    SUCCEEDS only when the tool partition is enum-projected (an injected column
    would reject it with CONSTRAINT_VIOLATION). The ``{table}`` slot is filled from
    a module-level template with an identifier validated by
    ``e2e_common.safe_sql_identifier`` (letters/digits/underscore only); the
    database is selected out-of-band via QueryExecutionContext, never
    interpolated, so there is no SQL-injection vector.

    Args:
        table: The schema-discovered Glue table name.

    Returns:
        The queryability-guard query string.
    """
    return _ATHENA_QUERYABLE_QUERY.format(table=e2e_common.safe_sql_identifier(table))


def run_query_to_state(
    athena_client: Any,
    workgroup: str,
    database: str,
    query: str,
    poll_timeout: float,
    poll_interval: float,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str]:
    """Start an Athena query and poll until it reaches a terminal state.

    Args:
        athena_client: A boto3 Athena client.
        workgroup: The Athena workgroup (supplies the encrypted result location).
        database: The query execution database context.
        query: The SQL text to run.
        poll_timeout: Maximum seconds to wait for a terminal state.
        poll_interval: Seconds between status polls.
        sleep_fn: Sleep function (injected for test isolation).
        clock: Monotonic clock function (injected for test isolation).

    Returns:
        ``(state, reason)`` where state is the terminal Athena state (or
        ``TIMEOUT``) and reason is the StateChangeReason (empty when none).

    Raises:
        e2e_common.E2EUsageError: When the query never reaches a terminal state
            within ``poll_timeout``.
    """
    start = athena_client.start_query_execution(
        QueryString=query,
        WorkGroup=workgroup,
        QueryExecutionContext={"Database": database},
    )
    execution_id = start["QueryExecutionId"]
    deadline = clock() + poll_timeout
    while True:
        execution = athena_client.get_query_execution(QueryExecutionId=execution_id)
        status = execution.get("QueryExecution", {}).get("Status", {})
        state = status.get("State", "")
        if state in _ATHENA_TERMINAL_STATES:
            return state, status.get("StateChangeReason", "")
        if clock() >= deadline:
            raise e2e_common.E2EUsageError(
                f"ERROR: Athena query {execution_id} did not reach a terminal state "
                f"within {poll_timeout}s (last state {state!r})."
            )
        sleep_fn(poll_interval)


def check_queryability(
    env: str,
    boto3_module: Any,
    output_path: str | None = None,
    poll_timeout: float | None = None,
    poll_interval: float | None = None,
    region: str | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Assert the prod telemetry table is enum-projected and queryable with no filter.

    Args:
        env: The environment name (sandbox, prod, qa).
        boto3_module: The boto3 module (injected for test isolation).
        output_path: Optional path to write the evidence JSON to.
        poll_timeout: Athena poll timeout (defaults to E2E_POLL_TIMEOUT).
        poll_interval: Athena poll interval (defaults to E2E_POLL_INTERVAL).
        region: Explicit AWS region (defaults to AWS_DEFAULT_REGION).
        sleep_fn: Sleep function (injected for test isolation).

    Returns:
        0 when every probe passes, 1 when any assertion fails.
    """
    resolve_region(region)
    timeout = poll_timeout if poll_timeout is not None else float(constants.E2E_POLL_TIMEOUT)
    interval = poll_interval if poll_interval is not None else float(constants.E2E_POLL_INTERVAL)

    session = build_default_session(boto3_module)
    glue = session.client("glue")
    athena = session.client("athena")
    probes: list[dict[str, str]] = []

    def emit(name: str, ok: bool, detail: str) -> None:
        probes.append({"probe": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    discovered = e2e_common.discover_glue_table(
        glue,
        constants.E2E_GLUE_REQUIRED_COLUMNS,
        constants.E2E_GLUE_PARTITION_KEYS,
    )
    if discovered is None:
        emit(
            "glue:table",
            False,
            "no Glue table matches the telemetry schema "
            f"(columns {list(constants.E2E_GLUE_REQUIRED_COLUMNS)}, "
            f"partitions {list(constants.E2E_GLUE_PARTITION_KEYS)})",
        )
        return _finish(probes, env, output_path)

    database = discovered["database"]
    table = discovered["table"]
    emit("glue:table", True, f"database={database} table={table}")

    parameters = discovered.get("parameters", {})
    tool_type = parameters.get(_PROJECTION_TYPE_PARAM, "")
    tool_values = parameters.get(_PROJECTION_VALUES_PARAM, "")
    if tool_type == _PROJECTION_TYPE_ENUM:
        emit(
            "glue:enum-projection",
            True,
            f"{_PROJECTION_TYPE_PARAM}={tool_type} {_PROJECTION_VALUES_PARAM}={tool_values}",
        )
    else:
        emit(
            "glue:enum-projection",
            False,
            f"{_PROJECTION_TYPE_PARAM}={tool_type!r} (expected {_PROJECTION_TYPE_ENUM!r}); "
            "an injected tool projection makes the table unqueryable without a tool filter",
        )

    workgroup = e2e_common.discover_workgroup(athena, env)
    if workgroup is None:
        emit("athena:workgroup", False, "no analytics workgroup found")
        return _finish(probes, env, output_path)
    emit("athena:workgroup", True, f"workgroup={workgroup}")

    query = build_queryability_query(table)
    state, reason = run_query_to_state(
        athena, workgroup, database, query, timeout, interval, sleep_fn
    )
    if state == "SUCCEEDED":
        emit(
            "athena:queryable",
            True,
            "no-tool-filter SELECT count(*) SUCCEEDED (table is queryable without a tool filter)",
        )
    else:
        emit(
            "athena:queryable",
            False,
            f"no-tool-filter SELECT count(*) reached {state} ({reason or 'no reason given'}); "
            "an injected tool projection rejects this query with CONSTRAINT_VIOLATION",
        )

    return _finish(probes, env, output_path)


def _finish(probes: list[dict[str, str]], env: str, output_path: str | None) -> int:
    """Render the probe results, optionally persist them, and derive the exit code.

    Args:
        probes: The accumulated probe results.
        env: The environment name (recorded in the evidence).
        output_path: Optional evidence-file path.

    Returns:
        0 when every probe passed, 1 otherwise.
    """
    failed = [p for p in probes if p["status"] == "FAIL"]
    overall = "FAIL" if failed else "PASS"
    report = {"env": env, "overall": overall, "probes": probes}
    for probe in probes:
        print(f"{probe['status']} {probe['probe']} -- {probe['detail']}", file=sys.stderr)
    print(
        f"OVERALL {overall} ({len(probes) - len(failed)}/{len(probes)} probes passed)",
        file=sys.stderr,
    )
    if output_path:
        path = pathlib.Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if overall == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector (defaults to sys.argv[1:]).

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="e2e_queryability_check",
        description=(
            "Assert the telemetry data-lake Glue table is enum-projected and queryable "
            "with no tool filter (post-deploy regression guard; no collector emit)."
        ),
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Target environment (sandbox, prod, qa).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the evidence JSON to.",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (defaults to AWS_DEFAULT_REGION).",
    )
    args = parser.parse_args(argv)

    import boto3 as boto3_module  # deferred so module import stays AWS-free (test isolation)

    try:
        return check_queryability(
            env=args.env,
            boto3_module=boto3_module,
            output_path=args.output,
            region=args.region,
        )
    except e2e_common.E2EUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
