"""Performance-test report: pull CloudWatch + client metrics, render one HTML report.

Invoked as::

    uv run python -m scripts.perf_report --env sandbox \\
        --results perf-results-sandbox.json \\
        --output perf-report-sandbox.html \\
        --evidence-out perf-evidence-sandbox.json
    make perf-report ENV=sandbox

Consumes the results JSON written by ``scripts.perf_loadgen`` (the
client-side half: request counts, latency percentiles, throughput/error
time series) and pulls the server-side half over the same run window from
CloudWatch: ECS (collector task CPU/memory), the internal ALB in front of
the collector, the CloudFront WAFv2 WebACL, the telemetry Firehose delivery
stream, and the data-lake ``cwl_split`` reshape Lambda. Every metric
dimension (cluster/service/ALB/WebACL/stream/function name) is INPUT-DRIVEN:
either passed explicitly as a CLI override, or discovered by listing +
filtering (never a hardcoded literal) the same way
``scripts.e2e_verify``'s discovery helpers already do -- deployed resource
names in this repo are namespace-derived (see
``scripts/constants.py``'s ``E2E_GLUE_REQUIRED_COLUMNS`` docstring and the
"namespace-derived names" design law), so matching by a fixed literal name
is never correct. A resource that cannot be resolved (no override, no
unambiguous discovery match) has its metrics SKIPPED -- clearly noted in
the HTML and evidence JSON -- rather than failing the whole report.

Renders one self-contained HTML file: every chart is a matplotlib PNG,
base64-embedded as a data URI, so the report has no external asset
dependency (opens standalone, no network, no CDN). A parallel JSON evidence
summary carries the same headline numbers plus pass/fail verdicts against
caller-supplied thresholds (every threshold is an optional CLI input with
no built-in default value -- an omitted threshold is simply not evaluated).

Exit codes::

    0 -- report generated; every evaluated threshold passed (or none were given)
    1 -- report generated; at least one evaluated threshold failed
    2 -- usage error (bad ENV / CLI value / results file not found)
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime
import html
import io
import json
import os
import pathlib
import sys
from typing import Any

from scripts import e2e_common

# ---------------------------------------------------------------------------
# Resource discovery (namespace-agnostic: list/describe/filter, never a
# hardcoded literal name -- mirrors scripts.e2e_verify's discovery helpers)
# ---------------------------------------------------------------------------


def discover_ecs(ecs_client: Any, env: str) -> tuple[str, str] | None:
    """Discover the collector's ECS cluster + service names.

    Lists every cluster, prefers one whose name contains ``env``, then lists
    that cluster's services and prefers one whose name contains ``"adot"``
    (the collector task's real service-name convention). Falls back to the
    sole cluster/service when there is exactly one and no ``env``/``"adot"``
    match narrows it further -- otherwise returns None so the caller skips
    ECS metrics rather than guessing.

    Args:
        ecs_client: A boto3 ECS client.
        env: The environment name (sandbox, prod, qa), used as a name filter.

    Returns:
        ``(cluster_name, service_name)``, or None when neither can be
        resolved unambiguously.
    """
    cluster_arns = _paginate(ecs_client.list_clusters, "clusterArns", "nextToken")
    if not cluster_arns:
        return None
    clusters = ecs_client.describe_clusters(clusters=cluster_arns).get("clusters", [])
    cluster_name = _pick_one(clusters, "clusterName", env)
    if cluster_name is None:
        return None

    service_arns = _paginate(
        ecs_client.list_services, "serviceArns", "nextToken", cluster=cluster_name
    )
    if not service_arns:
        return None
    services = ecs_client.describe_services(cluster=cluster_name, services=service_arns).get(
        "services", []
    )
    service_name = _pick_one(services, "serviceName", "adot")
    if service_name is None:
        return None
    return cluster_name, service_name


def discover_alb_arn_suffix(elbv2_client: Any) -> str | None:
    """Discover the collector's ALB CloudWatch dimension value.

    The deployed ALB name is a truncated-and-hashed namespace fragment (see
    ``providers/aws/references/collector-ingestion``), so it cannot be
    reliably matched by an env/keyword substring. This only auto-resolves
    when the account/region has EXACTLY ONE Application Load Balancer
    (expected for this stack's dedicated per-env account) -- otherwise it
    returns None and the caller must pass ``--alb-arn-suffix`` explicitly
    rather than guess.

    Args:
        elbv2_client: A boto3 ELBv2 client.

    Returns:
        The ``LoadBalancer`` dimension value (``app/<name>/<id>``), or None.
    """
    load_balancers = _paginate(
        elbv2_client.describe_load_balancers, "LoadBalancers", "NextMarker", marker_kwarg="Marker"
    )
    if len(load_balancers) != 1:
        return None
    arn = load_balancers[0].get("LoadBalancerArn", "")
    marker = "loadbalancer/"
    idx = arn.find(marker)
    return arn[idx + len(marker) :] if idx != -1 else None


def discover_waf_web_acl(wafv2_client: Any) -> str | None:
    """Discover the collector CloudFront distribution's WAFv2 WebACL name.

    Lists CLOUDFRONT-scope WebACLs and prefers one whose name contains both
    ``"adot"`` and ``"waf"`` (the deployed convention,
    ``"<namespace>-adot-waf"``); falls back to the sole WebACL when exactly
    one exists.

    Args:
        wafv2_client: A boto3 WAFV2 client (must be built for us-east-1;
            CLOUDFRONT-scope WebACLs are only visible there).

    Returns:
        The WebACL ``Name`` dimension value, or None when unresolved.
    """
    acls = wafv2_client.list_web_acls(Scope="CLOUDFRONT").get("WebACLs", [])
    name = next(
        (a["Name"] for a in acls if "adot" in a.get("Name", "") and "waf" in a.get("Name", "")),
        None,
    )
    if name is None and len(acls) == 1:
        name = acls[0].get("Name")
    return name


def discover_lambda_function(lambda_client: Any, env: str) -> str | None:
    """Discover the data-lake ``cwl_split`` reshape Lambda's function name.

    Mirrors ``e2e_common``/``e2e_verify``'s two-step discovery convention:
    prefer a function whose name contains both ``"cwl-split"`` and ``env``,
    falling back to the first function containing ``"cwl-split"``.

    Args:
        lambda_client: A boto3 Lambda client.
        env: The environment name (sandbox, prod, qa), used as a name filter.

    Returns:
        The function name, or None when unresolved.
    """
    names = [f.get("FunctionName", "") for f in lambda_client.list_functions().get("Functions", [])]
    return next(
        (n for n in names if "cwl-split" in n and env in n),
        next((n for n in names if "cwl-split" in n), None),
    )


def _pick_one(items: list[dict[str, Any]], key: str, substring: str) -> str | None:
    """Prefer an item whose ``key`` contains ``substring``; else the sole item."""
    matched = next((item[key] for item in items if substring in item.get(key, "")), None)
    if matched is not None:
        return str(matched)
    if len(items) == 1:
        return str(items[0][key])
    return None


def _paginate(
    operation: Any,
    items_key: str,
    token_key: str,
    marker_kwarg: str | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Follow a boto3 list operation's pagination token to collect every item."""
    items: list[Any] = []
    token: str | None = None
    while True:
        call_kwargs = dict(kwargs)
        if token:
            call_kwargs[marker_kwarg or token_key] = token
        response = operation(**call_kwargs)
        items.extend(response.get(items_key, []))
        token = response.get(token_key)
        if not token:
            return items


# ---------------------------------------------------------------------------
# Metric query construction + pull (batched via GetMetricData)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MetricQuery:
    """One CloudWatch ``GetMetricData`` query.

    Attributes:
        query_id: A ``[a-z][a-zA-Z0-9_]*`` id, unique within one request.
        chart: The chart this series contributes a line to.
        series_label: The legend label for this series within its chart.
        namespace: The CloudWatch namespace (e.g. ``AWS/ECS``).
        metric_name: The CloudWatch metric name.
        dimensions: The metric's dimensions.
        stat: The statistic or extended percentile (e.g. ``Average``, ``p95``).
    """

    query_id: str
    chart: str
    series_label: str
    namespace: str
    metric_name: str
    dimensions: list[dict[str, str]]
    stat: str


@dataclasses.dataclass(frozen=True)
class ResolvedResources:
    """The dimension values resolved (by override or discovery) for one report run."""

    ecs_cluster_name: str | None
    ecs_service_name: str | None
    alb_arn_suffix: str | None
    waf_web_acl_name: str | None
    waf_web_acl_rule: str
    waf_web_acl_region: str
    firehose_stream_name: str | None
    lambda_function_name: str | None


def build_metric_queries(resolved: ResolvedResources) -> list[MetricQuery]:
    """Build every ``MetricQuery`` for the resource groups that resolved.

    A resource group whose dimension(s) could not be resolved contributes no
    queries -- its charts render as "not resolved" rather than the report
    failing outright.

    Args:
        resolved: The resolved (or unresolved -- None) dimension values.

    Returns:
        The full list of queries to batch into ``GetMetricData``.
    """
    queries: list[MetricQuery] = []

    if resolved.ecs_cluster_name and resolved.ecs_service_name:
        ecs_dims = [
            {"Name": "ClusterName", "Value": resolved.ecs_cluster_name},
            {"Name": "ServiceName", "Value": resolved.ecs_service_name},
        ]
        queries += [
            MetricQuery(
                "ecscpu",
                "ECS CPU / Memory Utilization (%)",
                "CPUUtilization",
                "AWS/ECS",
                "CPUUtilization",
                ecs_dims,
                "Average",
            ),
            MetricQuery(
                "ecsmem",
                "ECS CPU / Memory Utilization (%)",
                "MemoryUtilization",
                "AWS/ECS",
                "MemoryUtilization",
                ecs_dims,
                "Average",
            ),
        ]

    if resolved.alb_arn_suffix:
        alb_dims = [{"Name": "LoadBalancer", "Value": resolved.alb_arn_suffix}]
        queries += [
            MetricQuery(
                "albp95",
                "ALB Target Response Time p95 (ms)",
                "TargetResponseTime p95",
                "AWS/ApplicationELB",
                "TargetResponseTime",
                alb_dims,
                "p95",
            ),
            MetricQuery(
                "albreq",
                "ALB Request Count & 5XX Count",
                "RequestCount",
                "AWS/ApplicationELB",
                "RequestCount",
                alb_dims,
                "Sum",
            ),
            MetricQuery(
                "alb5xx",
                "ALB Request Count & 5XX Count",
                "HTTPCode_Target_5XX_Count",
                "AWS/ApplicationELB",
                "HTTPCode_Target_5XX_Count",
                alb_dims,
                "Sum",
            ),
            MetricQuery(
                "albact",
                "ALB Connections (Active / Rejected)",
                "ActiveConnectionCount",
                "AWS/ApplicationELB",
                "ActiveConnectionCount",
                alb_dims,
                "Sum",
            ),
            MetricQuery(
                "albrej",
                "ALB Connections (Active / Rejected)",
                "RejectedConnectionCount",
                "AWS/ApplicationELB",
                "RejectedConnectionCount",
                alb_dims,
                "Sum",
            ),
        ]

    if resolved.waf_web_acl_name:
        waf_dims = [
            {"Name": "WebACL", "Value": resolved.waf_web_acl_name},
            {"Name": "Rule", "Value": resolved.waf_web_acl_rule},
            {"Name": "Region", "Value": resolved.waf_web_acl_region},
        ]
        queries += [
            MetricQuery(
                "wafallow",
                "WAF Allowed / Blocked Requests",
                "AllowedRequests",
                "AWS/WAFV2",
                "AllowedRequests",
                waf_dims,
                "Sum",
            ),
            MetricQuery(
                "wafblock",
                "WAF Allowed / Blocked Requests",
                "BlockedRequests",
                "AWS/WAFV2",
                "BlockedRequests",
                waf_dims,
                "Sum",
            ),
        ]

    if resolved.firehose_stream_name:
        fh_dims = [{"Name": "DeliveryStreamName", "Value": resolved.firehose_stream_name}]
        queries += [
            MetricQuery(
                "fhin",
                "Firehose Incoming / Throttled / Delivered Records",
                "IncomingRecords",
                "AWS/Firehose",
                "IncomingRecords",
                fh_dims,
                "Sum",
            ),
            MetricQuery(
                "fhthr",
                "Firehose Incoming / Throttled / Delivered Records",
                "ThrottledRecords",
                "AWS/Firehose",
                "ThrottledRecords",
                fh_dims,
                "Sum",
            ),
            MetricQuery(
                "fhdel",
                "Firehose Incoming / Throttled / Delivered Records",
                "DeliveryToS3.Records",
                "AWS/Firehose",
                "DeliveryToS3.Records",
                fh_dims,
                "Sum",
            ),
            MetricQuery(
                "fhfresh",
                "Firehose Delivery Freshness (s)",
                "DeliveryToS3.DataFreshness",
                "AWS/Firehose",
                "DeliveryToS3.DataFreshness",
                fh_dims,
                "Average",
            ),
        ]

    if resolved.lambda_function_name:
        fn_dims = [{"Name": "FunctionName", "Value": resolved.lambda_function_name}]
        queries += [
            MetricQuery(
                "lamdur",
                "Lambda Duration (ms)",
                "Duration",
                "AWS/Lambda",
                "Duration",
                fn_dims,
                "Average",
            ),
            MetricQuery(
                "lamerr",
                "Lambda Errors / Throttles / Concurrent Executions",
                "Errors",
                "AWS/Lambda",
                "Errors",
                fn_dims,
                "Sum",
            ),
            MetricQuery(
                "lamthr",
                "Lambda Errors / Throttles / Concurrent Executions",
                "Throttles",
                "AWS/Lambda",
                "Throttles",
                fn_dims,
                "Sum",
            ),
            MetricQuery(
                "lamcon",
                "Lambda Errors / Throttles / Concurrent Executions",
                "ConcurrentExecutions",
                "AWS/Lambda",
                "ConcurrentExecutions",
                fn_dims,
                "Maximum",
            ),
        ]

    return queries


@dataclasses.dataclass(frozen=True)
class MetricSeries:
    """One pulled CloudWatch time series, x-axis normalized to the window."""

    chart: str
    series_label: str
    elapsed_seconds: list[float]
    values: list[float]

    @property
    def total(self) -> float:
        return sum(self.values)


def pull_metric_series(
    cloudwatch_client: Any,
    queries: list[MetricQuery],
    window_start: datetime.datetime,
    window_end: datetime.datetime,
    period_seconds: int,
) -> dict[str, MetricSeries]:
    """Pull every query's time series in one batched ``GetMetricData`` call.

    Args:
        cloudwatch_client: A boto3 CloudWatch client.
        queries: The queries to batch (empty is valid -- returns ``{}``).
        window_start: The window start (aware UTC datetime).
        window_end: The window end (aware UTC datetime).
        period_seconds: The CloudWatch aggregation period, in seconds.

    Returns:
        ``{query_id: MetricSeries}`` for every query, x-axis values expressed
        as elapsed seconds since ``window_start`` (ascending).
    """
    if not queries:
        return {}
    metric_data_queries = [
        {
            "Id": query.query_id,
            "Label": query.series_label,
            "MetricStat": {
                "Metric": {
                    "Namespace": query.namespace,
                    "MetricName": query.metric_name,
                    "Dimensions": query.dimensions,
                },
                "Period": period_seconds,
                "Stat": query.stat,
            },
        }
        for query in queries
    ]
    by_id = {query.query_id: query for query in queries}
    results: dict[str, MetricSeries] = {}
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "MetricDataQueries": metric_data_queries,
            "StartTime": window_start,
            "EndTime": window_end,
            "ScanBy": "TimestampAscending",
        }
        if next_token:
            kwargs["NextToken"] = next_token
        response = cloudwatch_client.get_metric_data(**kwargs)
        for entry in response.get("MetricDataResults", []):
            query = by_id[entry["Id"]]
            timestamps = entry.get("Timestamps", [])
            values = entry.get("Values", [])
            elapsed = [(ts - window_start).total_seconds() for ts in timestamps]
            existing = results.get(entry["Id"])
            if existing is None:
                results[entry["Id"]] = MetricSeries(
                    query.chart, query.series_label, elapsed, values
                )
            else:
                results[entry["Id"]] = MetricSeries(
                    query.chart,
                    query.series_label,
                    existing.elapsed_seconds + elapsed,
                    existing.values + values,
                )
        next_token = response.get("NextToken")
        if not next_token:
            return results


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ThresholdCheck:
    """One caller-supplied pass/fail gate against a headline number.

    ``threshold`` is ``None`` when the caller did not supply that CLI flag --
    the check is then reported ``skipped`` and never fails the run.
    """

    name: str
    observed: float
    threshold: float | None

    @property
    def status(self) -> str:
        if self.threshold is None:
            return "skipped"
        return "pass" if self.observed <= self.threshold else "fail"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observed": self.observed,
            "threshold": self.threshold,
            "status": self.status,
        }


