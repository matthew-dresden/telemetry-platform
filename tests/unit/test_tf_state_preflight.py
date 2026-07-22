"""Unit tests for scripts/tf_state_preflight.py.

Implements the docs/release-pipeline.md per-guard pytest matrix for tf_state_preflight.
The guard verifies the remote-state encryption foundation (the state-bootstrap
KMS CMK) before any plan/apply and fails closed (D16). State locking is S3-native
(use_lockfile, D5) so there is no DynamoDB lock table to check, and per-unit state
buckets are created+hardened on demand by Terragrunt's backend bootstrap.

Cases:
- Pass path: CMK alias resolves to an Enabled key
- Fail path: CMK alias/key not found
- Fail path: CMK exists but is Disabled / PendingDeletion
- Fail path: DescribeKey response unparseable
- fetch_state_cmk: success, NotFoundException -> None, other error re-raised
- resolve_account_id: returns the account from sts:GetCallerIdentity
- main(): success, CMK-not-found, and STS-failure exit codes
"""

from __future__ import annotations

import pytest

from scripts.tf_state_preflight import (
    StateKmsKeyNotEnabledError,
    StateKmsKeyNotFoundError,
    assert_state_cmk_ready,
)

# ---------------------------------------------------------------------------
# Fixtures: minimal boto3 kms:DescribeKey response shapes
# ---------------------------------------------------------------------------

ENABLED_KEY = {"KeyMetadata": {"KeyId": "abc-123", "KeyState": "Enabled"}}
DISABLED_KEY = {"KeyMetadata": {"KeyId": "abc-123", "KeyState": "Disabled"}}
PENDING_DELETION_KEY = {"KeyMetadata": {"KeyId": "abc-123", "KeyState": "PendingDeletion"}}

ALIAS = "alias/123456789012-tfstate"


# ---------------------------------------------------------------------------
# Tests for assert_state_cmk_ready
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_assert_state_cmk_ready_passes_for_enabled_key() -> None:
    """An existing, Enabled state CMK passes (no exception)."""
    assert_state_cmk_ready(alias_name=ALIAS, describe_key_response=ENABLED_KEY)


@pytest.mark.unit
def test_assert_state_cmk_ready_raises_not_found_for_missing_key() -> None:
    """A missing CMK alias/key raises StateKmsKeyNotFoundError."""
    with pytest.raises(StateKmsKeyNotFoundError, match=ALIAS):
        assert_state_cmk_ready(alias_name=ALIAS, describe_key_response=None)


@pytest.mark.unit
@pytest.mark.parametrize(
    "describe_response,description",
    [
        (DISABLED_KEY, "Disabled key"),
        (PENDING_DELETION_KEY, "PendingDeletion key"),
    ],
)
def test_assert_state_cmk_ready_fails_for_non_enabled_key(
    describe_response: dict, description: str
) -> None:
    """A CMK that is not Enabled raises StateKmsKeyNotEnabledError."""
    with pytest.raises(StateKmsKeyNotEnabledError, match="Enabled"):
        assert_state_cmk_ready(alias_name=ALIAS, describe_key_response=describe_response)


@pytest.mark.unit
@pytest.mark.parametrize(
    "describe_response,description",
    [
        ({}, "empty response body"),
        ({"KeyMetadata": {}}, "KeyMetadata without KeyState"),
    ],
)
def test_assert_state_cmk_ready_fails_for_unparseable_response(
    describe_response: dict, description: str
) -> None:
    """An unparseable DescribeKey response raises StateKmsKeyNotEnabledError."""
    with pytest.raises(StateKmsKeyNotEnabledError, match="Enabled"):
        assert_state_cmk_ready(alias_name=ALIAS, describe_key_response=describe_response)


# ---------------------------------------------------------------------------
# Tests for fetch_state_cmk
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fetch_state_cmk_returns_response_on_success() -> None:
    """fetch_state_cmk returns the DescribeKey response dict on success."""
    from scripts.tf_state_preflight import fetch_state_cmk

    class FakeKmsClient:
        def describe_key(self, **kwargs: str) -> dict:
            return ENABLED_KEY

    assert fetch_state_cmk(FakeKmsClient(), ALIAS) == ENABLED_KEY


@pytest.mark.unit
def test_fetch_state_cmk_returns_none_on_not_found() -> None:
    """fetch_state_cmk returns None when the alias/key does not exist."""
    from scripts.tf_state_preflight import fetch_state_cmk

    class FakeClientError(Exception):
        response = {"Error": {"Code": "NotFoundException"}}

    class FakeKmsClient:
        def describe_key(self, **kwargs: str) -> dict:
            raise FakeClientError("not found")

    assert fetch_state_cmk(FakeKmsClient(), ALIAS) is None


@pytest.mark.unit
def test_fetch_state_cmk_reraises_other_errors() -> None:
    """fetch_state_cmk re-raises errors other than NotFound (fail-closed)."""
    from scripts.tf_state_preflight import fetch_state_cmk

    class FakeClientError(Exception):
        response = {"Error": {"Code": "AccessDeniedException"}}

    class FakeKmsClient:
        def describe_key(self, **kwargs: str) -> dict:
            raise FakeClientError("denied")

    with pytest.raises(FakeClientError):
        fetch_state_cmk(FakeKmsClient(), ALIAS)


# ---------------------------------------------------------------------------
# Tests for resolve_account_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_account_id_returns_account() -> None:
    """resolve_account_id returns the Account from sts:GetCallerIdentity."""
    from scripts.tf_state_preflight import resolve_account_id

    class FakeStsClient:
        def get_caller_identity(self) -> dict:
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/x"}

    assert resolve_account_id(FakeStsClient()) == "123456789012"


# ---------------------------------------------------------------------------
# Tests for _extract_error_code
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_error_code_returns_empty_for_non_client_error() -> None:
    """_extract_error_code returns empty string for non-ClientError exceptions."""
    from scripts.tf_state_preflight import _extract_error_code

    class FakeError(Exception):
        pass

    assert _extract_error_code(FakeError("some error")) == ""


@pytest.mark.unit
def test_extract_error_code_returns_code_for_boto3_error() -> None:
    """_extract_error_code returns the Code from a boto3-shaped exception."""
    from scripts.tf_state_preflight import _extract_error_code

    class FakeClientError(Exception):
        response = {"Error": {"Code": "NotFoundException"}}

    assert _extract_error_code(FakeClientError()) == "NotFoundException"


# ---------------------------------------------------------------------------
# main() entry point tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_0_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 0 when the CMK foundation is verified."""
    import scripts.tf_state_preflight as mod

    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def mock_run_preflight(account_id: str, region: str) -> None:
        assert account_id == "123456789012"

    monkeypatch.setattr(mod, "run_preflight", mock_run_preflight)
    assert mod.main() == 0


@pytest.mark.unit
def test_main_returns_1_when_cmk_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 1 when the state CMK cannot be found."""
    import scripts.tf_state_preflight as mod

    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def mock_run_preflight(account_id: str, region: str) -> None:
        raise mod.StateKmsKeyNotFoundError("cmk not found")

    monkeypatch.setattr(mod, "run_preflight", mock_run_preflight)
    assert mod.main() == 1


@pytest.mark.unit
def test_main_returns_1_when_account_resolution_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 1 when the account id cannot be resolved and no override is set."""
    import scripts.tf_state_preflight as mod

    monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def mock_resolve(_client: object) -> str:
        raise RuntimeError("no credentials")

    # Force the sts path and make it fail.
    monkeypatch.setattr(mod, "resolve_account_id", mock_resolve)
    assert mod.main() == 1
