"""Virtual-user load driver for a telemetry-collector performance test.

Invoked as::

    uv run python -m scripts.perf_loadgen --env sandbox --sessions 2000 \\
        --ramp-seconds 300 --hold-seconds 1800 --ramp-down-seconds 120 \\
        --results-out perf-results-sandbox.json
    make perf-loadgen ENV=sandbox

Simulates ``--sessions`` logical Claude Code sessions hitting the collector
concurrently. Each session behaves the way the real product does (see
``docs/data-model.md`` / ``docs/sending-telemetry.md``): a structured
OTLP/HTTP metrics export roughly every ``--metrics-interval-seconds``
(default 60s, matching ``OTEL_METRIC_EXPORT_INTERVAL=60000``) and a
structured OTLP/HTTP log-event burst roughly every
``--log-burst-interval-seconds`` with up to
``--log-burst-jitter-seconds`` of jitter (real usage-event emission is
bursty/event-driven, not perfectly periodic). Sessions start staggered
across ``--ramp-seconds`` and stop staggered across
``--ramp-down-seconds``, holding steady state for ``--hold-seconds`` in
between, so the realistic aggregate request rate is tens-to-hundreds of
requests per second -- never ``--sessions`` simultaneous requests at once.

Every record reuses the exact Claude-shaped OTLP payload builders the
existing OTLP e2e harness already validated end to end
(``scripts.otlp_e2e_loadgen.build_structured_events_request`` /
``build_structured_metrics_request``): resource ``service.name`` is the
synthetic marker ``constants.E2E_STRUCTURED_SERVICE_NAME``, so every
record this driver sends lands under the reserved
``tool = constants.E2E_TOOL_VALUE`` ("e2e-smoke") partition and is cleaned
up by the same tooling that already manages that partition.

Architecture (see the module docstrings on the classes below for detail):

  * ``session_windows`` / ``iter_schedule`` -- a PURE, lazily-evaluated
    scheduler. Memory is bounded by O(sessions) (a small min-heap of each
    session's next due event) regardless of how long the run holds steady
    state; no full-run event list is ever materialized.
  * ``LoadRunner`` -- a thin real-time pump that waits for each scheduled
    event's due time (using the same ``threading.Event().wait()``
    active-wait idiom ``scripts.e2e_common.poll_until`` already uses
    elsewhere in this harness -- this is the load driver's core
    request-pacing mechanism, not a "wait for external readiness"
    shortcut) and dispatches it to a bounded pool of workers, each holding
    one persistent keep-alive HTTPS connection (``PersistentHttpSender``).
  * ``ResultsAggregator`` -- a thread-safe, memory-bounded incremental
    aggregator (reservoir-sampled latency percentiles, O(#buckets)
    throughput/error time series) writable from every worker thread.

Exit codes::

    0 -- the run completed (results, including any recorded per-request
         errors, are written to --results-out / stdout; pass/fail judgement
         against thresholds is scripts.perf_report's job, not this driver's)
    2 -- usage error (bad ENV / CLI value / malformed --resolve)
"""

from __future__ import annotations

import argparse
import dataclasses
import heapq
import http.client
import json
import math
import pathlib
import random
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from scripts import constants, e2e_common
from scripts import otlp_e2e_loadgen as lg

# ---------------------------------------------------------------------------
# Scheduling (pure, fully unit-testable -- no I/O, no wall-clock waiting)
# ---------------------------------------------------------------------------

#: A metrics export mimics OTEL_METRIC_EXPORT_INTERVAL; a log burst mimics an
#: event-driven cluster of usage-event records (session start, tool calls, ...).
_KIND_METRICS = "metrics"
_KIND_LOG_BURST = "log_burst"


@dataclasses.dataclass(frozen=True)
class ScheduledEvent:
    """One due virtual-user action.

    Attributes:
        due_at: Seconds elapsed since run start at which this event fires.
        session_id: The 0-based logical session index that owns this event.
        kind: ``_KIND_METRICS`` or ``_KIND_LOG_BURST``.
    """

    due_at: float
    session_id: int
    kind: str


