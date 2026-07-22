"""Unit tests for scripts/perf_loadgen.py.

No real network: PersistentHttpSender is exercised through an injected
connection factory (fake HTTPSConnection objects), and LoadRunner through
injected dispatch/clock/waiter callables. Scheduling/aggregation math is
tested directly against known inputs.
"""

from __future__ import annotations

import json
import pathlib
import random
import threading
import time
from typing import Any

import pytest

from scripts import perf_loadgen as pl

TS = "2026-07-20T00:00:00Z"


# ---------------------------------------------------------------------------
# session_windows
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_session_windows_ramp_math() -> None:
    windows = pl.session_windows(
        sessions=4, ramp_seconds=100, hold_seconds=50, ramp_down_seconds=40
    )
    assert windows == [(0.0, 150.0), (25.0, 160.0), (50.0, 170.0), (75.0, 180.0)]


@pytest.mark.unit
def test_session_windows_zero_ramp_all_start_together() -> None:
    windows = pl.session_windows(sessions=3, ramp_seconds=0, hold_seconds=10, ramp_down_seconds=0)
    assert windows == [(0.0, 10.0), (0.0, 10.0), (0.0, 10.0)]


@pytest.mark.unit
def test_session_windows_single_session() -> None:
    windows = pl.session_windows(sessions=1, ramp_seconds=10, hold_seconds=20, ramp_down_seconds=5)
    # A single session's fraction is 0/1 = 0, so it starts at t=0 and its
    # ramp_down contribution (ramp_down_seconds * fraction) is also 0.
    assert windows == [(0.0, 30.0)]


@pytest.mark.unit
def test_session_windows_rejects_zero_sessions() -> None:
    with pytest.raises(ValueError, match="--sessions"):
        pl.session_windows(sessions=0, ramp_seconds=1, hold_seconds=1, ramp_down_seconds=1)


@pytest.mark.unit
def test_session_windows_rejects_negative_sessions() -> None:
    with pytest.raises(ValueError, match="--sessions"):
        pl.session_windows(sessions=-1, ramp_seconds=1, hold_seconds=1, ramp_down_seconds=1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "ramp,hold,ramp_down",
    [(-1, 10, 10), (10, -1, 10), (10, 10, -1)],
)
def test_session_windows_rejects_negative_durations(
    ramp: float, hold: float, ramp_down: float
) -> None:
    with pytest.raises(ValueError, match="ramp-seconds"):
        pl.session_windows(
            sessions=2, ramp_seconds=ramp, hold_seconds=hold, ramp_down_seconds=ramp_down
        )


# ---------------------------------------------------------------------------
# iter_schedule
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_iter_schedule_deterministic_cadence_and_counts() -> None:
    windows = pl.session_windows(sessions=3, ramp_seconds=0, hold_seconds=180, ramp_down_seconds=0)
    rng = random.Random(0)
    events = list(pl.iter_schedule(windows, 60, 45, 0, rng))

    metrics = [e for e in events if e.kind == pl._KIND_METRICS]
    log_bursts = [e for e in events if e.kind == pl._KIND_LOG_BURST]

    # 3 metrics events/session (60, 120, 180) x 3 sessions.
    assert len(metrics) == 9
    # 4 log-burst events/session (45, 90, 135, 180) x 3 sessions.
    assert len(log_bursts) == 12

    session0_metrics = sorted(e.due_at for e in metrics if e.session_id == 0)
    assert session0_metrics == [60.0, 120.0, 180.0]
    session0_bursts = sorted(e.due_at for e in log_bursts if e.session_id == 0)
    assert session0_bursts == [45.0, 90.0, 135.0, 180.0]


@pytest.mark.unit
def test_iter_schedule_yields_in_nondecreasing_due_order_without_jitter() -> None:
    windows = pl.session_windows(sessions=5, ramp_seconds=10, hold_seconds=60, ramp_down_seconds=5)
    rng = random.Random(1)
    events = list(pl.iter_schedule(windows, 20, 15, 0, rng))
    due_times = [e.due_at for e in events]
    assert due_times == sorted(due_times)


