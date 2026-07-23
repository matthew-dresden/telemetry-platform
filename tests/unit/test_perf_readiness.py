"""Unit tests for scripts/perf_readiness.py.

No real network: the HTTP poster is injected as a fake callable that returns a
scripted sequence of ``(status, detail)`` outcomes, and the poll interval is
driven to zero so the bounded poll loop runs without real waiting. The tests
assert:
  * is_serving classifies transport errors (0) and 5xx as NOT serving, and any
    non-5xx HTTP status as serving;
  * probe_until_ready polls past transport errors / 5xx and stops on the first
    non-5xx response, returning the last observation;
  * a probe that never becomes ready returns not-ready within the budget;
  * the poster POSTs the minimal empty OTLP-logs body with a User-Agent header;
  * _default_poster rejects a non-https endpoint (usage error);
  * main exits 0 on ready, 1 on timeout (fail-fast), 2 on a bad endpoint, and
    writes evidence to --output.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from scripts import constants
from scripts import perf_readiness as pr


class _FakePoster:
    """Returns a scripted sequence of (status, detail); repeats the last forever."""

    def __init__(self, outcomes: list[tuple[int, str]]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, body: bytes, content_type: str, user_agent: str
    ) -> tuple[int, str]:
        self.calls.append(
            {"url": url, "body": body, "content_type": content_type, "user_agent": user_agent}
        )
        index = min(len(self.calls) - 1, len(self._outcomes) - 1)
        return self._outcomes[index]


# ---------------------------------------------------------------------------
# is_serving
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, True),
        (400, True),
        (403, True),
        (404, True),
        (415, True),
        (429, True),
        (499, True),
        (0, False),  # transport/TLS/connection error
        (500, False),
        (502, False),
        (503, False),
        (504, False),
    ],
)
def test_is_serving_classification(status: int, expected: bool) -> None:
    assert pr.is_serving(status) is expected


# ---------------------------------------------------------------------------
# probe_until_ready
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_probe_ready_on_first_non_5xx() -> None:
    poster = _FakePoster([(200, "HTTP 200 OK")])
    ready, status, detail = pr.probe_until_ready(
        "https://d123.cloudfront.net/v1/logs", timeout=5, interval=0, poster=poster
    )
    assert ready is True
    assert status == 200
    assert detail == "HTTP 200 OK"
    assert len(poster.calls) == 1


@pytest.mark.unit
def test_probe_polls_past_transport_error_and_5xx() -> None:
    # First a connection error (0), then a 503 (origin not ready), then a 415 (serving).
    poster = _FakePoster([(0, "transport-error: refused"), (503, "HTTP 503"), (415, "HTTP 415")])
    ready, status, detail = pr.probe_until_ready(
        "https://d123.cloudfront.net/v1/logs", timeout=5, interval=0, poster=poster
    )
    assert ready is True
    assert status == 415
    assert len(poster.calls) == 3


@pytest.mark.unit
def test_probe_posts_minimal_otlp_body_with_user_agent() -> None:
    poster = _FakePoster([(200, "HTTP 200 OK")])
    pr.probe_until_ready(
        "https://edge.example/v1/logs",
        timeout=1,
        interval=0,
        poster=poster,
        user_agent="ua/1.0",
    )
    call = poster.calls[0]
    assert call["body"] == pr._MINIMAL_OTLP_LOGS_JSON
    assert call["body"] == b'{"resourceLogs":[]}'
    assert call["content_type"] == constants.E2E_CONTENT_TYPE_JSON
    assert call["user_agent"] == "ua/1.0"
    assert call["url"] == "https://edge.example/v1/logs"


@pytest.mark.unit
def test_probe_times_out_when_never_serving() -> None:
    # Always a 503 -- never becomes ready; the bounded budget must expire.
    poster = _FakePoster([(503, "HTTP 503")])
    ready, status, detail = pr.probe_until_ready(
        "https://d123.cloudfront.net/v1/logs", timeout=0, interval=0, poster=poster
    )
    assert ready is False
    assert status == 503


# ---------------------------------------------------------------------------
# _default_poster scheme validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_poster_rejects_non_https() -> None:
    with pytest.raises(pr.ReadinessUsageError):
        pr._default_poster("http://insecure/v1/logs", b"{}", "application/json", "ua")


@pytest.mark.unit
def test_default_poster_rejects_hostless_url() -> None:
    with pytest.raises(pr.ReadinessUsageError):
        pr._default_poster("https:///v1/logs", b"{}", "application/json", "ua")


class _FakeResponse:
    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason
        self.read_called = False

    def read(self) -> bytes:
        self.read_called = True
        return b""


class _FakeConn:
    """Records the POST call and returns a scripted response; raises if configured."""

    last: _FakeConn | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.request_args: dict[str, Any] = {}
        self.closed = False
        self.response = _FakeResponse(200, "OK")
        self.raise_exc: Exception | None = None
        _FakeConn.last = self

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.request_args = {"method": method, "path": path, "body": body, "headers": headers}

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.mark.unit
def test_default_poster_posts_and_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr.http.client, "HTTPSConnection", _FakeConn)
    status, detail = pr._default_poster(
        "https://edge.example/v1/logs", b'{"resourceLogs":[]}', "application/json", "ua/9"
    )
    assert status == 200
    assert detail == "HTTP 200 OK"
    conn = _FakeConn.last
    assert conn is not None
    assert conn.host == "edge.example"
    assert conn.port == 443
    assert conn.request_args["method"] == "POST"
    assert conn.request_args["path"] == "/v1/logs"
    assert conn.request_args["headers"]["User-Agent"] == "ua/9"
    assert conn.request_args["headers"]["Content-Type"] == "application/json"
    assert conn.closed is True


@pytest.mark.unit
def test_default_poster_surfaces_transport_error_as_status_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _factory(host: str, port: int, timeout: float) -> _FakeConn:
        conn = _FakeConn(host, port, timeout)
        conn.raise_exc = OSError("connection refused")
        return conn

    monkeypatch.setattr(pr.http.client, "HTTPSConnection", _factory)
    status, detail = pr._default_poster(
        "https://edge.example/v1/logs", b"{}", "application/json", "ua"
    )
    assert status == 0
    assert "transport-error" in detail
    assert _FakeConn.last is not None
    assert _FakeConn.last.closed is True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_ready_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(pr, "probe_until_ready", lambda **kwargs: (True, 200, "HTTP 200 OK"))
    out = tmp_path / "evidence.json"
    code = pr.main(
        ["--endpoint", "https://edge.example/v1/logs", "--timeout", "1", "--output", str(out)]
    )
    assert code == 0
    evidence = json.loads(out.read_text())
    assert evidence["ready"] is True
    assert evidence["last_status"] == 200
    assert evidence["endpoint"] == "https://edge.example/v1/logs"


@pytest.mark.unit
def test_main_timeout_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pr, "probe_until_ready", lambda **kwargs: (False, 503, "HTTP 503"))
    code = pr.main(["--endpoint", "https://edge.example/v1/logs", "--timeout", "1"])
    assert code == 1
    assert "did not answer at the HTTP layer" in capsys.readouterr().err


@pytest.mark.unit
def test_main_bad_endpoint_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = pr.main(["--endpoint", "http://insecure/v1/logs"])
    assert code == 2
    assert "https://" in capsys.readouterr().err


@pytest.mark.unit
def test_parser_defaults_from_constants() -> None:
    args = pr.build_parser().parse_args(["--endpoint", "https://edge/v1/logs"])
    assert args.timeout == float(constants.PERF_READINESS_TIMEOUT)
    assert args.poll_interval == float(constants.PERF_READINESS_POLL_INTERVAL)
    assert args.user_agent == constants.E2E_USER_AGENT
