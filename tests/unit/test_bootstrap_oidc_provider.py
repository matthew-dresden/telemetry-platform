"""Unit tests for scripts/bootstrap_oidc_provider.py.

FR-10 (spec section 4.10): GitHub OIDC identity provider
token.actions.githubusercontent.com with audience sts.amazonaws.com must
exist in the qa account and the prod account. The bootstrap tool is
verify-first and idempotent. It refuses sandbox/root targets (D33/D-11)
and never modifies a pre-existing provider with an unexpected audience.

Coverage requirements: 100% line+branch (spec section 10).
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ACCOUNTS_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "accounts.json"


def _load_accounts() -> dict[str, Any]:
    return json.loads(ACCOUNTS_JSON_PATH.read_text())


def _make_list_providers_response(arns: list[str]) -> dict[str, Any]:
    """Build a list_open_id_connect_providers response with the given ARNs."""
    return {"OpenIDConnectProviderList": [{"Arn": arn} for arn in arns]}


def _make_get_provider_response(client_ids: list[str], thumbprints: list[str]) -> dict[str, Any]:
    """Build a get_open_id_connect_provider response."""
    return {
        "ClientIDList": client_ids,
        "ThumbprintList": thumbprints,
        "Url": "token.actions.githubusercontent.com",
    }


def _make_create_provider_response(arn: str) -> dict[str, Any]:
    """Build a create_open_id_connect_provider response."""
    return {"OpenIDConnectProviderArn": arn}


def _patch_boto_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch boto3.Session in the bootstrap_oidc_provider module.

    Returns the mock IAM client so callers can configure return_value and
    assert call counts.
    """
    import scripts.bootstrap_oidc_provider as m

    mock_iam = MagicMock()
    mock_session_instance = MagicMock()
    mock_session_instance.client.return_value = mock_iam
    mock_boto3 = MagicMock()
    mock_boto3.Session.return_value = mock_session_instance
    monkeypatch.setattr(m, "boto3", mock_boto3)
    # Also patch boto3.Session at the library level so tests that re-execute the
    # module via runpy (run_name="__main__") get the mock too: runpy re-imports
    # the module fresh, so patching only the already-imported instance's boto3
    # would leave the fresh import calling the real boto3.Session(profile_name=...)
    # and depending on an ambient AWS profile (hermeticity gap, fails in clean CI).
    monkeypatch.setattr("boto3.Session", mock_boto3.Session)
    return mock_iam


# ---------------------------------------------------------------------------
# Module import sanity check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_module_importable() -> None:
    """scripts.bootstrap_oidc_provider must be importable without side effects."""
    import scripts.bootstrap_oidc_provider as m

    assert hasattr(m, "bootstrap_oidc_provider"), (
        "Module must export the bootstrap_oidc_provider function."
    )


# ---------------------------------------------------------------------------
# Account resolution helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_account_info_qa() -> None:
    """_resolve_account_info must return the qa profile and account ID."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    profile, account_id, account_role = m._resolve_account_info("qa", accounts_data)
    assert profile == "qa"
    assert account_id == "333333333333"
    assert "qa" in account_role.lower()


@pytest.mark.unit
def test_resolve_account_info_prod() -> None:
    """_resolve_account_info must return the prod profile and account ID."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    profile, account_id, account_role = m._resolve_account_info("prod", accounts_data)
    assert profile == "prod"
    assert account_id == "111111111111"
    assert "prod" in account_role.lower()


@pytest.mark.unit
def test_resolve_account_info_unknown_env() -> None:
    """_resolve_account_info must raise UsageError for an unknown ENV."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    with pytest.raises(m.UsageError, match="unknown ENV"):
        m._resolve_account_info("nonexistent", accounts_data)


# ---------------------------------------------------------------------------
# Forbidden target refusal (D33/D-11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "env,ledger_ref",
    [
        ("sandbox", "D33"),
        ("root", "D-11"),
    ],
)
def test_forbidden_target_raises_error(
    env: str, ledger_ref: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bootstrap_oidc_provider must refuse sandbox/root targets before any AWS call.

    Sandbox refuses per D33 (no OIDC provider needed in sandbox).
    Root refuses per D-11 (role-chaining is used, no OIDC provider needed).
    The tool must raise ForbiddenTargetError naming the ledger reference and
    must NOT make any AWS call.
    """
    import scripts.bootstrap_oidc_provider as m

    mock_iam = _patch_boto_session(monkeypatch)
    accounts_data = _load_accounts()

    with pytest.raises(m.ForbiddenTargetError) as exc_info:
        m.bootstrap_oidc_provider(env=env, accounts_data=accounts_data)

    error_msg = str(exc_info.value)
    assert ledger_ref in error_msg, (
        f"ForbiddenTargetError for {env!r} must name {ledger_ref!r}; got: {error_msg!r}"
    )
    mock_iam.list_open_id_connect_providers.assert_not_called()
    mock_iam.create_open_id_connect_provider.assert_not_called()