def session_windows(
    sessions: int,
    ramp_seconds: float,
    hold_seconds: float,
    ramp_down_seconds: float,
) -> list[tuple[float, float]]:
    """Return each session's ``(start_at, stop_at)`` seconds-from-run-start.

    Sessions start staggered linearly across ``[0, ramp_seconds)`` (session 0
    first, session ``sessions - 1`` last) so the aggregate request rate ramps
    up smoothly instead of firing every virtual user simultaneously at
    ``t=0``. They stop staggered the same way, in the same order, across the
    trailing ``ramp_down_seconds`` window, so the aggregate rate ramps back
    down smoothly too. Every session holds steady state for exactly
    ``hold_seconds`` in between.

    Args:
        sessions: Number of logical virtual-user sessions (>= 1).
        ramp_seconds: Width of the ramp-up window (>= 0).
        hold_seconds: Width of the steady-state hold window (>= 0).
        ramp_down_seconds: Width of the ramp-down window (>= 0).

    Returns:
        A list of length ``sessions``, index i = session i's window.

    Raises:
        ValueError: When ``sessions < 1`` or any duration is negative.
    """
    if sessions < 1:
        raise ValueError(f"--sessions must be >= 1, got {sessions}")
    if ramp_seconds < 0 or hold_seconds < 0 or ramp_down_seconds < 0:
        raise ValueError(
            "--ramp-seconds, --hold-seconds, and --ramp-down-seconds must all be >= 0 "
            f"(got ramp={ramp_seconds}, hold={hold_seconds}, ramp_down={ramp_down_seconds})"
        )
    windows: list[tuple[float, float]] = []
    for i in range(sessions):
        fraction = i / sessions
        start_at = ramp_seconds * fraction
        stop_at = ramp_seconds + hold_seconds + ramp_down_seconds * fraction
        windows.append((start_at, stop_at))
    return windows


def iter_schedule(
    windows: list[tuple[float, float]],
    metrics_interval_seconds: float,
    log_burst_interval_seconds: float,
    log_burst_jitter_seconds: float,
    rng: random.Random,
) -> Iterator[ScheduledEvent]:
    """Lazily yield every metrics/log-burst event in due-time order.

    Implemented as a min-heap holding at most two pending entries per session
    (its next metrics export and its next log burst): each time an entry is
    popped and yielded, that session/kind's NEXT occurrence is computed and
    pushed back (if it still falls within the session's window). Memory is
    therefore bounded by O(sessions) regardless of how long the run holds
    steady state -- the full schedule is never materialized as one list.

    Metrics exports fire on a fixed cadence (mirroring
    ``OTEL_METRIC_EXPORT_INTERVAL``, a real periodic timer). Log bursts fire
    on the configured cadence plus fresh independent jitter each time
    (mirroring event-driven, bursty usage-event emission) -- jitter is drawn
    uniformly from ``[-log_burst_jitter_seconds, +log_burst_jitter_seconds]``.

    Args:
        windows: Per-session ``(start_at, stop_at)`` pairs, e.g. from
            ``session_windows``.
        metrics_interval_seconds: Seconds between one session's consecutive
            metrics exports (> 0).
        log_burst_interval_seconds: Mean seconds between one session's
            consecutive log bursts (> 0).
        log_burst_jitter_seconds: Half-width of the uniform jitter applied to
            every log-burst interval (>= 0).
        rng: The random source for jitter draws (injected for determinism).

    Yields:
        Every event across every session, in non-decreasing ``due_at`` order
        except for the (expected, harmless) local reordering jitter can
        introduce between two events already close together in time.

    Raises:
        ValueError: When an interval is <= 0 or the jitter is negative.
    """
    if metrics_interval_seconds <= 0:
        raise ValueError(f"--metrics-interval-seconds must be > 0, got {metrics_interval_seconds}")
    if log_burst_interval_seconds <= 0:
        raise ValueError(
            f"--log-burst-interval-seconds must be > 0, got {log_burst_interval_seconds}"
        )
    if log_burst_jitter_seconds < 0:
        raise ValueError(f"--log-burst-jitter-seconds must be >= 0, got {log_burst_jitter_seconds}")

    heap: list[tuple[float, int, str]] = []
    for session_id, (start_at, stop_at) in enumerate(windows):
        first_metrics = start_at + metrics_interval_seconds
        if first_metrics <= stop_at:
            heapq.heappush(heap, (first_metrics, session_id, _KIND_METRICS))
        jitter = rng.uniform(-log_burst_jitter_seconds, log_burst_jitter_seconds)
        first_log_burst = max(start_at, start_at + log_burst_interval_seconds + jitter)
        if first_log_burst <= stop_at:
            heapq.heappush(heap, (first_log_burst, session_id, _KIND_LOG_BURST))

    while heap:
        due_at, session_id, kind = heapq.heappop(heap)
        yield ScheduledEvent(due_at=due_at, session_id=session_id, kind=kind)
        _, stop_at = windows[session_id]
        if kind == _KIND_METRICS:
            next_due = due_at + metrics_interval_seconds
        else:
            jitter = rng.uniform(-log_burst_jitter_seconds, log_burst_jitter_seconds)
            next_due = due_at + log_burst_interval_seconds + jitter
        if next_due <= stop_at:
            heapq.heappush(heap, (next_due, session_id, kind))