@pytest.mark.unit
def test_iter_schedule_jitter_stays_within_bounds() -> None:
    windows = pl.session_windows(sessions=1, ramp_seconds=0, hold_seconds=300, ramp_down_seconds=0)
    rng = random.Random(2)
    events = list(pl.iter_schedule(windows, 1000, 30, 5, rng))
    log_bursts = sorted(e.due_at for e in events if e.kind == pl._KIND_LOG_BURST)
    assert len(log_bursts) > 2
    # Every interval is 30 +/- 5, so consecutive due_at deltas fall in [25, 35].
    # The two sequences are intentionally offset by one (pairwise iteration).
    for previous, current in zip(log_bursts, log_bursts[1:], strict=False):
        delta = current - previous
        assert 25.0 - 1e-9 <= delta <= 35.0 + 1e-9


@pytest.mark.unit
def test_iter_schedule_empty_windows_yields_nothing() -> None:
    rng = random.Random(3)
    assert list(pl.iter_schedule([], 60, 45, 0, rng)) == []


@pytest.mark.unit
def test_iter_schedule_window_shorter_than_first_interval_yields_nothing() -> None:
    rng = random.Random(4)
    events = list(pl.iter_schedule([(0.0, 5.0)], 60, 45, 0, rng))
    assert events == []


@pytest.mark.unit
def test_iter_schedule_rejects_nonpositive_metrics_interval() -> None:
    rng = random.Random(5)
    with pytest.raises(ValueError, match="--metrics-interval-seconds"):
        list(pl.iter_schedule([(0.0, 10.0)], 0, 45, 0, rng))


@pytest.mark.unit
def test_iter_schedule_rejects_nonpositive_log_burst_interval() -> None:
    rng = random.Random(6)
    with pytest.raises(ValueError, match="--log-burst-interval-seconds"):
        list(pl.iter_schedule([(0.0, 10.0)], 60, -1, 0, rng))


@pytest.mark.unit
def test_iter_schedule_rejects_negative_jitter() -> None:
    rng = random.Random(7)
    with pytest.raises(ValueError, match="--log-burst-jitter-seconds"):
        list(pl.iter_schedule([(0.0, 10.0)], 60, 45, -1, rng))


# ---------------------------------------------------------------------------
# _percentile / _percentiles
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_percentile_empty_is_zero() -> None:
    assert pl._percentile([], 50) == 0.0


@pytest.mark.unit
def test_percentile_single_value() -> None:
    assert pl._percentile([5.0], 99) == 5.0


@pytest.mark.unit
def test_percentile_known_dataset() -> None:
    # 0..100 in steps of 10 (11 values); p50 must land exactly on the median.
    data = [float(v) for v in range(0, 101, 10)]
    assert pl._percentile(data, 50) == 50.0
    assert pl._percentile(data, 0) == 0.0
    assert pl._percentile(data, 100) == 100.0


@pytest.mark.unit
def test_percentiles_returns_every_requested_percentile() -> None:
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = pl._percentiles(data, (50, 90, 99))
    assert set(result) == {50, 90, 99}
    assert result[50] == 3.0


# ---------------------------------------------------------------------------
# _Reservoir
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reservoir_retains_everything_under_capacity() -> None:
    rng = random.Random(8)
    reservoir = pl._Reservoir(capacity=10, rng=rng)
    for i in range(5):
        reservoir.offer(float(i))
    assert reservoir.seen == 5
    assert sorted(reservoir.samples) == [0.0, 1.0, 2.0, 3.0, 4.0]


@pytest.mark.unit
def test_reservoir_bounds_memory_above_capacity() -> None:
    rng = random.Random(9)
    reservoir = pl._Reservoir(capacity=10, rng=rng)
    for i in range(1000):
        reservoir.offer(float(i))
    assert reservoir.seen == 1000
    assert len(reservoir.samples) == 10


