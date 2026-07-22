"""tf_state_preflight -- pre-apply remote-state foundation guard.

Run via: uv run python -m scripts.tf_state_preflight

Verifies, before any plan or apply may proceed, that the remote-state encryption
foundation created by the state-bootstrap unit is present and usable: the
customer-managed KMS CMK aliased ``alias/<account-id>-tfstate`` (D10/B19) exists
and is in the ``Enabled`` state. Every per-unit Terragrunt state bucket is created
and hardened on demand by Terragrunt's own backend bootstrap (root.hcl
``remote_state`` config: SSE-KMS with this CMK, versioning, public-access-block,
TLS, root-access policy), so the single shared precondition that must hold up
front is that this CMK foundation is ready. State locking is S3-native
(``use_lockfile = true``, decision D5); there is no DynamoDB lock table, so no
lock-table precondition is checked. Fails closed (D16): any unmet precondition
causes a non-zero exit.

Account and region are derived at runtime (no per-unit configuration required):
    AWS_REGION / AWS_DEFAULT_REGION -- the AWS region (defaults to us-east-1).
    AWS_ACCOUNT_ID                  -- optional override; otherwise resolved via
                                       sts:GetCallerIdentity using ambient creds.

Usage:
    uv run python -m scripts.tf_state_preflight
"""

from __future__ import annotations

import os
import sys
from typing import Any, cast

# ---------------------------------------------------------------------------
# Custom exceptions (specific, not generic Exception)
# ---------------------------------------------------------------------------


class StateKmsKeyNotFoundError(RuntimeError):
    """Raised when the remote-state KMS CMK alias cannot be found."""


class StateKmsKeyNotEnabledError(RuntimeError):
    """Raised when the remote-state KMS CMK exists but is not in Enabled state."""


# ---------------------------------------------------------------------------
# Pure assertion function (dependency-injected response for testability)
# ---------------------------------------------------------------------------


def assert_state_cmk_ready(
    alias_name: str,
    describe_key_response: dict[str, Any] | None,
) -> None:
    """Assert that the remote-state KMS CMK exists and is enabled.

    Args:
        alias_name: The CMK alias (e.g. 'alias/123456789012-tfstate'), for messages.
        describe_key_response: The kms:DescribeKey response body (resolved via the
            alias), or None if the alias/key was not found.

    Raises:
        StateKmsKeyNotFoundError: If the response is None (alias/key not found).
        StateKmsKeyNotEnabledError: If the key is not in the Enabled state.
    """
    if describe_key_response is None:
        raise StateKmsKeyNotFoundError(
            f"ERROR: Remote-state KMS CMK '{alias_name}' could not be found or accessed.\n"
            f"  The customer-managed state-encryption CMK must exist before plan/apply "
            f"proceeds (D10/B19).\n"
            f"  Remedy: run the state-bootstrap unit to create the state CMK, then retry."
        )

    try:
        key_state = describe_key_response["KeyMetadata"]["KeyState"]
    except (KeyError, TypeError) as exc:
        raise StateKmsKeyNotEnabledError(
            f"ERROR: Remote-state KMS CMK '{alias_name}' state could not be determined "
            f"(unparseable DescribeKey response).\n"
            f"  The state CMK must be Enabled before plan/apply proceeds (D10/B19).\n"
            f"  Remedy: verify the state CMK exists and is enabled, then retry."
        ) from exc

    if key_state != "Enabled":
        raise StateKmsKeyNotEnabledError(
            f"ERROR: Remote-state KMS CMK '{alias_name}' is in state '{key_state}', "
            f"expected 'Enabled'.\n"
            f"  The state CMK must be Enabled before plan/apply proceeds (D10/B19).\n"
            f"  Remedy: enable (or restore) the state CMK, then retry."
        )


# ---------------------------------------------------------------------------
# AWS API calls (separated for dependency injection in tests)
# ---------------------------------------------------------------------------


def fetch_state_cmk(boto3_kms_client: Any, alias_name: str) -> dict[str, Any] | None:
    """Fetch the state CMK metadata by alias. Returns None if not found.

    kms:DescribeKey accepts an alias ARN/name as KeyId, resolving it to the target
    key. A missing alias surfaces as NotFoundException.
    """
    try:
        return cast(dict[str, Any], boto3_kms_client.describe_key(KeyId=alias_name))
    except Exception as exc:
        client_error_code = _extract_error_code(exc)
        if client_error_code in ("NotFoundException", "NotFound"):
            return None
        raise


def resolve_account_id(boto3_sts_client: Any) -> str:
    """Resolve the current AWS account id via sts:GetCallerIdentity."""
    identity = cast(dict[str, Any], boto3_sts_client.get_caller_identity())
    return str(identity["Account"])


def _extract_error_code(exc: BaseException) -> str:
    """Extract the boto3 ClientError code from an exception, or return empty string."""
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    error = cast(dict[str, Any], response).get("Error", {})
    return str(cast(dict[str, Any], error).get("Code", ""))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_preflight(account_id: str, region: str) -> None:
    """Run the remote-state foundation preflight check.

    Args:
        account_id: The AWS account id (used to derive the state CMK alias).
        region: The AWS region.

    Raises:
        StateKmsKeyNotFoundError: If the state CMK alias cannot be found.
        StateKmsKeyNotEnabledError: If the state CMK is not Enabled.
    """
    import boto3

    kms_client = boto3.client("kms", region_name=region)

    # The state-bootstrap unit creates the state CMK under this deterministic alias
    # (root.hcl: state_kms_key_alias = "alias/${account_id}-tfstate").
    alias_name = f"alias/{account_id}-tfstate"

    describe_response = fetch_state_cmk(kms_client, alias_name)
    assert_state_cmk_ready(alias_name=alias_name, describe_key_response=describe_response)
    print(f"OK: remote-state CMK '{alias_name}' exists and is Enabled.")


def main() -> int:
    """Entry point for `uv run python -m scripts.tf_state_preflight`.

    Resolves the region (AWS_REGION / AWS_DEFAULT_REGION, default us-east-1) and the
    account id (AWS_ACCOUNT_ID override, else sts:GetCallerIdentity), then verifies
    the remote-state CMK foundation. Fails closed (D16) on any precondition failure.

    Returns:
        0 on success, 1 on any precondition failure.
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    if not account_id:
        try:
            import boto3

            account_id = resolve_account_id(boto3.client("sts", region_name=region))
        except Exception as exc:  # noqa: BLE001 -- surface any creds/STS failure verbatim
            print(
                f"ERROR: Could not resolve the AWS account id via sts:GetCallerIdentity: {exc}\n"
                f"  Ensure AWS credentials are configured, or set AWS_ACCOUNT_ID explicitly.",
                file=sys.stderr,
            )
            return 1

    try:
        run_preflight(account_id=account_id, region=region)
    except (StateKmsKeyNotFoundError, StateKmsKeyNotEnabledError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("tf-state-preflight PASSED: remote-state CMK foundation verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