# ---------------------------------------------------------------------------
# Results aggregation (thread-safe, memory-bounded)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RequestOutcome:
    """The outcome of dispatching one ``ScheduledEvent``.

    Attributes:
        session_id: The originating session's 0-based index.
        kind: ``_KIND_METRICS`` or ``_KIND_LOG_BURST``.
        latency_seconds: Wall-clock request latency (send to response-read).
        status: The observed HTTP status, or ``0`` on transport failure.
        record_count: Number of OTLP records the request carried.
        error: ``None`` when ``status`` was one of the request's expected
            statuses; otherwise a short diagnostic string.
    """

    session_id: int
    kind: str
    latency_seconds: float
    status: int
    record_count: int
    error: str | None


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Return the linear-interpolated ``pct``-th percentile of sorted samples.

    Args:
        sorted_samples: Ascending-sorted sample values.
        pct: Percentile in ``[0, 100]``.

    Returns:
        The interpolated percentile value, or ``0.0`` for an empty input.
    """
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    rank = (len(sorted_samples) - 1) * (pct / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_samples[int(rank)]
    lower_weight = sorted_samples[lower] * (upper - rank)
    upper_weight = sorted_samples[upper] * (rank - lower)
    return lower_weight + upper_weight


def _percentiles(samples: list[float], pcts: tuple[int, ...]) -> dict[int, float]:
    """Return ``{pct: value}`` for every percentile in ``pcts``."""
    ordered = sorted(samples)
    return {pct: _percentile(ordered, pct) for pct in pcts}


class _Reservoir:
    """A fixed-capacity uniform random sample (Algorithm R), thread-unsafe.

    Bounds retained latency samples to ``capacity`` regardless of how many
    values are offered, while keeping the retained subset a statistically
    representative uniform sample of every value ever offered -- so
    percentiles computed over the reservoir approximate the true
    percentiles over the full (unbounded, never-materialized) stream.
    Callers are responsible for their own locking (``ResultsAggregator``
    holds a lock around every ``offer`` call).
    """

    def __init__(self, capacity: int, rng: random.Random) -> None:
        if capacity < 1:
            raise ValueError(f"reservoir capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._rng = rng
        self._samples: list[float] = []
        self._seen = 0

    def offer(self, value: float) -> None:
        self._seen += 1
        if len(self._samples) < self._capacity:
            self._samples.append(value)
            return
        index = self._rng.randint(0, self._seen - 1)
        if index < self._capacity:
            self._samples[index] = value

    @property
    def seen(self) -> int:
        return self._seen

    @property
    def samples(self) -> list[float]:
        return self._samples


@dataclasses.dataclass
class _Bucket:
    """Accumulators for one fixed-width time bucket."""

    requests: int = 0
    records: int = 0
    errors: int = 0
    latency_sum_seconds: float = 0.0
    by_kind: dict[str, int] = dataclasses.field(default_factory=dict)


class ResultsAggregator:
    """Thread-safe, memory-bounded incremental aggregator for run results.

    Every worker thread calls ``record`` once per completed request; a
    single lock serializes updates (contention is negligible next to one
    HTTPS round trip). Two levels of latency percentile are kept, both
    memory-bounded by reservoir sampling (see ``_Reservoir``) rather than
    retaining every sample for the life of the run:

      * a single global reservoir (``max_latency_samples``) backing the
        headline p50/p90/p99/min/max/mean;
      * one small per-bucket reservoir (``max_samples_per_bucket``) per time
        bucket, backing the "percentiles over time" chart series.

    Throughput/error/record counts are exact (not sampled) and cost O(1)
    memory per distinct status code and O(run_duration / bucket_seconds)
    memory for the bucket time series -- both independent of session count.
    """

    def __init__(
        self,
        bucket_seconds: float,
        max_latency_samples: int,
        max_samples_per_bucket: int,
        rng: random.Random,
    ) -> None:
        if bucket_seconds <= 0:
            raise ValueError(f"--bucket-seconds must be > 0, got {bucket_seconds}")
        self._bucket_seconds = bucket_seconds
        self._max_samples_per_bucket = max_samples_per_bucket
        self._rng = rng
        self._lock = threading.Lock()
        self._global_reservoir = _Reservoir(max_latency_samples, rng)
        self._latency_min: float | None = None
        self._latency_max: float | None = None
        self._latency_sum = 0.0
        self._status_counts: dict[str, int] = {}
        self._by_kind_totals: dict[str, int] = {}
        self._total_requests = 0
        self._total_records = 0
        self._total_errors = 0
        self._buckets: dict[int, _Bucket] = {}
        self._bucket_reservoirs: dict[int, _Reservoir] = {}

    def record(self, outcome: RequestOutcome, elapsed_seconds: float) -> None:
        """Fold one completed request's outcome into the aggregate state.

        Args:
            outcome: The dispatched request's result.
            elapsed_seconds: Seconds since run start when the response was
                observed (used only for time-bucket assignment).
        """
        with self._lock:
            self._total_requests += 1
            self._total_records += outcome.record_count
            is_error = outcome.error is not None
            if is_error:
                self._total_errors += 1
            status_key = str(outcome.status)
            self._status_counts[status_key] = self._status_counts.get(status_key, 0) + 1
            self._by_kind_totals[outcome.kind] = self._by_kind_totals.get(outcome.kind, 0) + 1

            self._global_reservoir.offer(outcome.latency_seconds)
            self._latency_sum += outcome.latency_seconds
            self._latency_min = (
                outcome.latency_seconds
                if self._latency_min is None
                else min(self._latency_min, outcome.latency_seconds)
            )
            self._latency_max = (
                outcome.latency_seconds
                if self._latency_max is None
                else max(self._latency_max, outcome.latency_seconds)
            )

            bucket_index = max(0, int(elapsed_seconds // self._bucket_seconds))
            bucket = self._buckets.setdefault(bucket_index, _Bucket())
            bucket.requests += 1
            bucket.records += outcome.record_count
            bucket.latency_sum_seconds += outcome.latency_seconds
            bucket.by_kind[outcome.kind] = bucket.by_kind.get(outcome.kind, 0) + 1
            if is_error:
                bucket.errors += 1
            reservoir = self._bucket_reservoirs.setdefault(
                bucket_index, _Reservoir(self._max_samples_per_bucket, self._rng)
            )
            reservoir.offer(outcome.latency_seconds)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the aggregate state so far."""
        with self._lock:
            global_pcts = _percentiles(self._global_reservoir.samples, (50, 90, 99))
            total = self._total_requests
            buckets = []
            for index in sorted(self._buckets):
                bucket = self._buckets[index]
                bucket_pcts = _percentiles(self._bucket_reservoirs[index].samples, (50, 90, 99))
                buckets.append(
                    {
                        "bucket_start_seconds": index * self._bucket_seconds,
                        "requests": bucket.requests,
                        "records": bucket.records,
                        "errors": bucket.errors,
                        "requests_per_second": bucket.requests / self._bucket_seconds,
                        "records_per_second": bucket.records / self._bucket_seconds,
                        "error_rate": (bucket.errors / bucket.requests) if bucket.requests else 0.0,
                        "avg_latency_ms": (
                            (bucket.latency_sum_seconds / bucket.requests) * 1000
                            if bucket.requests
                            else 0.0
                        ),
                        "latency_ms": {
                            "p50": bucket_pcts[50] * 1000,
                            "p90": bucket_pcts[90] * 1000,
                            "p99": bucket_pcts[99] * 1000,
                        },
                        "by_kind": dict(sorted(bucket.by_kind.items())),
                    }
                )
            return {
                "totals": {
                    "total_requests": total,
                    "total_records": self._total_records,
                    "total_errors": self._total_errors,
                    "error_rate": (self._total_errors / total) if total else 0.0,
                    "status_counts": dict(sorted(self._status_counts.items())),
                    "by_kind": dict(sorted(self._by_kind_totals.items())),
                },
                "latency_ms": {
                    "p50": global_pcts[50] * 1000,
                    "p90": global_pcts[90] * 1000,
                    "p99": global_pcts[99] * 1000,
                    "min": (self._latency_min or 0.0) * 1000,
                    "max": (self._latency_max or 0.0) * 1000,
                    "mean": ((self._latency_sum / total) * 1000) if total else 0.0,
                    "sample_count": len(self._global_reservoir.samples),
                    "samples_seen": self._global_reservoir.seen,
                },
                "buckets": buckets,
            }