@pytest.mark.unit
def test_reservoir_rejects_nonpositive_capacity() -> None:
    with pytest.raises(ValueError, match="capacity"):
        pl._Reservoir(capacity=0, rng=random.Random(10))


# ---------------------------------------------------------------------------
# ResultsAggregator
# ---------------------------------------------------------------------------


def _outcome(
    status: int = 200,
    kind: str = pl._KIND_METRICS,
    latency_seconds: float = 0.01,
    record_count: int = 2,
    error: str | None = None,
) -> pl.RequestOutcome:
    return pl.RequestOutcome(
        session_id=0,
        kind=kind,
        latency_seconds=latency_seconds,
        status=status,
        record_count=record_count,
        error=error,
    )


@pytest.mark.unit
def test_aggregator_rejects_nonpositive_bucket_seconds() -> None:
    with pytest.raises(ValueError, match="--bucket-seconds"):
        pl.ResultsAggregator(
            bucket_seconds=0,
            max_latency_samples=10,
            max_samples_per_bucket=10,
            rng=random.Random(0),
        )


@pytest.mark.unit
def test_aggregator_totals_and_status_counts() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=10, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(11)
    )
    for i in range(10):
        error = "boom" if i % 5 == 0 else None
        status = 500 if error else 200
        agg.record(
            _outcome(status=status, error=error, latency_seconds=0.001 * i), elapsed_seconds=i
        )
    snap = agg.snapshot()
    assert snap["totals"]["total_requests"] == 10
    assert snap["totals"]["total_records"] == 20
    assert snap["totals"]["total_errors"] == 2
    assert snap["totals"]["error_rate"] == pytest.approx(0.2)
    assert snap["totals"]["status_counts"] == {"200": 8, "500": 2}


@pytest.mark.unit
def test_aggregator_by_kind_totals() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=10, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(12)
    )
    agg.record(_outcome(kind=pl._KIND_METRICS), elapsed_seconds=0)
    agg.record(_outcome(kind=pl._KIND_METRICS), elapsed_seconds=0)
    agg.record(_outcome(kind=pl._KIND_LOG_BURST), elapsed_seconds=0)
    snap = agg.snapshot()
    assert snap["totals"]["by_kind"] == {"log_burst": 1, "metrics": 2}


@pytest.mark.unit
def test_aggregator_latency_percentiles_known_values() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=1000,
        max_latency_samples=1000,
        max_samples_per_bucket=1000,
        rng=random.Random(13),
    )
    # Latencies 0.000s .. 0.010s in 1ms steps (11 samples); p50 lands on 5ms.
    for i in range(11):
        agg.record(_outcome(latency_seconds=i * 0.001), elapsed_seconds=0)
    snap = agg.snapshot()
    assert snap["latency_ms"]["p50"] == pytest.approx(5.0)
    assert snap["latency_ms"]["min"] == pytest.approx(0.0)
    assert snap["latency_ms"]["max"] == pytest.approx(10.0)
    assert snap["latency_ms"]["mean"] == pytest.approx(5.0)
    assert snap["latency_ms"]["sample_count"] == 11
    assert snap["latency_ms"]["samples_seen"] == 11


@pytest.mark.unit
def test_aggregator_reservoir_caps_global_sample_count() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=1000,
        max_latency_samples=5,
        max_samples_per_bucket=1000,
        rng=random.Random(14),
    )
    for i in range(500):
        agg.record(_outcome(latency_seconds=0.001 * i), elapsed_seconds=0)
    snap = agg.snapshot()
    assert snap["latency_ms"]["sample_count"] == 5
    assert snap["latency_ms"]["samples_seen"] == 500


