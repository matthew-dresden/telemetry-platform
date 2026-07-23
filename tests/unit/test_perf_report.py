"""Unit tests for scripts/perf_report.py.

Every AWS client is a MagicMock returning canned list/describe/get responses;
no network or AWS call is made (mirrors tests/unit/test_otlp_e2e_verify.py's
convention). Chart rendering exercises the real matplotlib Agg backend
(headless, no display) and asserts real PNG output.
"""

from __future__ import annotations

import base64
import datetime
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts import perf_report as pr

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _resolved(
    ecs_cluster_name: str | None = "cluster",
    ecs_service_name: str | None = "service",
    alb_arn_suffix: str | None = "app/alb/abc",
    waf_web_acl_name: str | None = "acl",
    firehose_stream_name: str | None = "stream",
    lambda_function_name: str | None = "fn",
) -> pr.ResolvedResources:
    return pr.ResolvedResources(
        ecs_cluster_name=ecs_cluster_name,
        ecs_service_name=ecs_service_name,
        alb_arn_suffix=alb_arn_suffix,
        waf_web_acl_name=waf_web_acl_name,
        waf_web_acl_rule="ALL",
        waf_web_acl_region="us-east-1",
        firehose_stream_name=firehose_stream_name,
        lambda_function_name=lambda_function_name,
    )


_SAMPLE_RESULTS: dict[str, Any] = {
    "run_id": "run-1",
    "env": "sandbox",
    "started_at": "2026-07-20T00:00:00Z",
    "finished_at": "2026-07-20T00:02:00Z",
    "totals": {
        "total_requests": 100,
        "total_records": 200,
        "total_errors": 1,
        "error_rate": 0.01,
        "status_counts": {"200": 99, "500": 1},
        "by_kind": {"metrics": 60, "log_burst": 40},
    },
    "latency_ms": {
        "p50": 10.0,
        "p90": 20.0,
        "p99": 30.0,
        "min": 1.0,
        "max": 50.0,
        "mean": 12.0,
        "sample_count": 100,
        "samples_seen": 100,
    },
    "buckets": [
        {
            "bucket_start_seconds": 0,
            "requests": 50,
            "records": 100,
            "errors": 1,
            "requests_per_second": 1.0,
            "records_per_second": 2.0,
            "error_rate": 0.02,
            "avg_latency_ms": 11.0,
            "latency_ms": {"p50": 10.0, "p90": 20.0, "p99": 25.0},
            "by_kind": {"metrics": 30, "log_burst": 20},
        },
        {
            "bucket_start_seconds": 60,
            "requests": 50,
            "records": 100,
            "errors": 0,
            "requests_per_second": 1.0,
            "records_per_second": 2.0,
            "error_rate": 0.0,
            "avg_latency_ms": 9.0,
            "latency_ms": {"p50": 8.0, "p90": 15.0, "p99": 22.0},
            "by_kind": {"metrics": 30, "log_burst": 20},
        },
    ],
}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_ecs_prefers_env_and_adot_match() -> None:
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": ["a1", "a2"]}
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterName": "prod-cluster"}, {"clusterName": "sandbox-cluster"}]
    }
    ecs.list_services.return_value = {"serviceArns": ["s1", "s2"]}
    ecs.describe_services.return_value = {
        "services": [{"serviceName": "sandbox-portal"}, {"serviceName": "sandbox-adot"}]
    }
    assert pr.discover_ecs(ecs, "sandbox") == ("sandbox-cluster", "sandbox-adot")


@pytest.mark.unit
def test_discover_ecs_falls_back_to_sole_cluster_and_service() -> None:
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": ["a1"]}
    ecs.describe_clusters.return_value = {"clusters": [{"clusterName": "only-cluster"}]}
    ecs.list_services.return_value = {"serviceArns": ["s1"]}
    ecs.describe_services.return_value = {"services": [{"serviceName": "only-service"}]}
    assert pr.discover_ecs(ecs, "sandbox") == ("only-cluster", "only-service")


@pytest.mark.unit
def test_discover_ecs_no_clusters_returns_none() -> None:
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": []}
    assert pr.discover_ecs(ecs, "sandbox") is None