# ---------------------------------------------------------------------------
# Verify-first: provider already present with correct audience (no-op)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ["qa", "prod"])
def test_idempotent_noop_when_provider_already_present(
    env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bootstrap_oidc_provider must skip creation when the provider already exists.

    The verify-first contract: list providers, find token.actions.githubusercontent.com
    with audience sts.amazonaws.com -> log already-present and return without
    calling create_open_id_connect_provider.
    """
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    account_id = next(
        acct_id for acct_id, info in accounts_data.items() if info.get("aws_profile") == env
    )
    provider_arn = f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"

    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response(
        [provider_arn]
    )
    mock_iam.get_open_id_connect_provider.return_value = _make_get_provider_response(
        client_ids=["sts.amazonaws.com"],
        thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
    )

    result = m.bootstrap_oidc_provider(env=env, accounts_data=accounts_data)

    assert result == m.ProviderResult.ALREADY_PRESENT
    mock_iam.create_open_id_connect_provider.assert_not_called()


# ---------------------------------------------------------------------------
# Verify-first: provider absent -> create
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ["qa", "prod"])
def test_creates_provider_when_absent(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """bootstrap_oidc_provider must create the provider only when it is absent.

    The create path: list returns empty (no provider), so the tool calls
    create_open_id_connect_provider with the correct URL, audience, and
    thumbprint. Returns ProviderResult.CREATED.
    """
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    account_id = next(
        acct_id for acct_id, info in accounts_data.items() if info.get("aws_profile") == env
    )
    new_arn = f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"

    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response([])
    mock_iam.create_open_id_connect_provider.return_value = _make_create_provider_response(new_arn)

    result = m.bootstrap_oidc_provider(env=env, accounts_data=accounts_data)

    assert result == m.ProviderResult.CREATED
    mock_iam.create_open_id_connect_provider.assert_called_once()
    create_call_kwargs = mock_iam.create_open_id_connect_provider.call_args
    assert "token.actions.githubusercontent.com" in str(create_call_kwargs)
    assert "sts.amazonaws.com" in str(create_call_kwargs)


# ---------------------------------------------------------------------------
# Verify-first ordering: list MUST be called before create
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_called_before_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verify-first contract requires list to be called before create.

    This test asserts the call ordering: list_open_id_connect_providers
    must be called first, then create_open_id_connect_provider.
    """
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    mock_iam = _patch_boto_session(monkeypatch)

    call_order: list[str] = []

    def _track_list(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_order.append("list")
        return _make_list_providers_response([])

    def _track_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_order.append("create")
        return _make_create_provider_response(
            "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
        )

    mock_iam.list_open_id_connect_providers.side_effect = _track_list
    mock_iam.create_open_id_connect_provider.side_effect = _track_create

    m.bootstrap_oidc_provider(env="qa", accounts_data=accounts_data)

    list_idx = call_order.index("list")
    create_idx = call_order.index("create")
    assert list_idx < create_idx, (
        f"list_open_id_connect_providers must be called before create; actual order: {call_order!r}"
    )


# ---------------------------------------------------------------------------
# Wrong-audience fail-safe: never modify a provider with unexpected audience
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("env", ["qa", "prod"])
def test_wrong_audience_fail_safe_raises_without_mutating(
    env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing provider with an unexpected audience must cause fail-safe exit.

    The tool must raise OidcProviderWrongAudienceError and must NOT call
    any mutating IAM API (create, update_open_id_connect_provider_thumbprint,
    add_client_id_to_open_id_connect_provider, remove_client_id_from_open_id_connect_provider,
    delete_open_id_connect_provider).
    """
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    account_id = next(
        acct_id for acct_id, info in accounts_data.items() if info.get("aws_profile") == env
    )
    provider_arn = f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"

    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response(
        [provider_arn]
    )
    # Provider exists but has a foreign audience (not sts.amazonaws.com)
    mock_iam.get_open_id_connect_provider.return_value = _make_get_provider_response(
        client_ids=["foreign-audience.example.com"],
        thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
    )

    with pytest.raises(m.OidcProviderWrongAudienceError) as exc_info:
        m.bootstrap_oidc_provider(env=env, accounts_data=accounts_data)

    error_msg = str(exc_info.value)
    assert "foreign-audience.example.com" in error_msg, (
        f"OidcProviderWrongAudienceError must report actual audiences; got: {error_msg!r}"
    )
    assert provider_arn in error_msg, (
        f"OidcProviderWrongAudienceError must include the provider ARN; got: {error_msg!r}"
    )

    # Assert NO mutating call was made
    mock_iam.create_open_id_connect_provider.assert_not_called()
    mock_iam.update_open_id_connect_provider_thumbprint.assert_not_called()
    mock_iam.add_client_id_to_open_id_connect_provider.assert_not_called()
    mock_iam.remove_client_id_from_open_id_connect_provider.assert_not_called()
    mock_iam.delete_open_id_connect_provider.assert_not_called()


# ---------------------------------------------------------------------------
# AWS credential/permission failure path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_aws_credential_failure_surfaces_error_with_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS ClientError must be surfaced with a remediation line naming the profile.

    When list_open_id_connect_providers raises a ClientError (e.g. missing
    permissions, expired creds), the exception propagates as a
    CredentialError that includes the AWS profile name so the operator
    knows which profile to fix.
    """
    import botocore.exceptions

    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    mock_iam = _patch_boto_session(monkeypatch)

    error_response = {
        "Error": {
            "Code": "AccessDenied",
            "Message": "User is not authorized to perform iam:ListOpenIDConnectProviders",
        }
    }
    mock_iam.list_open_id_connect_providers.side_effect = botocore.exceptions.ClientError(
        error_response, "ListOpenIDConnectProviders"
    )

    with pytest.raises(m.CredentialError) as exc_info:
        m.bootstrap_oidc_provider(env="qa", accounts_data=accounts_data)

    error_msg = str(exc_info.value)
    assert "qa" in error_msg, f"CredentialError must name the AWS profile; got: {error_msg!r}"
    assert "iam:ListOpenIDConnectProviders" in error_msg or "AccessDenied" in error_msg, (
        f"CredentialError must include the underlying AWS error; got: {error_msg!r}"
    )


# ---------------------------------------------------------------------------
# accounts.json loading errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_accounts_json_raises_usage_error(tmp_path: pathlib.Path) -> None:
    """A missing accounts.json must raise UsageError with the missing path."""
    import scripts.bootstrap_oidc_provider as m

    fake_path = tmp_path / "missing_accounts.json"
    with pytest.raises(m.UsageError, match="not found"):
        m.load_accounts_data(fake_path)


@pytest.mark.unit
def test_invalid_accounts_json_raises_usage_error(tmp_path: pathlib.Path) -> None:
    """An invalid (non-JSON) accounts.json must raise UsageError."""
    import scripts.bootstrap_oidc_provider as m

    bad_json = tmp_path / "accounts.json"
    bad_json.write_text("this is not json {{{")
    with pytest.raises(m.UsageError, match="invalid JSON"):
        m.load_accounts_data(bad_json)


# ---------------------------------------------------------------------------
# CLI main() integration: exit codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_exits_0_on_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 0 when the provider is already present."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    provider_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response(
        [provider_arn]
    )
    mock_iam.get_open_id_connect_provider.return_value = _make_get_provider_response(
        client_ids=["sts.amazonaws.com"],
        thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
    )
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "qa"])
    assert exc_info.value.code == 0