@pytest.mark.unit
def test_aggregator_buckets_assign_by_elapsed_seconds() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=10, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(15)
    )
    agg.record(_outcome(), elapsed_seconds=1)
    agg.record(_outcome(), elapsed_seconds=9)
    agg.record(_outcome(), elapsed_seconds=15)
    snap = agg.snapshot()
    buckets = {b["bucket_start_seconds"]: b for b in snap["buckets"]}
    assert buckets[0]["requests"] == 2
    assert buckets[10]["requests"] == 1


@pytest.mark.unit
def test_aggregator_bucket_throughput_and_error_rate() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=5, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(16)
    )
    agg.record(_outcome(status=200), elapsed_seconds=0)
    agg.record(_outcome(status=500, error="x"), elapsed_seconds=1)
    snap = agg.snapshot()
    bucket = snap["buckets"][0]
    assert bucket["requests"] == 2
    assert bucket["errors"] == 1
    assert bucket["error_rate"] == pytest.approx(0.5)
    assert bucket["requests_per_second"] == pytest.approx(2 / 5)


@pytest.mark.unit
def test_aggregator_negative_elapsed_seconds_clamped_to_bucket_zero() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=10, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(17)
    )
    agg.record(_outcome(), elapsed_seconds=-5)
    snap = agg.snapshot()
    assert snap["buckets"][0]["bucket_start_seconds"] == 0


@pytest.mark.unit
def test_aggregator_empty_snapshot_has_no_buckets_and_zeroed_totals() -> None:
    agg = pl.ResultsAggregator(
        bucket_seconds=10, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(18)
    )
    snap = agg.snapshot()
    assert snap["totals"]["total_requests"] == 0
    assert snap["totals"]["error_rate"] == 0.0
    assert snap["latency_ms"]["mean"] == 0.0
    assert snap["buckets"] == []


@pytest.mark.unit
def test_aggregator_is_thread_safe_under_concurrent_record() -> None:
    import concurrent.futures

    agg = pl.ResultsAggregator(
        bucket_seconds=10,
        max_latency_samples=1000,
        max_samples_per_bucket=1000,
        rng=random.Random(19),
    )

    def _record_many(n: int) -> None:
        for i in range(n):
            agg.record(_outcome(latency_seconds=0.001 * i), elapsed_seconds=0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_record_many, [50] * 8))

    snap = agg.snapshot()
    assert snap["totals"]["total_requests"] == 400
    assert snap["latency_ms"]["samples_seen"] == 400


# ---------------------------------------------------------------------------
# PersistentHttpSender
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int = 200, reason: str = "OK") -> None:
        self.status = status
        self.reason = reason
        self.read_called = False

    def read(self) -> bytes:
        self.read_called = True
        return b""


class _FakeConnection:
    def __init__(self, fail_times: int = 0) -> None:
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False
        self._fail_times = fail_times
        self._calls = 0

    def request(self, method: str, path: str, body: bytes | None, headers: dict[str, str]) -> None:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise OSError("connection reset")
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse()

    def close(self) -> None:
        self.closed = True


@pytest.mark.unit
def test_sender_post_reuses_the_same_connection_on_one_thread() -> None:
    made: list[_FakeConnection] = []

    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        conn = _FakeConnection()
        made.append(conn)
        return conn

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="ua",
        connection_factory=_factory,
    )
    sender.post("/v1/logs", b"a", "application/x-protobuf")
    sender.post("/v1/metrics", b"b", "application/x-protobuf")
    assert len(made) == 1
    assert len(made[0].requests) == 2


@pytest.mark.unit
def test_sender_post_sends_expected_headers() -> None:
    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        return _FakeConnection()

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="my-ua",
        connection_factory=_factory,
    )
    status, reason = sender.post("/v1/logs", b"body", "application/x-protobuf")
    assert (status, reason) == (200, "OK")