@pytest.mark.unit
def test_discover_ecs_ambiguous_cluster_returns_none() -> None:
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": ["a1", "a2"]}
    ecs.describe_clusters.return_value = {
        "clusters": [{"clusterName": "prod-cluster"}, {"clusterName": "qa-cluster"}]
    }
    assert pr.discover_ecs(ecs, "sandbox") is None


@pytest.mark.unit
def test_discover_ecs_no_services_returns_none() -> None:
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": ["a1"]}
    ecs.describe_clusters.return_value = {"clusters": [{"clusterName": "sandbox-cluster"}]}
    ecs.list_services.return_value = {"serviceArns": []}
    assert pr.discover_ecs(ecs, "sandbox") is None


@pytest.mark.unit
def test_discover_ecs_ambiguous_service_returns_none() -> None:
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": ["a1"]}
    ecs.describe_clusters.return_value = {"clusters": [{"clusterName": "sandbox-cluster"}]}
    ecs.list_services.return_value = {"serviceArns": ["s1", "s2"]}
    ecs.describe_services.return_value = {
        "services": [{"serviceName": "one"}, {"serviceName": "two"}]
    }
    assert pr.discover_ecs(ecs, "sandbox") is None


@pytest.mark.unit
def test_discover_alb_arn_suffix_sole_alb() -> None:
    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {
        "LoadBalancers": [
            {
                "LoadBalancerArn": (
                    "arn:aws:elasticloadbalancing:us-east-1:1:loadbalancer/app/name/abc123"
                )
            }
        ]
    }
    assert pr.discover_alb_arn_suffix(elbv2) == "app/name/abc123"


@pytest.mark.unit
def test_discover_alb_arn_suffix_ambiguous_returns_none() -> None:
    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {
        "LoadBalancers": [{"LoadBalancerArn": "a"}, {"LoadBalancerArn": "b"}]
    }
    assert pr.discover_alb_arn_suffix(elbv2) is None


@pytest.mark.unit
def test_discover_alb_arn_suffix_none_returns_none() -> None:
    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {"LoadBalancers": []}
    assert pr.discover_alb_arn_suffix(elbv2) is None


@pytest.mark.unit
def test_discover_alb_arn_suffix_malformed_arn_returns_none() -> None:
    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {
        "LoadBalancers": [{"LoadBalancerArn": "not-an-arn"}]
    }
    assert pr.discover_alb_arn_suffix(elbv2) is None


@pytest.mark.unit
def test_discover_waf_web_acl_prefers_adot_waf_match() -> None:
    wafv2 = MagicMock()
    wafv2.list_web_acls.return_value = {
        "WebACLs": [{"Name": "unrelated"}, {"Name": "telemetry-sandbox-adot-waf"}]
    }
    assert pr.discover_waf_web_acl(wafv2) == "telemetry-sandbox-adot-waf"


@pytest.mark.unit
def test_discover_waf_web_acl_falls_back_to_sole_acl() -> None:
    wafv2 = MagicMock()
    wafv2.list_web_acls.return_value = {"WebACLs": [{"Name": "only-one"}]}
    assert pr.discover_waf_web_acl(wafv2) == "only-one"


@pytest.mark.unit
def test_discover_waf_web_acl_ambiguous_returns_none() -> None:
    wafv2 = MagicMock()
    wafv2.list_web_acls.return_value = {"WebACLs": [{"Name": "one"}, {"Name": "two"}]}
    assert pr.discover_waf_web_acl(wafv2) is None


@pytest.mark.unit
def test_discover_lambda_function_prefers_env_match() -> None:
    lam = MagicMock()
    lam.list_functions.return_value = {
        "Functions": [
            {"FunctionName": "prod-cwl-split"},
            {"FunctionName": "sandbox-cwl-split"},
        ]
    }
    assert pr.discover_lambda_function(lam, "sandbox") == "sandbox-cwl-split"


@pytest.mark.unit
def test_discover_lambda_function_falls_back_without_env_match() -> None:
    lam = MagicMock()
    lam.list_functions.return_value = {"Functions": [{"FunctionName": "prod-cwl-split"}]}
    assert pr.discover_lambda_function(lam, "sandbox") == "prod-cwl-split"