@pytest.mark.unit
def test_main_exits_0_on_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 0 when a new provider is successfully created."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    new_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response([])
    mock_iam.create_open_id_connect_provider.return_value = _make_create_provider_response(new_arn)
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "qa"])
    assert exc_info.value.code == 0


@pytest.mark.unit
def test_main_exits_1_on_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 1 when an existing provider has the wrong audience."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    provider_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response(
        [provider_arn]
    )
    mock_iam.get_open_id_connect_provider.return_value = _make_get_provider_response(
        client_ids=["wrong-audience.example.com"],
        thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
    )
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "qa"])
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_main_exits_1_on_forbidden_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 1 when called with sandbox or root ENV."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "sandbox"])
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_main_exits_1_on_credential_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 1 on AWS credential/permission failure."""
    import botocore.exceptions

    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    mock_iam = _patch_boto_session(monkeypatch)
    error_response = {
        "Error": {"Code": "AccessDenied", "Message": "User is not authorized"},
    }
    mock_iam.list_open_id_connect_providers.side_effect = botocore.exceptions.ClientError(
        error_response, "ListOpenIDConnectProviders"
    )
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "qa"])
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_main_exits_2_on_unknown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 2 on unknown ENV (usage error)."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "nonexistent-env"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Create call contains the correct arguments
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_create_call_uses_correct_url_and_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """The create call must use the correct GitHub OIDC URL, audience, and thumbprint."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    new_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response([])
    mock_iam.create_open_id_connect_provider.return_value = _make_create_provider_response(new_arn)

    m.bootstrap_oidc_provider(env="qa", accounts_data=accounts_data)

    assert mock_iam.create_open_id_connect_provider.call_count == 1
    kwargs = mock_iam.create_open_id_connect_provider.call_args.kwargs
    assert kwargs.get("Url") == "https://token.actions.githubusercontent.com" or (
        "token.actions.githubusercontent.com" in kwargs.get("Url", "")
    ), f"Url kwarg must reference token.actions.githubusercontent.com; got {kwargs!r}"
    assert "sts.amazonaws.com" in kwargs.get("ClientIDList", []), (
        f"ClientIDList must include sts.amazonaws.com; got {kwargs!r}"
    )
    assert kwargs.get("ThumbprintList"), f"ThumbprintList must be non-empty; got {kwargs!r}"


