"""Recursively delete the perf-test env's analytics Athena workgroup(s).

Invoked as::

    uv run python -m scripts.athena_workgroup_cleanup --env sandbox --ambient-credentials
    make athena-workgroup-cleanup ENV=sandbox ATHENA_CLEANUP_ARGS="--ambient-credentials"

Why this exists (ephemeral perf-test teardown):
  ``terragrunt run-all destroy`` fails deleting the analytics Athena workgroup
  (e.g. ``telemetry-useast1-sandbox-shared-analytics-000-analytics``) because a
  WorkGroup that has accumulated query history cannot be deleted without the
  recursive/force option, and the ``athena-workgroup`` primitive exposes no
  ``force_destroy`` input. This one-off cleanup recursively deletes the
  namespace-discovered sandbox analytics workgroup(s) via
  ``delete_work_group(RecursiveDeleteOption=True)`` so the subsequent
  ``terragrunt destroy`` finds them already gone and no-ops.

  Long-term fix (OUT OF SCOPE here): add a ``force_destroy`` input to the
  ``athena-workgroup`` primitive + the analytics reference so the declarative
  destroy handles it directly, removing the need for this imperative step.

Discovery is namespace-derived, NEVER a hardcoded literal: the deployed
workgroup name is namespace-scoped (``<namespace>-analytics``), so the
workgroup(s) are found by listing every WorkGroup and selecting those whose name
carries both the ``analytics`` service segment and the ``--env`` segment -- the
same list+filter convention ``scripts.e2e_common.discover_workgroup`` and the
other namespace-agnostic discovery helpers use. The AWS built-in ``primary``
workgroup never matches (no ``analytics`` segment) and is never touched.

Idempotent: a workgroup that is already gone (deleted by a prior run or a
concurrent destroy) is tolerated as a clean no-op, so this is safe to call from
an ``always()`` cleanup even when nothing needs deleting.

Exit codes::

    0 -- every discovered analytics workgroup was deleted or already absent
    1 -- an AWS operation failed (surfaced fail-fast, never swallowed)
    2 -- usage error (bad ENV / no region resolvable)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

from scripts import e2e_common

# Namespace service segment of the analytics unit's workgroup name
# (terragrunt/_envcommon/analytics.hcl: workgroup_name = "${namespace}-analytics").
# Used only as a name-filter token for discovery, never as a full literal name.
_ANALYTICS_SEGMENT = "analytics"


class AthenaCleanupUsageError(ValueError):
    """Raised on a bad CLI/ENV value or an unresolvable region -- exit 2."""


def discover_analytics_workgroups(athena_client: Any, env: str) -> list[str]:
    """Return every analytics Athena workgroup name for ``env`` (namespace-derived).

    Lists every WorkGroup (following pagination) and selects those whose name
    carries BOTH the ``analytics`` service segment and the ``env`` segment -- the
    same list+filter convention ``e2e_common.discover_workgroup`` uses, returning
    the FULL set (there may be more than one instance) rather than the first
    match. Order-stable and de-duplicated. The AWS built-in ``primary`` workgroup
    never matches (it has no ``analytics`` segment).

    Args:
        athena_client: A boto3 Athena client.
        env: The environment segment every match must carry (e.g. ``sandbox``).

    Returns:
        The matching workgroup names, order-stable and de-duplicated.
    """
    names: list[str] = []
    seen: set[str] = set()
    token: str | None = None
    while True:
        kwargs = {"NextToken": token} if token else {}
        response = athena_client.list_work_groups(**kwargs)
        for group in response.get("WorkGroups", []):
            name = group.get("Name", "")
            if _ANALYTICS_SEGMENT in name and env in name and name not in seen:
                seen.add(name)
                names.append(name)
        token = response.get("NextToken")
        if not token:
            return names


def _is_absent_error(exc: Exception) -> bool:
    """True when ``exc`` reports an Athena workgroup that no longer exists (no-op).

    ``delete_work_group`` on an already-deleted workgroup raises a botocore
    ``ClientError`` -- Athena reports this as ``InvalidRequestException`` whose
    message says the workgroup ``is not found`` (some SDK/regions surface a
    ``ResourceNotFoundException`` / ``EntityNotFoundException``). Either is a
    clean no-op for an idempotent teardown; anything else is surfaced fail-fast.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error", {})
    code = error.get("Code", "")
    if code in ("ResourceNotFoundException", "EntityNotFoundException"):
        return True
    message = str(error.get("Message", "")).lower()
    return code == "InvalidRequestException" and (
        "not found" in message or "does not exist" in message
    )