@pytest.mark.unit
def test_discover_lambda_function_none_returns_none() -> None:
    lam = MagicMock()
    lam.list_functions.return_value = {"Functions": [{"FunctionName": "unrelated"}]}
    assert pr.discover_lambda_function(lam, "sandbox") is None


@pytest.mark.unit
def test_paginate_follows_next_token() -> None:
    responses = [
        {"Items": [1, 2], "NextToken": "t1"},
        {"Items": [3], "NextToken": None},
    ]

    def _operation(**kwargs: Any) -> dict[str, Any]:
        return responses.pop(0)

    items = pr._paginate(_operation, "Items", "NextToken")
    assert items == [1, 2, 3]


@pytest.mark.unit
def test_paginate_uses_marker_kwarg() -> None:
    responses = [
        {"Items": [1], "NextMarker": "m1"},
        {"Items": [2], "NextMarker": None},
    ]
    seen_kwargs: list[dict[str, Any]] = []

    def _operation(**kwargs: Any) -> dict[str, Any]:
        seen_kwargs.append(kwargs)
        return responses.pop(0)

    items = pr._paginate(_operation, "Items", "NextMarker", marker_kwarg="Marker")
    assert items == [1, 2]
    assert seen_kwargs[1] == {"Marker": "m1"}


@pytest.mark.unit
def test_pick_one_prefers_substring_match() -> None:
    items = [{"name": "foo"}, {"name": "sandbox-thing"}]
    assert pr._pick_one(items, "name", "sandbox") == "sandbox-thing"


@pytest.mark.unit
def test_pick_one_falls_back_to_sole_item() -> None:
    items = [{"name": "only"}]
    assert pr._pick_one(items, "name", "no-match") == "only"


@pytest.mark.unit
def test_pick_one_ambiguous_returns_none() -> None:
    items = [{"name": "a"}, {"name": "b"}]
    assert pr._pick_one(items, "name", "no-match") is None


# ---------------------------------------------------------------------------
# build_metric_queries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_metric_queries_all_resolved_covers_every_group() -> None:
    queries = pr.build_metric_queries(_resolved())
    namespaces = {q.namespace for q in queries}
    assert namespaces == {
        "AWS/ECS",
        "AWS/ApplicationELB",
        "AWS/WAFV2",
        "AWS/Firehose",
        "AWS/Lambda",
    }
    # ids must be unique within one GetMetricData request.
    ids = [q.query_id for q in queries]
    assert len(ids) == len(set(ids))


@pytest.mark.unit
def test_build_metric_queries_skips_unresolved_groups() -> None:
    resolved = _resolved(
        ecs_cluster_name=None,
        alb_arn_suffix=None,
        waf_web_acl_name=None,
        firehose_stream_name=None,
        lambda_function_name=None,
    )
    queries = pr.build_metric_queries(resolved)
    assert queries == []


@pytest.mark.unit
def test_build_metric_queries_ecs_requires_both_cluster_and_service() -> None:
    resolved = _resolved(
        ecs_service_name=None,
        alb_arn_suffix=None,
        waf_web_acl_name=None,
        firehose_stream_name=None,
        lambda_function_name=None,
    )
    queries = pr.build_metric_queries(resolved)
    assert queries == []


@pytest.mark.unit
def test_build_metric_queries_waf_dimensions_include_rule_and_region() -> None:
    resolved = _resolved(
        ecs_cluster_name=None,
        alb_arn_suffix=None,
        firehose_stream_name=None,
        lambda_function_name=None,
    )
    queries = pr.build_metric_queries(resolved)
    waf_query = next(q for q in queries if q.namespace == "AWS/WAFV2")
    dim_names = {d["Name"] for d in waf_query.dimensions}
    assert dim_names == {"WebACL", "Rule", "Region"}


# ---------------------------------------------------------------------------
# pull_metric_series
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pull_metric_series_empty_queries_returns_empty_dict() -> None:
    cw = MagicMock()
    start = datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC)
    end = start + datetime.timedelta(seconds=60)
    assert pr.pull_metric_series(cw, [], start, end, 60) == {}
    cw.get_metric_data.assert_not_called()