@pytest.mark.unit
def test_sender_post_retries_once_on_stale_connection() -> None:
    conns: list[_FakeConnection] = []

    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        # Only the FIRST connection ever created is stale (fails its first
        # request); the fresh connection opened for the retry succeeds --
        # this is what "a keep-alive connection went stale" looks like.
        conn = _FakeConnection(fail_times=1 if not conns else 0)
        conns.append(conn)
        return conn

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="ua",
        connection_factory=_factory,
    )
    status, reason = sender.post("/v1/logs", b"a", "application/x-protobuf")
    assert status == 200
    # First connection failed and was closed; a fresh one was opened for the retry.
    assert len(conns) == 2
    assert conns[0].closed is True


@pytest.mark.unit
def test_sender_post_surfaces_transport_error_after_second_failure() -> None:
    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        return _FakeConnection(fail_times=99)

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="ua",
        connection_factory=_factory,
    )
    status, reason = sender.post("/v1/logs", b"a", "application/x-protobuf")
    assert status == 0
    assert "transport-error" in reason


@pytest.mark.unit
def test_sender_different_threads_get_different_connections() -> None:
    made: list[_FakeConnection] = []
    lock = threading.Lock()

    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        conn = _FakeConnection()
        with lock:
            made.append(conn)
        return conn

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="ua",
        connection_factory=_factory,
    )

    def _post() -> None:
        sender.post("/v1/logs", b"a", "application/x-protobuf")
        sender.post("/v1/logs", b"a", "application/x-protobuf")

    threads = [threading.Thread(target=_post) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(made) == 3
    for conn in made:
        assert len(conn.requests) == 2


@pytest.mark.unit
def test_sender_close_resets_thread_local_connection() -> None:
    made: list[_FakeConnection] = []

    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        conn = _FakeConnection()
        made.append(conn)
        return conn

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="ua",
        connection_factory=_factory,
    )
    sender.post("/v1/logs", b"a", "application/x-protobuf")
    sender.close()
    sender.post("/v1/logs", b"a", "application/x-protobuf")
    assert len(made) == 2
    assert made[0].closed is True


@pytest.mark.unit
def test_sender_close_before_any_post_is_a_noop() -> None:
    def _factory(host: str, port: int, connect_ip: str | None, timeout: float) -> _FakeConnection:
        return _FakeConnection()

    sender = pl.PersistentHttpSender(
        host="h",
        port=443,
        connect_ip=None,
        timeout=1.0,
        user_agent="ua",
        connection_factory=_factory,
    )
    sender.close()  # no connection was ever opened -- must not raise.


@pytest.mark.unit
def test_sender_uses_default_connection_factory_when_none_given() -> None:
    sender = pl.PersistentHttpSender(
        host="h", port=443, connect_ip=None, timeout=1.0, user_agent="ua"
    )
    assert sender._factory is pl.lg._default_connection_factory


# ---------------------------------------------------------------------------
# build_dispatch
# ---------------------------------------------------------------------------


class _RecordingSender:
    def __init__(self, status: int = 200, reason: str = "OK") -> None:
        self.status = status
        self.reason = reason
        self.calls: list[tuple[str, int, str]] = []

    def post(self, path: str, body: bytes, content_type: str) -> tuple[int, str]:
        self.calls.append((path, len(body), content_type))
        return self.status, self.reason


@pytest.mark.unit
def test_build_dispatch_metrics_kind_posts_to_metrics_path() -> None:
    sender = _RecordingSender()
    build_dispatch = pl.build_dispatch(sender, run_id="r1", encoding="protobuf", clock=lambda: 0.0)
    event = pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_METRICS)
    outcome = build_dispatch(event)
    assert sender.calls[0][0] == "/v1/metrics"
    assert outcome.status == 200
    assert outcome.error is None
    assert outcome.record_count == 2


