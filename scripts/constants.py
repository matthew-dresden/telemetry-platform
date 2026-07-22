"""Shared constants and utilities for the telemetry-platform monorepo automation scripts.

All shared scope-bucket names, override label/env-var keys, and other
named constants live here so that no literal strings appear in logic modules.
Configuration values (paths, roots, thresholds) come from monorepo-config.json;
this module holds only symbolic names that are referenced across multiple scripts.

Shared utilities:
    write_output(output_path, key, value): Append a key=value pair to the output file.
"""

from __future__ import annotations

import os as _os
import pathlib as _pathlib

# ---------------------------------------------------------------------------
# Scope bucket names
# ---------------------------------------------------------------------------

SCOPE_MODULE: str = "module"
SCOPE_TERRAGRUNT: str = "terragrunt"
SCOPE_CONFIG: str = "config"
SCOPE_MULTI_MODULE: str = "multi-module"
SCOPE_MIXED: str = "mixed"
SCOPE_ALL: str = "all"

# ---------------------------------------------------------------------------
# Override label and env-var keys
# ---------------------------------------------------------------------------

# The GitHub PR label an org admin attaches to trigger scope=all revalidation.
SCOPE_OVERRIDE_LABEL: str = "detect-scope-override"

# GitHub Actions output key names written to the --output file.
OUTPUT_KEY_SCOPE: str = "scope"
OUTPUT_KEY_MODULE_PATH: str = "module_path"
OUTPUT_KEY_MODULES: str = "modules"
OUTPUT_KEY_SCOPE_OVERRIDE: str = "scope_override"
OUTPUT_KEY_MODULE_TYPE: str = "module_type"

# ---------------------------------------------------------------------------
# Error prefix used by all CLI scripts (GitHub Actions annotation format)
# ---------------------------------------------------------------------------

GH_ERROR_PREFIX: str = "::error::"

# ---------------------------------------------------------------------------
# Release commit constants (docs/release-pipeline.md, B16)
# ---------------------------------------------------------------------------

# Prefix that identifies a release commit authored by the release bot.
# should_skip=true iff the message starts with this prefix AND the actor is the bot.
RELEASE_COMMIT_PREFIX: str = "chore(release):"

# Identity of the release bot actor (docs/release-pipeline.md).
BOT_ACTOR: str = "github-actions[bot]"

# ---------------------------------------------------------------------------
# Conventional-commit type-to-bump mapping (docs/release-pipeline.md / constants.py:23-28)
# ---------------------------------------------------------------------------

# Commit types that produce a MINOR (feature) bump.
MINOR_TYPES: frozenset[str] = frozenset(
    {"feat", "perf", "build", "ci", "revert", "release", "meta", "module"}
)

# Commit types that produce a PATCH (fix) bump.
PATCH_TYPES: frozenset[str] = frozenset({"fix", "chore", "docs", "style", "refactor", "test"})

# All recognized conventional-commit types (MINOR + PATCH).
VALID_COMMIT_TYPES: frozenset[str] = MINOR_TYPES | PATCH_TYPES

# ---------------------------------------------------------------------------
# Changelog section mapping (docs/release-pipeline.md, ci_generate_changelog.py)
# ---------------------------------------------------------------------------

# Maps commit type to the changelog section header it appears under.
COMMIT_TYPE_TO_SECTION: dict[str, str] = {
    "feat": "Features",
    "perf": "Performance",
    "build": "Build",
    "ci": "CI",
    "revert": "Reverts",
    "release": "Releases",
    "meta": "Meta",
    "module": "Modules",
    "fix": "Bug Fixes",
    "chore": "Chores",
    "docs": "Documentation",
    "style": "Style",
    "refactor": "Refactoring",
    "test": "Tests",
}

# ---------------------------------------------------------------------------
# Output key names for release scripts
# ---------------------------------------------------------------------------

OUTPUT_KEY_NEXT_VERSION: str = "next_version"
OUTPUT_KEY_BUMP_TYPE: str = "bump_type"
OUTPUT_KEY_TAG_PREFIX: str = "tag_prefix"
OUTPUT_KEY_FULL_TAG: str = "full_tag"
OUTPUT_KEY_IS_INITIAL: str = "is_initial"
OUTPUT_KEY_SHOULD_SKIP: str = "should_skip"
OUTPUT_KEY_CHANGELOG_PATH: str = "changelog_path"