@pytest.mark.unit
def test_pull_metric_series_normalizes_timestamps_to_elapsed_seconds() -> None:
    start = datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC)
    end = start + datetime.timedelta(seconds=120)
    cw = MagicMock()
    cw.get_metric_data.return_value = {
        "MetricDataResults": [
            {
                "Id": "ecscpu",
                "Timestamps": [start + datetime.timedelta(seconds=60)],
                "Values": [42.0],
            }
        ],
        "NextToken": None,
    }
    queries = [pr.MetricQuery("ecscpu", "chart", "CPU", "AWS/ECS", "CPUUtilization", [], "Average")]
    series = pr.pull_metric_series(cw, queries, start, end, 60)
    assert series["ecscpu"].elapsed_seconds == [60.0]
    assert series["ecscpu"].values == [42.0]
    assert series["ecscpu"].total == 42.0


@pytest.mark.unit
def test_pull_metric_series_follows_next_token() -> None:
    start = datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC)
    end = start + datetime.timedelta(seconds=120)
    cw = MagicMock()
    cw.get_metric_data.side_effect = [
        {
            "MetricDataResults": [{"Id": "q1", "Timestamps": [start], "Values": [1.0]}],
            "NextToken": "page2",
        },
        {
            "MetricDataResults": [
                {
                    "Id": "q1",
                    "Timestamps": [start + datetime.timedelta(seconds=60)],
                    "Values": [2.0],
                }
            ],
            "NextToken": None,
        },
    ]
    queries = [pr.MetricQuery("q1", "chart", "label", "AWS/ECS", "CPUUtilization", [], "Average")]
    series = pr.pull_metric_series(cw, queries, start, end, 60)
    assert series["q1"].elapsed_seconds == [0.0, 60.0]
    assert series["q1"].values == [1.0, 2.0]
    assert cw.get_metric_data.call_count == 2


# ---------------------------------------------------------------------------
# headline / thresholds
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_headline_pulls_client_and_cloudwatch_numbers() -> None:
    metric_series = {
        "alb5xx": pr.MetricSeries("chart", "label", [0.0, 60.0], [1.0, 2.0]),
        "wafblock": pr.MetricSeries("chart", "label", [0.0], [5.0]),
    }
    headline = pr.build_headline(_SAMPLE_RESULTS, metric_series)
    assert headline["client_total_requests"] == 100.0
    assert headline["client_error_rate"] == 0.01
    assert headline["client_p99_latency_ms"] == 30.0
    assert headline["alb_5xx_count"] == 3.0
    assert headline["waf_blocked_requests"] == 5.0
    assert headline["firehose_throttled_records"] == 0.0
    assert headline["lambda_errors"] == 0.0


@pytest.mark.unit
def test_threshold_check_skipped_when_no_threshold_given() -> None:
    check = pr.ThresholdCheck("x", observed=5.0, threshold=None)
    assert check.status == "skipped"
    assert check.as_dict()["threshold"] is None


@pytest.mark.unit
def test_threshold_check_pass_and_fail() -> None:
    assert pr.ThresholdCheck("x", observed=1.0, threshold=2.0).status == "pass"
    assert pr.ThresholdCheck("x", observed=3.0, threshold=2.0).status == "fail"
    assert pr.ThresholdCheck("x", observed=2.0, threshold=2.0).status == "pass"


@pytest.mark.unit
def test_evaluate_thresholds_builds_every_check() -> None:
    parser = pr.build_parser()
    args = parser.parse_args(
        [
            "--env",
            "sandbox",
            "--results",
            "r.json",
            "--output",
            "o.html",
            "--threshold-error-rate",
            "0.05",
        ]
    )
    headline = {
        "client_error_rate": 0.01,
        "client_p99_latency_ms": 10.0,
        "alb_5xx_count": 0.0,
        "waf_blocked_requests": 0.0,
        "firehose_throttled_records": 0.0,
        "lambda_errors": 0.0,
    }
    checks = pr.evaluate_thresholds(args, headline)
    by_name = {c.name: c for c in checks}
    assert by_name["client_error_rate"].status == "pass"
    assert by_name["client_p99_latency_ms"].status == "skipped"


# ---------------------------------------------------------------------------
# Chart rendering
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_line_chart_with_data_produces_valid_png() -> None:
    png = pr.render_line_chart("Title", "x", "y", {"series-a": ([0.0, 1.0, 2.0], [1.0, 2.0, 3.0])})
    assert png.startswith(_PNG_MAGIC)
    assert len(png) > 100