def build_headline(
    results: dict[str, Any], metric_series: dict[str, MetricSeries]
) -> dict[str, float]:
    """Compute the headline numbers threshold checks + the evidence summary use.

    Args:
        results: The parsed ``perf_loadgen`` results JSON.
        metric_series: The pulled CloudWatch series, keyed by query id.

    Returns:
        A flat ``{name: value}`` dict of headline numbers. A CloudWatch-backed
        number is ``0.0`` when its resource never resolved (no series pulled)
        -- distinguished from a genuine zero-valued metric in the evidence
        JSON's ``resource_resolution`` section, not here.
    """
    totals = results.get("totals", {})
    latency_ms = results.get("latency_ms", {})
    return {
        "client_total_requests": float(totals.get("total_requests", 0)),
        "client_error_rate": float(totals.get("error_rate", 0.0)),
        "client_p50_latency_ms": float(latency_ms.get("p50", 0.0)),
        "client_p90_latency_ms": float(latency_ms.get("p90", 0.0)),
        "client_p99_latency_ms": float(latency_ms.get("p99", 0.0)),
        "alb_5xx_count": metric_series["alb5xx"].total if "alb5xx" in metric_series else 0.0,
        "alb_rejected_connections": (
            metric_series["albrej"].total if "albrej" in metric_series else 0.0
        ),
        "waf_blocked_requests": (
            metric_series["wafblock"].total if "wafblock" in metric_series else 0.0
        ),
        "firehose_throttled_records": (
            metric_series["fhthr"].total if "fhthr" in metric_series else 0.0
        ),
        "lambda_errors": metric_series["lamerr"].total if "lamerr" in metric_series else 0.0,
    }