# ---------------------------------------------------------------------------
# Transport: persistent (keep-alive) HTTPS sender
# ---------------------------------------------------------------------------


class PersistentHttpSender:
    """Thread-affine OTLP/HTTP sender reusing one keep-alive connection per thread.

    ``otlp_e2e_loadgen.OtlpHttpSender`` deliberately opens a fresh HTTPS
    connection (full TCP + TLS handshake) on every ``post`` -- correct for a
    small, bursty conformance run, but it cannot sustain the request rate a
    realistic ~2000-session load needs. This sender instead keeps exactly
    ONE persistent connection PER CALLING THREAD (stored in
    ``threading.local()``), reused across every request that thread sends.
    HTTPS connections are not safe to share ACROSS threads, so this is only
    safe when each thread's connection is never touched by another thread --
    true here because ``LoadRunner`` dispatches through a
    ``ThreadPoolExecutor``, which pins each submitted task to exactly one
    worker thread for its duration, and no two tasks ever run concurrently
    on the same worker thread.
    """

    def __init__(
        self,
        host: str,
        port: int,
        connect_ip: str | None,
        timeout: float,
        user_agent: str,
        connection_factory: lg.ConnectionFactory | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_ip = connect_ip
        self._timeout = timeout
        self._user_agent = user_agent
        self._factory: lg.ConnectionFactory = connection_factory or lg._default_connection_factory
        self._local = threading.local()

    def _connection(self) -> http.client.HTTPSConnection:
        conn: http.client.HTTPSConnection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._factory(self._host, self._port, self._connect_ip, self._timeout)
            self._local.conn = conn
        return conn

    def _reset_connection(self) -> None:
        conn: http.client.HTTPSConnection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
        self._local.conn = None

    def _send(self, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
        conn = self._connection()
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        status = response.status
        reason = response.reason
        response.read()
        return status, reason

    def post(self, path: str, body: bytes, content_type: str) -> tuple[int, str]:
        """POST ``body`` to ``https://<host><path>`` over this thread's connection.

        A realistic ``User-Agent`` is always sent (the collector WAF blocks
        User-Agent-less requests, same as ``otlp_e2e_loadgen.OtlpHttpSender``).
        A keep-alive connection can go stale between requests (e.g. the peer
        idle-timed it out); exactly one transparent retry on a fresh
        connection is attempted so that ordinary staleness is not conflated
        with a real request failure. A second failure is surfaced as status
        0 with the exception text as the reason -- never masked, never
        retried again.

        Args:
            path: The request path (e.g. ``/v1/metrics``).
            body: The encoded request body.
            content_type: The Content-Type header value.

        Returns:
            ``(http_status, reason)``.
        """
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "User-Agent": self._user_agent,
            "Connection": "keep-alive",
        }
        try:
            return self._send(path, body, headers)
        except OSError, http.client.HTTPException:
            self._reset_connection()
            try:
                return self._send(path, body, headers)
            except (OSError, http.client.HTTPException) as exc:
                self._reset_connection()
                return 0, f"transport-error: {exc}"

    def close(self) -> None:
        """Close this thread's persistent connection, if one is open."""
        self._reset_connection()


def build_dispatch(
    sender: PersistentHttpSender,
    run_id: str,
    encoding: str,
    clock: Callable[[], float] = time.perf_counter,
) -> Callable[[ScheduledEvent], RequestOutcome]:
    """Return a dispatch function binding a sender/run_id/encoding to events.

    Args:
        sender: The persistent-connection HTTP sender to post through.
        run_id: This run's identifier, embedded in every record's payload.
        encoding: ``protobuf`` or ``json``.
        clock: Monotonic latency clock (injected for deterministic tests).

    Returns:
        A callable turning one ``ScheduledEvent`` into a ``RequestOutcome``.
    """

    def _dispatch(event: ScheduledEvent) -> RequestOutcome:
        timestamp = e2e_common.utc_now_iso()
        plan = (
            lg.build_structured_metrics_request(timestamp, run_id, encoding)
            if event.kind == _KIND_METRICS
            else lg.build_structured_events_request(timestamp, run_id, encoding)
        )
        start = clock()
        status, reason = sender.post(plan.path, plan.body, plan.content_type)
        latency_seconds = clock() - start
        error = None if status in plan.expected_statuses else f"status={status} reason={reason}"
        return RequestOutcome(
            session_id=event.session_id,
            kind=event.kind,
            latency_seconds=latency_seconds,
            status=status,
            record_count=len(plan.specs),
            error=error,
        )

    return _dispatch


# ---------------------------------------------------------------------------
# Real-time pump
# ---------------------------------------------------------------------------


def _default_waiter(seconds: float) -> None:
    """Block for ``seconds`` using the active-wait idiom, not ``time.sleep``.

    Mirrors ``scripts.e2e_common.poll_until``'s own pacing primitive
    (``threading.Event().wait(timeout=...)``); a fresh ``Event`` is used
    per call since nothing ever sets it -- the call always blocks for the
    full timeout (or returns immediately for ``seconds <= 0``).
    """
    if seconds <= 0:
        return
    threading.Event().wait(timeout=seconds)


class LoadRunner:
    """Real-time pump: waits for each event's due time, then dispatches it.

    Concurrency is bounded by a ``threading.Semaphore(max_inflight)``
    acquired before every submit and released when the dispatched task
    completes: at most ``max_inflight`` requests are ever in flight at
    once. If dispatch momentarily falls behind schedule, the pump blocks on
    the semaphore rather than queuing unboundedly many pending futures --
    natural backpressure, not an unbounded queue.
    """

    def __init__(
        self,
        dispatch: Callable[[ScheduledEvent], RequestOutcome],
        aggregator: ResultsAggregator,
        max_inflight: int,
        clock: Callable[[], float] = time.monotonic,
        waiter: Callable[[float], None] = _default_waiter,
    ) -> None:
        if max_inflight < 1:
            raise ValueError(f"--max-inflight must be >= 1, got {max_inflight}")
        self._dispatch = dispatch
        self._aggregator = aggregator
        self._clock = clock
        self._waiter = waiter
        self._semaphore = threading.Semaphore(max_inflight)
        self._executor = ThreadPoolExecutor(max_workers=max_inflight)

    def _task(self, event: ScheduledEvent, run_start: float) -> None:
        try:
            try:
                outcome = self._dispatch(event)
            except Exception as exc:
                # One request's failure (including an unexpected bug in dispatch
                # itself) must never abort the whole run. Surfaced loudly
                # (printed immediately) AND recorded, never swallowed.
                print(
                    f"ERROR: perf_loadgen dispatch failed for session={event.session_id} "
                    f"kind={event.kind}: {exc}",
                    file=sys.stderr,
                )
                outcome = RequestOutcome(
                    session_id=event.session_id,
                    kind=event.kind,
                    latency_seconds=0.0,
                    status=0,
                    record_count=0,
                    error=f"internal-error: {exc}",
                )
            elapsed_seconds = self._clock() - run_start
            self._aggregator.record(outcome, elapsed_seconds)
        finally:
            self._semaphore.release()

    def run(self, schedule: Iterator[ScheduledEvent]) -> None:
        """Pump ``schedule`` to completion, blocking until every event fires."""
        run_start = self._clock()
        for event in schedule:
            deadline = run_start + event.due_at
            remaining = deadline - self._clock()
            if remaining > 0:
                self._waiter(remaining)
            self._semaphore.acquire()
            self._executor.submit(self._task, event, run_start)
        self._executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the load driver CLI."""
    parser = argparse.ArgumentParser(
        prog="scripts.perf_loadgen",
        description=(
            "Virtual-user load driver simulating concurrent Claude Code sessions "
            "against the OTLP telemetry collector."
        ),
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment to target (resolves the published collector FQDN from domains.json).",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help=(
            "Override the collector host (default: collector.<pretty_apex> for --env). Accepts a "
            "bare host or a full https:// URL (e.g. the perf-test workflow's CloudFront "
            "default-domain collector_endpoint https://d123.cloudfront.net/v1/logs); only the "
            "host is used -- the OTLP signal path and --port are applied by the driver."
        ),
    )
    parser.add_argument(
        "--resolve",
        default=None,
        help="curl-style HOST:IP (or HOST:PORT:IP) to pin the edge IP while keeping SNI.",
    )
    parser.add_argument(
        "--connect-to",
        dest="connect_to",
        default=None,
        help="curl-style HOST:PORT:CONNECT_HOST:CONNECT_PORT (or CONNECT_HOST) to pin the edge IP.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=constants.E2E_HTTPS_PORT,
        help=f"HTTPS port (default: {constants.E2E_HTTPS_PORT}).",
    )
    parser.add_argument(
        "--encoding",
        default="protobuf",
        choices=("protobuf", "json"),
        help="OTLP/HTTP encoding (default: protobuf).",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=2000,
        help="Number of logical virtual-user sessions to simulate (default: 2000).",
    )
    parser.add_argument(
        "--ramp-seconds",
        dest="ramp_seconds",
        type=float,
        default=300.0,
        help="Seconds over which sessions stagger their start (default: 300).",
    )
    parser.add_argument(
        "--hold-seconds",
        dest="hold_seconds",
        type=float,
        default=1800.0,
        help="Seconds every session holds steady state after starting (default: 1800).",
    )
    parser.add_argument(
        "--ramp-down-seconds",
        dest="ramp_down_seconds",
        type=float,
        default=120.0,
        help="Seconds over which sessions stagger their stop (default: 120).",
    )
    parser.add_argument(
        "--metrics-interval-seconds",
        dest="metrics_interval_seconds",
        type=float,
        default=60.0,
        help="Seconds between one session's metrics exports (default: 60, matches "
        "OTEL_METRIC_EXPORT_INTERVAL=60000).",
    )
    parser.add_argument(
        "--log-burst-interval-seconds",
        dest="log_burst_interval_seconds",
        type=float,
        default=45.0,
        help="Mean seconds between one session's log-event bursts (default: 45).",
    )
    parser.add_argument(
        "--log-burst-jitter-seconds",
        dest="log_burst_jitter_seconds",
        type=float,
        default=15.0,
        help="Uniform +/- jitter applied to the log-burst interval (default: 15).",
    )
    parser.add_argument(
        "--max-inflight",
        dest="max_inflight",
        type=int,
        default=64,
        help="Bounded worker-pool size / max concurrent in-flight requests (default: 64).",
    )
    parser.add_argument(
        "--bucket-seconds",
        dest="bucket_seconds",
        type=float,
        default=30.0,
        help="Width of each time bucket in the results time series (default: 30).",
    )
    parser.add_argument(
        "--max-latency-samples",
        dest="max_latency_samples",
        type=int,
        default=50000,
        help="Cap on retained samples for the global latency-percentile reservoir "
        "(default: 50000). Bounds memory independent of run size.",
    )
    parser.add_argument(
        "--max-samples-per-bucket",
        dest="max_samples_per_bucket",
        type=int,
        default=500,
        help="Cap on retained samples per time bucket for the per-bucket latency-"
        "percentile reservoir (default: 500).",
    )
    parser.add_argument(
        "--jitter-seed",
        dest="jitter_seed",
        type=int,
        default=None,
        help="Seed the jitter/reservoir random source for a reproducible run "
        "(default: unseeded / OS entropy).",
    )
    parser.add_argument(
        "--timeout-seconds",
        dest="timeout_seconds",
        type=float,
        default=float(constants.E2E_HTTP_TIMEOUT),
        help=f"Per-request connect/read timeout in seconds "
        f"(default: {constants.E2E_HTTP_TIMEOUT}).",
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="Run id (default: generated). Every record's payload embeds this as $.run_id.",
    )
    parser.add_argument(
        "--results-out",
        dest="results_out",
        default=None,
        help="Path to write the results JSON (default: stdout only).",
    )
    parser.add_argument(
        "--user-agent",
        dest="user_agent",
        default=constants.E2E_USER_AGENT,
        help=f"User-Agent header sent on every request (default: {constants.E2E_USER_AGENT}).",
    )
    return parser


def build_results(
    args: argparse.Namespace,
    host: str,
    connect_ip: str | None,
    run_id: str,
    started_at: str,
    finished_at: str,
    aggregator: ResultsAggregator,
) -> dict[str, Any]:
    """Assemble the final results manifest: run config + aggregate snapshot.

    Args:
        args: The parsed CLI namespace (its values become the recorded config).
        host: The resolved collector host.
        connect_ip: The resolved connect IP, if any (``--resolve``/``--connect-to``).
        run_id: This run's identifier.
        started_at: ISO-8601 run start.
        finished_at: ISO-8601 run finish.
        aggregator: The populated ``ResultsAggregator``.

    Returns:
        A JSON-serializable manifest: the client-side half of the perf report.
    """
    return {
        "run_id": run_id,
        "env": args.env,
        "endpoint_host": host,
        "connect_ip": connect_ip,
        "port": args.port,
        "encoding": args.encoding,
        "tool_value": constants.E2E_TOOL_VALUE,
        "sessions": args.sessions,
        "ramp_seconds": args.ramp_seconds,
        "hold_seconds": args.hold_seconds,
        "ramp_down_seconds": args.ramp_down_seconds,
        "metrics_interval_seconds": args.metrics_interval_seconds,
        "log_burst_interval_seconds": args.log_burst_interval_seconds,
        "log_burst_jitter_seconds": args.log_burst_jitter_seconds,
        "max_inflight": args.max_inflight,
        "started_at": started_at,
        "finished_at": finished_at,
        **aggregator.snapshot(),
    }


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the virtual-user load driver."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        host = lg._resolve_endpoint_host(args)
        connect_ip: str | None = None
        if args.resolve:
            connect_ip = lg.parse_resolve(args.resolve, host)
        if args.connect_to:
            connect_ip = lg.parse_connect_to(args.connect_to)
        run_id = args.run_id or e2e_common.generate_run_id()
        windows = session_windows(
            args.sessions, args.ramp_seconds, args.hold_seconds, args.ramp_down_seconds
        )
        rng = random.Random(args.jitter_seed)
    except (e2e_common.E2EUsageError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    sender = PersistentHttpSender(
        host=host,
        port=args.port,
        connect_ip=connect_ip,
        timeout=args.timeout_seconds,
        user_agent=args.user_agent,
    )
    dispatch = build_dispatch(sender, run_id, args.encoding)
    aggregator = ResultsAggregator(
        bucket_seconds=args.bucket_seconds,
        max_latency_samples=args.max_latency_samples,
        max_samples_per_bucket=args.max_samples_per_bucket,
        rng=rng,
    )
    runner = LoadRunner(dispatch=dispatch, aggregator=aggregator, max_inflight=args.max_inflight)
    schedule = iter_schedule(
        windows,
        args.metrics_interval_seconds,
        args.log_burst_interval_seconds,
        args.log_burst_jitter_seconds,
        rng,
    )

    started_at = e2e_common.utc_now_iso()
    print(
        f"perf_loadgen run_id={run_id} env={args.env} host={host} sessions={args.sessions} "
        f"ramp={args.ramp_seconds}s hold={args.hold_seconds}s ramp_down={args.ramp_down_seconds}s"
    )
    runner.run(schedule)
    finished_at = e2e_common.utc_now_iso()

    results = build_results(args, host, connect_ip, run_id, started_at, finished_at, aggregator)
    totals = results["totals"]
    print(
        f"perf_loadgen done: requests={totals['total_requests']} "
        f"records={totals['total_records']} error_rate={totals['error_rate']:.4f} "
        f"p50={results['latency_ms']['p50']:.1f}ms p99={results['latency_ms']['p99']:.1f}ms"
    )

    if args.results_out:
        out_path = pathlib.Path(args.results_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"results written to {args.results_out}")
    else:
        print(json.dumps(results, indent=2))

    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