@pytest.mark.unit
def test_render_line_chart_no_data_still_produces_valid_png() -> None:
    png = pr.render_line_chart("Title", "x", "y", {})
    assert png.startswith(_PNG_MAGIC)


@pytest.mark.unit
def test_render_line_chart_series_with_empty_x_values_is_skipped() -> None:
    png = pr.render_line_chart("Title", "x", "y", {"empty": ([], [])})
    assert png.startswith(_PNG_MAGIC)


@pytest.mark.unit
def test_render_line_chart_mixed_empty_and_populated_series() -> None:
    # One series has data (has_data=True overall) while a second series in the
    # SAME chart is empty -- exercises the inner per-series skip branch.
    png = pr.render_line_chart(
        "Title", "x", "y", {"has-data": ([0.0, 1.0], [1.0, 2.0]), "empty": ([], [])}
    )
    assert png.startswith(_PNG_MAGIC)


@pytest.mark.unit
def test_build_client_chart_series_has_three_charts() -> None:
    series = pr.build_client_chart_series(_SAMPLE_RESULTS)
    assert set(series) == {
        "Client Throughput",
        "Client Latency Percentiles (ms)",
        "Client Error Rate",
    }
    x_values, y_values = series["Client Throughput"]["requests/s"]
    assert x_values == [0.0, 60.0]
    assert y_values == [1.0, 1.0]


@pytest.mark.unit
def test_build_client_chart_series_handles_no_buckets() -> None:
    series = pr.build_client_chart_series({"buckets": []})
    for chart_series in series.values():
        for x_values, y_values in chart_series.values():
            assert x_values == []
            assert y_values == []


@pytest.mark.unit
def test_build_cloudwatch_chart_series_groups_by_chart_title() -> None:
    metric_series = {
        "ecscpu": pr.MetricSeries("ECS CPU / Memory Utilization (%)", "CPU", [0.0], [1.0]),
        "ecsmem": pr.MetricSeries("ECS CPU / Memory Utilization (%)", "Mem", [0.0], [2.0]),
    }
    grouped = pr.build_cloudwatch_chart_series(metric_series)
    assert set(grouped["ECS CPU / Memory Utilization (%)"]) == {"CPU", "Mem"}


@pytest.mark.unit
def test_render_all_charts_covers_every_documented_chart() -> None:
    metric_series = {
        "ecscpu": pr.MetricSeries("ECS CPU / Memory Utilization (%)", "CPU", [0.0], [1.0]),
    }
    charts = pr.render_all_charts(_SAMPLE_RESULTS, metric_series)
    titles = [title for title, _ in charts]
    assert titles == [t for t, _, _ in pr._CLIENT_CHARTS] + [t for t, _, _ in pr._CLOUDWATCH_CHARTS]
    for _, png in charts:
        assert png.startswith(_PNG_MAGIC)


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_html_report_is_self_contained_with_expected_sections() -> None:
    headline = {"client_error_rate": 0.0, "alb_5xx_count": 1.0}
    thresholds = [pr.ThresholdCheck("client_error_rate", 0.0, 0.05)]
    resolution = {"ecs": "resolved: c, s", "alb": "NOT resolved -- pass an explicit override"}
    charts = [("Client Throughput", b"\x89PNG\r\n\x1a\nfakepngbytes")]
    output = pr.render_html_report(
        env="sandbox",
        run_id="run-1",
        generated_at="2026-07-20T00:05:00Z",
        window_start="2026-07-20T00:00:00Z",
        window_end="2026-07-20T00:02:00Z",
        headline=headline,
        thresholds=thresholds,
        resource_resolution=resolution,
        charts=charts,
    )
    assert output.startswith("<!doctype html>")
    assert "<html" in output and "</html>" in output
    assert 'id="headline"' in output
    assert 'id="thresholds"' in output
    assert 'id="resource-resolution"' in output
    assert "Client Throughput" in output
    assert "data:image/png;base64," in output
    decoded = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepngbytes").decode("ascii")
    assert decoded in output
    assert "PASS" in output
    # No external asset references (self-contained: no http(s):// src/href).
    assert "http://" not in output
    assert "https://" not in output


