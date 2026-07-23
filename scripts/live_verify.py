"""FR-13: scripts/live_verify.py -- read-only live verification prober.

Invoked as:
    uv run python -m scripts.live_verify --check <check> --env <sandbox|qa|prod|root>
    make live-verify CHECK=<check> ENV=<env>

Zero mutating AWS calls are issued. Only Get*/List*/Describe*/head_* style API
calls appear in this module. Unit tests assert this invariant by inspecting the
call surface.

ENV-to-profile resolution:
    Reads the aws_profile key from terragrunt/common/accounts.json. The --env
    argument is authoritative for the target account: the resolved profile is
    always passed as boto3 Session(profile_name=...), so an ambient AWS_PROFILE
    in the caller's environment can never silently redirect probes at a
    different account than --env selected.

Output:
    Human lines to stdout (OK <probe> / FAIL <probe>: <expected> vs <actual>)
    Every probe failure also emits an ERROR:-prefixed diagnostic to stderr so a
    failure can never be silently masked.
    JSON evidence written via --output (spec section 5.5 schema).

Exit codes:
    0 -- all probes passed
    1 -- one or more probes failed
    2 -- usage error (unknown CHECK or ENV)

Readiness-flavored probes (CloudFront Deployed, ACM ISSUED) use _poll_until,
which provides active, Event-based readiness detection over a configurable
bounded budget driven by LIVE_VERIFY_TIMEOUT and LIVE_VERIFY_POLL_INTERVAL
(sourced from environment variables via scripts.constants). No time.sleep
synchronization is used: inter-poll waits are implemented via threading.Event.
Point-in-time probes (S3, IAM, KMS, etc.) fail fast and never degrade to
fallback successes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from scripts.constants import (
    LIVE_VERIFY_HTTPS_PORT,
    LIVE_VERIFY_NETWORK_TIMEOUT,
    LIVE_VERIFY_POLL_INTERVAL,
    LIVE_VERIFY_TIMEOUT,
    TERRATEST_PROJECT_TAG,
)


class UsageError(Exception):
    """Raised for unknown CHECK or ENV values -- exit 2."""


class ProbeError(Exception):
    """Raised when a probe fails -- exit 1."""


_REPO_ROOT = pathlib.Path(__file__).parent.parent
_ACCOUNTS_JSON_PATH = _REPO_ROOT / "terragrunt" / "common" / "accounts.json"
_OIDC_ROLES_JSON_PATH = _REPO_ROOT / "terragrunt" / "common" / "oidc-roles.json"
_EXEMPTIONS_JSON_PATH = (
    _REPO_ROOT / "terragrunt" / "common" / "no-project-resources-exemptions.json"
)

_VALID_CHECKS = frozenset(
    {
        "oidc-provider",
        "oidc-roles",
        "state-backend",
        "stack",
        "endpoints",
        "observability",
        "no-project-resources",
        "repo-settings",
    }
)

_VALID_ENVS = frozenset({"sandbox", "qa", "prod", "root"})

# Published service-hostname subdomain labels (FR-13 endpoints check).
#
# The stack publishes TWO distinct public hostnames per service, and the two are NOT the
# same shape. These labels are the canonical single source of truth in the LIVE/_envcommon
# IaC, NOT invented here:
#   - collector-ingestion / acm-collector publish (INSTANCE-SCOPED service + stable pretty):
#       collector_service_fqdn = "collector-${environment_instance}.${service_apex}"
#       collector_pretty_fqdn  = "collector.${pretty_apex}"
#       (terragrunt/_envcommon/collector-ingestion.hcl, terragrunt/_envcommon/acm-collector.hcl)
#   - portal / acm-portal publish (INSTANCE-SCOPED service + stable pretty):
#       portal_service_fqdn = "telemetry-${environment_instance}.${service_apex}"
#       portal_pretty_fqdn  = "telemetry.${pretty_apex}"
#       (terragrunt/_envcommon/portal.hcl, terragrunt/_envcommon/acm-portal.hcl)
#
# The SERVICE hostname is the numbered instance-set record the dns-collector / dns-portal
# units publish as an A ALIAS to the CloudFront distribution:
#   <label>-<active_set>.<service_apex>  (e.g. collector-000.prod.telemetry.example.com)
# The PRETTY hostname is a stable CNAME the _singletons/pretty units point at the ACTIVE set's
# service record:
#   <label>.<pretty_apex>                (e.g. collector.telemetry.example.com)
# The active instance-set number (<active_set>) is the blue/green switch read from
# <env>/active.hcl (locals.active); it is NEVER hardcoded here.
#
# The bare, instance-less <label>.<service_apex> (e.g.
# collector.prod.telemetry.example.com) is NEVER published. Probing it FALSE-FAILS an
# env whose service_apex differs from its pretty_apex (prod), because there is no such record; the
# endpoints check therefore probes the INSTANCE-SCOPED service FQDN and the pretty FQDN -- never
# the bare service apex hostname.
# The bare apexes themselves (dns_service_apex / dns_pretty_apex) carry no record either and are
# probed only as an OPTIONAL, never-failing informational resolver call.
_COLLECTOR_HOST_LABEL = "collector"
_PORTAL_HOST_LABEL = "telemetry"

# Product segment (first path token) of the terragrunt LIVE tree: terragrunt/live/<product>/...
# Mirrors root.hcl's product namespace field (see docs/terragrunt-concepts.md). Used to
# locate the per-env active-set file (terragrunt/live/<product>/<region>/<env>/active.hcl)
# whose locals.active is the blue/green-active numbered instance set the pretty CNAME targets.
_PRODUCT = "telemetry"

# Matches the single `active = "<set>"` assignment inside a terragrunt active.hcl locals block,
# e.g. terragrunt/live/telemetry/us-east-1/prod/active.hcl -> locals { active = "000" }.
_ACTIVE_SET_RE = re.compile(r'active\s*=\s*"([^"]+)"')

# Namespace components that are fixed across every observability leaf in the LIVE
# tree (FR-13 observability check). The namespace the stack derives is:
#   <product>-<region_clean>-<environment>-<environment_instance>-<service>-<service_instance>
# (terragrunt/_envcommon/observability.hcl local.namespace, mirroring root.hcl).
# observability is a once-per-env SINGLETON: its leaf lives under the "shared" tier
#   sandbox: terragrunt/.../sandbox/_singletons/shared/observability/000
#   prod:    terragrunt/.../prod/_singletons/shared/observability/000
# so the 4th namespace field (environment_instance) is the singleton-tier basename
# "shared" -- NOT "000" (that would be a per-set serving instance). product / service /
# service_instance are identical across both leaves; only region_clean (from
# AWS_DEFAULT_REGION with dashes stripped) and environment (the --env value) vary.
_OBSERVABILITY_PRODUCT = _PRODUCT
# The singleton "shared" tier basename is the environment_instance field for the
# observability leaf (relocated to _singletons/shared); see instance-sets-architecture.md
# section 4.2. The deployed SNS topic is telemetry-<region>-<env>-shared-observability-000
# -observability, so this must be "shared" or the live SNS topic ARN check targets a
# non-existent topic.
_OBSERVABILITY_ENVIRONMENT_INSTANCE = "shared"
_OBSERVABILITY_SERVICE = "observability"
_OBSERVABILITY_SERVICE_INSTANCE = "000"

# Suffix appended to the namespace to form the SNS topic name
# (terragrunt/_envcommon/observability.hcl: topic_name = "${local.namespace}-observability").
_OBSERVABILITY_TOPIC_SUFFIX = "observability"

# The seven CloudWatch alarm names the observability unit creates. The alarm name is
# the MAP KEY of the alarms input (providers/aws/primitives/cloudwatch/main.tf:
# alarm_name = each.key), so the live AlarmName values are exactly these keys -- they
# carry NO env prefix and NO namespace prefix (the "sandbox-"/"prod-" alarm_name field in
# alarm_configs is overridden by each.key). The observability check lists alarms without a
# misleading name prefix filter and asserts at least one of these expected names is present.
_OBSERVABILITY_ALARM_NAMES = frozenset(
    {
        "alb_5xx_count",
        "alb_target_response_time_p95",
        "adot_memory_limiter_drops",
        "ecs_running_task_count",
        "waf_blocked_requests",
        "firehose_data_freshness",
        "athena_bytes_scanned",
    }
)

_GH_REQUIRED_VARIABLES = frozenset(
    {
        "AWS_DEFAULT_REGION",
        "AWS_QA_TERRATEST_ROLE_ARN",
        "AWS_TERRAGRUNT_PLAN_ROLE_ARN",
        "LOCK_MAX_AGE_MINUTES",
        "PORTAL_ARTIFACT_BUCKET",
        "PORTAL_LAMBDA_S3_KEY",
    }
)

_GH_REQUIRED_ENVIRONMENTS = frozenset({"terratest-approval", "prod-apply"})


def _resolve_profile(env: str, accounts_data: dict[str, Any]) -> str:
    """Resolve the aws_profile for the given ENV from accounts.json.

    Args:
        env: The environment name (sandbox, qa, prod, root).
        accounts_data: Parsed accounts.json content.

    Returns:
        The aws_profile string.

    Raises:
        UsageError: When no account row has an aws_profile matching env.
    """
    for acct_info in accounts_data.values():
        if acct_info.get("aws_profile") == env:
            return env
    raise UsageError(f"unknown ENV {env!r}: no account in accounts.json has aws_profile={env!r}")


def _resolve_account_id(env: str, accounts_data: dict[str, Any]) -> str:
    """Resolve the AWS account ID for the given ENV from accounts.json.

    Args:
        env: The environment name (sandbox, qa, prod, root).
        accounts_data: Parsed accounts.json content.

    Returns:
        The account ID string.

    Raises:
        UsageError: When no account row has an aws_profile matching env.
    """
    for acct_id, acct_info in accounts_data.items():
        if acct_info.get("aws_profile") == env:
            return str(acct_id)
    raise UsageError(f"unknown ENV {env!r}: no account in accounts.json has aws_profile={env!r}")


def _default_dns_resolver(fqdn: str) -> tuple[bool, str, str | None]:
    """Attempt DNS resolution of fqdn.

    Returns:
        (success, detail, resolved_ip_or_None)
    """
    try:
        ip = socket.gethostbyname(fqdn)
        return True, f"DNS resolved {fqdn} -> {ip}", ip
    except OSError as exc:
        return False, f"DNS resolution failed for {fqdn}: {exc}", None


def _default_tls_checker(fqdn: str, port: int = LIVE_VERIFY_HTTPS_PORT) -> tuple[bool, str]:
    """Verify TLS handshake against fqdn:port.

    Returns:
        (success, detail)
    """
    try:
        ctx = ssl.create_default_context()
        # Pin TLS 1.2 as the floor. The default context still permits TLS 1.0/1.1
        # depending on the OpenSSL build, and this probe exists to assert the
        # endpoint meets current transport policy -- accepting a deprecated
        # protocol here would report a pass for a configuration we consider bad.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        with ctx.wrap_socket(
            socket.create_connection((fqdn, port), timeout=LIVE_VERIFY_NETWORK_TIMEOUT),
            server_hostname=fqdn,
        ) as sock:
            cert = sock.getpeercert()
            if not cert:
                return False, f"TLS handshake for {fqdn} returned no peer certificate"
            san_entries = cert.get("subjectAltName", ())
            sans = [
                entry[1]
                for entry in san_entries
                if isinstance(entry, tuple) and len(entry) == 2 and entry[0] == "DNS"
            ]
            return True, f"TLS OK for {fqdn}, SANs: {sans}"
    except Exception as exc:
        return False, f"TLS handshake failed for {fqdn}: {exc}"


def _default_http_prober(url: str) -> tuple[bool, str]:
    """Issue an HTTPS probe to url.

    Only https:// URLs are permitted. Any other scheme raises ValueError to
    prevent accidental file:// or custom-scheme access. The connection is made
    using http.client.HTTPSConnection, which is inherently scheme-restricted
    (no file:// risk, no bandit B310 concern).

    Returns:
        (success, detail)
    """
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError(
            f"ERROR: _default_http_prober only permits https:// URLs; "
            f"got scheme {parsed.scheme!r} for URL {url!r}"
        )
    host = parsed.hostname or ""
    port = parsed.port or LIVE_VERIFY_HTTPS_PORT
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        conn = http.client.HTTPSConnection(host, port, timeout=LIVE_VERIFY_NETWORK_TIMEOUT)
        conn.request("GET", path)
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return True, f"HTTP probe {url} returned {status}"
    except Exception as exc:
        return False, f"HTTP probe failed for {url}: {exc}"


def _default_active_set_resolver(env: str, region: str, repo_root: pathlib.Path) -> str:
    """Resolve the active (blue/green) instance-set number for env from its active.hcl.

    The published SERVICE hostname is instance-scoped (``<label>-<active_set>.<service_apex>``)
    and the pretty CNAME points at the ACTIVE set. The active set is the ``locals.active`` value
    in ``terragrunt/live/<product>/<region>/<env>/active.hcl`` -- the same file the
    ``_singletons/pretty`` CNAME units read to compose their target (blue/green switch). It is
    derived here, never hardcoded, so the endpoints check probes the correct instance-set for the
    env exactly as the IaC published it.

    Args:
        env: The environment name (sandbox, qa, prod).
        region: The AWS region (dashed form, e.g. ``us-east-1``) -- the LIVE-tree region segment.
        repo_root: Repository root the LIVE tree hangs off of.

    Returns:
        The active instance-set string (e.g. ``"000"``).

    Raises:
        UsageError: When the active.hcl file is absent or its ``active`` local cannot be parsed.
            Fail-fast: an unresolvable active set is a real gap (the endpoints check cannot know
            which instance-set record was published) and must surface, never degrade to a guess.
    """
    active_hcl = repo_root / "terragrunt" / "live" / _PRODUCT / region / env / "active.hcl"
    if not active_hcl.exists():
        raise UsageError(
            f"ERROR: active-set file not found at {active_hcl}. The endpoints check derives the "
            f"instance-scoped service FQDN (<label>-<active>.<service_apex>) from this file; it "
            f"must exist for env {env!r} in region {region!r}."
        )
    match = _ACTIVE_SET_RE.search(active_hcl.read_text())
    if not match:
        raise UsageError(
            f"ERROR: could not parse an 'active = \"<set>\"' assignment from {active_hcl}. "
            f"Expected a terragrunt locals block declaring the active instance-set number."
        )
    return match.group(1)


def _default_gh_runner(args: list[str]) -> tuple[int, str, str]:
    """Run a gh CLI command and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _poll_until(
    predicate: Callable[[], bool],
    timeout: float,
    interval: float,
    timeout_msg: str,
) -> bool:
    """Poll predicate() until it returns True or timeout expires.

    Active readiness detection over a configurable, bounded budget: actual
    resource state is re-checked each iteration and the interval pause is
    implemented with threading.Event.wait, so no time.sleep synchronization
    delay is introduced. The total budget and poll interval are supplied by the
    caller as injectable parameters -- there are no hard-coded timeouts; the
    env-driven LIVE_VERIFY_TIMEOUT / LIVE_VERIFY_POLL_INTERVAL defaults live in
    scripts.constants. Returns True when predicate passes, False on timeout.
    On timeout, prints the timeout_msg diagnostic.

    Args:
        predicate: Callable returning True when the condition is met.
        timeout: Maximum total seconds to wait.
        interval: Seconds between checks.
        timeout_msg: Diagnostic emitted to stdout on timeout.
    """
    deadline = time.monotonic() + timeout
    _stop = threading.Event()
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(timeout_msg)
            return False
        _stop.wait(timeout=min(interval, remaining))


class LiveVerify:
    """Read-only live verification prober for all eight check types.

    Each check is implemented as a set of named probes. All AWS API calls
    are read-only (Get*/List*/Describe*/head_* only). Probes emit
    'OK <probe>' or 'FAIL <probe>: <expected> vs <actual>' lines to stdout.
    """

    def __init__(
        self,
        check: str,
        env: str,
        output_path: str | None,
        accounts_data: dict[str, Any],
        exemptions_data: dict[str, Any],
        oidc_roles_data: dict[str, Any],
        boto3_module: Any,
        gh_runner: Callable[..., tuple[int, str, str]] | None,
        dns_resolver: Callable[..., tuple[bool, str, str | None]] | None = None,
        tls_checker: Callable[..., tuple[bool, str]] | None = None,
        http_prober: Callable[..., tuple[bool, str]] | None = None,
        active_set_resolver: Callable[[str, str, pathlib.Path], str] | None = None,
        poll_timeout: float | None = None,
        poll_interval: float | None = None,
        gh_repo: str | None = None,
        aws_region: str | None = None,
    ) -> None:
        if check not in _VALID_CHECKS:
            raise UsageError(f"unknown CHECK {check!r}: must be one of {sorted(_VALID_CHECKS)!r}")

        # Validate env and resolve account -- raises UsageError on unknown env
        _resolve_profile(env, accounts_data)

        self._check = check
        self._env = env
        self._output_path = output_path
        self._accounts_data = accounts_data
        self._exemptions_data = exemptions_data
        self._oidc_roles_data = oidc_roles_data
        self._boto3 = boto3_module
        self._gh_runner: Callable[..., tuple[int, str, str]] = gh_runner or _default_gh_runner
        self._dns_resolver: Callable[..., tuple[bool, str, str | None]] = (
            dns_resolver or _default_dns_resolver
        )
        self._tls_checker: Callable[..., tuple[bool, str]] = tls_checker or _default_tls_checker
        self._http_prober: Callable[..., tuple[bool, str]] = http_prober or _default_http_prober
        self._active_set_resolver: Callable[[str, str, pathlib.Path], str] = (
            active_set_resolver or _default_active_set_resolver
        )
        # Readiness-polling budget driven by env-var-sourced constants (spec section 7).
        # Callers may inject smaller values for fast unit-test cycles.
        self._poll_timeout: float = (
            poll_timeout if poll_timeout is not None else LIVE_VERIFY_TIMEOUT
        )
        self._poll_interval: float = (
            poll_interval if poll_interval is not None else LIVE_VERIFY_POLL_INTERVAL
        )

        self._account_id = _resolve_account_id(env, accounts_data)
        self._profile = _resolve_profile(env, accounts_data)

        # GitHub repo slug for repo-settings check.
        # Callers may inject the value directly; otherwise resolved from GITHUB_REPOSITORY env var
        # at run time inside _check_repo_settings (fail-fast if unset when that check is used).
        self._gh_repo: str | None = gh_repo

        # AWS region for region-scoped ARN construction (ACM, SNS).
        # Callers may inject the value directly; otherwise resolved from AWS_DEFAULT_REGION env var
        # at run time inside _get_region (fail-fast if unset when a region-scoped probe is used).
        self._aws_region: str | None = aws_region

        # Session is created lazily in _get_session
        self._session: Any = None

    def _get_session(self) -> Any:
        """Return a cached boto3 Session bound to the ENV-resolved profile.

        The ``--env`` argument is authoritative for the target account: the
        profile resolved from ``common/accounts.json`` (``self._profile``) is
        always passed as ``profile_name``, so an ambient ``AWS_PROFILE`` in the
        caller's environment can never silently redirect probes at a different
        account than the one ``--env`` selected (spec section 4.13 / FR-13:
        "Account/region resolve from ENV -> profile mapping in
        common/accounts.json"). ``self._profile`` is guaranteed non-empty:
        ``__init__`` resolves it via ``_resolve_profile``, which raises
        ``UsageError`` for any ENV without a profile, so there is no
        no-profile branch. Missing or invalid credentials for the resolved
        profile fail fast at the first AWS call (surfaced as the underlying
        botocore error -- never degraded to a wrong-account fallback).
        """
        if self._session is None:
            self._session = self._boto3.Session(profile_name=self._profile)
        return self._session

    def _get_region(self) -> str:
        """Return the AWS region for region-scoped ARN construction.

        Resolution order:
        1. Injectable aws_region parameter passed to __init__ (test isolation).
        2. AWS_DEFAULT_REGION environment variable.

        Raises:
            UsageError: When no region can be resolved (env var unset and no injection).
        """
        if self._aws_region:
            return self._aws_region
        import os

        region = os.environ.get("AWS_DEFAULT_REGION", "").strip()
        if not region:
            raise UsageError(
                "ERROR: AWS_DEFAULT_REGION environment variable is not set. "
                "Set it to the target AWS region (e.g. us-east-1) before running live-verify."
            )
        return region

    def _observability_namespace(self) -> str:
        """Return the namespace the observability stack derives for the current env.

        Mirrors ``terragrunt/_envcommon/observability.hcl`` ``local.namespace``:
            <product>-<region_clean>-<environment>-<environment_instance>-
            <service>-<service_instance>
        where ``region_clean`` is the AWS region with dashes stripped and ``environment``
        is the env (``sandbox``/``prod``) -- the same value the ``--env`` argument selects.
        The observability leaf is a once-per-env singleton under the ``shared`` tier, so the
        environment_instance field is the tier basename ``shared``. All other components are
        fixed across both observability leaves (sandbox + prod).

        Example (sandbox, us-east-1):
            telemetry-useast1-sandbox-shared-observability-000
        """
        region_clean = self._get_region().replace("-", "")
        return "-".join(
            [
                _OBSERVABILITY_PRODUCT,
                region_clean,
                self._env,
                _OBSERVABILITY_ENVIRONMENT_INSTANCE,
                _OBSERVABILITY_SERVICE,
                _OBSERVABILITY_SERVICE_INSTANCE,
            ]
        )

    def _observability_topic_name(self) -> str:
        """Return the namespace-derived SNS topic name for the current env.

        Mirrors ``terragrunt/_envcommon/observability.hcl``:
            ``topic_name = "${local.namespace}-observability"``
        """
        return f"{self._observability_namespace()}-{_OBSERVABILITY_TOPIC_SUFFIX}"

    def _get_gh_repo(self) -> str:
        """Return the GitHub repository slug for repo-settings check.

        Resolution order:
        1. Injectable gh_repo parameter passed to __init__ (test isolation).
        2. GITHUB_REPOSITORY environment variable.

        Raises:
            UsageError: When no repo slug can be resolved.
        """
        if self._gh_repo:
            return self._gh_repo
        import os

        repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if not repo:
            raise UsageError(
                "ERROR: GITHUB_REPOSITORY environment variable is not set. "
                "Set it to the repository slug (e.g. org/repo) before running "
                "live-verify --check repo-settings."
            )
        return repo

    def _client(self, service_name: str, **kwargs: Any) -> Any:
        """Create a boto3 client for the given service."""
        session = self._get_session()
        return session.client(service_name, **kwargs)

    def run(self) -> int:
        """Execute all probes for the configured check.

        Returns:
            0 if all probes passed, 1 if any failed.
        """
        probes: list[dict[str, Any]] = []
        run_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        check_fn = {
            "oidc-provider": self._check_oidc_provider,
            "oidc-roles": self._check_oidc_roles,
            "state-backend": self._check_state_backend,
            "stack": self._check_stack,
            "endpoints": self._check_endpoints,
            "observability": self._check_observability,
            "no-project-resources": self._check_no_project_resources,
            "repo-settings": self._check_repo_settings,
        }[self._check]

        check_fn(probes)

        overall = "OK" if all(p["status"] == "OK" for p in probes) else "FAIL"
        exit_code = 0 if overall == "OK" else 1

        if self._output_path:
            evidence = {
                "check": self._check,
                "env": self._env,
                "run_at": run_at,
                "account_id": self._account_id,
                "probes": probes,
                "result": overall,
            }
            pathlib.Path(self._output_path).write_text(json.dumps(evidence, indent=2))

        return exit_code

    def _emit_ok(self, probes: list[dict[str, Any]], name: str, detail: str = "") -> None:
        """Emit an OK probe line and record it."""
        print(f"OK {name}" + (f": {detail}" if detail else ""))
        probes.append({"name": name, "status": "OK", "detail": detail})

    def _emit_fail(
        self,
        probes: list[dict[str, Any]],
        name: str,
        expected: str,
        actual: str,
    ) -> None:
        """Emit a FAIL probe line and record it.

        Every probe failure is surfaced fail-fast: the human-readable ``FAIL``
        line goes to stdout (alongside the ``OK`` lines) and an ``ERROR:``
        prefixed diagnostic is written to stderr so a failed probe can never be
        silently masked or degrade to a fallback success.
        """
        print(f"FAIL {name}: {expected} vs {actual}")
        print(
            f"ERROR: probe {name} failed: expected {expected}, got {actual}",
            file=sys.stderr,
        )
        probes.append(
            {
                "name": name,
                "status": "FAIL",
                "detail": f"expected={expected} actual={actual}",
            }
        )

    # ------------------------------------------------------------------
    # oidc-provider check
    # ------------------------------------------------------------------

    def _check_oidc_provider(self, probes: list[dict[str, Any]]) -> None:
        """Probe: GitHub OIDC provider exists with correct audience."""
        iam = self._client("iam")
        providers = iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])

        # Match the ARN's provider-host component exactly rather than looking for
        # the host anywhere in the string: an unrelated provider registered as
        # e.g. `.../token.actions.githubusercontent.com.evil.test` would satisfy a
        # substring test and be probed as if it were GitHub's.
        gh_provider_arns = [
            p["Arn"] for p in providers if p["Arn"].endswith("/token.actions.githubusercontent.com")
        ]

        probe_name = "oidc-provider:list-providers"
        if not gh_provider_arns:
            self._emit_fail(
                probes,
                probe_name,
                expected="token.actions.githubusercontent.com provider present",
                actual="provider not found",
            )
            return

        self._emit_ok(probes, probe_name, detail=f"found {len(gh_provider_arns)} provider(s)")

        for provider_arn in gh_provider_arns:
            detail = iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)
            audience_probe = f"oidc-provider:audience:{provider_arn}"
            client_ids = detail.get("ClientIDList", [])
            if "sts.amazonaws.com" in client_ids:
                self._emit_ok(probes, audience_probe, detail="sts.amazonaws.com present")
            else:
                self._emit_fail(
                    probes,
                    audience_probe,
                    expected="sts.amazonaws.com in ClientIDList",
                    actual=f"{client_ids!r}",
                )

    # ------------------------------------------------------------------
    # oidc-roles check
    # ------------------------------------------------------------------

    def _check_oidc_roles(self, probes: list[dict[str, Any]]) -> None:
        """Probe: expected IAM roles exist with the declared trust shape."""
        import botocore.exceptions

        iam = self._client("iam")
        account_roles = self._oidc_roles_data.get(self._account_id, {}).get("roles", {})

        if not account_roles:
            self._emit_ok(
                probes,
                f"oidc-roles:no-roles-for-account:{self._account_id}",
                detail="no roles declared for this account in oidc-roles.json",
            )
            return

        for role_name, role_spec in account_roles.items():
            existence_probe = f"oidc-roles:role-exists:{role_name}"
            try:
                role_data = iam.get_role(RoleName=role_name)["Role"]
                self._emit_ok(probes, existence_probe, detail=role_data.get("Arn", ""))
            except botocore.exceptions.ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code in ("NoSuchEntity", "NoSuchEntityException"):
                    self._emit_fail(
                        probes,
                        existence_probe,
                        expected=f"role {role_name!r} exists",
                        actual="role not found",
                    )
                    continue
                raise

            # Check trust shape
            trust_probe = f"oidc-roles:trust-shape:{role_name}"
            trust_doc = role_data.get("AssumeRolePolicyDocument", {})
            expected_sub = role_spec.get("sub")
            if expected_sub:
                # OIDC trust: look for sub condition
                has_sub = any(
                    expected_sub
                    == stmt.get("Condition", {})
                    .get("StringLike", {})
                    .get("token.actions.githubusercontent.com:sub")
                    or expected_sub
                    == stmt.get("Condition", {})
                    .get("StringEquals", {})
                    .get("token.actions.githubusercontent.com:sub")
                    for stmt in trust_doc.get("Statement", [])
                )
                if has_sub:
                    self._emit_ok(probes, trust_probe, detail=f"sub={expected_sub}")
                else:
                    self._emit_fail(
                        probes,
                        trust_probe,
                        expected=f"sub={expected_sub!r} in trust policy",
                        actual="sub condition not found",
                    )
            else:
                # Role-chaining trust (dns-writer): check for principal (not OIDC sub)
                has_principal = bool(trust_doc.get("Statement"))
                if has_principal:
                    self._emit_ok(
                        probes,
                        trust_probe,
                        detail="trust policy present (chained role)",
                    )
                else:
                    self._emit_fail(
                        probes,
                        trust_probe,
                        expected="trust policy with principal",
                        actual="empty trust policy",
                    )

    # ------------------------------------------------------------------
    # state-backend check
    # ------------------------------------------------------------------

    def _check_state_backend(self, probes: list[dict[str, Any]]) -> None:
        """Probe: S3 state/access-log/artifact buckets + KMS CMK hardening posture."""
        import botocore.exceptions

        s3 = self._client("s3")
        kms = self._client("kms")

        acct = self._account_id
        bucket_prefix = f"{acct}-tfstate"
        state_bucket = f"{bucket_prefix}-tfstate"
        access_log_bucket = f"{bucket_prefix}-access-logs"
        kms_alias = f"alias/{acct}-tfstate"

        buckets_to_check = [
            (f"state-backend:head-bucket:{state_bucket}", state_bucket),
            (f"state-backend:head-bucket:{access_log_bucket}", access_log_bucket),
        ]

        for probe_name, bucket_name in buckets_to_check:
            try:
                s3.head_bucket(Bucket=bucket_name)
                self._emit_ok(probes, probe_name, detail=f"bucket {bucket_name!r} exists")
            except botocore.exceptions.ClientError as exc:
                self._emit_fail(
                    probes,
                    probe_name,
                    expected=f"bucket {bucket_name!r} exists",
                    actual=f"error: {exc.response['Error']['Code']}",
                )

        # Check versioning on state bucket
        versioning_probe = f"state-backend:get-bucket-versioning:{state_bucket}"
        try:
            resp = s3.get_bucket_versioning(Bucket=state_bucket)
            status = resp.get("Status", "")
            if status == "Enabled":
                self._emit_ok(probes, versioning_probe, detail="versioning Enabled")
            else:
                self._emit_fail(
                    probes,
                    versioning_probe,
                    expected="Status=Enabled",
                    actual=f"Status={status!r}",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                versioning_probe,
                expected="versioning Enabled",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Check SSE-KMS encryption
        encryption_probe = f"state-backend:get-bucket-encryption:{state_bucket}"
        try:
            enc_resp = s3.get_bucket_encryption(Bucket=state_bucket)
            rules = enc_resp.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
            kms_rule = next(
                (
                    r
                    for r in rules
                    if r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
                    == "aws:kms"
                ),
                None,
            )
            if kms_rule:
                self._emit_ok(probes, encryption_probe, detail="SSE-KMS with customer CMK")
            else:
                self._emit_fail(
                    probes,
                    encryption_probe,
                    expected="SSEAlgorithm=aws:kms",
                    actual=f"rules={rules!r}",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                encryption_probe,
                expected="SSE-KMS encryption",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Check public access block
        pab_probe = f"state-backend:get-public-access-block:{state_bucket}"
        try:
            pab_resp = s3.get_public_access_block(Bucket=state_bucket)
            cfg = pab_resp.get("PublicAccessBlockConfiguration", {})
            all_blocked = all(
                cfg.get(key, False)
                for key in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            )
            if all_blocked:
                self._emit_ok(probes, pab_probe, detail="all public access blocked")
            else:
                self._emit_fail(
                    probes,
                    pab_probe,
                    expected="all public access blocked",
                    actual=f"config={cfg!r}",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                pab_probe,
                expected="public access block enabled",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Check bucket policy for TLS-only
        policy_probe = f"state-backend:get-bucket-policy:{state_bucket}"
        try:
            policy_resp = s3.get_bucket_policy(Bucket=state_bucket)
            policy = json.loads(policy_resp.get("Policy", "{}"))
            has_tls = any(
                stmt.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
                for stmt in policy.get("Statement", [])
            )
            if has_tls:
                self._emit_ok(probes, policy_probe, detail="TLS-only deny statement present")
            else:
                self._emit_fail(
                    probes,
                    policy_probe,
                    expected=("TLS-only (aws:SecureTransport=false deny) in bucket policy"),
                    actual="TLS deny statement not found",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                policy_probe,
                expected="TLS-only bucket policy",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Check KMS CMK is enabled
        kms_probe = f"state-backend:describe-key:{kms_alias}"
        try:
            key_resp = kms.describe_key(KeyId=kms_alias)
            key_state = key_resp.get("KeyMetadata", {}).get("KeyState", "")
            if key_state == "Enabled":
                self._emit_ok(probes, kms_probe, detail=f"CMK {kms_alias!r} Enabled")
            else:
                self._emit_fail(
                    probes,
                    kms_probe,
                    expected="KeyState=Enabled",
                    actual=f"KeyState={key_state!r}",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                kms_probe,
                expected=f"CMK {kms_alias!r} exists and is enabled",
                actual=f"error: {exc.response['Error']['Code']}",
            )

    # ------------------------------------------------------------------
    # stack check
    # ------------------------------------------------------------------

    def _check_stack(self, probes: list[dict[str, Any]]) -> None:
        """Probe: per-unit stack outputs live (buckets, CF, ACM, Route53, etc.)."""
        import botocore.exceptions

        s3 = self._client("s3")
        cf = self._client("cloudfront")
        acm = self._client("acm")
        route53 = self._client("route53")
        firehose = self._client("firehose")
        glue = self._client("glue")
        athena = self._client("athena")
        ecs = self._client("ecs")
        cw = self._client("cloudwatch")

        # Probe: data-lake S3 bucket head.
        # Fail-fast: a head_bucket error means the expected data-lake bucket is
        # absent or inaccessible. This must surface as a probe failure rather
        # than be masked as a "bucket name varies by deploy" fallback success.
        probe_s3 = f"stack:s3-head-bucket:{self._env}"
        try:
            s3.head_bucket(Bucket=f"{self._account_id}-{self._env}-data-lake")
            self._emit_ok(probes, probe_s3, detail="data-lake bucket exists")
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_s3,
                expected="data-lake bucket exists",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: CloudFront distribution status.
        # Readiness-polling probe: polls until Status=Deployed or budget expires.
        # Uses LIVE_VERIFY_TIMEOUT / LIVE_VERIFY_POLL_INTERVAL via threading.Event.wait
        # (active readiness detection -- no time.sleep synchronization).
        probe_cf = f"stack:cloudfront-deployed:{self._env}"
        cf_status_holder: list[str] = [""]
        cf_error_holder: list[str] = [""]

        def _cf_deployed() -> bool:
            try:
                r = cf.get_distribution(Id=f"{self._env}-collector")
                s: str = str(r.get("Distribution", {}).get("Status", ""))
                cf_status_holder[0] = s
                return s == "Deployed"
            except botocore.exceptions.ClientError as exc:
                cf_error_holder[0] = exc.response["Error"]["Code"]
                return False

        cf_ready = _poll_until(
            _cf_deployed,
            timeout=self._poll_timeout,
            interval=self._poll_interval,
            timeout_msg=(
                f"FAIL {probe_cf}: CloudFront status=Deployed not reached within"
                f" {self._poll_timeout}s -- investigate with:"
                f" aws cloudfront get-distribution --id {self._env}-collector"
            ),
        )
        if cf_ready:
            self._emit_ok(probes, probe_cf, detail="CloudFront status=Deployed")
        elif cf_error_holder[0]:
            self._emit_fail(
                probes,
                probe_cf,
                expected="CloudFront distribution exists and is Deployed",
                actual=f"error: {cf_error_holder[0]}",
            )
        else:
            self._emit_fail(
                probes,
                probe_cf,
                expected="CloudFront status=Deployed",
                actual=f"status={cf_status_holder[0]!r} after {self._poll_timeout}s",
            )

        # Probe: ACM certificate ISSUED.
        # Readiness-polling probe: polls until Status=ISSUED or budget expires.
        probe_acm = f"stack:acm-certificate-issued:{self._env}"
        acm_arn = f"arn:aws:acm:{self._get_region()}:{self._account_id}:certificate/{self._env}"
        acm_status_holder: list[str] = [""]
        acm_error_holder: list[str] = [""]

        def _acm_issued() -> bool:
            try:
                r = acm.describe_certificate(CertificateArn=acm_arn)
                s: str = str(r.get("Certificate", {}).get("Status", ""))
                acm_status_holder[0] = s
                return s == "ISSUED"
            except botocore.exceptions.ClientError as exc:
                acm_error_holder[0] = exc.response["Error"]["Code"]
                return False

        acm_ready = _poll_until(
            _acm_issued,
            timeout=self._poll_timeout,
            interval=self._poll_interval,
            timeout_msg=(
                f"FAIL {probe_acm}: ACM Status=ISSUED not reached within"
                f" {self._poll_timeout}s -- investigate with:"
                f" aws acm describe-certificate --certificate-arn {acm_arn}"
            ),
        )
        if acm_ready:
            self._emit_ok(probes, probe_acm, detail="ACM certificate ISSUED")
        elif acm_error_holder[0]:
            self._emit_fail(
                probes,
                probe_acm,
                expected="ACM certificate exists and is ISSUED",
                actual=f"error: {acm_error_holder[0]}",
            )
        else:
            self._emit_fail(
                probes,
                probe_acm,
                expected="Status=ISSUED",
                actual=f"Status={acm_status_holder[0]!r} after {self._poll_timeout}s",
            )

        # Probe: Route53 hosted zone + records
        probe_r53 = f"stack:route53-hosted-zone:{self._env}"
        try:
            r53_resp = route53.list_hosted_zones_by_name(DNSName=f"{self._env}.")
            zones = r53_resp.get("HostedZones", [])
            if zones:
                self._emit_ok(probes, probe_r53, detail=f"found {len(zones)} zone(s)")
                zone_id = zones[0]["Id"].split("/")[-1]
                records_probe = f"stack:route53-records:{self._env}"
                records_resp = route53.list_resource_record_sets(HostedZoneId=zone_id)
                records = records_resp.get("ResourceRecordSets", [])
                if records:
                    self._emit_ok(
                        probes,
                        records_probe,
                        detail=f"found {len(records)} record(s)",
                    )
                else:
                    self._emit_fail(
                        probes,
                        records_probe,
                        expected="at least one record set",
                        actual="no records found",
                    )
            else:
                self._emit_fail(
                    probes,
                    probe_r53,
                    expected="hosted zone exists",
                    actual="no zones found",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_r53,
                expected="Route53 hosted zone accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: Firehose delivery stream ACTIVE
        probe_firehose = f"stack:firehose-active:{self._env}"
        try:
            fh_resp = firehose.describe_delivery_stream(DeliveryStreamName=f"{self._env}-telemetry")
            fh_status = fh_resp.get("DeliveryStreamDescription", {}).get("DeliveryStreamStatus", "")
            if fh_status == "ACTIVE":
                self._emit_ok(probes, probe_firehose, detail="Firehose stream ACTIVE")
            else:
                self._emit_fail(
                    probes,
                    probe_firehose,
                    expected="DeliveryStreamStatus=ACTIVE",
                    actual=f"DeliveryStreamStatus={fh_status!r}",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_firehose,
                expected="Firehose delivery stream exists and is ACTIVE",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: Glue database present
        probe_glue = f"stack:glue-database:{self._env}"
        try:
            glue_resp = glue.get_database(Name=f"{self._env}_telemetry")
            db_name = glue_resp.get("Database", {}).get("Name", "")
            if db_name:
                self._emit_ok(probes, probe_glue, detail=f"Glue database {db_name!r} present")
            else:
                self._emit_fail(
                    probes,
                    probe_glue,
                    expected="Glue database present",
                    actual="database returned empty name",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_glue,
                expected="Glue database present",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: Athena workgroup present
        probe_athena = f"stack:athena-workgroup:{self._env}"
        try:
            ath_resp = athena.get_work_group(WorkGroup=f"{self._env}-telemetry")
            wg_name = ath_resp.get("WorkGroup", {}).get("Name", "")
            if wg_name:
                self._emit_ok(
                    probes,
                    probe_athena,
                    detail=f"Athena workgroup {wg_name!r} present",
                )
            else:
                self._emit_fail(
                    probes,
                    probe_athena,
                    expected="Athena workgroup present",
                    actual="workgroup returned empty name",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_athena,
                expected="Athena workgroup present",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: ECS service stable
        probe_ecs = f"stack:ecs-service-stable:{self._env}"
        try:
            ecs_resp = ecs.describe_services(
                cluster=f"{self._env}-telemetry",
                services=[f"{self._env}-collector"],
            )
            services = ecs_resp.get("services", [])
            if services:
                svc = services[0]
                running = svc.get("runningCount", 0)
                desired = svc.get("desiredCount", 0)
                if running == desired and svc.get("status") == "ACTIVE":
                    self._emit_ok(
                        probes,
                        probe_ecs,
                        detail=(f"ECS service ACTIVE, running={running} desired={desired}"),
                    )
                else:
                    self._emit_fail(
                        probes,
                        probe_ecs,
                        expected="ECS service ACTIVE with running==desired",
                        actual=(
                            f"status={svc.get('status')!r} running={running} desired={desired}"
                        ),
                    )
            else:
                self._emit_fail(
                    probes,
                    probe_ecs,
                    expected="ECS service present",
                    actual="no services returned",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_ecs,
                expected="ECS service accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: CloudWatch alarms present
        probe_cw = f"stack:cloudwatch-alarms:{self._env}"
        try:
            # The observability unit names alarms by the cloudwatch primitive map
            # key (e.g. alb_5xx_count) with NO env/namespace prefix, so the old
            # AlarmNamePrefix=f"{env}-" filter matched zero of the real alarms.
            # This coarse liveness probe just asserts alarms exist; the precise
            # per-alarm-name validation lives in the observability check.
            cw_resp = cw.describe_alarms()
            alarms = cw_resp.get("MetricAlarms", [])
            if alarms:
                self._emit_ok(probes, probe_cw, detail=f"found {len(alarms)} alarm(s)")
            else:
                self._emit_fail(
                    probes,
                    probe_cw,
                    expected="at least one CloudWatch alarm",
                    actual="no alarms found",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_cw,
                expected="CloudWatch alarms accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

    # ------------------------------------------------------------------
    # endpoints check
    # ------------------------------------------------------------------

    def _check_endpoints(self, probes: list[dict[str, Any]]) -> None:
        """Probe: DNS resolution, TLS handshake, HTTP probe for the published service FQDNs.

        The stack publishes TWO distinct hostname shapes per service (derived per-env from
        ``common/domains.json`` so the same code works for sandbox AND prod):

          - the INSTANCE-SCOPED service FQDN the dns-collector / dns-portal units publish as an
            A ALIAS to the CloudFront distribution:
              ``<label>-<active_set>.<service_apex>``
              (e.g. ``collector-000.prod.telemetry.example.com``)
            ``<active_set>`` is the blue/green-active numbered instance set read from
            ``<env>/active.hcl`` (the same source the ``_singletons/pretty`` CNAME units target),
            never hardcoded.
          - the stable PRETTY FQDN the ``_singletons/pretty`` units publish as a CNAME to the
            active set's service record:
              ``<label>.<pretty_apex>``
              (e.g. ``collector.telemetry.example.com``)

        The bare, instance-less ``<label>.<service_apex>`` (e.g.
        ``collector.prod.telemetry.example.com``) is NEVER published; probing it
        FALSE-FAILS an env whose service_apex differs from its pretty_apex (prod). The check
        therefore probes the instance-scoped service FQDN and the pretty FQDN -- never the bare
        service apex hostname. When service_apex == pretty_apex (sandbox) both shapes are still
        distinct records and both are probed.

        The bare apexes (``dns_service_apex`` / ``dns_pretty_apex``) carry no record either and
        are probed only as an OPTIONAL informational probe (recorded OK whether or not they
        resolve) so a no-record-by-design apex never causes a hard FAIL.
        """
        domains_path = _REPO_ROOT / "terragrunt" / "common" / "domains.json"
        if not domains_path.exists():
            self._emit_fail(
                probes,
                "endpoints:domains-json",
                expected="terragrunt/common/domains.json exists",
                actual="file not found",
            )
            return

        domains = json.loads(domains_path.read_text())
        env_domains = domains.get(self._env, {})

        # The service apex and pretty apex are sourced from domains.json keyed by env (the same
        # source the IaC uses, D45/D47). They may be identical (sandbox) or distinct (prod).
        service_apex = env_domains.get("dns_service_apex")
        pretty_apex = env_domains.get("dns_pretty_apex")

        if not service_apex and not pretty_apex:
            self._emit_ok(
                probes,
                f"endpoints:no-domains:{self._env}",
                detail="no apex domains configured for this env",
            )
            return

        # Build the REAL published hostnames, preserving order and de-duplicating.
        published_fqdns: list[str] = []
        seen_fqdns: set[str] = set()

        def _add_fqdn(fqdn: str) -> None:
            if fqdn not in seen_fqdns:
                seen_fqdns.add(fqdn)
                published_fqdns.append(fqdn)

        # Instance-scoped service FQDNs: <label>-<active_set>.<service_apex>. The active set is
        # read from <env>/active.hcl (blue/green switch), never hardcoded. Resolving it fails
        # fast: an unresolvable active set is a real gap (we cannot know which instance-set
        # record was published), so it surfaces as a probe FAIL rather than a guessed hostname.
        if service_apex:
            active_probe = f"endpoints:active-set:{self._env}"
            try:
                active_set = self._active_set_resolver(self._env, self._get_region(), _REPO_ROOT)
            except (UsageError, OSError) as exc:
                self._emit_fail(
                    probes,
                    active_probe,
                    expected=(
                        "active instance-set resolvable from"
                        f" terragrunt/live/.../{self._env}/active.hcl"
                    ),
                    actual=str(exc),
                )
            else:
                self._emit_ok(
                    probes,
                    active_probe,
                    detail=f"active instance-set = {active_set!r} (service FQDN instance)",
                )
                for label in (_COLLECTOR_HOST_LABEL, _PORTAL_HOST_LABEL):
                    _add_fqdn(f"{label}-{active_set}.{service_apex}")

        # Stable pretty FQDNs: <label>.<pretty_apex> -- the public contract for every env.
        if pretty_apex:
            for label in (_COLLECTOR_HOST_LABEL, _PORTAL_HOST_LABEL):
                _add_fqdn(f"{label}.{pretty_apex}")

        for fqdn in published_fqdns:
            dns_probe = f"endpoints:dns-resolve:{fqdn}"
            success, detail, _ip = self._dns_resolver(fqdn)
            if success:
                self._emit_ok(probes, dns_probe, detail=detail)
            else:
                self._emit_fail(probes, dns_probe, expected="DNS resolved", actual=detail)

            tls_probe = f"endpoints:tls-handshake:{fqdn}"
            tls_ok, tls_detail = self._tls_checker(fqdn)
            if tls_ok:
                self._emit_ok(probes, tls_probe, detail=tls_detail)
            else:
                self._emit_fail(probes, tls_probe, expected="TLS handshake OK", actual=tls_detail)

            http_probe = f"endpoints:https-probe:{fqdn}"
            http_ok, http_detail = self._http_prober(f"https://{fqdn}/")
            if http_ok:
                self._emit_ok(probes, http_probe, detail=http_detail)
            else:
                self._emit_fail(
                    probes,
                    http_probe,
                    expected="HTTPS probe OK",
                    actual=http_detail,
                )

        # Optional, non-failing apex probe. The bare apex has no record by design, so its
        # resolution is recorded as an informational OK regardless of outcome -- it must
        # never turn the endpoints check red. Probe the distinct apexes (service + pretty).
        optional_apexes: list[str] = []
        seen_apexes: set[str] = set()
        for apex in (service_apex, pretty_apex):
            if apex and apex not in seen_apexes:
                seen_apexes.add(apex)
                optional_apexes.append(apex)
        for apex in optional_apexes:
            apex_probe = f"endpoints:apex-optional:{apex}"
            apex_ok, apex_detail, _apex_ip = self._dns_resolver(apex)
            self._emit_ok(
                probes,
                apex_probe,
                detail=(
                    f"apex {apex} is record-less by design (optional probe);"
                    f" resolver result: {apex_detail}"
                ),
            )

    # ------------------------------------------------------------------
    # observability check
    # ------------------------------------------------------------------

    def _check_observability(self, probes: list[dict[str, Any]]) -> None:
        """Probe: CloudWatch alarms, budgets, anomaly monitors, SNS subscriptions."""
        import botocore.exceptions

        cw = self._client("cloudwatch")
        budgets = self._client("budgets")
        ce = self._client("ce")
        sns = self._client("sns")

        # Probe: CloudWatch alarms exist and none in ALARM state.
        #
        # The observability unit's alarm NAMES are the map keys of its alarms input
        # (providers/aws/primitives/cloudwatch/main.tf: alarm_name = each.key), e.g.
        # alb_5xx_count, ecs_running_task_count, ... -- they carry NO "<env>-" prefix.
        # The old AlarmNamePrefix=f"{self._env}-" filter therefore matched ZERO of the
        # real alarms. List ALL alarms (no misleading prefix filter) and assert that at
        # least one of the expected observability alarm names is present.
        probe_alarms = f"observability:describe-alarms:{self._env}"
        try:
            cw_resp = cw.describe_alarms()
            alarms = cw_resp.get("MetricAlarms", [])
            present_names = {a.get("AlarmName", "") for a in alarms}
            matched_names = present_names & _OBSERVABILITY_ALARM_NAMES
            if not matched_names:
                self._emit_fail(
                    probes,
                    probe_alarms,
                    expected=(
                        "at least one observability alarm present"
                        f" (one of {sorted(_OBSERVABILITY_ALARM_NAMES)!r})"
                    ),
                    actual=(
                        f"none of the expected alarm names found among"
                        f" {len(alarms)} alarm(s): {sorted(present_names)!r}"
                    ),
                )
            else:
                alarming = [a for a in alarms if a.get("StateValue") == "ALARM"]
                if alarming:
                    self._emit_fail(
                        probes,
                        probe_alarms,
                        expected="no alarms in ALARM state",
                        actual=(
                            f"{len(alarming)} alarm(s) in ALARM:"
                            f" {[a['AlarmName'] for a in alarming]!r}"
                        ),
                    )
                else:
                    self._emit_ok(
                        probes,
                        probe_alarms,
                        detail=(
                            f"{len(alarms)} alarm(s) total,"
                            f" {len(matched_names)} expected observability alarm(s)"
                            f" present {sorted(matched_names)!r}, none in ALARM state"
                        ),
                    )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_alarms,
                expected="CloudWatch alarms accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: Budgets present
        probe_budgets = f"observability:describe-budgets:{self._env}"
        try:
            budgets_resp = budgets.describe_budgets(AccountId=self._account_id)
            budget_list = budgets_resp.get("Budgets", [])
            if budget_list:
                self._emit_ok(
                    probes,
                    probe_budgets,
                    detail=f"{len(budget_list)} budget(s) present",
                )
            else:
                self._emit_fail(
                    probes,
                    probe_budgets,
                    expected="at least one budget present",
                    actual="no budgets found",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_budgets,
                expected="budgets accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: Cost anomaly monitors + subscriptions present
        probe_anomaly = f"observability:get-anomaly-monitors:{self._env}"
        try:
            monitors_resp = ce.get_anomaly_monitors()
            monitors = monitors_resp.get("AnomalyMonitors", [])
            if monitors:
                self._emit_ok(
                    probes,
                    probe_anomaly,
                    detail=f"{len(monitors)} anomaly monitor(s) present",
                )
            else:
                self._emit_fail(
                    probes,
                    probe_anomaly,
                    expected="at least one anomaly monitor",
                    actual="no monitors found",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_anomaly,
                expected="anomaly monitors accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        probe_subs = f"observability:get-anomaly-subscriptions:{self._env}"
        try:
            subs_resp = ce.get_anomaly_subscriptions()
            subs = subs_resp.get("AnomalySubscriptions", [])
            if subs:
                self._emit_ok(
                    probes,
                    probe_subs,
                    detail=f"{len(subs)} anomaly subscription(s) present",
                )
            else:
                self._emit_fail(
                    probes,
                    probe_subs,
                    expected="at least one anomaly subscription",
                    actual="no subscriptions found",
                )
        except botocore.exceptions.ClientError as exc:
            self._emit_fail(
                probes,
                probe_subs,
                expected="anomaly subscriptions accessible",
                actual=f"error: {exc.response['Error']['Code']}",
            )

        # Probe: the namespace-derived SNS topic has at least one subscription.
        #
        # The topic name is namespace-derived, mirroring _envcommon/observability.hcl
        # (topic_name = "${namespace}-observability"), NOT the old hardcoded "<env>-alerts".
        #
        # SNS email-subscription confirmation is OPERATOR-gated (the subscriber clicks the
        # confirmation link out-of-band), so a subscription in PendingConfirmation is the
        # EXPECTED interim state and must be accepted as OK -- requiring "Confirmed" here
        # would falsely FAIL a correctly-provisioned, awaiting-operator-confirmation topic.
        # The probe asserts a subscription EXISTS (any state except the tombstone
        # "Deleted"); a confirmed ARN is reported as confirmed, a PendingConfirmation is
        # reported as pending-but-present.
        probe_sns = f"observability:sns-subscription-present:{self._env}"
        topic_arn = (
            f"arn:aws:sns:{self._get_region()}:{self._account_id}"
            f":{self._observability_topic_name()}"
        )
        try:
            sns_resp = sns.list_subscriptions_by_topic(TopicArn=topic_arn)
            subs_list = sns_resp.get("Subscriptions", [])
            # A subscription counts as present unless its SubscriptionArn is the "Deleted"
            # tombstone. PendingConfirmation IS counted as present (operator-gated).
            live_subs = [s for s in subs_list if s.get("SubscriptionArn") != "Deleted"]
            if not live_subs:
                self._emit_fail(
                    probes,
                    probe_sns,
                    expected=f"at least one subscription on SNS topic '{topic_arn}'",
                    actual=(
                        "no subscriptions found"
                        if not subs_list
                        else f"all {len(subs_list)} subscription(s) are Deleted tombstones"
                    ),
                )
            else:
                confirmed = [
                    s
                    for s in live_subs
                    if s.get("SubscriptionArn", "").startswith("arn:")
                    and s.get("SubscriptionArn") != "PendingConfirmation"
                ]
                pending = [
                    s for s in live_subs if s.get("SubscriptionArn") == "PendingConfirmation"
                ]
                self._emit_ok(
                    probes,
                    probe_sns,
                    detail=(
                        f"{len(live_subs)} subscription(s) present on '{topic_arn}'"
                        f" ({len(confirmed)} confirmed, {len(pending)} pending"
                        f" operator confirmation -- accepted)"
                    ),
                )
        except botocore.exceptions.ClientError as exc:
            # Fail-fast: an inaccessible alerts topic must surface as a probe failure,
            # never be silently skipped as OK.
            self._emit_fail(
                probes,
                probe_sns,
                expected=f"SNS topic '{topic_arn}' accessible with at least one subscription",
                actual=f"error: {exc.response['Error']['Code']}",
            )

    # ------------------------------------------------------------------
    # no-project-resources check
    # ------------------------------------------------------------------

    def _check_no_project_resources(self, probes: list[dict[str, Any]]) -> None:
        """Probe: zero non-exempt Project=<TERRATEST_PROJECT_TAG> resources remain."""
        rgt = self._client("resourcegroupstaggingapi")

        probe_tag = f"tagging-api:Project={TERRATEST_PROJECT_TAG}"
        all_arns: list[str] = []
        pagination_token = ""

        while True:
            kwargs: dict[str, Any] = {
                "TagFilters": [{"Key": "Project", "Values": [TERRATEST_PROJECT_TAG]}],
            }
            if pagination_token:
                kwargs["PaginationToken"] = pagination_token
            resp = rgt.get_resources(**kwargs)
            resources = resp.get("ResourceTagMappingList", [])
            all_arns.extend(r["ResourceARN"] for r in resources)
            pagination_token = resp.get("PaginationToken", "")
            if not pagination_token:
                break

        exempt_patterns = self._exemptions_data.get(self._account_id, [])

        def _is_exempt(arn: str) -> bool:
            return any(arn == pattern or arn.startswith(pattern) for pattern in exempt_patterns)

        non_exempt = [arn for arn in all_arns if not _is_exempt(arn)]

        if all_arns:
            exempt_count = len(all_arns) - len(non_exempt)
            self._emit_ok(
                probes,
                probe_tag,
                detail=(f"{len(all_arns)} match(es), {exempt_count} on bootstrap exemption list"),
            )
        else:
            self._emit_ok(probes, probe_tag, detail="0 matches")

        exemption_probe = "exemption-list"
        exempt_detail = ", ".join(exempt_patterns) if exempt_patterns else "none"
        self._emit_ok(probes, exemption_probe, detail=f"exempt: {exempt_detail}")

        if non_exempt:
            for arn in non_exempt:
                print(f"FAIL non-project-resources:non-exempt-resource: {arn}")
            self._emit_fail(
                probes,
                "no-project-resources:non-exempt-count",
                expected="0 non-exempt resources",
                actual=f"{len(non_exempt)} non-exempt resource(s) found",
            )

    # ------------------------------------------------------------------
    # repo-settings check
    # ------------------------------------------------------------------

    def _check_repo_settings(self, probes: list[dict[str, Any]]) -> None:
        """Probe: GitHub repo variables and environments are configured (FR-8)."""
        # Check variables
        probe_vars = "repo-settings:gh-variable-list"
        rc, stdout, stderr = self._gh_runner(["variable", "list", "--json", "name"])
        if rc != 0:
            # Try non-JSON format
            rc, stdout, stderr = self._gh_runner(["variable", "list"])

        present_vars: set[str] = set()
        if rc == 0:
            # Parse either JSON or plain text output
            if stdout.strip().startswith("["):
                try:
                    var_data = json.loads(stdout)
                    present_vars = {v.get("name", "") for v in var_data}
                except json.JSONDecodeError:
                    present_vars = {line.strip() for line in stdout.splitlines() if line.strip()}
            else:
                present_vars = {
                    line.strip().split()[0] for line in stdout.splitlines() if line.strip()
                }

        missing_vars = _GH_REQUIRED_VARIABLES - present_vars
        if not missing_vars:
            self._emit_ok(
                probes,
                probe_vars,
                detail=f"all {len(_GH_REQUIRED_VARIABLES)} required variables present",
            )
        else:
            self._emit_fail(
                probes,
                probe_vars,
                expected=f"all of {sorted(_GH_REQUIRED_VARIABLES)!r}",
                actual=f"missing: {sorted(missing_vars)!r}",
            )

        # Check environments
        repo = self._get_gh_repo()
        for env_name in sorted(_GH_REQUIRED_ENVIRONMENTS):
            probe_env = f"repo-settings:gh-environment:{env_name}"
            rc, stdout, stderr = self._gh_runner(["api", f"repos/{repo}/environments/{env_name}"])
            if rc != 0:
                self._emit_fail(
                    probes,
                    probe_env,
                    expected=f"environment {env_name!r} exists",
                    actual=f"not found (exit {rc})",
                )
                continue

            self._emit_ok(probes, probe_env, detail=f"environment {env_name!r} exists")

            if env_name == "prod-apply":
                # Check has at least one required reviewer
                probe_reviewer = "repo-settings:prod-apply-has-reviewer"
                try:
                    env_data = json.loads(stdout)
                    rules = env_data.get("protection_rules", [])
                    has_reviewer = any(
                        r.get("type") == "required_reviewers" and r.get("reviewers") for r in rules
                    )
                    if has_reviewer:
                        self._emit_ok(
                            probes,
                            probe_reviewer,
                            detail="prod-apply has at least one required reviewer",
                        )
                    else:
                        self._emit_fail(
                            probes,
                            probe_reviewer,
                            expected="prod-apply has at least one required reviewer",
                            actual="no required_reviewers rule found",
                        )
                except json.JSONDecodeError:
                    # Fail-fast: an unparseable response for the prod-apply
                    # required-reviewer check is a real gap in branch protection,
                    # not a "skipped" fallback to mask as success.
                    self._emit_fail(
                        probes,
                        probe_reviewer,
                        expected="prod-apply reviewer data parseable as JSON",
                        actual="non-JSON or missing response from gh api",
                    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for live_verify.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).
    """
    parser = argparse.ArgumentParser(
        prog="scripts.live_verify",
        description="Read-only live verification prober (FR-13).",
    )
    parser.add_argument(
        "--check",
        required=True,
        help=f"Check to run. One of: {', '.join(sorted(_VALID_CHECKS))}",
    )
    parser.add_argument(
        "--env",
        required=True,
        help="Environment to probe. One of: sandbox, qa, prod, root",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the JSON evidence file (spec section 5.5).",
    )

    args = parser.parse_args(argv)

    accounts_data = json.loads(_ACCOUNTS_JSON_PATH.read_text())
    exemptions_data = json.loads(_EXEMPTIONS_JSON_PATH.read_text())
    oidc_roles_data = json.loads(_OIDC_ROLES_JSON_PATH.read_text())

    try:
        import boto3 as boto3_module
    except ImportError as exc:
        print(
            f"ERROR: boto3 is not available: {exc}. Run 'uv sync' to install dependencies.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        runner = LiveVerify(
            check=args.check,
            env=args.env,
            output_path=args.output,
            accounts_data=accounts_data,
            exemptions_data=exemptions_data,
            oidc_roles_data=oidc_roles_data,
            boto3_module=boto3_module,
            gh_runner=None,
        )
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    exit_code = runner.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
