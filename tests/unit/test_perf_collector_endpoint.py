"""Unit tests for scripts.perf_collector_endpoint and the CloudFront discovery helper."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from scripts import e2e_common, perf_collector_endpoint


class _FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def paginate(self) -> list[dict[str, Any]]:
        return self._pages


class _FakeCloudFront:
    """Minimal CloudFront client stub exposing a list_distributions paginator."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "list_distributions"
        return _FakePaginator(self._pages)


def _dist(domain: str, aliases: list[str]) -> dict[str, Any]:
    return {"DomainName": domain, "Aliases": {"Items": aliases}}


ALIAS = "collector.sandbox.telemetry.example.com"


@pytest.mark.unit
def test_discover_matches_by_alias_across_pages() -> None:
    pages = [
        {
            "DistributionList": {
                "Items": [_dist("dOTHER.cloudfront.net", ["portal.sandbox.example"])]
            }
        },
        {"DistributionList": {"Items": [_dist("dMATCH.cloudfront.net", [ALIAS])]}},
    ]
    assert (
        e2e_common.discover_cloudfront_domain(_FakeCloudFront(pages), ALIAS)
        == "dMATCH.cloudfront.net"
    )


@pytest.mark.unit
def test_discover_falls_back_to_sole_distribution() -> None:
    pages = [{"DistributionList": {"Items": [_dist("dSOLE.cloudfront.net", [])]}}]
    assert (
        e2e_common.discover_cloudfront_domain(_FakeCloudFront(pages), ALIAS)
        == "dSOLE.cloudfront.net"
    )


@pytest.mark.unit
def test_discover_returns_none_when_no_match_and_multiple() -> None:
    pages = [
        {
            "DistributionList": {
                "Items": [_dist("d1.cloudfront.net", ["a"]), _dist("d2.cloudfront.net", ["b"])]
            }
        }
    ]
    assert e2e_common.discover_cloudfront_domain(_FakeCloudFront(pages), ALIAS) is None


@pytest.mark.unit
def test_discover_handles_empty_distribution_list() -> None:
    assert (
        e2e_common.discover_cloudfront_domain(_FakeCloudFront([{"DistributionList": {}}]), ALIAS)
        is None
    )


_DOMAINS = {"sandbox": {"dns_pretty_apex": "sandbox.telemetry.example.com"}}


@pytest.mark.unit
def test_resolve_collector_endpoint_builds_https_v1_logs_url() -> None:
    cf = _FakeCloudFront([{"DistributionList": {"Items": [_dist("dABC.cloudfront.net", [ALIAS])]}}])
    endpoint = perf_collector_endpoint.resolve_collector_endpoint("sandbox", _DOMAINS, cf)
    assert endpoint == "https://dABC.cloudfront.net/v1/logs"


@pytest.mark.unit
def test_resolve_collector_endpoint_raises_when_unresolved() -> None:
    cf = _FakeCloudFront([{"DistributionList": {"Items": []}}])
    with pytest.raises(perf_collector_endpoint.EndpointResolutionError):
        perf_collector_endpoint.resolve_collector_endpoint("sandbox", _DOMAINS, cf)


@pytest.mark.unit
def test_main_writes_endpoint_to_output_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    domains_file = tmp_path / "domains.json"
    domains_file.write_text('{"sandbox": {"dns_pretty_apex": "sandbox.telemetry.example.com"}}')
    out = tmp_path / "gh_output"
    out.write_text("")

    cf = _FakeCloudFront([{"DistributionList": {"Items": [_dist("dXYZ.cloudfront.net", [ALIAS])]}}])

    class _FakeSession:
        def client(self, name: str) -> Any:
            assert name == "cloudfront"
            return cf

    monkeypatch.setattr(e2e_common, "build_ambient_session", lambda _boto3: _FakeSession())

    rc = perf_collector_endpoint.main(
        ["--env", "sandbox", "--output", str(out), "--domains", str(domains_file)]
    )
    assert rc == 0
    assert "collector_endpoint=https://dXYZ.cloudfront.net/v1/logs" in out.read_text()


@pytest.mark.unit
def test_main_returns_1_when_unresolved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    domains_file = tmp_path / "domains.json"
    domains_file.write_text('{"sandbox": {"dns_pretty_apex": "sandbox.telemetry.example.com"}}')
    out = tmp_path / "gh_output"
    out.write_text("")

    cf = _FakeCloudFront([{"DistributionList": {"Items": []}}])

    class _FakeSession:
        def client(self, name: str) -> Any:
            return cf

    monkeypatch.setattr(e2e_common, "build_ambient_session", lambda _boto3: _FakeSession())

    rc = perf_collector_endpoint.main(
        ["--env", "sandbox", "--output", str(out), "--domains", str(domains_file)]
    )
    assert rc == 1
    assert out.read_text() == ""