@pytest.mark.unit
def test_render_html_report_shows_fail_when_a_threshold_fails() -> None:
    thresholds = [pr.ThresholdCheck("alb_5xx_count", observed=10.0, threshold=1.0)]
    output = pr.render_html_report(
        env="sandbox",
        run_id="r",
        generated_at="t",
        window_start="a",
        window_end="b",
        headline={},
        thresholds=thresholds,
        resource_resolution={},
        charts=[],
    )
    assert "FAIL" in output
    assert "result-fail" in output


@pytest.mark.unit
def test_render_html_report_escapes_untrusted_strings() -> None:
    output = pr.render_html_report(
        env="<script>alert(1)</script>",
        run_id="r",
        generated_at="t",
        window_start="a",
        window_end="b",
        headline={},
        thresholds=[],
        resource_resolution={},
        charts=[],
    )
    assert "<script>alert(1)</script>" not in output
    assert "&lt;script&gt;" in output


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_parser_defaults() -> None:
    parser = pr.build_parser()
    args = parser.parse_args(["--env", "sandbox", "--results", "r.json", "--output", "o.html"])
    assert args.period_seconds == 60
    assert args.waf_web_acl_rule == "ALL"
    assert args.threshold_error_rate is None
    assert args.ambient_credentials is False


@pytest.mark.unit
def test_resolve_region_from_cli_arg() -> None:
    parser = pr.build_parser()
    args = parser.parse_args(
        [
            "--env",
            "sandbox",
            "--results",
            "r.json",
            "--output",
            "o.html",
            "--aws-region",
            "eu-west-1",
        ]
    )
    assert pr._resolve_region(args) == "eu-west-1"


@pytest.mark.unit
def test_resolve_region_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    parser = pr.build_parser()
    args = parser.parse_args(["--env", "sandbox", "--results", "r.json", "--output", "o.html"])
    assert pr._resolve_region(args) == "us-west-2"


@pytest.mark.unit
def test_resolve_region_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    parser = pr.build_parser()
    args = parser.parse_args(["--env", "sandbox", "--results", "r.json", "--output", "o.html"])
    with pytest.raises(pr.e2e_common.E2EUsageError, match="AWS_DEFAULT_REGION"):
        pr._resolve_region(args)


@pytest.mark.unit
def test_client_factory_uses_named_profile_by_default() -> None:
    session = MagicMock()
    boto3_module = MagicMock()
    boto3_module.Session.return_value = session
    accounts_data = json.loads(
        (
            pathlib.Path(__file__).parent.parent.parent / "terragrunt" / "common" / "accounts.json"
        ).read_text()
    )
    pr._client_factory(boto3_module, "sandbox", accounts_data, ambient_credentials=False)
    boto3_module.Session.assert_called_once_with(profile_name="sandbox")


@pytest.mark.unit
def test_client_factory_uses_ambient_session_when_requested() -> None:
    session = MagicMock()
    boto3_module = MagicMock()
    boto3_module.Session.return_value = session
    pr._client_factory(boto3_module, "sandbox", {}, ambient_credentials=True)
    boto3_module.Session.assert_called_once_with()


@pytest.mark.unit
def test_resource_resolution_notes_reports_resolved_and_not_resolved() -> None:
    resolved = _resolved(alb_arn_suffix=None)
    notes = pr.resource_resolution_notes(resolved)
    assert notes["ecs"].startswith("resolved:")
    assert notes["alb"].startswith("NOT resolved")


def _client_registry(clients: dict[str, Any]) -> Any:
    def _client(service_name: str, **kwargs: Any) -> Any:
        return clients.setdefault(service_name, MagicMock())

    return _client


@pytest.mark.unit
def test_resolve_resources_uses_cli_overrides_without_discovery() -> None:
    clients: dict[str, Any] = {}
    parser = pr.build_parser()
    args = parser.parse_args(
        [
            "--env",
            "sandbox",
            "--results",
            "r.json",
            "--output",
            "o.html",
            "--ecs-cluster-name",
            "c",
            "--ecs-service-name",
            "s",
            "--alb-arn-suffix",
            "app/x/1",
            "--waf-web-acl-name",
            "w",
            "--firehose-stream-name",
            "f",
            "--lambda-function-name",
            "l",
        ]
    )
    resolved = pr.resolve_resources(args, _client_registry(clients), "us-east-1")
    assert resolved.ecs_cluster_name == "c"
    assert resolved.alb_arn_suffix == "app/x/1"
    assert resolved.waf_web_acl_name == "w"
    assert resolved.firehose_stream_name == "f"
    assert resolved.lambda_function_name == "l"
    # No discovery calls were needed since every override was supplied.
    assert clients == {}