# ---------------------------------------------------------------------------
# Sweep polling budgets (FR-4, spec section 7)
# Sourced from environment variables TT_SWEEP_POLL_TIMEOUT / TT_SWEEP_POLL_INTERVAL.
# Default values are defined here as the single constants site (spec section 7).
# ---------------------------------------------------------------------------

# Maximum total seconds to wait for a Tagging API call to succeed under throttling.
# Read from environment at module import time; tests that need to override this
# must set the env var BEFORE importing scripts.constants or scripts.terratest_sweep.
# The sweep script reads these via os.environ at call time (not from cached module values)
# to support test-time env overrides.
TT_SWEEP_POLL_TIMEOUT: int = int(_os.environ.get("TT_SWEEP_POLL_TIMEOUT", "300"))

# Seconds to wait between Tagging API retry attempts.
TT_SWEEP_POLL_INTERVAL: float = float(_os.environ.get("TT_SWEEP_POLL_INTERVAL", "10"))

# Minimum age (minutes) a terratest resource must have before the GLOBAL
# (cross-run, run_id=None) sweep will delete it. The auto-generated run id
# encodes its own UTC creation timestamp (tt-<yyyymmddHHMMSS>-<rand>), so the
# global sweep parses that timestamp from the resource's terratest-run tag value
# and SKIPS resources younger than this threshold. This prevents the daily
# scheduled global delete-sweep from racing the PR scope=all matrix and deleting
# another in-flight job's just-created resources (e.g. scheduling a fixture KMS
# key for deletion mid-apply, or deleting an ECS cluster a CreateService still
# references). Real orphans persist far longer than this window (until at least
# the next daily sweep, ~24h), so genuine leaks are still cleaned. The
# run-SCOPED per-module sweep (run_id set) is exempt: it deletes only its own
# just-finished run. Read from env at call time so tests can override it.
TT_SWEEP_MIN_AGE_MINUTES: int = int(_os.environ.get("TT_SWEEP_MIN_AGE_MINUTES", "120"))

# Maximum number of loop-until-stable delete passes SWEEP_MODE=delete performs.
# Each pass RE-ENUMERATES the tag-scoped inventory (fresh Tagging API describe)
# and then runs one full reverse-dependency-tier delete over it. VPC-family
# deletion is eventually consistent: a VPC cannot be deleted until its ENIs /
# subnets / IGW / NAT gateway have finished detaching, which can take longer than
# a single pass. Re-enumerating between passes lets those eventually-consistent
# deletions (and any resource the Tagging API only just indexed) be picked up on
# the next pass, so the delete step fully drains before the fail-on-residue check
# step runs. The loop stops early as soon as a pass leaves NO transient
# DependencyViolation pending (everything is deleted / already-gone /
# spared-as-in-flight); the FINAL pass surfaces any still-stuck resource as a
# failure so a genuine leak is never masked (fail-safe). Read from env at call
# time so tests can override it.
TT_SWEEP_MAX_DELETE_PASSES: int = int(_os.environ.get("TT_SWEEP_MAX_DELETE_PASSES", "6"))

# ---------------------------------------------------------------------------
# Shared output utility
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Terratest runner constants (FR-1, spec section 4.1 / 7)
# ---------------------------------------------------------------------------

# Prefix for auto-generated run ids: tt-<yyyymmddHHMMSS>-<6-char-random>
TERRATEST_RUN_ID_PREFIX: str = "tt"

# The number of random characters appended to the auto-generated run id.
TERRATEST_RUN_ID_RANDOM_CHARS: int = 6

# strftime/strptime format of the UTC timestamp embedded in an auto-generated
# run id (tt-<yyyymmddHHMMSS>-<rand>). The global sweep parses this back out of
# the terratest-run tag value to compute resource age (TT_SWEEP_MIN_AGE_MINUTES).
TERRATEST_RUN_ID_TIMESTAMP_FORMAT: str = "%Y%m%d%H%M%S"