def evaluate_thresholds(
    args: argparse.Namespace, headline: dict[str, float]
) -> list[ThresholdCheck]:
    """Build every configured ``ThresholdCheck`` from CLI inputs + headline numbers."""
    return [
        ThresholdCheck(
            "client_error_rate", headline["client_error_rate"], args.threshold_error_rate
        ),
        ThresholdCheck(
            "client_p99_latency_ms",
            headline["client_p99_latency_ms"],
            args.threshold_p99_latency_ms,
        ),
        ThresholdCheck("alb_5xx_count", headline["alb_5xx_count"], args.threshold_alb_5xx),
        ThresholdCheck(
            "waf_blocked_requests",
            headline["waf_blocked_requests"],
            args.threshold_waf_blocked_requests,
        ),
        ThresholdCheck(
            "firehose_throttled_records",
            headline["firehose_throttled_records"],
            args.threshold_firehose_throttled_records,
        ),
        ThresholdCheck("lambda_errors", headline["lambda_errors"], args.threshold_lambda_errors),
    ]


# ---------------------------------------------------------------------------
# Chart rendering (matplotlib, headless Agg backend -> embedded PNG)
# ---------------------------------------------------------------------------


def render_line_chart(
    title: str,
    x_label: str,
    y_label: str,
    series: dict[str, tuple[list[float], list[float]]],
) -> bytes:
    """Render one multi-series line chart to PNG bytes (headless).

    Args:
        title: The chart title.
        x_label: The x-axis label.
        y_label: The y-axis label.
        series: ``{series_label: (x_values, y_values)}``. A series with no
            x-values is skipped; an empty ``series`` (or all-empty series)
            renders a "no data" placeholder instead of empty axes.

    Returns:
        PNG-encoded image bytes.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(figsize=(9, 3.5), dpi=110)
    has_data = any(x_values for x_values, _ in series.values())
    if has_data:
        for series_label, (x_values, y_values) in series.items():
            if x_values:
                axes.plot(
                    x_values, y_values, label=series_label, linewidth=1.5, marker="o", markersize=2
                )
        axes.legend(loc="upper right", fontsize=8)
    else:
        axes.text(
            0.5, 0.5, "no data", ha="center", va="center", transform=axes.transAxes, fontsize=10
        )
    axes.set_title(title, fontsize=11)
    axes.set_xlabel(x_label, fontsize=9)
    axes.set_ylabel(y_label, fontsize=9)
    axes.grid(True, alpha=0.3)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    plt.close(figure)
    return buffer.getvalue()


#: (chart_title, x_label, y_label) for every client-side (loadgen bucket) chart.
_CLIENT_CHARTS: tuple[tuple[str, str, str], ...] = (
    ("Client Throughput", "elapsed seconds", "per second"),
    ("Client Latency Percentiles (ms)", "elapsed seconds", "milliseconds"),
    ("Client Error Rate", "elapsed seconds", "error rate"),
)


def build_client_chart_series(
    results: dict[str, Any],
) -> dict[str, dict[str, tuple[list[float], list[float]]]]:
    """Derive the three client-side chart series dicts from loadgen buckets.

    Args:
        results: The parsed ``perf_loadgen`` results JSON.

    Returns:
        ``{chart_title: {series_label: (x_values, y_values)}}`` for exactly
        the three chart titles in ``_CLIENT_CHARTS``.
    """
    buckets = results.get("buckets", [])
    x_values = [float(b["bucket_start_seconds"]) for b in buckets]
    return {
        "Client Throughput": {
            "requests/s": (x_values, [float(b["requests_per_second"]) for b in buckets]),
            "records/s": (x_values, [float(b["records_per_second"]) for b in buckets]),
        },
        "Client Latency Percentiles (ms)": {
            "p50": (x_values, [float(b["latency_ms"]["p50"]) for b in buckets]),
            "p90": (x_values, [float(b["latency_ms"]["p90"]) for b in buckets]),
            "p99": (x_values, [float(b["latency_ms"]["p99"]) for b in buckets]),
        },
        "Client Error Rate": {
            "error rate": (x_values, [float(b["error_rate"]) for b in buckets]),
        },
    }


def build_cloudwatch_chart_series(
    metric_series: dict[str, MetricSeries],
) -> dict[str, dict[str, tuple[list[float], list[float]]]]:
    """Group pulled CloudWatch series by their chart title.

    Args:
        metric_series: The pulled series, keyed by query id.

    Returns:
        ``{chart_title: {series_label: (x_values, y_values)}}``.
    """
    grouped: dict[str, dict[str, tuple[list[float], list[float]]]] = {}
    for series in metric_series.values():
        grouped.setdefault(series.chart, {})[series.series_label] = (
            series.elapsed_seconds,
            series.values,
        )
    return grouped


#: Every CloudWatch-backed chart, in report order, with its axis labels.
_CLOUDWATCH_CHARTS: tuple[tuple[str, str, str], ...] = (
    ("ECS CPU / Memory Utilization (%)", "elapsed seconds", "percent"),
    ("ALB Target Response Time p95 (ms)", "elapsed seconds", "milliseconds"),
    ("ALB Request Count & 5XX Count", "elapsed seconds", "count"),
    ("ALB Connections (Active / Rejected)", "elapsed seconds", "count"),
    ("WAF Allowed / Blocked Requests", "elapsed seconds", "count"),
    ("Firehose Incoming / Throttled / Delivered Records", "elapsed seconds", "count"),
    ("Firehose Delivery Freshness (s)", "elapsed seconds", "seconds"),
    ("Lambda Duration (ms)", "elapsed seconds", "milliseconds"),
    ("Lambda Errors / Throttles / Concurrent Executions", "elapsed seconds", "count"),
)


def render_all_charts(
    results: dict[str, Any], metric_series: dict[str, MetricSeries]
) -> list[tuple[str, bytes]]:
    """Render every client + CloudWatch chart, in a fixed, documented order.

    Args:
        results: The parsed ``perf_loadgen`` results JSON.
        metric_series: The pulled CloudWatch series, keyed by query id.

    Returns:
        ``[(chart_title, png_bytes), ...]`` in report order.
    """
    client_series = build_client_chart_series(results)
    cloudwatch_series = build_cloudwatch_chart_series(metric_series)
    charts: list[tuple[str, bytes]] = []
    for title, x_label, y_label in _CLIENT_CHARTS:
        charts.append((title, render_line_chart(title, x_label, y_label, client_series[title])))
    for title, x_label, y_label in _CLOUDWATCH_CHARTS:
        charts.append(
            (title, render_line_chart(title, x_label, y_label, cloudwatch_series.get(title, {})))
        )
    return charts


# ---------------------------------------------------------------------------
# HTML assembly (one self-contained file: no external assets)
# ---------------------------------------------------------------------------


def render_html_report(
    env: str,
    run_id: str,
    generated_at: str,
    window_start: str,
    window_end: str,
    headline: dict[str, float],
    thresholds: list[ThresholdCheck],
    resource_resolution: dict[str, str],
    charts: list[tuple[str, bytes]],
) -> str:
    """Assemble one self-contained HTML report: headline + thresholds + charts.

    Args:
        env: The target environment.
        run_id: The load-generator run id.
        generated_at: ISO-8601 report generation time.
        window_start: ISO-8601 CloudWatch window start.
        window_end: ISO-8601 CloudWatch window end.
        headline: The flat headline-number dict (``build_headline``).
        thresholds: The evaluated threshold checks.
        resource_resolution: ``{resource_group: detail}`` resolution notes.
        charts: ``[(chart_title, png_bytes), ...]``.

    Returns:
        The complete HTML document as a string.
    """
    overall = "FAIL" if any(t.status == "fail" for t in thresholds) else "PASS"
    headline_rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{value:.4f}</td></tr>"
        for name, value in sorted(headline.items())
    )
    threshold_rows = "".join(
        f"<tr><td>{html.escape(t.name)}</td><td>{t.observed:.4f}</td>"
        f"<td>{'-' if t.threshold is None else f'{t.threshold:.4f}'}</td>"
        f"<td>{html.escape(t.status)}</td></tr>"
        for t in thresholds
    )
    resolution_rows = "".join(
        f"<tr><td>{html.escape(group)}</td><td>{html.escape(detail)}</td></tr>"
        for group, detail in sorted(resource_resolution.items())
    )
    chart_sections = "".join(
        f'<section class="chart"><h2>{html.escape(title)}</h2>'
        f'<img alt="{html.escape(title)}" '
        f'src="data:image/png;base64,{base64.b64encode(png).decode("ascii")}"></section>'
        for title, png in charts
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Telemetry Collector Performance Report -- {html.escape(env)}</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 2rem; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1rem; margin: 0 0 0.5rem 0; }}
  table {{ border-collapse: collapse; margin-bottom: 1.5rem; }}
  td, th {{ border: 1px solid #ccc; padding: 0.35rem 0.75rem; text-align: left; }}
  td, th {{ font-size: 0.9rem; }}
  section.chart {{ margin-bottom: 1.5rem; }}
  section.chart img {{ max-width: 100%; border: 1px solid #ddd; }}
  .result-pass {{ color: #0a7a2f; font-weight: bold; }}
  .result-fail {{ color: #b3261e; font-weight: bold; }}
</style>
</head>
<body>
<h1>Telemetry Collector Performance Report</h1>
<p>env={html.escape(env)} run_id={html.escape(run_id)} generated_at={html.escape(generated_at)}</p>
<p>CloudWatch window: {html.escape(window_start)} .. {html.escape(window_end)}</p>
<p>Overall result: <span class="result-{overall.lower()}">{overall}</span></p>

<section id="headline">
<h2>Headline numbers</h2>
<table><tr><th>metric</th><th>value</th></tr>{headline_rows}</table>
</section>

<section id="thresholds">
<h2>Threshold checks</h2>
<table><tr><th>name</th><th>observed</th><th>threshold</th><th>status</th></tr>{threshold_rows}</table>
</section>

<section id="resource-resolution">
<h2>Resource resolution</h2>
<table><tr><th>resource group</th><th>detail</th></tr>{resolution_rows}</table>
</section>

{chart_sections}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the performance report CLI."""
    parser = argparse.ArgumentParser(
        prog="scripts.perf_report",
        description=(
            "Pull CloudWatch + client metrics over a perf_loadgen run window and "
            "render one self-contained graphical HTML report."
        ),
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment to report on (resolves account/profile from accounts.json).",
    )
    parser.add_argument(
        "--results",
        required=True,
        help="Path to the results JSON written by scripts.perf_loadgen (--results-out).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the self-contained HTML report.",
    )
    parser.add_argument(
        "--evidence-out",
        dest="evidence_out",
        default=None,
        help="Path to write the JSON evidence summary (default: stdout only).",
    )
    parser.add_argument(
        "--window-start",
        dest="window_start",
        default=None,
        help="ISO-8601 CloudWatch window start (default: derived from --results).",
    )
    parser.add_argument(
        "--window-end",
        dest="window_end",
        default=None,
        help="ISO-8601 CloudWatch window end (default: derived from --results).",
    )
    parser.add_argument(
        "--period-seconds",
        dest="period_seconds",
        type=int,
        default=60,
        help="CloudWatch GetMetricData aggregation period, in seconds (default: 60).",
    )
    parser.add_argument(
        "--ambient-credentials",
        dest="ambient_credentials",
        action="store_true",
        help=(
            "Build the boto3 session from the ambient/default credential chain (the CI "
            "OIDC-assumed role) instead of the env-named AWS profile."
        ),
    )
    parser.add_argument(
        "--aws-region",
        dest="aws_region",
        default=None,
        help="AWS region for the boto3 session (default: $AWS_DEFAULT_REGION).",
    )
    parser.add_argument(
        "--ecs-cluster-name",
        dest="ecs_cluster_name",
        default=None,
        help="Override the ECS cluster name (default: discovered).",
    )
    parser.add_argument(
        "--ecs-service-name",
        dest="ecs_service_name",
        default=None,
        help="Override the ECS service name (default: discovered).",
    )
    parser.add_argument(
        "--alb-arn-suffix",
        dest="alb_arn_suffix",
        default=None,
        help="Override the ALB CloudWatch dimension value app/<name>/<id> (default: discovered).",
    )
    parser.add_argument(
        "--waf-web-acl-name",
        dest="waf_web_acl_name",
        default=None,
        help="Override the WAFv2 WebACL name (default: discovered).",
    )
    parser.add_argument(
        "--waf-web-acl-rule",
        dest="waf_web_acl_rule",
        default="ALL",
        help='WAFv2 Rule dimension value (default: "ALL", the AWS aggregate-all-rules value).',
    )
    parser.add_argument(
        "--waf-web-acl-region",
        dest="waf_web_acl_region",
        default=None,
        help="WAFv2 Region dimension value (default: --aws-region; matches the IaC's own "
        "observability Region dimension binding).",
    )
    parser.add_argument(
        "--firehose-stream-name",
        dest="firehose_stream_name",
        default=None,
        help="Override the Firehose delivery stream name (default: discovered).",
    )
    parser.add_argument(
        "--lambda-function-name",
        dest="lambda_function_name",
        default=None,
        help="Override the cwl_split reshape Lambda function name (default: discovered).",
    )
    parser.add_argument(
        "--threshold-error-rate",
        dest="threshold_error_rate",
        type=float,
        default=None,
        help="Max acceptable client error rate (default: not evaluated).",
    )
    parser.add_argument(
        "--threshold-p99-latency-ms",
        dest="threshold_p99_latency_ms",
        type=float,
        default=None,
        help="Max acceptable client p99 latency in ms (default: not evaluated).",
    )
    parser.add_argument(
        "--threshold-alb-5xx",
        dest="threshold_alb_5xx",
        type=float,
        default=None,
        help="Max acceptable summed ALB 5XX count over the window (default: not evaluated).",
    )
    parser.add_argument(
        "--threshold-waf-blocked-requests",
        dest="threshold_waf_blocked_requests",
        type=float,
        default=None,
        help="Max acceptable summed WAF blocked-request count (default: not evaluated).",
    )
    parser.add_argument(
        "--threshold-firehose-throttled-records",
        dest="threshold_firehose_throttled_records",
        type=float,
        default=None,
        help="Max acceptable summed Firehose throttled-record count (default: not evaluated).",
    )
    parser.add_argument(
        "--threshold-lambda-errors",
        dest="threshold_lambda_errors",
        type=float,
        default=None,
        help="Max acceptable summed cwl_split Lambda error count (default: not evaluated).",
    )
    return parser


