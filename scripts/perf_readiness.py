"""Perf-test collector reachability probe (single-request readiness gate).

Invoked as::

    uv run python -m scripts.perf_readiness --endpoint https://d123.cloudfront.net/v1/logs
    make perf-readiness PERF_ENDPOINT=https://d123.cloudfront.net/v1/logs

A fresh ephemeral sandbox stand-up cannot create the cross-account public DNS
delegation, so the pretty collector FQDN (``collector.<pretty_apex>``) does not
resolve. The perf-test lane therefore reaches the collector via the CloudFront
distribution's DEFAULT domain (``dXXXX.cloudfront.net``), which resolves and
serves with no custom DNS. This module validates that a SINGLE request reaches
the collector at that endpoint BEFORE the full load run starts.

The probe POSTs a minimal valid OTLP-logs JSON request (an empty
``ExportLogsServiceRequest`` -- ``{"resourceLogs":[]}`` -- so nothing synthetic
lands in the lake) with a realistic ``User-Agent`` (the collector WAF blocks
User-Agent-less requests) and polls until the edge answers at the HTTP layer:

  * ANY structured HTTP response whose status is < 500 (200/400/403/404/415/429
    ...) proves the edge is serving -> READY. A minimal valid OTLP-logs request
    to a fully-serving collector returns 200; a 4xx still proves the HTTP layer
    is up (which is all this readiness gate asserts).
  * a transport/TLS/connection error (surfaced as status 0) OR a gateway 5xx
    (502/503/504 -- CloudFront reached but the origin is not serving yet) means
    KEEP POLLING until the edge comes up.

The poll budget is bounded and env-driven
(``PERF_READINESS_TIMEOUT`` / ``PERF_READINESS_POLL_INTERVAL`` via
``scripts.constants``; unset falls back to sane defaults). The inter-poll pause
uses ``scripts.e2e_common.poll_until`` (``threading.Event.wait``) -- no
``time.sleep`` synchronization. On timeout the probe fails fast (non-zero exit)
with an actionable diagnostic.

Exit codes::

    0 -- the collector answered at the HTTP layer within the budget
    1 -- the budget expired before any non-5xx HTTP response (fail-fast)
    2 -- usage error (non-https endpoint / bad CLI value)
"""

from __future__ import annotations

import argparse
import http.client
import json
import pathlib
import sys
import urllib.parse
from collections.abc import Callable

from scripts import constants, e2e_common

# A minimal valid OTLP/HTTP logs request: the JSON encoding of an empty
# ExportLogsServiceRequest. It carries zero LogRecords, so a fully-serving
# collector accepts it (HTTP 200) without landing any synthetic data in the lake.
_MINIMAL_OTLP_LOGS_JSON: bytes = b'{"resourceLogs":[]}'

# HTTP statuses at or above this value are gateway/origin-not-ready responses
# (502/503/504 ...): the CloudFront edge answered but the origin is not serving
# yet, so the probe keeps polling. Anything below proves the HTTP layer is up.
_SERVER_ERROR_FLOOR = 500

# Transport/TLS/connection failure sentinel (no HTTP response was received).
_TRANSPORT_ERROR_STATUS = 0


class ReadinessUsageError(ValueError):
    """Raised on a bad CLI/endpoint value -- exit 2."""


def is_serving(status: int) -> bool:
    """Return True when ``status`` proves the collector is serving at the HTTP layer.

    A structured HTTP response with a status strictly below
    ``_SERVER_ERROR_FLOOR`` (200/400/403/404/415/429 ...) proves the edge is up
    and answering. The transport-error sentinel (0) and any 5xx gateway/origin
    response (502/503/504) are NOT serving -- the caller keeps polling.

    Args:
        status: The observed HTTP status, or ``0`` on transport failure.

    Returns:
        True when the status proves the HTTP layer is serving.
    """
    return status != _TRANSPORT_ERROR_STATUS and status < _SERVER_ERROR_FLOOR