# ---------------------------------------------------------------------------
# Cost Explorer anomaly-monitor sweep patterns (FR-4, follow-up #84 item 1)
#
# Cost Explorer anomaly monitors and subscriptions are NOT indexed by the
# Resource Groups Tagging API (they are absent even when tagged), so the
# tag-scoped sweep never sees them and they orphan. The sweep therefore
# enumerates them directly via the CE API (get_anomaly_monitors /
# get_anomaly_subscriptions) and deletes the ones whose name matches a terratest
# fixture pattern. Monitors named test-monitor-<unix-ts> additionally carry a
# parseable creation timestamp the sweep uses for the same min-age guard as the
# tag-based path, so a global CE sweep never deletes an in-flight test's monitor.
# ---------------------------------------------------------------------------

# Name prefixes/suffixes that identify a terratest-created CE anomaly monitor.
CE_MONITOR_NAME_PREFIXES: tuple[str, ...] = ("test-monitor-",)
CE_MONITOR_NAME_SUFFIXES: tuple[str, ...] = ("-monitor",)

# Name prefixes/suffixes that identify a terratest-created CE anomaly subscription.
CE_SUBSCRIPTION_NAME_PREFIXES: tuple[str, ...] = ("test-sub-",)
CE_SUBSCRIPTION_NAME_SUFFIXES: tuple[str, ...] = ("-subscription",)

# Project tag value exported to go-test and Terraform as TF_VAR_project_tag.
TERRATEST_PROJECT_TAG: str = "telemetry-platform"

# Go build constraint that gates the live-integration terratest test files
# (//go:build terratest). These tests do real terraform apply/plan and require
# AWS credentials + TERRATEST_RUN_ID, so they MUST NOT compile into the default
# `go test ./...` build that backs make go-unit-test-coverage. make tf-test
# (run_terratest.py) and the Go static gates that should cover all sources
# (go vet / govulncheck) pass `-tags <this value>` to include them. This single
# source of truth keeps run_terratest.py, run_go.py, and the `//go:build`
# constraint in the *_test.go files consistent.
TERRATEST_BUILD_TAG: str = "terratest"

# Name of the shared Terraform provider plugin cache directory created by the
# runner before invoking go test. The default location is $HOME/<TF_PLUGIN_CACHE_DIR_NAME>.
# Overridable at runtime via the TF_PLUGIN_CACHE_DIR environment variable.
TF_PLUGIN_CACHE_DIR_NAME: str = ".tf-plugin-cache"

# Name of the per-module file the runner writes with the resolved run id after
# each tf-test invocation. Living under the module directory (one file per
# module) lets concurrent runs of different modules each scope their own
# zero-orphan proof without colliding. Read by the terratest-sweep-module
# Make target. Transient artifact -- gitignored, never committed.
TERRATEST_RUN_ID_FILE_NAME: str = ".terratest-run-id"

# Static placeholder run id exported as TF_VAR_terratest_run_id during OFFLINE
# static scans (trivy misconfiguration scanning) so the run-id-scoped fixture
# names -- which derive a uniqueness suffix from var.terratest_run_id (the
# `substr(var.terratest_run_id, length - 6, 6)` parallel-isolation idiom) --
# resolve to a concrete value. Without it the scanner cannot resolve the
# access-log destination bucket name and falsely reports CloudFront/S3 access
# logging as disabled (AWS-0010/AWS-0089). This is a scan-time placeholder ONLY;
# the real terratest run always injects a fresh run id at apply time, so the
# scanned value never reaches AWS. Mirrors the committed example terraform.tfvars
# offline value. Must be at least 6 characters so the suffix substr is non-empty.
TERRATEST_OFFLINE_SCAN_RUN_ID: str = "offline-validate"

# Default go-test timeout applied when GO_TEST_TIMEOUT is absent from both
# test.config and the GO_TEST_TIMEOUT environment variable.
# The runner precedence order is: env var > test.config value > this constant.
DEFAULT_GO_TEST_TIMEOUT: str = "20m"

# Deny-set account roles that must not be used for terratest runs.
# Read from accounts.json -- these constants are the role name strings, not
# hardcoded account IDs (spec section 3.6).
TERRATEST_DENIED_ROLES: frozenset[str] = frozenset({"prod-infra", "dns-owner"})

# ---------------------------------------------------------------------------
# Suite-mode constants (FR-5, spec section 4.5)
# ---------------------------------------------------------------------------

# Env var name that holds the expected module roster count for the suite.
# When set, _discover_suite_modules fails closed when the discovered count
# does not match this value (D-25 roster-count contract).
# Overridable via the TERRATEST_MODULE_ROSTER_COUNT environment variable.
TERRATEST_MODULE_ROSTER_COUNT_ENV_VAR: str = "TERRATEST_MODULE_ROSTER_COUNT"