# ---------------------------------------------------------------------------
# ProviderResult enum values
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provider_result_has_expected_values() -> None:
    """ProviderResult must have CREATED and ALREADY_PRESENT members."""
    import scripts.bootstrap_oidc_provider as m

    assert hasattr(m, "ProviderResult")
    assert hasattr(m.ProviderResult, "CREATED")
    assert hasattr(m.ProviderResult, "ALREADY_PRESENT")
    assert m.ProviderResult.CREATED != m.ProviderResult.ALREADY_PRESENT


# ---------------------------------------------------------------------------
# Exception class hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exception_classes_exist() -> None:
    """ForbiddenTargetError, OidcProviderWrongAudienceError, and CredentialError must exist."""
    import scripts.bootstrap_oidc_provider as m

    assert issubclass(m.ForbiddenTargetError, Exception)
    assert issubclass(m.OidcProviderWrongAudienceError, Exception)
    assert issubclass(m.CredentialError, Exception)


# ---------------------------------------------------------------------------
# No mutating calls on idempotent re-run
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_accounts_from_default_path_returns_dict() -> None:
    """_load_accounts_from_default_path() must return the parsed accounts.json dict."""
    import scripts.bootstrap_oidc_provider as m

    data = m._load_accounts_from_default_path()
    assert isinstance(data, dict), "accounts.json must parse to a dict"
    assert data, "accounts.json must be non-empty"


@pytest.mark.unit
@pytest.mark.parametrize(
    "account_role,expected_ledger",
    [
        ("sandbox", "D33"),
        ("dns-owner", "D-11"),
    ],
)
def test_check_forbidden_target_by_account_role(account_role: str, expected_ledger: str) -> None:
    """_check_forbidden_target must refuse when account_role is forbidden.

    This covers the case where env itself is not in _FORBIDDEN_PROFILES but
    the underlying account_role is a forbidden role (e.g., an aliased env name).
    """
    import scripts.bootstrap_oidc_provider as m

    # Use an env alias that is NOT in _FORBIDDEN_PROFILES, but has a forbidden role
    with pytest.raises(m.ForbiddenTargetError) as exc_info:
        m._check_forbidden_target(env="somealias", account_role=account_role)
    error_msg = str(exc_info.value)
    assert expected_ledger in error_msg, (
        f"ForbiddenTargetError for account_role={account_role!r} must name "
        f"{expected_ledger!r}; got: {error_msg!r}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "account_role,expected_ledger",
    [
        ("sandbox", "D33"),
        ("dns-owner", "D-11"),
    ],
)
def test_check_forbidden_target_primary_line_names_ledger(
    account_role: str, expected_ledger: str
) -> None:
    """The primary (first) error line must include the correct ledger ref in parens.

    When the target is refused due to account_role (not env name), the primary
    error line must show the ledger reference as '(<ref>)' -- not the fallback
    'see bootstrap-ordering.md'. This ensures operators see the precise spec
    reference at a glance.
    """
    import scripts.bootstrap_oidc_provider as m

    with pytest.raises(m.ForbiddenTargetError) as exc_info:
        m._check_forbidden_target(env="somealias", account_role=account_role)
    first_line = str(exc_info.value).split("\n")[0]
    assert f"({expected_ledger})" in first_line, (
        f"Primary error line must contain '({expected_ledger})' when "
        f"account_role={account_role!r}; got first line: {first_line!r}"
    )