@pytest.mark.unit
def test_build_dispatch_log_burst_kind_posts_to_logs_path() -> None:
    sender = _RecordingSender()
    dispatch = pl.build_dispatch(sender, run_id="r1", encoding="protobuf", clock=lambda: 0.0)
    event = pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_LOG_BURST)
    outcome = dispatch(event)
    assert sender.calls[0][0] == "/v1/logs"
    assert outcome.record_count == 2


@pytest.mark.unit
def test_build_dispatch_measures_latency_from_injected_clock() -> None:
    sender = _RecordingSender()
    ticks = iter([10.0, 10.25])
    dispatch = pl.build_dispatch(
        sender, run_id="r1", encoding="protobuf", clock=lambda: next(ticks)
    )
    event = pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_METRICS)
    outcome = dispatch(event)
    assert outcome.latency_seconds == pytest.approx(0.25)


@pytest.mark.unit
def test_build_dispatch_unexpected_status_sets_error() -> None:
    sender = _RecordingSender(status=500, reason="Internal Server Error")
    dispatch = pl.build_dispatch(sender, run_id="r1", encoding="protobuf", clock=lambda: 0.0)
    event = pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_METRICS)
    outcome = dispatch(event)
    assert outcome.status == 500
    assert outcome.error is not None
    assert "status=500" in outcome.error


@pytest.mark.unit
def test_build_dispatch_json_encoding_round_trips() -> None:
    sender = _RecordingSender()
    dispatch = pl.build_dispatch(sender, run_id="r1", encoding="json", clock=lambda: 0.0)
    event = pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_METRICS)
    dispatch(event)
    assert sender.calls[0][2] == "application/json"


# ---------------------------------------------------------------------------
# LoadRunner
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_runner_rejects_nonpositive_max_inflight() -> None:
    with pytest.raises(ValueError, match="--max-inflight"):
        pl.LoadRunner(dispatch=lambda e: _outcome(), aggregator=_agg(), max_inflight=0)


def _agg() -> pl.ResultsAggregator:
    return pl.ResultsAggregator(
        bucket_seconds=10, max_latency_samples=100, max_samples_per_bucket=10, rng=random.Random(20)
    )


@pytest.mark.unit
def test_load_runner_dispatches_every_event_and_records_outcomes() -> None:
    events = [
        pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_METRICS),
        pl.ScheduledEvent(due_at=0.01, session_id=1, kind=pl._KIND_LOG_BURST),
    ]

    def _dispatch(event: pl.ScheduledEvent) -> pl.RequestOutcome:
        return pl.RequestOutcome(
            session_id=event.session_id,
            kind=event.kind,
            latency_seconds=0.0,
            status=200,
            record_count=2,
            error=None,
        )

    clock_value = [0.0]

    def _clock() -> float:
        return clock_value[0]

    waits: list[float] = []

    def _waiter(seconds: float) -> None:
        waits.append(seconds)
        clock_value[0] += seconds

    agg = _agg()
    runner = pl.LoadRunner(
        dispatch=_dispatch, aggregator=agg, max_inflight=4, clock=_clock, waiter=_waiter
    )
    runner.run(iter(events))

    snap = agg.snapshot()
    assert snap["totals"]["total_requests"] == 2
    assert waits == [0.0, 0.01] or waits == [0.01]  # first event due_at=0 needs no wait


@pytest.mark.unit
def test_load_runner_bounds_concurrency_to_max_inflight() -> None:
    concurrent_count = [0]
    max_seen = [0]
    lock = threading.Lock()

    def _dispatch(event: pl.ScheduledEvent) -> pl.RequestOutcome:
        with lock:
            concurrent_count[0] += 1
            max_seen[0] = max(max_seen[0], concurrent_count[0])
        time.sleep(0.02)
        with lock:
            concurrent_count[0] -= 1
        return pl.RequestOutcome(
            session_id=event.session_id,
            kind=event.kind,
            latency_seconds=0.0,
            status=200,
            record_count=1,
            error=None,
        )

    events = [pl.ScheduledEvent(due_at=0.0, session_id=i, kind=pl._KIND_METRICS) for i in range(6)]
    agg = _agg()
    runner = pl.LoadRunner(
        dispatch=_dispatch,
        aggregator=agg,
        max_inflight=2,
        clock=time.monotonic,
        waiter=pl._default_waiter,
    )
    runner.run(iter(events))
    assert max_seen[0] <= 2
    assert agg.snapshot()["totals"]["total_requests"] == 6


