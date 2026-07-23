"""Consumer-side verifier for the OTLP end-to-end test harness.

Invoked as::

    uv run python -m scripts.e2e_verify --env sandbox --manifest run.json
    make e2e-verify ENV=sandbox MANIFEST=run.json

Given a sent-manifest produced by ``scripts.otlp_e2e_loadgen``, this prober
walks the full consumer pipeline and asserts the synthetic data flowed through
it intact:

    CWL ``/telemetry/<ns>/ingest/otlp-logs`` group received the records
      -> Firehose IncomingRecords / DeliveryToS3 metrics advanced
      -> S3 ``raw/tool=e2e-smoke/dt=<date>/`` Parquet objects exist + are
         CMK-encrypted, with no fresh ``errors/`` objects
      -> Glue telemetry-events table (data columns timestamp/event_type/payload;
         partition keys tool, dt) is present. The ``tool`` partition uses Athena
         enum projection (``projection.tool.type=enum`` over a governed
         ``projection.tool.values`` set), which registers NO physical partition in
         the Glue catalog -- ``get_partitions`` returns empty by design -- so for a
         projection-managed table the presence of the run's data is proven by the
         Athena reconciliation below, not by enumerating catalog partitions; only a
         physically-partitioned table is checked with ``get_partitions``
      -> Athena queryability guard: ``SELECT count(*) FROM <table>`` with NO tool
         filter must SUCCEED. enum projection keeps the table queryable without a
         ``WHERE tool = '...'`` predicate; the previous injected projection rejected
         such queries with ``CONSTRAINT_VIOLATION``. This is the direct regression
         guard for that fix.
      -> Athena ``SELECT event_type, count(*) ... WHERE tool = 'e2e-smoke' AND
         json_extract_scalar(payload, '$.run_id') = '<run-id>'`` reconciles to the
         manifest per-event counts, results are encrypted, the workgroup enforces a
         bytes-scanned cost cap. Every synthetic record shares the fixed
         ``tool = e2e-smoke`` enum value; runs are separated by the payload
         ``run_id``, never by a per-run tool value. Both bound values are native
         Athena query parameters, never string-interpolated.
      -> QuickSight data-source / data-sets / dashboards exist; the embed-url
         Lambda returns 200/401/403 for valid/invalid/bad-origin requests;
         generate-embed-url-for-registered-user is exercised best-effort.

Post-verify cleanup (opt-in ``--cleanup``): this run's synthetic objects under the
shared ``raw/tool=e2e-smoke/dt=<date>/`` partition(s) for the run's date(s) are
deleted. Only the reserved ``e2e-smoke`` tool partition is ever touched.

Resource names are NEVER hardcoded: every resource is discovered via AWS
list/describe (Firehose -> its own S3 destination, Glue -> the table whose
schema matches the telemetry columns/partitions, etc.) or via the namespace
pattern, exactly like ``scripts.live_verify``.

All waits are active readiness polling over an env-driven, bounded budget
(``E2E_POLL_TIMEOUT`` / ``E2E_POLL_INTERVAL``) -- no ``time.sleep``.

Probe statuses: ``OK`` (passed), ``FAIL`` (a hard pipeline assertion failed),
``INFO`` (a console-managed / best-effort observation that never fails the run).

Exit codes::

    0 -- every hard probe passed
    1 -- one or more hard probes failed
    2 -- usage error (unknown ENV / unreadable manifest)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any

from scripts import constants, e2e_common

# Reconciliation query template. Every synthetic record shares the fixed
# ``tool = 'e2e-smoke'`` enum partition, so a run is scoped by the ``run_id``
# embedded in the payload (``$.run_id``), extracted with ``json_extract_scalar``.
# Both the tool value and the run id are bound as native Athena query parameters
# (the ``?`` placeholders + ExecutionParameters), never string-interpolated -- so
# there is no SQL-injection vector. The ``{table}`` slot is filled only with an
# identifier already validated by ``_safe_identifier``; the database is selected
# out-of-band via QueryExecutionContext, not interpolated. Kept as a module-level
# constant filled via ``str.format`` (not an inline f-string) so the SQL text is a
# single auditable literal.
_ATHENA_COUNT_QUERY = (
    "SELECT event_type, count(*) AS c FROM {table} "
    "WHERE tool = ? AND json_extract_scalar(payload, '$.run_id') = ? "
    "GROUP BY event_type"
)

# Queryability-guard query. A plain ``SELECT count(*)`` with NO tool filter MUST
# succeed against the enum-projected table; the previous injected projection
# rejected any query lacking a static ``WHERE tool = '...'`` equality with
# ``CONSTRAINT_VIOLATION``. This is the direct regression guard for that fix.
_ATHENA_QUERYABLE_QUERY = "SELECT count(*) AS c FROM {table}"

# The Glue partition key the synthetic ``tool`` value is filtered on. It is the
# partition the data-lake table declares with Athena enum projection
# (``projection.tool.type=enum``); kept as a single named constant so the
# get_partitions predicate and the projection-aware branch never drift apart.
_TOOL_PARTITION_KEY = "tool"


def _safe_identifier(identifier: str) -> str:
    """Return ``identifier`` when it is a safe SQL identifier, else raise.

    Thin wrapper over the shared ``e2e_common.safe_sql_identifier`` (single source
    of truth for the identifier allowlist), kept as a module-local name for the
    existing call sites and tests.

    Args:
        identifier: A catalog identifier (e.g. a table name).

    Returns:
        The identifier unchanged.

    Raises:
        E2EError: When the identifier contains characters outside the allowlist.
    """
    return e2e_common.safe_sql_identifier(identifier)


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_ACCOUNTS_JSON_PATH = _REPO_ROOT / "terragrunt" / "common" / "accounts.json"
_DOMAINS_JSON_PATH = _REPO_ROOT / "terragrunt" / "common" / "domains.json"

# Probe status values.
_OK = "OK"
_FAIL = "FAIL"
_INFO = "INFO"


class E2EVerifier:
    """Read-only-flavoured consumer verifier for one sent-manifest."""

    def __init__(
        self,
        env: str,
        manifest: dict[str, Any],
        accounts_data: dict[str, Any],
        domains_data: dict[str, Any],
        boto3_module: Any,
        output_path: str | None = None,
        poll_timeout: float | None = None,
        poll_interval: float | None = None,
        aws_region: str | None = None,
        cleanup: bool = False,
        ambient_credentials: bool = False,
    ) -> None:
        self._account_id, self._profile = e2e_common.resolve_account(env, accounts_data)
        self._env = env
        self._manifest = manifest
        self._domains = domains_data
        self._boto3 = boto3_module
        self._output_path = output_path
        self._poll_timeout = (
            poll_timeout if poll_timeout is not None else float(constants.E2E_POLL_TIMEOUT)
        )
        self._poll_interval = (
            poll_interval if poll_interval is not None else float(constants.E2E_POLL_INTERVAL)
        )
        self._aws_region = aws_region
        self._cleanup = cleanup
        # When True, build the boto3 session from the ambient/default credential chain
        # (CI OIDC-assumed role) instead of the env-named profile -- there is no
        # sandbox/prod/qa named profile under CI OIDC. account_id (used for QuickSight)
        # still comes from accounts.json, which matches the ambient identity's account.
        self._ambient_credentials = ambient_credentials
        # The fixed reserved tool value every record carries (``e2e-smoke``); the run
        # itself is identified by ``run_id``, embedded in each record's payload.
        self._tool = manifest["tool_value"]
        self._run_id = manifest["run_id"]
        self._session: Any = None

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _get_region(self) -> str:
        if self._aws_region:
            return self._aws_region
        region = os.environ.get("AWS_DEFAULT_REGION", "").strip()
        if not region:
            raise e2e_common.E2EUsageError(
                "ERROR: AWS_DEFAULT_REGION is not set. Set it to the target region "
                "(e.g. us-east-1) before running e2e-verify."
            )
        return region

    def _client(self, service_name: str) -> Any:
        if self._session is None:
            if self._ambient_credentials:
                self._session = e2e_common.build_ambient_session(self._boto3)
            else:
                self._session = e2e_common.build_session(self._boto3, self._profile)
        return self._session.client(service_name)

    def _emit(
        self,
        probes: list[dict[str, Any]],
        name: str,
        status: str,
        detail: str,
    ) -> None:
        print(f"{status} {name}: {detail}")
        if status == _FAIL:
            print(f"ERROR: probe {name} failed: {detail}", file=sys.stderr)
        probes.append({"name": name, "status": status, "detail": detail})

    # ------------------------------------------------------------------
    # Discovery (namespace-agnostic: list/describe, never hardcode names)
    # ------------------------------------------------------------------

    def _discover_log_group(self, logs: Any) -> str | None:
        """Return the otlp-logs CWL group for this env, or None when absent."""
        paginator_resp = logs.describe_log_groups(logGroupNamePrefix="/telemetry/")
        for group in paginator_resp.get("logGroups", []):
            name = group.get("logGroupName", "")
            if name.endswith("/ingest/otlp-logs") and f"-{self._env}-" in name:
                return str(name)
        return None

    def _discover_firehose(self, firehose: Any) -> dict[str, str] | None:
        """Discover the telemetry delivery stream and its S3 destination.

        Thin wrapper over ``e2e_common.discover_firehose_stream`` (shared with
        ``scripts.perf_report``) -- see that function's docstring for the
        discovery strategy.
        """
        return e2e_common.discover_firehose_stream(firehose, self._env)

    def _discover_embed_function(self, lambda_client: Any) -> str | None:
        """Find the portal embed-url Lambda function for this env."""
        functions = lambda_client.list_functions().get("Functions", [])
        names = [f.get("FunctionName", "") for f in functions]
        return next(
            (n for n in names if "embed" in n and self._env in n),
            next((n for n in names if "embed" in n), None),
        )

    @staticmethod
    def _discover_function_url(lambda_client: Any, function: str) -> str | None:
        """Return the embed-url Lambda's Function URL, or None when none exists."""
        import botocore.exceptions

        try:
            config = lambda_client.get_function_url_config(FunctionName=function)
        except botocore.exceptions.ClientError:
            return None
        url = config.get("FunctionUrl", "")
        return str(url) or None

    @staticmethod
    def s3_listing_prefix(firehose_prefix: str, tool: str) -> str:
        """Derive a concrete S3 listing prefix for a tool partition.

        Firehose dynamic-partitioning prefixes contain ``!{...}`` expressions and
        a ``tool=`` partition segment. The static portion up to ``tool=`` is the
        listable base; the concrete tool partition is appended.

        Args:
            firehose_prefix: The Firehose destination ``Prefix`` field.
            tool: The fixed reserved tool partition value (``e2e-smoke``).

        Returns:
            A concrete listable prefix ``<base>tool=<tool>/``.
        """
        base = firehose_prefix
        for sentinel in ("tool=", "!{"):
            idx = base.find(sentinel)
            if idx != -1:
                base = base[:idx]
                break
        return f"{base}tool={tool}/"

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _count_run_events(self, logs: Any, group: str) -> int:
        """Count every CWL event matching this run, following ``nextToken`` pages.

        Every synthetic record shares the fixed ``tool = e2e-smoke`` value, so the
        CWL filter matches on the run's unique ``run_id`` (embedded in each log
        body's payload as ``$.run_id``), not on the shared tool value -- otherwise a
        prior run's ``e2e-smoke`` events under the same group would be miscounted.

        ``filter_log_events`` returns at most one page of events (bounded by the API's
        per-call size limit) plus a ``nextToken`` when more remain. Reading only the
        first page under-counts a multi-page result set and false-FAILs the
        reconciliation, so every page is followed until no ``nextToken`` is returned.

        Args:
            logs: A boto3 CloudWatch Logs client.
            group: The discovered otlp-logs log group name.

        Returns:
            The total number of run-matching events across all pages.
        """
        total = 0
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "logGroupName": group,
                "filterPattern": f'"{self._run_id}"',
            }
            if token:
                kwargs["nextToken"] = token
            resp = logs.filter_log_events(**kwargs)
            total += len(resp.get("events", []))
            token = resp.get("nextToken")
            if not token:
                return total

    def _check_cwl(self, probes: list[dict[str, Any]]) -> None:
        logs = self._client("logs")
        group = self._discover_log_group(logs)
        if group is None:
            self._emit(
                probes,
                "cwl:log-group",
                _FAIL,
                f"no /telemetry/*/ingest/otlp-logs group found for env {self._env}",
            )
            return
        self._emit(probes, "cwl:log-group", _OK, f"discovered {group}")

        expected: int = self._manifest.get("total_records", 0)
        seen: list[int] = [0]

        def _received() -> bool:
            seen[0] = self._count_run_events(logs, group)
            return seen[0] >= max(1, expected)

        ready = e2e_common.poll_until(
            _received,
            self._poll_timeout,
            self._poll_interval,
            timeout_msg=(
                f"FAIL cwl:received: only {seen[0]} of {expected} events for "
                f"run {self._run_id} within {self._poll_timeout}s"
            ),
        )
        if ready:
            self._emit(probes, "cwl:received", _OK, f"{seen[0]} event(s) for run {self._run_id}")
        else:
            self._emit(
                probes,
                "cwl:received",
                _FAIL,
                f"{seen[0]} of {expected} events for run {self._run_id}",
            )

    def _check_firehose(self, probes: list[dict[str, Any]]) -> dict[str, str] | None:
        firehose = self._client("firehose")
        discovered = self._discover_firehose(firehose)
        if discovered is None:
            self._emit(probes, "firehose:stream", _FAIL, "no telemetry delivery stream found")
            return None
        self._emit(
            probes,
            "firehose:stream",
            _OK,
            f"stream={discovered['stream']} bucket={discovered['bucket']}",
        )

        cw = self._client("cloudwatch")
        start_time, end_time = self._metric_window(
            self._manifest["started_at"], self._manifest["finished_at"]
        )
        for metric in ("IncomingRecords", "DeliveryToS3.Records"):
            resp = cw.get_metric_statistics(
                Namespace="AWS/Firehose",
                MetricName=metric,
                Dimensions=[{"Name": "DeliveryStreamName", "Value": discovered["stream"]}],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=["Sum"],
            )
            total = sum(point.get("Sum", 0) for point in resp.get("Datapoints", []))
            status = _OK if total > 0 else _INFO
            self._emit(
                probes,
                f"firehose:metric:{metric}",
                status,
                f"sum={total} over the run window",
            )
        return discovered

    @staticmethod
    def _metric_window(
        started_at: str,
        finished_at: str,
        min_span_seconds: int = 60,
    ) -> tuple[datetime.datetime, datetime.datetime]:
        """Compute a valid CloudWatch ``(StartTime, EndTime)`` from the manifest.

        Thin wrapper over ``e2e_common.metric_window`` (shared with
        ``scripts.perf_report``) -- see that function's docstring for why the
        window is floored/widened.
        """
        return e2e_common.metric_window(started_at, finished_at, min_span_seconds)

    def _check_s3(self, probes: list[dict[str, Any]], firehose: dict[str, str]) -> None:
        if not firehose.get("bucket"):
            self._emit(probes, "s3:bucket", _FAIL, "Firehose destination has no S3 bucket")
            return
        s3 = self._client("s3")
        bucket = firehose["bucket"]
        prefix = self.s3_listing_prefix(firehose.get("prefix", ""), self._tool)

        objects: list[dict[str, Any]] = []

        def _objects_present() -> bool:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            objects[:] = resp.get("Contents", [])
            return len(objects) > 0

        ready = e2e_common.poll_until(
            _objects_present,
            self._poll_timeout,
            self._poll_interval,
            timeout_msg=(
                f"FAIL s3:objects: no objects under s3://{bucket}/{prefix} within "
                f"{self._poll_timeout}s"
            ),
        )
        if not ready:
            self._emit(probes, "s3:objects", _FAIL, f"no objects under s3://{bucket}/{prefix}")
            return
        self._emit(
            probes,
            "s3:objects",
            _OK,
            f"{len(objects)} object(s) under s3://{bucket}/{prefix}",
        )

        # CMK encryption assertion on the first object.
        head = s3.head_object(Bucket=bucket, Key=objects[0]["Key"])
        sse = head.get("ServerSideEncryption", "")
        kms_key = head.get("SSEKMSKeyId", "")
        if sse == "aws:kms" and kms_key:
            self._emit(probes, "s3:encryption", _OK, f"SSE-KMS with key {kms_key}")
        else:
            self._emit(
                probes,
                "s3:encryption",
                _FAIL,
                f"expected SSE-KMS (CMK); got SSE={sse!r} key={kms_key!r}",
            )

        # No fresh error objects produced during the run window.
        error_prefix = self._error_listing_prefix(firehose.get("error_prefix", ""))
        err_resp = s3.list_objects_v2(Bucket=bucket, Prefix=error_prefix)
        fresh = [
            obj
            for obj in err_resp.get("Contents", [])
            if str(obj.get("LastModified", "")) >= self._manifest["started_at"]
        ]
        if fresh:
            self._emit(
                probes,
                "s3:errors",
                _FAIL,
                f"{len(fresh)} fresh error object(s) under s3://{bucket}/{error_prefix}",
            )
        else:
            self._emit(
                probes,
                "s3:errors",
                _OK,
                f"no fresh error objects under s3://{bucket}/{error_prefix}",
            )

    @staticmethod
    def _error_listing_prefix(error_prefix: str) -> str:
        """Return the static base of a Firehose ErrorOutputPrefix for listing."""
        base = error_prefix
        idx = base.find("!{")
        return base[:idx] if idx != -1 else base

    def _check_glue(self, probes: list[dict[str, Any]]) -> dict[str, str] | None:
        glue = self._client("glue")
        discovered = e2e_common.discover_glue_table(
            glue,
            constants.E2E_GLUE_REQUIRED_COLUMNS,
            constants.E2E_GLUE_PARTITION_KEYS,
        )
        if discovered is None:
            self._emit(
                probes,
                "glue:table",
                _FAIL,
                "no Glue table matches the telemetry schema "
                f"(columns {list(constants.E2E_GLUE_REQUIRED_COLUMNS)}, "
                f"partitions {list(constants.E2E_GLUE_PARTITION_KEYS)})",
            )
            return None
        self._emit(
            probes,
            "glue:table",
            _OK,
            f"database={discovered['database']} table={discovered['table']}",
        )

        # The data-lake table declares the tool partition with Athena ENUM partition
        # projection (projection.tool.type=enum over projection.tool.values): Athena
        # resolves the partition set from the projection config, NOT from a physical
        # partition in the Glue catalog, so get_partitions returns an empty list BY
        # DESIGN. Requiring a physical partition would false-FAIL a correctly
        # projected table (issue #85); the run's data presence is proven instead by
        # the Athena reconciliation query downstream. Only a physically-partitioned
        # table is checked with get_partitions.
        if self._is_projected_partition(discovered.get("parameters", {}), _TOOL_PARTITION_KEY):
            proj_values = discovered.get("parameters", {}).get(
                f"projection.{_TOOL_PARTITION_KEY}.values", ""
            )
            self._emit(
                probes,
                "glue:partitions",
                _OK,
                f"{_TOOL_PARTITION_KEY} partition uses enum projection "
                f"(projection.{_TOOL_PARTITION_KEY}.type=enum, values={proj_values!r}); no "
                "physical catalog partition to enumerate -- presence proven by the Athena "
                "reconciliation",
            )
            return discovered

        partitions: list[dict[str, Any]] = []

        def _partitions_present() -> bool:
            resp = glue.get_partitions(
                DatabaseName=discovered["database"],
                TableName=discovered["table"],
                Expression=f"{_TOOL_PARTITION_KEY} = '{self._tool}'",
            )
            partitions[:] = resp.get("Partitions", [])
            return len(partitions) > 0

        ready = e2e_common.poll_until(
            _partitions_present,
            self._poll_timeout,
            self._poll_interval,
            timeout_msg=(
                f"FAIL glue:partitions: no tool={self._tool} partition within {self._poll_timeout}s"
            ),
        )
        if ready:
            self._emit(
                probes,
                "glue:partitions",
                _OK,
                f"{len(partitions)} partition(s) for tool={self._tool}",
            )
        else:
            self._emit(
                probes,
                "glue:partitions",
                _FAIL,
                f"no partition for tool={self._tool}",
            )
        return discovered

    @staticmethod
    def _is_projected_partition(parameters: dict[str, Any], partition_key: str) -> bool:
        """Return True when ``partition_key`` is Athena partition-projection managed.

        Athena partition projection is turned on by the table-level parameter
        ``projection.enabled=true`` and a per-column type ``projection.<column>.type``
        (``enum`` for the governed ``tool`` set, ``date`` for ``dt``). A
        projection-managed column has NO physical partition registered in the Glue
        catalog -- Athena computes the partition set from the projection config -- so
        ``get_partitions`` returns an empty list by design and must NOT be treated as
        a missing partition. Mirrors the data-lake ``glue_table_parameters``
        (providers/aws/references/data-lake/locals.tf: ``projection.tool.type=enum``).

        Args:
            parameters: The Glue table-level ``Parameters`` map (TBLPROPERTIES).
            partition_key: The partition column to inspect (e.g. ``tool``).

        Returns:
            True when projection is enabled and ``partition_key`` declares a
            projection type (i.e. it is projection-managed, no physical partition).
        """
        enabled = str(parameters.get("projection.enabled", "")).strip().lower() == "true"
        proj_type = str(parameters.get(f"projection.{partition_key}.type", "")).strip().lower()
        return enabled and bool(proj_type)

    @staticmethod
    def _build_count_query(table: str) -> str:
        """Build the per-event_type reconciliation count query for this run.

        Every synthetic record shares the fixed ``tool = 'e2e-smoke'`` enum
        partition, so a run is scoped by ``tool = ?`` AND the ``run_id`` embedded in
        the payload, extracted with ``json_extract_scalar(payload, '$.run_id') = ?``.
        The ``dt`` date projection needs no predicate -- Athena resolves it from its
        configured range. Both values are bound via ``ExecutionParameters`` (the two
        ``?`` placeholders), never string-interpolated, so there is no SQL-injection
        vector; the table identifier is validated by ``_safe_identifier`` before
        substitution and the database is selected out-of-band via
        ``QueryExecutionContext``.

        Args:
            table: The already-validated Glue table identifier.

        Returns:
            The reconciliation query string with two ``?`` markers (tool, run_id).
        """
        return _ATHENA_COUNT_QUERY.format(table=table)

    @staticmethod
    def _build_queryability_query(table: str) -> str:
        """Build the no-tool-filter queryability-guard query (``SELECT count(*)``).

        This query carries NO ``WHERE`` predicate at all: it must SUCCEED against the
        enum-projected table. The previous injected projection rejected any query
        lacking a static ``WHERE tool = '...'`` equality with ``CONSTRAINT_VIOLATION``;
        enum projection keeps the table queryable with no tool filter. The table
        identifier is validated by ``_safe_identifier`` before substitution.

        Args:
            table: The already-validated Glue table identifier.

        Returns:
            The queryability-guard query string (no bound parameters).
        """
        return _ATHENA_QUERYABLE_QUERY.format(table=table)

    def _check_athena(self, probes: list[dict[str, Any]], glue: dict[str, str]) -> None:
        athena = self._client("athena")
        workgroup = e2e_common.discover_workgroup(athena, self._env)
        if workgroup is None:
            self._emit(probes, "athena:workgroup", _FAIL, "no analytics workgroup found")
            return
        self._emit(probes, "athena:workgroup", _OK, f"workgroup={workgroup}")

        # Workgroup governance: cost cap + result encryption.
        wg = athena.get_work_group(WorkGroup=workgroup).get("WorkGroup", {})
        wg_cfg = wg.get("Configuration", {})
        cutoff = wg_cfg.get("BytesScannedCutoffPerQuery")
        if cutoff:
            self._emit(probes, "athena:cost-cap", _OK, f"BytesScannedCutoffPerQuery={cutoff}")
        else:
            self._emit(probes, "athena:cost-cap", _FAIL, "no BytesScannedCutoffPerQuery configured")
        enc = wg_cfg.get("ResultConfiguration", {}).get("EncryptionConfiguration")
        if enc:
            self._emit(
                probes,
                "athena:results-encrypted",
                _OK,
                f"result encryption={enc.get('EncryptionOption')}",
            )
        else:
            self._emit(
                probes,
                "athena:results-encrypted",
                _FAIL,
                "workgroup result encryption not enforced",
            )

        # Queryability regression guard: a no-tool-filter SELECT count(*) MUST
        # SUCCEED against the enum-projected table. The previous injected projection
        # rejected any query lacking a static WHERE tool='...' equality with
        # CONSTRAINT_VIOLATION; this probe is the direct guard for that fix.
        table_id = _safe_identifier(glue["table"])
        self._run_athena_query(
            probes,
            athena,
            workgroup,
            glue["database"],
            self._build_queryability_query(table_id),
            probe_name="athena:queryable",
            fetch_rows=False,
        )

        expected_counts: dict[str, int] = self._manifest.get("counts_by_event", {})
        if not expected_counts:
            # A non-data conformance mode (oversize/malformed/wrong-signal/...)
            # sends no records that land in the lake, so there is nothing to
            # reconcile. The queryability guard above still ran.
            self._emit(
                probes,
                "athena:reconcile",
                _OK,
                "no data records recorded in the manifest; nothing to reconcile",
            )
            return

        query = self._build_count_query(table_id)
        rows = self._run_athena_query(
            probes,
            athena,
            workgroup,
            glue["database"],
            query,
            parameters=[self._tool, self._run_id],
        )
        if rows is None:
            return

        found: dict[str, int] = {}
        for row in rows:
            data = row.get("Data", [])
            if len(data) < 2:
                continue
            event_type = data[0].get("VarCharValue", "")
            count_str = data[1].get("VarCharValue", "0")
            if event_type == "event_type":  # header row
                continue
            found[event_type] = int(count_str)

        report = e2e_common.reconcile_counts(expected_counts, found)
        status = _OK if report["matched"] else _FAIL
        self._emit(
            probes,
            "athena:reconcile",
            status,
            f"expected={report['total_expected']} found={report['total_found']} "
            f"matched={report['matched']}",
        )

    def _run_athena_query(
        self,
        probes: list[dict[str, Any]],
        athena: Any,
        workgroup: str,
        database: str,
        query: str,
        parameters: list[str] | None = None,
        probe_name: str = "athena:query",
        fetch_rows: bool = True,
    ) -> list[dict[str, Any]] | None:
        """Run an Athena query, poll to completion, return result rows or None.

        Values are bound via ``ExecutionParameters`` (native Athena prepared-
        statement parameters), never string-interpolated. The query-execution
        outcome is emitted under ``probe_name`` so distinct queries (the
        queryability guard vs. the reconciliation query) surface as distinct probes.
        When ``fetch_rows`` is False the result rows are not retrieved (the
        queryability guard only asserts the query SUCCEEDED); an empty list is
        returned on success in that case.
        """
        request: dict[str, Any] = {
            "QueryString": query,
            "WorkGroup": workgroup,
            "QueryExecutionContext": {"Database": database},
        }
        if parameters:
            request["ExecutionParameters"] = parameters
        start = athena.start_query_execution(**request)
        query_id = start["QueryExecutionId"]
        state_holder = ["", ""]

        def _completed() -> bool:
            resp = athena.get_query_execution(QueryExecutionId=query_id)
            status = resp.get("QueryExecution", {}).get("Status", {})
            state_holder[0] = status.get("State", "")
            state_holder[1] = status.get("StateChangeReason", "")
            return state_holder[0] in ("SUCCEEDED", "FAILED", "CANCELLED")

        e2e_common.poll_until(
            _completed,
            self._poll_timeout,
            self._poll_interval,
            timeout_msg=f"FAIL {probe_name} {query_id} did not settle within {self._poll_timeout}s",
        )
        if state_holder[0] != "SUCCEEDED":
            self._emit(
                probes,
                probe_name,
                _FAIL,
                f"query {query_id} state={state_holder[0]} reason={state_holder[1]}",
            )
            return None
        self._emit(probes, probe_name, _OK, f"query {query_id} SUCCEEDED")
        if not fetch_rows:
            return []
        return self._fetch_query_rows(athena, query_id)

    @staticmethod
    def _fetch_query_rows(athena: Any, query_id: str) -> list[dict[str, Any]]:
        """Return every Athena result row, following ``NextToken`` pagination.

        ``get_query_results`` returns at most 1000 rows per page plus a ``NextToken``
        when more remain; reading only the first page under-counts a result set larger
        than one page and false-FAILs the reconciliation. Athena prepends the
        column-header row to the FIRST page only, so subsequent pages contribute pure
        data rows -- the caller's header-row skip therefore stays correct across pages.

        Args:
            athena: A boto3 Athena client.
            query_id: The QueryExecutionId of a SUCCEEDED query.

        Returns:
            All result rows across every page, in order.
        """
        rows: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"QueryExecutionId": query_id}
            if token:
                kwargs["NextToken"] = token
            results = athena.get_query_results(**kwargs)
            rows.extend(results.get("ResultSet", {}).get("Rows", []))
            token = results.get("NextToken")
            if not token:
                return rows

    def _check_quicksight(self, probes: list[dict[str, Any]]) -> None:
        import botocore.exceptions

        qs = self._client("quicksight")
        try:
            sources = qs.list_data_sources(AwsAccountId=self._account_id).get("DataSources", [])
            athena_sources = [s for s in sources if s.get("Type") == "ATHENA"]
            self._emit(
                probes,
                "quicksight:data-sources",
                _INFO,
                f"{len(sources)} data-source(s), {len(athena_sources)} Athena",
            )
            datasets = qs.list_data_sets(AwsAccountId=self._account_id).get("DataSetSummaries", [])
            self._emit(probes, "quicksight:data-sets", _INFO, f"{len(datasets)} data-set(s)")
            dashboards = qs.list_dashboards(AwsAccountId=self._account_id).get(
                "DashboardSummaryList", []
            )
            self._emit(probes, "quicksight:dashboards", _INFO, f"{len(dashboards)} dashboard(s)")
        except botocore.exceptions.ClientError as exc:
            # QuickSight is console-managed and may be unprovisioned; this is an
            # observation, never a hard failure of the data-plane verification.
            self._emit(probes, "quicksight:enumerate", _INFO, f"not enumerable: {exc}")

    def _check_embed_lambda(self, probes: list[dict[str, Any]]) -> None:
        lambda_client = self._client("lambda")
        function = self._discover_embed_function(lambda_client)
        if function is None:
            self._emit(probes, "embed:function", _INFO, "no embed-url Lambda discovered")
            return
        self._emit(probes, "embed:function", _OK, f"discovered {function}")

        function_url = self._discover_function_url(lambda_client, function)
        self._emit(
            probes,
            "embed:function-url",
            _INFO,
            function_url if function_url else "no Function URL configured",
        )

        endpoints = e2e_common.resolve_endpoints(self._env, self._domains)
        good_origin = f"https://{endpoints['portal_pretty_fqdn']}"
        valid_jwt = self._structural_jwt()
        cases = [
            ("embed:invalid-token", "not-a-jwt", good_origin, (401,)),
            ("embed:bad-origin", valid_jwt, "https://evil.example", (403,)),
            ("embed:valid", valid_jwt, good_origin, (200,)),
        ]
        for name, token, origin, expected in cases:
            status_code, detail = self._invoke_embed(lambda_client, function, token, origin)
            if status_code is None:
                # The Lambda could not be exercised read-only (function error or a
                # response with no statusCode); report NA rather than a spurious
                # statusCode=0 failure.
                self._emit(probes, name, _INFO, f"indeterminate: {detail}")
                continue
            if name == "embed:valid":
                # The valid case depends on console-managed QuickSight provisioning;
                # treat anything other than 200 as INFO, never a hard failure.
                verdict = _OK if status_code in expected else _INFO
            else:
                verdict = _OK if status_code in expected else _FAIL
            self._emit(probes, name, verdict, f"status={status_code} expected={list(expected)}")

    @staticmethod
    def _structural_jwt() -> str:
        """Return a structurally-valid (header.payload.signature) JWT.

        The embed Lambda's OIDC guard structurally validates the token (three
        dot-separated segments, base64url JSON payload) before any AWS call; in
        production the ALB/API Gateway verifies the signature. This token passes
        the structural guard so the valid-token path can be exercised.
        """
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
        payload = base64.urlsafe_b64encode(b'{"sub":"synthetic-e2e"}').rstrip(b"=").decode("ascii")
        return f"{header}.{payload}.signature"

    @staticmethod
    def _invoke_embed(
        lambda_client: Any, function: str, token: str, origin: str
    ) -> tuple[int | None, str]:
        """Invoke the embed-url Lambda with a proxy event and parse the result.

        The Lambda is invoked synchronously with an API-Gateway-proxy-shaped event
        (the same shape ``apps/portal-embed-url/app.py`` consumes), which exercises
        its OIDC + domain-allow-list guards read-only without provisioning a real
        OIDC session. The handler returns a proxy response carrying ``statusCode``.

        Returns:
            ``(status_code, detail)``. ``status_code`` is the parsed proxy
            ``statusCode`` when determinate. It is ``None`` (with a diagnostic
            ``detail``) when the invocation reported a ``FunctionError`` or the
            returned payload carried no ``statusCode`` -- so the caller degrades
            to NA instead of treating an un-parseable response as ``statusCode=0``.
        """
        event = {
            "headers": {"Authorization": f"Bearer {token}"},
            "queryStringParameters": {"origin": origin},
        }
        resp = lambda_client.invoke(
            FunctionName=function,
            Payload=json.dumps(event).encode("utf-8"),
        )
        payload_stream = resp.get("Payload")
        raw = payload_stream.read() if payload_stream is not None else b""
        snippet = raw.decode("utf-8", errors="replace")[:200]
        if resp.get("FunctionError"):
            return None, f"lambda FunctionError={resp['FunctionError']!r}: {snippet}"
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            return None, f"response payload is not JSON ({exc}): {snippet}"
        if not isinstance(body, dict) or "statusCode" not in body:
            return None, f"response payload has no statusCode: {snippet}"
        return int(body["statusCode"]), ""

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self) -> int:
        probes: list[dict[str, Any]] = []
        run_at = e2e_common.utc_now_iso()

        self._check_cwl(probes)
        firehose = self._check_firehose(probes)
        if firehose is not None:
            self._check_s3(probes, firehose)
        glue = self._check_glue(probes)
        if glue is not None:
            self._check_athena(probes, glue)
        self._check_quicksight(probes)
        self._check_embed_lambda(probes)

        # Opt-in post-verify cleanup of this run's synthetic S3 objects. Runs after
        # every probe so the verification evidence is complete first.
        if self._cleanup:
            self._cleanup_run_objects(probes, firehose)

        failed = [p for p in probes if p["status"] == _FAIL]
        overall = _FAIL if failed else _OK
        exit_code = 1 if failed else 0

        if self._output_path:
            evidence = {
                "env": self._env,
                "account_id": self._account_id,
                "run_id": self._run_id,
                "tool_value": self._tool,
                "verified_at": run_at,
                "probes": probes,
                "result": overall,
            }
            pathlib.Path(self._output_path).write_text(json.dumps(evidence, indent=2))

        print(f"\nRESULT {overall} ({len(probes)} probes, {len(failed)} failed)")
        return exit_code

    # ------------------------------------------------------------------
    # Post-verify cleanup
    # ------------------------------------------------------------------

    def _cleanup_run_objects(
        self, probes: list[dict[str, Any]], firehose: dict[str, str] | None
    ) -> None:
        """Delete this run's synthetic objects under the shared e2e-smoke partition.

        Every synthetic record shares the fixed ``tool = e2e-smoke`` partition, so a
        per-run tool prefix no longer exists to delete wholesale. Instead this deletes
        the ``raw/tool=e2e-smoke/dt=<date>/`` partition(s) for the run's date(s)
        (``expected_dt_partitions``). Post-deploy runs are serial and the data is
        synthetic, so deleting the whole reserved ``e2e-smoke`` date partition is safe;
        no other tool's partition is ever touched. Delete failures fail fast (a hard
        probe) so a botched cleanup is never silent.

        Args:
            probes: The probe accumulator.
            firehose: The discovered Firehose destination (bucket + prefix), or None.
        """
        if firehose is None or not firehose.get("bucket"):
            self._emit(
                probes,
                "cleanup:objects",
                _FAIL,
                "no Firehose S3 destination discovered; cannot clean up run objects",
            )
            return
        bucket = firehose["bucket"]
        tool_base = self.s3_listing_prefix(firehose.get("prefix", ""), self._tool)
        dt_partitions = self._manifest.get("expected_dt_partitions", [])
        s3 = self._client("s3")
        deleted = 0
        for dt in dt_partitions:
            prefix = f"{tool_base}dt={dt}/"
            deleted += self._delete_prefix(s3, bucket, prefix)
        self._emit(
            probes,
            "cleanup:objects",
            _OK,
            f"deleted {deleted} object(s) under s3://{bucket}/{tool_base}dt=<{len(dt_partitions)} "
            f"date(s)> for run {self._run_id}",
        )

    @staticmethod
    def _delete_prefix(s3: Any, bucket: str, prefix: str) -> int:
        """Delete every object under ``prefix``, returning the count deleted.

        Lists the prefix page by page (``list_objects_v2`` + ``ContinuationToken``)
        and deletes each page in a single batched ``delete_objects`` call (the API
        caps a batch at 1000 keys, which equals the list page size).

        Args:
            s3: A boto3 S3 client.
            bucket: The destination bucket.
            prefix: The concrete ``tool=e2e-smoke/dt=<date>/`` key prefix to purge.

        Returns:
            The number of objects deleted.
        """
        deleted = 0
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            contents = resp.get("Contents", [])
            keys = [{"Key": obj["Key"]} for obj in contents if "Key" in obj]
            if keys:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
                deleted += len(keys)
            token = resp.get("NextContinuationToken")
            if not resp.get("IsTruncated") or not token:
                return deleted


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the verifier CLI."""
    parser = argparse.ArgumentParser(
        prog="scripts.e2e_verify",
        description="Consumer-side verifier for the OTLP end-to-end test harness.",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment to verify (resolves account/profile from accounts.json).",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to the sent-manifest JSON produced by scripts.otlp_e2e_loadgen.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the JSON evidence file.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "After verifying, delete this run's synthetic objects under the shared "
            "raw/tool=e2e-smoke/dt=<date>/ partition(s) for the run's date(s). Only the "
            "reserved e2e-smoke tool partition is touched."
        ),
    )
    parser.add_argument(
        "--ambient-credentials",
        action="store_true",
        help=(
            "Build the boto3 session from the ambient/default credential chain (the CI "
            "OIDC-assumed role) instead of the env-named AWS profile. Required in CI, "
            "where no sandbox/prod/qa named profile exists."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the consumer verifier."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        accounts_data = e2e_common.load_json(_ACCOUNTS_JSON_PATH)
        domains_data = e2e_common.load_json(_DOMAINS_JSON_PATH)
        manifest_path = pathlib.Path(args.manifest)
        if not manifest_path.exists():
            raise e2e_common.E2EUsageError(f"ERROR: manifest not found: {args.manifest}")
        manifest = json.loads(manifest_path.read_text())
    except e2e_common.E2EUsageError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    try:
        import boto3 as boto3_module
    except ImportError as exc:
        print(f"ERROR: boto3 is not available: {exc}. Run 'uv sync'.", file=sys.stderr)
        sys.exit(1)

    try:
        verifier = E2EVerifier(
            env=args.env,
            manifest=manifest,
            accounts_data=accounts_data,
            domains_data=domains_data,
            boto3_module=boto3_module,
            output_path=args.output,
            cleanup=args.cleanup,
            ambient_credentials=args.ambient_credentials,
        )
    except e2e_common.E2EUsageError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    sys.exit(verifier.run())


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