def _resolve_region(args: argparse.Namespace) -> str:
    """Resolve the AWS region from --aws-region or $AWS_DEFAULT_REGION."""
    if args.aws_region:
        return str(args.aws_region)
    region = os.environ.get("AWS_DEFAULT_REGION", "").strip()
    if not region:
        raise e2e_common.E2EUsageError(
            "ERROR: --aws-region was not given and AWS_DEFAULT_REGION is not set. "
            "Set one of them to the target region (e.g. us-east-1)."
        )
    return region


def _client_factory(
    boto3_module: Any, env: str, accounts_data: dict[str, Any], ambient_credentials: bool
) -> Any:
    """Return a ``service_name -> boto3 client`` factory bound to one session."""
    if ambient_credentials:
        session = e2e_common.build_ambient_session(boto3_module)
    else:
        _account_id, profile = e2e_common.resolve_account(env, accounts_data)
        session = e2e_common.build_session(boto3_module, profile)
    return session.client


def resolve_resources(args: argparse.Namespace, client: Any, region: str) -> ResolvedResources:
    """Resolve every metric-dimension value via CLI override or discovery.

    Args:
        args: The parsed CLI namespace.
        client: A ``service_name -> boto3 client`` factory (see ``_client_factory``).
        region: The resolved AWS region (used as the WAF Region dimension default).

    Returns:
        The resolved (or unresolved -- None) dimension values.
    """
    ecs_cluster_name = args.ecs_cluster_name
    ecs_service_name = args.ecs_service_name
    if not (ecs_cluster_name and ecs_service_name):
        discovered = discover_ecs(client("ecs"), args.env)
        if discovered is not None:
            ecs_cluster_name = ecs_cluster_name or discovered[0]
            ecs_service_name = ecs_service_name or discovered[1]

    alb_arn_suffix = args.alb_arn_suffix or discover_alb_arn_suffix(client("elbv2"))

    # CLOUDFRONT-scope WebACLs (and their CloudWatch metrics) are only visible
    # to a WAFV2 client built for the SAME region --waf-web-acl-region
    # resolves to below (input-driven, default = the resolved AWS region --
    # NOT a hardcoded literal): both the discovery client and the metric
    # dimension must agree on one region.
    waf_region = args.waf_web_acl_region or region
    waf_web_acl_name = args.waf_web_acl_name
    if not waf_web_acl_name:
        waf_web_acl_name = discover_waf_web_acl(client("wafv2", region_name=waf_region))

    firehose_stream_name = args.firehose_stream_name or (
        (e2e_common.discover_firehose_stream(client("firehose"), args.env) or {}).get("stream")
    )

    lambda_function_name = args.lambda_function_name or discover_lambda_function(
        client("lambda"), args.env
    )

    return ResolvedResources(
        ecs_cluster_name=ecs_cluster_name,
        ecs_service_name=ecs_service_name,
        alb_arn_suffix=alb_arn_suffix,
        waf_web_acl_name=waf_web_acl_name,
        waf_web_acl_rule=args.waf_web_acl_rule,
        waf_web_acl_region=waf_region,
        firehose_stream_name=firehose_stream_name,
        lambda_function_name=lambda_function_name,
    )