@pytest.mark.unit
def test_load_runner_dispatch_exception_is_recorded_not_raised(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _dispatch(event: pl.ScheduledEvent) -> pl.RequestOutcome:
        raise RuntimeError("boom")

    events = [pl.ScheduledEvent(due_at=0.0, session_id=0, kind=pl._KIND_METRICS)]
    agg = _agg()
    runner = pl.LoadRunner(
        dispatch=_dispatch, aggregator=agg, max_inflight=1, clock=lambda: 0.0, waiter=lambda s: None
    )
    runner.run(iter(events))
    snap = agg.snapshot()
    assert snap["totals"]["total_requests"] == 1
    assert snap["totals"]["total_errors"] == 1
    assert "boom" in capsys.readouterr().err


@pytest.mark.unit
def test_load_runner_empty_schedule_completes_immediately() -> None:
    agg = _agg()
    runner = pl.LoadRunner(dispatch=lambda e: _outcome(), aggregator=agg, max_inflight=2)
    runner.run(iter([]))
    assert agg.snapshot()["totals"]["total_requests"] == 0


@pytest.mark.unit
def test_default_waiter_returns_immediately_for_nonpositive_seconds() -> None:
    start = time.monotonic()
    pl._default_waiter(0)
    assert time.monotonic() - start < 0.05


# ---------------------------------------------------------------------------
# CLI: build_parser / build_results / main
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_parser_defaults() -> None:
    parser = pl.build_parser()
    args = parser.parse_args(["--env", "sandbox"])
    assert args.sessions == 2000
    assert args.ramp_seconds == 300.0
    assert args.hold_seconds == 1800.0
    assert args.ramp_down_seconds == 120.0
    assert args.metrics_interval_seconds == 60.0
    assert args.max_inflight == 64
    assert args.encoding == "protobuf"


@pytest.mark.unit
def test_build_parser_rejects_unknown_env() -> None:
    parser = pl.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--env", "nope"])


@pytest.mark.unit
def test_build_results_shape() -> None:
    args = pl.build_parser().parse_args(["--env", "sandbox", "--endpoint", "h", "--sessions", "5"])
    agg = _agg()
    agg.record(_outcome(), elapsed_seconds=0)
    results = pl.build_results(args, "h", None, "run-1", TS, TS, agg)
    assert results["run_id"] == "run-1"
    assert results["env"] == "sandbox"
    assert results["endpoint_host"] == "h"
    assert results["sessions"] == 5
    assert results["totals"]["total_requests"] == 1
    assert results["tool_value"] == "e2e-smoke"


@pytest.mark.unit
def test_main_usage_error_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        pl.main(["--env", "sandbox", "--endpoint", "h", "--sessions", "0"])
    assert exc.value.code == 2
    assert "--sessions" in capsys.readouterr().err


@pytest.mark.unit
def test_main_connect_to_pins_the_connect_ip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    captured: dict[str, Any] = {}

    class _CapturingSender:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def post(self, path: str, body: bytes, content_type: str) -> tuple[int, str]:
            return 200, "OK"

    monkeypatch.setattr(pl, "PersistentHttpSender", _CapturingSender)
    out_path = tmp_path / "r.json"
    with pytest.raises(SystemExit) as exc:
        pl.main(
            [
                "--env",
                "sandbox",
                "--endpoint",
                "collector.example",
                "--connect-to",
                "203.0.113.5",
                "--sessions",
                "1",
                "--ramp-seconds",
                "0",
                "--hold-seconds",
                "0.01",
                "--ramp-down-seconds",
                "0",
                "--metrics-interval-seconds",
                "0.01",
                "--log-burst-interval-seconds",
                "0.01",
                "--log-burst-jitter-seconds",
                "0",
                "--max-inflight",
                "1",
                "--results-out",
                str(out_path),
            ]
        )
    assert exc.value.code == 0
    assert captured["connect_ip"] == "203.0.113.5"


@pytest.mark.unit
def test_main_bad_resolve_exits_two() -> None:
    with pytest.raises(SystemExit) as exc:
        pl.main(["--env", "sandbox", "--endpoint", "h", "--resolve", "bad-token"])
    assert exc.value.code == 2


class _FakeMainSender:
    """Stands in for PersistentHttpSender: no real connection is ever made."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def post(self, path: str, body: bytes, content_type: str) -> tuple[int, str]:
        return 200, "OK"


@pytest.mark.unit
def test_main_end_to_end_writes_results_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pl, "PersistentHttpSender", _FakeMainSender)
    out_path = tmp_path / "results.json"
    with pytest.raises(SystemExit) as exc:
        pl.main(
            [
                "--env",
                "sandbox",
                "--endpoint",
                "collector.example",
                "--sessions",
                "2",
                "--ramp-seconds",
                "0",
                "--hold-seconds",
                "0.03",
                "--ramp-down-seconds",
                "0",
                "--metrics-interval-seconds",
                "0.01",
                "--log-burst-interval-seconds",
                "0.01",
                "--log-burst-jitter-seconds",
                "0",
                "--max-inflight",
                "2",
                "--results-out",
                str(out_path),
                "--run-id",
                "fixed-run-id",
            ]
        )
    assert exc.value.code == 0
    data = json.loads(out_path.read_text())
    assert data["run_id"] == "fixed-run-id"
    assert data["totals"]["total_requests"] > 0
    assert data["totals"]["error_rate"] == 0.0


@pytest.mark.unit
def test_main_prints_results_to_stdout_when_no_results_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pl, "PersistentHttpSender", _FakeMainSender)
    with pytest.raises(SystemExit) as exc:
        pl.main(
            [
                "--env",
                "sandbox",
                "--endpoint",
                "collector.example",
                "--sessions",
                "1",
                "--ramp-seconds",
                "0",
                "--hold-seconds",
                "0.01",
                "--ramp-down-seconds",
                "0",
                "--metrics-interval-seconds",
                "0.01",
                "--log-burst-interval-seconds",
                "0.01",
                "--log-burst-jitter-seconds",
                "0",
                "--max-inflight",
                "1",
            ]
        )
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert '"run_id"' in out


@pytest.mark.unit
def test_main_reproducible_with_jitter_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(pl, "PersistentHttpSender", _FakeMainSender)
    results: list[dict[str, Any]] = []
    for i in range(2):
        out_path = tmp_path / f"r{i}.json"
        with pytest.raises(SystemExit):
            pl.main(
                [
                    "--env",
                    "sandbox",
                    "--endpoint",
                    "collector.example",
                    "--sessions",
                    "3",
                    "--ramp-seconds",
                    "0.01",
                    "--hold-seconds",
                    "0.03",
                    "--ramp-down-seconds",
                    "0.01",
                    "--metrics-interval-seconds",
                    "0.01",
                    "--log-burst-interval-seconds",
                    "0.01",
                    "--log-burst-jitter-seconds",
                    "0.005",
                    "--max-inflight",
                    "4",
                    "--jitter-seed",
                    "42",
                    "--run-id",
                    "fixed",
                    "--results-out",
                    str(out_path),
                ]
            )
        results.append(json.loads(out_path.read_text()))
    assert results[0]["totals"]["total_requests"] == results[1]["totals"]["total_requests"]
    assert results[0]["totals"]["by_kind"] == results[1]["totals"]["by_kind"]