def _default_poster(url: str, body: bytes, content_type: str, user_agent: str) -> tuple[int, str]:
    """POST ``body`` to an ``https://`` ``url`` and return ``(status, detail)``.

    Only ``https://`` URLs are permitted; any other scheme raises
    ``ReadinessUsageError`` (no ``file://`` / custom-scheme risk). A transport,
    TLS, or connection failure is surfaced as status ``0`` with the exception
    text as the detail -- never masked, so the caller keeps polling instead of
    treating a connection error as a serving response.

    Args:
        url: The full ``https://`` endpoint (e.g. ``https://d123.cloudfront.net/v1/logs``).
        body: The request body bytes.
        content_type: The Content-Type header value.
        user_agent: The User-Agent header value (WAF blocks UA-less requests).

    Returns:
        ``(http_status, detail)``; status ``0`` on transport failure.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ReadinessUsageError(
            f"ERROR: --endpoint must be an https:// URL; got scheme {parsed.scheme!r} "
            f"for URL {url!r}."
        )
    host = parsed.hostname or ""
    if not host:
        raise ReadinessUsageError(f"ERROR: --endpoint has no host: {url!r}.")
    port = parsed.port or constants.E2E_HTTPS_PORT
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "User-Agent": user_agent,
    }
    try:
        conn = http.client.HTTPSConnection(host, port, timeout=constants.E2E_HTTP_TIMEOUT)
        try:
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            status = response.status
            reason = response.reason
            response.read()
            return status, f"HTTP {status} {reason}"
        finally:
            conn.close()
    except (OSError, http.client.HTTPException) as exc:
        return _TRANSPORT_ERROR_STATUS, f"transport-error: {exc}"


def probe_until_ready(
    endpoint: str,
    timeout: float,
    interval: float,
    poster: Callable[[str, bytes, str, str], tuple[int, str]],
    user_agent: str = constants.E2E_USER_AGENT,
) -> tuple[bool, int, str]:
    """Poll ``endpoint`` until the collector answers at the HTTP layer or ``timeout`` expires.

    Active, bounded readiness detection: each poll POSTs the minimal OTLP-logs
    request and the inter-poll pause is ``threading.Event.wait`` (via
    ``e2e_common.poll_until``) -- no ``time.sleep`` synchronization. The last
    observed ``(status, detail)`` is captured so the caller can report exactly
    why the budget expired.

    Args:
        endpoint: The full ``https://`` collector endpoint URL.
        timeout: Maximum total seconds to poll.
        interval: Seconds between polls.
        poster: ``(url, body, content_type, user_agent) -> (status, detail)`` sender
            (injected for tests).
        user_agent: User-Agent header sent on every probe.

    Returns:
        ``(ready, last_status, last_detail)`` -- ``ready`` True when a non-5xx
        HTTP response was seen within the budget.
    """
    last_status = [_TRANSPORT_ERROR_STATUS]
    last_detail = ["no probe attempted"]

    def _attempt() -> bool:
        status, detail = poster(
            endpoint, _MINIMAL_OTLP_LOGS_JSON, constants.E2E_CONTENT_TYPE_JSON, user_agent
        )
        last_status[0] = status
        last_detail[0] = detail
        print(f"perf-readiness probe {endpoint} -> {detail}")
        return is_serving(status)

    # The timeout_msg is built before polling begins, so it carries the generic
    # investigation guidance (the actual last observation is printed per-probe above and
    # surfaced by main()); it must not claim a specific last observation it cannot yet know.
    ready = e2e_common.poll_until(
        _attempt,
        timeout=timeout,
        interval=interval,
        timeout_msg=(
            f"ERROR: perf-readiness timed out after {timeout}s waiting for {endpoint} to answer "
            "at the HTTP layer (no non-5xx HTTP response). "
            "Investigate with: curl -sS -o /dev/null -w '%{http_code}\\n' "
            "-X POST -H 'Content-Type: application/json' "
            f"-H 'User-Agent: {user_agent}' --data '{_MINIMAL_OTLP_LOGS_JSON.decode()}' {endpoint} "
            "-- and check the CloudFront distribution status (Deployed) + the collector ECS "
            "service (running==desired). Raise PERF_READINESS_TIMEOUT if the stack is simply "
            "slow to serve."
        ),
    )
    return ready, last_status[0], last_detail[0]


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the perf readiness probe."""
    parser = argparse.ArgumentParser(
        prog="scripts.perf_readiness",
        description=(
            "Poll a single OTLP-logs request against the collector's CloudFront default-domain "
            "endpoint until it answers at the HTTP layer (fail-fast on an env-driven timeout)."
        ),
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Full https:// collector endpoint to probe (e.g. https://d123.cloudfront.net/v1/logs).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(constants.PERF_READINESS_TIMEOUT),
        help=(
            "Max total seconds to poll (default: $PERF_READINESS_TIMEOUT or "
            f"{constants.PERF_READINESS_TIMEOUT})."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=float,
        default=float(constants.PERF_READINESS_POLL_INTERVAL),
        help=(
            "Seconds between polls (default: $PERF_READINESS_POLL_INTERVAL or "
            f"{constants.PERF_READINESS_POLL_INTERVAL})."
        ),
    )
    parser.add_argument(
        "--user-agent",
        dest="user_agent",
        default=constants.E2E_USER_AGENT,
        help=(
            "User-Agent header sent on every probe (default: "
            f"{constants.E2E_USER_AGENT}). The collector WAF blocks User-Agent-less requests."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the JSON evidence summary (default: stdout only).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the perf readiness probe."""
    args = build_parser().parse_args(argv)
    try:
        ready, status, detail = probe_until_ready(
            endpoint=args.endpoint,
            timeout=args.timeout,
            interval=args.poll_interval,
            poster=_default_poster,
            user_agent=args.user_agent,
        )
    except ReadinessUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    evidence = {
        "endpoint": args.endpoint,
        "ready": ready,
        "last_status": status,
        "last_detail": detail,
        "timeout_seconds": args.timeout,
        "poll_interval_seconds": args.poll_interval,
    }
    rendered = json.dumps(evidence, indent=2)
    print(rendered)
    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered)

    if not ready:
        # poll_until already printed the actionable timeout diagnostic; surface fail-fast.
        print(
            f"ERROR: collector at {args.endpoint} did not answer at the HTTP layer within "
            f"{args.timeout}s (last: {detail}).",
            file=sys.stderr,
        )
        return 1
    print(f"perf-readiness OK: {args.endpoint} is serving ({detail}).")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