@pytest.mark.unit
def test_resolve_resources_discovers_when_no_override_given() -> None:
    clients: dict[str, Any] = {}
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": ["a"]}
    ecs.describe_clusters.return_value = {"clusters": [{"clusterName": "sandbox-cluster"}]}
    ecs.list_services.return_value = {"serviceArns": ["s"]}
    ecs.describe_services.return_value = {"services": [{"serviceName": "sandbox-adot"}]}
    clients["ecs"] = ecs

    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {"LoadBalancers": []}
    clients["elbv2"] = elbv2

    wafv2 = MagicMock()
    wafv2.list_web_acls.return_value = {"WebACLs": []}
    clients["wafv2"] = wafv2

    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {"DeliveryStreamNames": []}
    clients["firehose"] = firehose

    lam = MagicMock()
    lam.list_functions.return_value = {"Functions": []}
    clients["lambda"] = lam

    parser = pr.build_parser()
    args = parser.parse_args(["--env", "sandbox", "--results", "r.json", "--output", "o.html"])
    resolved = pr.resolve_resources(args, _client_registry(clients), "us-east-1")
    assert resolved.ecs_cluster_name == "sandbox-cluster"
    assert resolved.ecs_service_name == "sandbox-adot"
    assert resolved.alb_arn_suffix is None
    assert resolved.waf_web_acl_name is None
    assert resolved.waf_web_acl_region == "us-east-1"


@pytest.mark.unit
def test_resolve_resources_ecs_discovery_returns_none_leaves_names_unset() -> None:
    clients: dict[str, Any] = {}
    ecs = MagicMock()
    ecs.list_clusters.return_value = {"clusterArns": []}
    clients["ecs"] = ecs

    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {"LoadBalancers": []}
    clients["elbv2"] = elbv2

    wafv2 = MagicMock()
    wafv2.list_web_acls.return_value = {"WebACLs": []}
    clients["wafv2"] = wafv2

    firehose = MagicMock()
    firehose.list_delivery_streams.return_value = {"DeliveryStreamNames": []}
    clients["firehose"] = firehose

    lam = MagicMock()
    lam.list_functions.return_value = {"Functions": []}
    clients["lambda"] = lam

    parser = pr.build_parser()
    args = parser.parse_args(["--env", "sandbox", "--results", "r.json", "--output", "o.html"])
    resolved = pr.resolve_resources(args, _client_registry(clients), "us-east-1")
    assert resolved.ecs_cluster_name is None
    assert resolved.ecs_service_name is None