# KMS key states that must be excluded from the orphan-residue count after
# schedule_key_deletion (the Tagging API keeps them visible for the mandatory
# 7-day pending-deletion window).
KMS_PENDING_DELETION_STATES: frozenset[str] = frozenset(
    {"PendingDeletion", "PendingReplicaDeletion"}
)

# ---------------------------------------------------------------------------
# Live-verify readiness polling budgets (FR-13, spec section 7)
# Sourced from environment variables LIVE_VERIFY_TIMEOUT / LIVE_VERIFY_POLL_INTERVAL.
# Default values are the single constants site (spec section 7).
# ---------------------------------------------------------------------------

# Maximum total seconds to wait for a readiness-flavoured probe to succeed
# (CloudFront Deployed, ACM ISSUED, DNS propagation).
# Read from environment at module import time; tests that need to override this
# must set the env var BEFORE importing scripts.constants or scripts.live_verify.
LIVE_VERIFY_TIMEOUT: int = int(_os.environ.get("LIVE_VERIFY_TIMEOUT", "1800"))

# Seconds to wait between readiness-probe retry attempts.
LIVE_VERIFY_POLL_INTERVAL: float = float(_os.environ.get("LIVE_VERIFY_POLL_INTERVAL", "15"))

# Seconds for individual socket/HTTP connection timeouts (TLS handshake, HTTP probe).
# Overridable via LIVE_VERIFY_NETWORK_TIMEOUT environment variable.
LIVE_VERIFY_NETWORK_TIMEOUT: int = int(_os.environ.get("LIVE_VERIFY_NETWORK_TIMEOUT", "10"))

# Default TCP port used for the HTTPS endpoint probes (TLS handshake, HTTP probe)
# when the probed URL/FQDN does not carry an explicit port. 443 is the IANA
# well-known port for the https scheme. Overridable via the
# LIVE_VERIFY_HTTPS_PORT environment variable.
LIVE_VERIFY_HTTPS_PORT: int = int(_os.environ.get("LIVE_VERIFY_HTTPS_PORT", "443"))

# ---------------------------------------------------------------------------
# OTLP end-to-end test harness constants
# (scripts/otlp_e2e_loadgen.py, scripts/e2e_verify.py, scripts/e2e_common.py)
#
# The deployed collector is OTLP/HTTP, LOGS-ONLY. These symbolic names are the
# single source of truth shared across the harness scripts so no literal string
# appears in harness logic. Endpoint hostnames are NOT hardcoded here -- they
# are composed at run time from terragrunt/common/domains.json (the same source
# the IaC uses, D45/D47); only the fixed sub-domain labels and protocol
# constants live here.
# ---------------------------------------------------------------------------

# OTLP/HTTP signal paths. The collector ingests logs only; the metrics/traces
# paths exist solely as negative (wrong-signal) probes.
E2E_OTLP_LOGS_PATH: str = "/v1/logs"
E2E_OTLP_METRICS_PATH: str = "/v1/metrics"
E2E_OTLP_TRACES_PATH: str = "/v1/traces"

# Collector health-check path + port (ADOT health_check extension on 13133).
E2E_HEALTH_PATH: str = "/"
E2E_HEALTH_PORT: int = 13133

# OTLP/gRPC receiver port. The public edge exposes HTTP only, so a TCP connect
# to this port must find NO listener (the grpc-probe negative test).
E2E_GRPC_PORT: int = 4317

# OTLP/HTTP content types accepted by the receiver.
E2E_CONTENT_TYPE_PROTOBUF: str = "application/x-protobuf"
E2E_CONTENT_TYPE_JSON: str = "application/json"

# ADOT OTLP receiver max_request_body_size in bytes (D44). A request body
# strictly larger than this must be rejected with HTTP 413.
E2E_MAX_BODY_BYTES: int = 4194304

# WAF per-IP rate limit (requests per 5-minute window) (D44).
E2E_WAF_RATE_LIMIT_PER_5MIN: int = 2000

# Fixed sub-domain labels the stack publishes
# (collector-ingestion.hcl / portal.hcl, D37/D47). The pretty hostname is the
# bare label; the service hostname carries the environment-instance suffix.
E2E_COLLECTOR_HOST_LABEL: str = "collector"
E2E_PORTAL_HOST_LABEL: str = "telemetry"

