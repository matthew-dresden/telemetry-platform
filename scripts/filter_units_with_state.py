"""filter_units_with_state -- drop change-scoped PLAN units whose remote state does not yet exist.

Run via: uv run python -m scripts.filter_units_with_state

Why this exists:
  A PR that edits shared Terragrunt config (root.hcl, _envcommon/*.hcl, common/*.json) maps to a
  LARGE dependent-unit scope (issue #91 parent-config fan-out) that can include units which have
  NEVER been applied in their target account -- e.g. the qa/dns-owner bootstrap units, which only
  ever stand up a state backend + a standing set. A `terraform plan` for such a unit fails hard at
  backend init because its S3 remote-state bucket was never created ("Failed to get existing
  workspaces: S3 bucket ... does not exist" / NoSuchBucket). You cannot PLAN what was never
  APPLIED, so those units block the change-scoped terragrunt-plan lane for every shared-config PR
  (issue #244), even though the PR does not touch them.

  This filter removes the never-deployed units from the PLAN scope UP FRONT, leaving only the units
  whose remote state already exists (and can therefore be planned). The skipped units are listed on
  a clear, visible INFO log line (never silently dropped). When the ENTIRE scope is never-deployed,
  the filter emits has_units=false so the plan step is a clean no-op (SUCCESS), not a failure.

  This is PLAN-lane only and does NOT weaken the apply lane: the terragrunt-apply workflow keeps its
  own bootstrap-exclusion + ci-deployable filters, and a real (already-deployed) unit is NEVER
  skipped -- only a unit with genuinely absent remote state is.

Zero-drift state resolution (no reimplementation of root.hcl's bucket/key derivation, DRY):
  the backend bucket + key for each unit are resolved by asking Terragrunt itself via
  ``terragrunt render --format json`` (offline config evaluation; no live-resource read), so the
  bucket-name hashing/shortening + state-key rules always match the real backend that root.hcl
  generates. Existence is then a single ``s3:HeadObject`` against that bucket+key using the SAME
  ambient credentials the plan job already assumed (the account's read-only plan role, which is
  granted s3:GetObject/ListBucket on the telemetry remote-state buckets).

Fail-fast (no fallback, CLAUDE.md): a unit whose ``terragrunt render`` fails, or a HeadObject that
  returns anything OTHER than a definitive "not found" (404 / NoSuchKey / NoSuchBucket) -- e.g. a
  403 AccessDenied or a transient 5xx -- aborts non-zero with an actionable message. A genuine
  error is NEVER masked as "not deployed"; only an unambiguous not-found skips a unit.

Outputs (written to $GITHUB_OUTPUT, reusing the scripts.detect_terragrunt_units output contract so
the workflow chains this step in front of the plan with no downstream key renaming):
    include_dir_flags   the --queue-include-dir flags for units WITH existing state (may be empty)
    has_units           "true" when >=1 unit with existing state remains, else "false" (no-op)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any, cast

from scripts.constants import write_output
from scripts.detect_terragrunt_units import (
    OUTPUT_KEY_HAS_UNITS,
    OUTPUT_KEY_INCLUDE_DIR_FLAGS,
    QUEUE_INCLUDE_DIR_TOKEN,
)
from scripts.resolve_deploy_role import PathParseError, extract_all_unit_paths

# Visible marker for the skipped never-deployed units (so the skip is transparent, never silent).
SKIP_LOG_PREFIX = "[skipped: no remote state / never deployed]"

# The S3 backend type root.hcl generates for a real deploy. A unit rendered with any other backend
# (e.g. the TG_OFFLINE_BACKEND=local copyability path) cannot be state-checked and is KEPT.
_S3_BACKEND = "s3"

# boto3 ClientError codes / HTTP statuses that mean the state object is DEFINITIVELY absent (the
# unit was never applied). Anything else (403, 5xx, ...) is a genuine error and fails fast.
_NOT_FOUND_ERROR_CODES = frozenset({"404", "NoSuchKey", "NoSuchBucket", "NotFound"})


class RenderError(RuntimeError):
    """Raised when `terragrunt render` fails for a unit (a genuine config error, fail-fast)."""


class StateCheckError(RuntimeError):
    """Raised when a state-existence HeadObject fails for a reason other than not-found."""


# ---------------------------------------------------------------------------
# Backend resolution via `terragrunt render` (zero-drift, DRY with root.hcl)
# ---------------------------------------------------------------------------


def render_unit_backend(unit_path: str, terragrunt_binary: str = "terragrunt") -> dict[str, Any]:
    """Return the fully-resolved terragrunt config for a unit as a dict (via `terragrunt render`).

    `terragrunt render --format json` evaluates the unit's config (root.hcl includes, locals,
    generate + remote_state blocks) OFFLINE and prints the resolved JSON to stdout, so the backend
    bucket/key come straight from Terragrunt's own evaluation of root.hcl -- never a Python
    re-derivation that could drift. Dependency-output resolution that cannot reach live state falls
    back to mock_outputs (render still exits 0); a non-zero exit is a genuine config error.

    Args:
        unit_path: Absolute (or repo-relative) path to the unit directory.
        terragrunt_binary: Name/path of the pinned terragrunt binary.

    Returns:
        The parsed render JSON object.

    Raises:
        RenderError: `terragrunt render` exited non-zero or emitted unparseable JSON.
    """
    result = subprocess.run(
        [terragrunt_binary, "render", "--format", "json", "--working-dir", unit_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RenderError(
            f"ERROR: `terragrunt render` failed for unit '{unit_path}' (exit "
            f"{result.returncode}).\n"
            f"  stderr: {result.stderr.strip() or '(none)'}\n"
            "  Remedy: this is a genuine Terragrunt config error -- fix the unit's HCL. The "
            "plan-scope\n"
            "  state filter does NOT mask it as 'not deployed'."
        )
    try:
        return cast(dict[str, Any], json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        raise RenderError(
            f"ERROR: `terragrunt render` for unit '{unit_path}' produced unparseable JSON.\n  {exc}"
        ) from exc


def extract_backend_bucket_key(rendered: dict[str, Any], unit_path: str) -> tuple[str, str, str]:
    """Extract (backend_type, bucket, key) from a rendered terragrunt config.

    Args:
        rendered: The parsed `terragrunt render` JSON for a unit.
        unit_path: The unit path (for error messages only).

    Returns:
        (backend_type, bucket, key). bucket/key are "" when the backend is not S3 (the caller then
        keeps the unit -- a non-S3 backend cannot be state-checked).

    Raises:
        RenderError: The rendered config declares an S3 backend but is missing the bucket/key.
    """
    remote_state = rendered.get("remote_state") or {}
    backend = str(remote_state.get("backend", ""))
    if backend != _S3_BACKEND:
        return backend, "", ""
    config = remote_state.get("config") or {}
    bucket = config.get("bucket")
    key = config.get("key")
    if not bucket or not key:
        raise RenderError(
            f"ERROR: unit '{unit_path}' renders an S3 backend with no resolved bucket/key "
            f"(bucket={bucket!r}, key={key!r}).\n"
            "  Remedy: this is a genuine backend-config error, not a not-deployed unit."
        )
    return backend, str(bucket), str(key)


# ---------------------------------------------------------------------------
# State existence check (s3:HeadObject) -- separated for dependency injection in tests
# ---------------------------------------------------------------------------


def make_s3_state_exists_fn(region: str) -> Callable[[str, str], bool]:
    """Build a ``(bucket, key) -> bool`` state-existence checker backed by a real boto3 S3 client.

    The check is a single ``s3:HeadObject``. A definitive not-found (404 / NoSuchKey /
    NoSuchBucket) means the unit was never applied and returns False; any other error is re-raised
    as ``StateCheckError`` (fail-fast -- a 403/5xx is never masked as "not deployed").
    """
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("s3", region_name=region)

    def _exists(bucket: str, key: str) -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            http_status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
            if code in _NOT_FOUND_ERROR_CODES or http_status == "404":
                return False
            raise StateCheckError(
                f"ERROR: HeadObject on remote state s3://{bucket}/{key} failed with a "
                f"non-not-found error (code={code!r}, http={http_status}).\n"
                "  This is NOT treated as 'never deployed' -- a genuine access/service error must "
                "not\n"
                "  silently drop a real unit from the plan scope (fail-fast). Remedy: verify the "
                "plan\n"
                "  role's remote-state read grants and retry."
            ) from exc

    return _exists


# ---------------------------------------------------------------------------
# Core partition (pure given the injected render + state-exists callables)
# ---------------------------------------------------------------------------


def partition_by_state_existence(
    unit_paths: list[str],
    render_fn: Callable[[str], dict[str, Any]],
    state_exists_fn: Callable[[str, str], bool],
) -> tuple[list[str], list[str]]:
    """Split units into (has_state, no_state) by whether their remote state object exists.

    Args:
        unit_paths: The candidate unit paths (order preserved).
        render_fn: unit_path -> parsed `terragrunt render` JSON (injected for testability).
        state_exists_fn: (bucket, key) -> bool, whether the remote-state object exists.

    Returns:
        (kept, skipped): ``kept`` are units whose remote state exists (or that use a non-S3
        backend and cannot be checked -- kept to avoid ever dropping a real unit); ``skipped`` are
        units whose remote state is definitively absent (never deployed).

    Raises:
        RenderError / StateCheckError: a genuine render or state-check error (fail-fast).
    """
    kept: list[str] = []
    skipped: list[str] = []
    for unit_path in unit_paths:
        rendered = render_fn(unit_path)
        backend, bucket, key = extract_backend_bucket_key(rendered, unit_path)
        if backend != _S3_BACKEND:
            # A non-S3 backend (only the offline copyability path) cannot be state-checked; keep it
            # rather than risk dropping a real unit.
            kept.append(unit_path)
            continue
        if state_exists_fn(bucket, key):
            kept.append(unit_path)
        else:
            skipped.append(unit_path)
    return kept, skipped


def _reassemble_flags(unit_paths: list[str]) -> str:
    """Re-build the --queue-include-dir flags string for the kept unit paths."""
    parts: list[str] = []
    for p in unit_paths:
        parts.extend([QUEUE_INCLUDE_DIR_TOKEN, p])
    return " ".join(parts)


def run(
    include_dir_flags: str,
    output_path: str,
    render_fn: Callable[[str], dict[str, Any]] | None = None,
    state_exists_fn: Callable[[str, str], bool] | None = None,
) -> None:
    """Filter the plan scope to units with existing remote state and write the GitHub outputs."""
    unit_paths = extract_all_unit_paths(include_dir_flags)

    if render_fn is None:
        render_fn = render_unit_backend
    if state_exists_fn is None:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        state_exists_fn = make_s3_state_exists_fn(region)

    kept, skipped = partition_by_state_existence(unit_paths, render_fn, state_exists_fn)

    if skipped:
        rendered = ", ".join(skipped)
        print(
            f"filter-units-with-state: {SKIP_LOG_PREFIX} {len(skipped)} unit(s) have no remote "
            f"state and are NOT planned (never applied -- cannot plan what was never applied): "
            f"{rendered}"
        )

    if not kept:
        write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, "")
        write_output(output_path, OUTPUT_KEY_HAS_UNITS, "false")
        print(
            "filter-units-with-state: 0 unit(s) with existing remote state remain -- the "
            "change-scoped plan is a no-op for this account (has_units=false)."
        )
        return

    flags_str = _reassemble_flags(kept)
    write_output(output_path, OUTPUT_KEY_INCLUDE_DIR_FLAGS, flags_str)
    write_output(output_path, OUTPUT_KEY_HAS_UNITS, "true")
    print(
        f"filter-units-with-state: {len(kept)} unit(s) with existing remote state will be planned "
        f"({len(skipped)} never-deployed skipped). include_dir_flags written to {output_path}."
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Filter a change-scoped Terragrunt PLAN scope to the units whose remote state already "
            "exists, skipping never-deployed units transparently so the plan lane stays green for "
            "a shared-config PR whose fan-out includes not-yet-applied units (issue #244)."
        )
    )
    parser.add_argument(
        "--include-dir-flags",
        required=True,
        help="The --queue-include-dir flags string for the plan scope (one account's units).",
    )
    parser.add_argument(
        "--terragrunt-root",
        required=False,
        default="terragrunt/",
        help="Path to the Terragrunt live tree root (unused for resolution; documents context).",
    )
    parser.add_argument("--output", required=True, help="Path to the GitHub Actions output file.")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for `uv run python -m scripts.filter_units_with_state`."""
    args = _parse_args(sys.argv[1:])
    try:
        run(include_dir_flags=args.include_dir_flags, output_path=args.output)
    except (PathParseError, RenderError, StateCheckError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