def resource_resolution_notes(resolved: ResolvedResources) -> dict[str, str]:
    """Return a human-readable resolution note per resource group."""

    def _note(label: str, *values: str | None) -> str:
        if all(values):
            return f"resolved: {', '.join(str(v) for v in values)}"
        return f"NOT resolved -- pass an explicit override to include {label} metrics"

    return {
        "ecs": _note("ECS", resolved.ecs_cluster_name, resolved.ecs_service_name),
        "alb": _note("ALB", resolved.alb_arn_suffix),
        "waf": _note("WAF", resolved.waf_web_acl_name),
        "firehose": _note("Firehose", resolved.firehose_stream_name),
        "lambda": _note("Lambda", resolved.lambda_function_name),
    }


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the performance report."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        results_path = pathlib.Path(args.results)
        if not results_path.exists():
            raise e2e_common.E2EUsageError(f"ERROR: results file not found: {args.results}")
        results = json.loads(results_path.read_text())

        window_start_iso = args.window_start
        window_end_iso = args.window_end
        if not (window_start_iso and window_end_iso):
            window_start, window_end = e2e_common.metric_window(
                results["started_at"], results["finished_at"]
            )
        else:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            window_start = datetime.datetime.strptime(window_start_iso, fmt).replace(
                tzinfo=datetime.UTC
            )
            window_end = datetime.datetime.strptime(window_end_iso, fmt).replace(
                tzinfo=datetime.UTC
            )

        region = _resolve_region(args)
        accounts_data = e2e_common.load_json(e2e_common.ACCOUNTS_JSON_PATH)
    except (e2e_common.E2EUsageError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"ERROR: {exc}" if not isinstance(exc, e2e_common.E2EUsageError) else str(exc),
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        import boto3 as boto3_module
    except ImportError as exc:
        print(f"ERROR: boto3 is not available: {exc}. Run 'uv sync'.", file=sys.stderr)
        sys.exit(1)

    client = _client_factory(boto3_module, args.env, accounts_data, args.ambient_credentials)
    resolved = resolve_resources(args, client, region)
    queries = build_metric_queries(resolved)
    metric_series = pull_metric_series(
        client("cloudwatch"), queries, window_start, window_end, args.period_seconds
    )

    headline = build_headline(results, metric_series)
    thresholds = evaluate_thresholds(args, headline)
    resolution_notes = resource_resolution_notes(resolved)
    charts = render_all_charts(results, metric_series)
    generated_at = e2e_common.utc_now_iso()

    report_html = render_html_report(
        env=args.env,
        run_id=str(results.get("run_id", "")),
        generated_at=generated_at,
        window_start=window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_end=window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        headline=headline,
        thresholds=thresholds,
        resource_resolution=resolution_notes,
        charts=charts,
    )
    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_html)
    print(f"HTML report written to {args.output}")

    overall = "FAIL" if any(t.status == "fail" for t in thresholds) else "PASS"
    evidence = {
        "env": args.env,
        "run_id": results.get("run_id"),
        "generated_at": generated_at,
        "window_start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "headline": headline,
        "thresholds": [t.as_dict() for t in thresholds],
        "resource_resolution": resolution_notes,
        "html_output": args.output,
        "result": overall,
    }
    if args.evidence_out:
        evidence_path = pathlib.Path(args.evidence_out)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2))
        print(f"evidence written to {args.evidence_out}")
    else:
        print(json.dumps(evidence, indent=2))

    print(f"RESULT {overall}")
    sys.exit(1 if overall == "FAIL" else 0)


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