@pytest.mark.unit
def test_find_github_provider_arn_returns_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_find_github_provider_arn must return None when no matching provider ARN exists."""
    import scripts.bootstrap_oidc_provider as m

    mock_iam = MagicMock()
    # Return providers that don't match the GitHub OIDC URL
    mock_iam.list_open_id_connect_providers.return_value = {
        "OpenIDConnectProviderList": [
            {"Arn": "arn:aws:iam::123456789012:oidc-provider/other-provider.example.com"}
        ]
    }
    result = m._find_github_provider_arn(mock_iam)
    assert result is None


@pytest.mark.unit
def test_create_fails_with_client_error_raises_credential_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When create_open_id_connect_provider raises ClientError, CredentialError is raised."""
    import botocore.exceptions

    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response([])
    error_response = {
        "Error": {
            "Code": "LimitExceeded",
            "Message": "Cannot exceed quota for OpenIDConnectProvider: 1",
        }
    }
    mock_iam.create_open_id_connect_provider.side_effect = botocore.exceptions.ClientError(
        error_response, "CreateOpenIDConnectProvider"
    )

    with pytest.raises(m.CredentialError) as exc_info:
        m.bootstrap_oidc_provider(env="qa", accounts_data=accounts_data)

    error_msg = str(exc_info.value)
    assert "qa" in error_msg, (
        f"CredentialError from create must name the profile; got: {error_msg!r}"
    )


@pytest.mark.unit
def test_main_exits_2_when_accounts_json_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must exit 2 when _load_accounts_from_default_path raises UsageError."""
    import scripts.bootstrap_oidc_provider as m

    def _raise_usage_error() -> dict:
        raise m.UsageError("ERROR: accounts.json not found at /bad/path\nRemediation: ...")

    monkeypatch.setattr(m, "_load_accounts_from_default_path", _raise_usage_error)

    with pytest.raises(SystemExit) as exc_info:
        m.main(["--env", "qa"])
    assert exc_info.value.code == 2


@pytest.mark.unit
def test_main_guard_invokes_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """The if __name__ == '__main__' guard must call main().

    Verifies the module's __main__ block by running it via runpy,
    which exercises the guarded line. main() itself is separately tested;
    here we just confirm the guard triggers it.
    """
    import runpy

    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    provider_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response(
        [provider_arn]
    )
    mock_iam.get_open_id_connect_provider.return_value = _make_get_provider_response(
        client_ids=["sts.amazonaws.com"],
        thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
    )
    monkeypatch.setattr(m, "_load_accounts_from_default_path", lambda: accounts_data)
    monkeypatch.setattr(sys, "argv", ["bootstrap_oidc_provider", "--env", "qa"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("scripts.bootstrap_oidc_provider", run_name="__main__")
    assert exc_info.value.code == 0


@pytest.mark.unit
def test_no_mutating_calls_on_idempotent_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second run against an existing correct provider must make zero mutating calls."""
    import scripts.bootstrap_oidc_provider as m

    accounts_data = _load_accounts()
    provider_arn = "arn:aws:iam::111111111111:oidc-provider/token.actions.githubusercontent.com"
    mock_iam = _patch_boto_session(monkeypatch)
    mock_iam.list_open_id_connect_providers.return_value = _make_list_providers_response(
        [provider_arn]
    )
    mock_iam.get_open_id_connect_provider.return_value = _make_get_provider_response(
        client_ids=["sts.amazonaws.com"],
        thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
    )

    # First run
    result1 = m.bootstrap_oidc_provider(env="prod", accounts_data=accounts_data)
    # Second run (idempotent)
    result2 = m.bootstrap_oidc_provider(env="prod", accounts_data=accounts_data)

    assert result1 == m.ProviderResult.ALREADY_PRESENT
    assert result2 == m.ProviderResult.ALREADY_PRESENT

    mock_iam.create_open_id_connect_provider.assert_not_called()
    mock_iam.update_open_id_connect_provider_thumbprint.assert_not_called()
    mock_iam.add_client_id_to_open_id_connect_provider.assert_not_called()
    mock_iam.remove_client_id_from_open_id_connect_provider.assert_not_called()
    mock_iam.delete_open_id_connect_provider.assert_not_called()