# Environment-instance segment of the service FQDN
# (collector-<env_instance>.<service_apex>); the live leaf is 000.
# Overridable via the E2E_ENVIRONMENT_INSTANCE environment variable.
E2E_ENVIRONMENT_INSTANCE: str = _os.environ.get("E2E_ENVIRONMENT_INSTANCE", "000")

# Fixed reserved tool value for the post-deploy e2e smoke pipeline. Every synthetic
# LogRecord the load generator emits carries tool = E2E_TOOL_VALUE -- a single fixed
# value, NOT a per-run value. The Glue tool partition uses Athena ENUM projection over
# a governed, fixed set of tool values (providers/aws/references/data-lake,
# projection.tool.values), so a per-run tool value would fall outside the enum and its
# partitions would never be projected (invisible to every query). enum keeps the table
# queryable with no tool filter (SELECT * works). A specific run's records are instead
# identified by a run_id embedded in the event payload ($.run_id), which the verifier
# reconciles within the shared tool=e2e-smoke partition. This value MUST be a member of
# the data-lake glue_partition_projection_tool_values enum list.
E2E_TOOL_VALUE: str = "e2e-smoke"

# Synthetic usage-event taxonomy, derived from the Glue telemetry_events schema
# (cols timestamp/tool/event_type/payload), NOT from any external tool.
E2E_EVENT_TYPES: tuple[str, ...] = (
    "command_execution",
    "command_error",
    "session_start",
    "session_end",
    "feature_usage",
    "perf_sample",
)

# Payload variety classes exercised by --mode variety.
E2E_PAYLOAD_VARIETIES: tuple[str, ...] = (
    "small",
    "nested",
    "unicode",
    "control-chars",
    "large",
)

# Synthetic structured-OTLP resource service.name, used ONLY by --mode
# structured-events / structured-metrics. Sending this value exercises the
# collector's structured-OTLP pipeline: the tool-registry's "e2e-smoke" entry
# (terragrunt/common/tool-registry.json) lists it in service_names, so the
# terragrunt-governed, registry-derived collector-ingestion
# structured_otlp_service_names allowlist includes it and the collector routes
# its records to the raw_log=false exporter, and the terragrunt-governed,
# registry-derived data-lake service_tool_map maps it to E2E_TOOL_VALUE
# ("e2e-smoke") so the cwl_split Lambda reshape lands the records under the
# shared reconcile tool partition.
E2E_STRUCTURED_SERVICE_NAME: str = "synthetic-claude-e2e"

# OTLP instrumentation-scope name stamped on every --mode structured-events
# LogRecord, matching the real claude_code telemetry scope name captured from
# claude v2.1.210.
E2E_STRUCTURED_SCOPE_NAME: str = "com.anthropic.claude_code.events"

# OTLP instrumentation-scope name stamped on every --mode structured-metrics Metric,
# matching the real claude_code METRICS scope name -- the EMF event's "OTelLib" field
# (see tests/unit/fixtures/claude/metrics/*.json) captured from claude v2.1.210. Distinct
# from E2E_STRUCTURED_SCOPE_NAME (the LOGS scope, which carries a ".events" suffix): the
# real Claude Code SDK uses a separate metrics instrumentation scope with no such suffix.
E2E_STRUCTURED_METRICS_SCOPE_NAME: str = "com.anthropic.claude_code"

# Fixed set of two synthetic structured-metrics instrument names, used ONLY by --mode
# structured-metrics. Deliberately namespaced under "synthetic.e2e." (NOT "claude_code.") so
# a raw Athena payload reader can never mistake one for a real Claude Code metric, while
# still exercising the collector's metrics/structured awsemf/EMF pipeline + the data-lake
# cwl_split _reshape_emf path end to end (ADR 0031).
E2E_STRUCTURED_METRIC_NAMES: tuple[str, str] = (
    "synthetic.e2e.counter_one",
    "synthetic.e2e.counter_two",
)

# Load-generator modes; each exercises one C-series conformance test, except
# structured-events (which exercises the structured-OTLP LOGS ingest pipeline:
# collector raw_log=false route + data-lake cwl_split reshape) and
# structured-metrics (which exercises the structured-OTLP METRICS ingest
# pipeline: collector awsemf/EMF route + data-lake cwl_split _reshape_emf),
# both end-to-end.
E2E_MODES: tuple[str, ...] = (
    "happy",
    "volume",
    "variety",
    "structured-events",
    "structured-metrics",
    "oversize",
    "bad-content-type",
    "malformed",
    "wrong-signal",
    "grpc-probe",
    "waf-trigger",
    "rate-burst",
)

