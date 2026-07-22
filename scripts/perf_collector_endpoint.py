"""perf_collector_endpoint -- resolve the collector CloudFront endpoint URL.

The ephemeral perf-test sandbox apply is scoped to the service account and cannot
create the cross-account public DNS record for the collector pretty FQDN, so that
FQDN does not resolve. ``terragrunt output`` on the collector unit also fails in
that context: reading a single unit's output re-resolves its ``dependency`` blocks,
and one of them (the prod ``dns-prod-zone`` bootstrap state) is not readable by the
sandbox apply role.

This helper sidesteps both problems: it discovers the collector's CloudFront
distribution directly via the AWS API (matching the distribution whose Aliases
include the env-specific collector FQDN from domains.json) and emits its
AWS-assigned default domain (``d1234abcdef.cloudfront.net``) as the readiness/load
endpoint. The default CloudFront domain resolves publicly and serves the collector
without any custom DNS.

Run via: ``make perf-collector-endpoint ENV=<env> OUTPUT=<github_output_file>``

Exit codes:
    0 -- endpoint resolved and written
    1 -- the collector CloudFront distribution could not be resolved
    2 -- usage error
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

from scripts import e2e_common


class EndpointResolutionError(Exception):
    """Raised when the collector CloudFront distribution cannot be resolved."""


def resolve_collector_endpoint(
    env: str, domains_data: dict[str, Any], cloudfront_client: Any
) -> str:
    """Resolve the collector's CloudFront default-domain endpoint URL for ``env``.

    Args:
        env: The environment name (e.g. ``sandbox``).
        domains_data: Parsed domains.json content.
        cloudfront_client: A boto3 CloudFront client.

    Returns:
        The collector endpoint URL, ``https://<default-domain>/v1/logs``.

    Raises:
        EndpointResolutionError: When no matching distribution is found.
    """
    env_cfg = domains_data.get(env)
    if not env_cfg:
        raise EndpointResolutionError(
            f"ERROR: env {env!r} not found in domains.json (available: {sorted(domains_data)!r})."
        )
    # The collector's CloudFront alias is the env-specific pretty FQDN. Derived directly
    # from dns_pretty_apex (this helper needs only the pretty alias, not the full
    # four-FQDN resolution), so it does not require dns_service_apex.
    target_alias = f"collector.{env_cfg['dns_pretty_apex']}"
    domain = e2e_common.discover_cloudfront_domain(cloudfront_client, target_alias)
    if not domain:
        raise EndpointResolutionError(
            f"ERROR: could not resolve a CloudFront distribution for the collector "
            f"(env={env!r}, alias={target_alias!r}). Verify the collector-ingestion "
            "unit applied and its CloudFront distribution exists in this account."
        )
    return f"https://{domain}/v1/logs"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        required=True,
        choices=sorted(e2e_common.VALID_ENVS),
        help="Environment whose collector CloudFront endpoint to resolve.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="File to append 'collector_endpoint=<url>' to (e.g. $GITHUB_OUTPUT).",
    )
    parser.add_argument(
        "--domains",
        default=str(e2e_common.DOMAINS_JSON_PATH),
        help=f"Path to domains.json (default {e2e_common.DOMAINS_JSON_PATH}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    import boto3

    domains_data = e2e_common.load_json(pathlib.Path(args.domains))
    session = e2e_common.build_ambient_session(boto3)
    cloudfront_client = session.client("cloudfront")
    try:
        endpoint = resolve_collector_endpoint(args.env, domains_data, cloudfront_client)
    except EndpointResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    with open(args.output, "a", encoding="utf-8") as fh:
        fh.write(f"collector_endpoint={endpoint}\n")
    print(f"perf-collector-endpoint: collector_endpoint={endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