@pytest.mark.unit
def test_resolve_resources_waf_client_built_for_us_east_1() -> None:
    clients: dict[str, Any] = {}
    seen_kwargs: dict[str, Any] = {}

    def _client(service_name: str, **kwargs: Any) -> Any:
        if service_name == "wafv2":
            seen_kwargs.update(kwargs)
            wafv2 = MagicMock()
            wafv2.list_web_acls.return_value = {"WebACLs": []}
            return wafv2
        return clients.setdefault(service_name, MagicMock())

    parser = pr.build_parser()
    args = parser.parse_args(
        [
            "--env",
            "sandbox",
            "--results",
            "r.json",
            "--output",
            "o.html",
            "--ecs-cluster-name",
            "c",
            "--ecs-service-name",
            "s",
            "--alb-arn-suffix",
            "app/x/1",
            "--firehose-stream-name",
            "f",
            "--lambda-function-name",
            "l",
        ]
    )
    pr.resolve_resources(args, _client, "us-east-1")
    assert seen_kwargs.get("region_name") == "us-east-1"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _write_results(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "results.json"
    path.write_text(json.dumps(_SAMPLE_RESULTS))
    return path


@pytest.mark.unit
def test_main_missing_results_file_exits_two(tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit) as exc:
        pr.main(
            [
                "--env",
                "sandbox",
                "--results",
                str(tmp_path / "nope.json"),
                "--output",
                str(tmp_path / "o.html"),
                "--aws-region",
                "us-east-1",
            ]
        )
    assert exc.value.code == 2


@pytest.mark.unit
def test_main_missing_region_exits_two(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    results_path = _write_results(tmp_path)
    with pytest.raises(SystemExit) as exc:
        pr.main(
            [
                "--env",
                "sandbox",
                "--results",
                str(results_path),
                "--output",
                str(tmp_path / "o.html"),
            ]
        )
    assert exc.value.code == 2


def _monkeypatch_all_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "_client_factory", lambda *a, **k: lambda name, **kw: MagicMock())
    monkeypatch.setattr(pr, "resolve_resources", lambda args, client, region: _resolved())
    monkeypatch.setattr(
        pr,
        "pull_metric_series",
        lambda client, queries, start, end, period: {
            "alb5xx": pr.MetricSeries("ALB Request Count & 5XX Count", "5XX", [0.0], [2.0]),
        },
    )


@pytest.mark.unit
def test_main_happy_path_writes_html_and_evidence_exit_zero(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_all_resolved(monkeypatch)
    results_path = _write_results(tmp_path)
    html_path = tmp_path / "report.html"
    evidence_path = tmp_path / "evidence.json"
    with pytest.raises(SystemExit) as exc:
        pr.main(
            [
                "--env",
                "sandbox",
                "--results",
                str(results_path),
                "--output",
                str(html_path),
                "--evidence-out",
                str(evidence_path),
                "--aws-region",
                "us-east-1",
            ]
        )
    assert exc.value.code == 0
    assert html_path.exists()
    assert "Client Throughput" in html_path.read_text()
    evidence = json.loads(evidence_path.read_text())
    assert evidence["result"] == "PASS"
    assert evidence["env"] == "sandbox"
    assert evidence["run_id"] == "run-1"


@pytest.mark.unit
def test_main_threshold_failure_exits_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_all_resolved(monkeypatch)
    results_path = _write_results(tmp_path)
    html_path = tmp_path / "report.html"
    evidence_path = tmp_path / "evidence.json"
    with pytest.raises(SystemExit) as exc:
        pr.main(
            [
                "--env",
                "sandbox",
                "--results",
                str(results_path),
                "--output",
                str(html_path),
                "--evidence-out",
                str(evidence_path),
                "--aws-region",
                "us-east-1",
                "--threshold-alb-5xx",
                "0",
            ]
        )
    assert exc.value.code == 1
    evidence = json.loads(evidence_path.read_text())
    assert evidence["result"] == "FAIL"


@pytest.mark.unit
def test_main_prints_evidence_to_stdout_when_no_evidence_out(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _monkeypatch_all_resolved(monkeypatch)
    results_path = _write_results(tmp_path)
    html_path = tmp_path / "report.html"
    with pytest.raises(SystemExit):
        pr.main(
            [
                "--env",
                "sandbox",
                "--results",
                str(results_path),
                "--output",
                str(html_path),
                "--aws-region",
                "us-east-1",
            ]
        )
    out = capsys.readouterr().out
    assert '"result"' in out


@pytest.mark.unit
def test_main_explicit_window_overrides_derivation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monkeypatch_all_resolved(monkeypatch)
    results_path = _write_results(tmp_path)
    html_path = tmp_path / "report.html"
    evidence_path = tmp_path / "evidence.json"
    with pytest.raises(SystemExit) as exc:
        pr.main(
            [
                "--env",
                "sandbox",
                "--results",
                str(results_path),
                "--output",
                str(html_path),
                "--evidence-out",
                str(evidence_path),
                "--aws-region",
                "us-east-1",
                "--window-start",
                "2026-07-19T23:00:00Z",
                "--window-end",
                "2026-07-19T23:05:00Z",
            ]
        )
    assert exc.value.code == 0
    evidence = json.loads(evidence_path.read_text())
    assert evidence["window_start"] == "2026-07-19T23:00:00Z"
    assert evidence["window_end"] == "2026-07-19T23:05:00Z"