# Schema signature of the telemetry data-lake Glue table. The deployed table
# name is namespace-derived (e.g. telemetry_useast1_sandbox_shared_data_lake_000
# _events), so the harness NEVER matches it by name: it discovers the table whose
# StorageDescriptor columns are a superset of E2E_GLUE_REQUIRED_COLUMNS and whose
# partition keys are a superset of E2E_GLUE_PARTITION_KEYS. Per the data-lake
# BUG-3 fix (data-lake/locals.tf), tool + dt are partition keys ONLY (disjoint
# from the data columns): the data columns are timestamp/event_type/payload and
# the partition keys are tool,dt. tool is therefore NOT a data column.
E2E_GLUE_REQUIRED_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "event_type",
    "payload",
)
E2E_GLUE_PARTITION_KEYS: tuple[str, ...] = ("tool", "dt")

# User-Agent product token sent on every synthetic OTLP/HTTP request. A realistic
# value is REQUIRED: the collector WAF (AWSManagedRulesCommonRuleSet rule
# NoUserAgent_HEADER) blocks requests with no User-Agent header, so a UA-less
# load generator never reaches the receiver. The product version is read from the
# monorepo VERSION file; the whole header value is overridable via E2E_USER_AGENT.
E2E_USER_AGENT_PRODUCT: str = "telemetry-platform-otlp-e2e"
_E2E_VERSION_FILE: _pathlib.Path = _pathlib.Path(__file__).resolve().parent.parent / "VERSION"
E2E_USER_AGENT: str = _os.environ.get("E2E_USER_AGENT", "") or (
    f"{E2E_USER_AGENT_PRODUCT}/{_E2E_VERSION_FILE.read_text(encoding='utf-8').strip()}"
)

# Default HTTPS port for the public collector edge. Overridable via E2E_HTTPS_PORT.
E2E_HTTPS_PORT: int = int(_os.environ.get("E2E_HTTPS_PORT", "443"))

# Network/HTTP connection timeout (seconds) for a single load-generator request.
# Overridable via the E2E_HTTP_TIMEOUT environment variable.
E2E_HTTP_TIMEOUT: int = int(_os.environ.get("E2E_HTTP_TIMEOUT", "30"))

# Readiness-polling budget for the consumer-side verifier (CWL/Firehose/S3/Glue/
# Athena propagation). Active readiness detection -- no time.sleep. Both values
# are env-driven (E2E_POLL_TIMEOUT / E2E_POLL_INTERVAL).
E2E_POLL_TIMEOUT: int = int(_os.environ.get("E2E_POLL_TIMEOUT", "900"))
E2E_POLL_INTERVAL: float = float(_os.environ.get("E2E_POLL_INTERVAL", "15"))

# Readiness-polling budget for the perf-test collector reachability probe
# (scripts/perf_readiness.py). A fresh sandbox stand-up reaches the collector via
# the CloudFront default domain; the probe POSTs a minimal valid OTLP-logs request
# and polls until the edge answers at the HTTP layer (any non-5xx response) or the
# budget expires (fail-fast). Active readiness detection -- no time.sleep. Both
# values are env-driven (PERF_READINESS_TIMEOUT / PERF_READINESS_POLL_INTERVAL) so
# an unset var falls back to these sane defaults. The default budget spans the
# CloudFront distribution "Deployed" + first ECS task-serving window.
PERF_READINESS_TIMEOUT: int = int(_os.environ.get("PERF_READINESS_TIMEOUT", "1800"))
PERF_READINESS_POLL_INTERVAL: float = float(_os.environ.get("PERF_READINESS_POLL_INTERVAL", "15"))


def write_output(output_path: str, key: str, value: str) -> None:
    """Append a key=value pair to the output file.

    Used by all CLI scripts to write GitHub Actions output variables.

    Args:
        output_path: File path to append to (e.g. $GITHUB_OUTPUT).
        key: Output variable name.
        value: Output variable value.
    """
    with open(output_path, "a") as fh:
        fh.write(f"{key}={value}\n")