def delete_workgroup(athena_client: Any, name: str) -> bool:
    """Recursively delete ``name``. Returns True when deleted, False when already absent.

    Uses ``RecursiveDeleteOption=True`` so a workgroup with saved queries / query
    history (which a plain delete refuses) is removed. An already-absent
    workgroup is tolerated as a clean no-op (idempotent).

    Raises:
        Exception: Any non-absence AWS error (surfaced fail-fast).
    """
    try:
        athena_client.delete_work_group(WorkGroup=name, RecursiveDeleteOption=True)
        return True
    except Exception as exc:
        if _is_absent_error(exc):
            return False
        raise


def cleanup(athena_client: Any, env: str) -> dict[str, Any]:
    """Discover and recursively delete ``env``'s analytics workgroup(s). Returns evidence."""
    discovered = discover_analytics_workgroups(athena_client, env)
    deleted: list[str] = []
    already_absent: list[str] = []
    for name in discovered:
        if delete_workgroup(athena_client, name):
            deleted.append(name)
        else:
            already_absent.append(name)
    return {
        "env": env,
        "discovered": discovered,
        "deleted": deleted,
        "already_absent": already_absent,
    }


def _resolve_region(args: argparse.Namespace) -> str:
    """Resolve the AWS region from --aws-region or $AWS_DEFAULT_REGION (fail-fast)."""
    if args.aws_region:
        return str(args.aws_region)
    region = os.environ.get("AWS_DEFAULT_REGION", "").strip()
    if not region:
        raise AthenaCleanupUsageError(
            "ERROR: --aws-region was not given and AWS_DEFAULT_REGION is not set. "
            "Set one of them to the target region (e.g. us-east-1)."
        )
    return region


def _build_athena_client(args: argparse.Namespace, boto3_module: Any, region: str) -> Any:
    """Build an Athena client for the resolved region + credential source."""
    if args.ambient_credentials:
        session = e2e_common.build_ambient_session(boto3_module)
    elif args.aws_profile:
        session = boto3_module.Session(profile_name=args.aws_profile)
    else:
        session = boto3_module.Session()
    return session.client("athena", region_name=region)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the Athena workgroup cleanup tool."""
    parser = argparse.ArgumentParser(
        prog="scripts.athena_workgroup_cleanup",
        description=(
            "Recursively delete the env's namespace-discovered analytics Athena workgroup(s) so "
            "an ephemeral terragrunt destroy can no-op them (the athena-workgroup primitive has "
            "no force_destroy input)."
        ),
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment segment used to discover the analytics workgroup(s).",
    )
    parser.add_argument(
        "--ambient-credentials",
        dest="ambient_credentials",
        action="store_true",
        help="Build the boto3 session from the ambient/default credential chain (CI OIDC role).",
    )
    parser.add_argument(
        "--aws-profile",
        dest="aws_profile",
        default=None,
        help="Named AWS profile (local dev). Ignored when --ambient-credentials is set.",
    )
    parser.add_argument(
        "--aws-region",
        dest="aws_region",
        default=None,
        help="AWS region for the Athena client (default: $AWS_DEFAULT_REGION).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the JSON evidence summary (default: stdout only).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    import boto3

    try:
        region = _resolve_region(args)
        athena = _build_athena_client(args, boto3, region)
        evidence = cleanup(athena, args.env)
    except AthenaCleanupUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        # Every AWS failure is surfaced fail-fast (exit 1) -- never silently swallowed.
        print(f"ERROR: athena workgroup cleanup failed: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(evidence, indent=2)
    print(rendered)
    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
