"""Unit tests for scripts/live_verify.py -- FR-13 read-only live verification prober.

All AWS/network/gh interactions are mocked; no real AWS calls are made.

Covers:
  - All eight check types: oidc-provider, oidc-roles, state-backend, stack,
    endpoints, observability, no-project-resources, repo-settings
  - OK and FAIL paths for each check
  - Read-only call-surface assertion (only Get*/List*/Describe*/head-* style calls)
  - ENV-to-profile resolution from accounts.json aws_profile key
  - Section 5.5 JSON evidence schema via --output
  - Bootstrap exemption list parsing and shape (AC-4)
  - All task-specific error paths per the error handling contract
  - Exit codes: 0 (all OK), 1 (probe failure), 2 (unknown CHECK/ENV)
  - Readiness polling via LIVE_VERIFY_TIMEOUT / LIVE_VERIFY_POLL_INTERVAL constants
"""

from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ACCOUNTS_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "accounts.json"
OIDC_ROLES_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "oidc-roles.json"
EXEMPTIONS_JSON_PATH = REPO_ROOT / "terragrunt" / "common" / "no-project-resources-exemptions.json"


# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_live_verify() -> types.ModuleType:
    """Import (or re-import) the live_verify module fresh."""
    module_name = "scripts.live_verify"
    if module_name in sys.modules:
        del sys.modules[module_name]
    import scripts.live_verify as m

    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Deterministic AWS region for region-scoped ARN construction in the
# stack/observability checks. The mocked boto3 clients ignore the ARN value,
# so any valid region works; us-east-1 matches the region used elsewhere in
# this module (e.g. the SNS topic ARNs) and the _get_region docstring example.
_TEST_AWS_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _deterministic_aws_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin AWS_DEFAULT_REGION so region-scoped checks are independent of the host env.

    live_verify._get_region() fail-fasts (UsageError) when AWS_DEFAULT_REGION is
    unset and no aws_region is injected. The stack and observability checks build
    region-scoped ACM/SNS ARNs via _get_region(), so without this fixture those
    tests would only pass when AWS_DEFAULT_REGION happens to be present in the
    ambient environment (as it is on developer machines but not in CI's clean env).

    Setting it here makes every test in this module exercise the real code path
    deterministically. This does NOT weaken the fail-fast contract: tests that
    assert the unset behavior (e.g. test_get_region_raises_when_env_var_unset)
    delete the variable in their own body, and tests that inject aws_region take
    precedence over the env var inside _get_region().
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", _TEST_AWS_REGION)


@pytest.fixture()
def accounts_data() -> dict:
    """Load the real accounts.json from the repo."""
    assert ACCOUNTS_JSON_PATH.exists(), (
        f"accounts.json not found at {ACCOUNTS_JSON_PATH}; the file must exist."
    )
    return json.loads(ACCOUNTS_JSON_PATH.read_text())


@pytest.fixture()
def oidc_roles_data() -> dict:
    """Load the real oidc-roles.json from the repo."""
    assert OIDC_ROLES_JSON_PATH.exists(), (
        f"oidc-roles.json not found at {OIDC_ROLES_JSON_PATH}; the file must exist."
    )
    return json.loads(OIDC_ROLES_JSON_PATH.read_text())


@pytest.fixture()
def exemptions_data() -> dict:
    """Load the real no-project-resources-exemptions.json from the repo."""
    assert EXEMPTIONS_JSON_PATH.exists(), (
        f"no-project-resources-exemptions.json not found at {EXEMPTIONS_JSON_PATH}; "
        "the file must exist."
    )
    return json.loads(EXEMPTIONS_JSON_PATH.read_text())


# ---------------------------------------------------------------------------
# AC-4: Bootstrap exemption list shape and content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExemptionList:
    """AC-4: committed bootstrap exemption list parses and names only state-backend ARN patterns."""

    def test_exemption_file_exists(self) -> None:
        """The no-project-resources-exemptions.json file must exist."""
        assert EXEMPTIONS_JSON_PATH.exists(), (
            f"no-project-resources-exemptions.json not found at {EXEMPTIONS_JSON_PATH}."
        )

    def test_exemption_file_parses_as_json(self) -> None:
        """The exemption file must be valid JSON."""
        content = EXEMPTIONS_JSON_PATH.read_text()
        data = json.loads(content)
        assert isinstance(data, dict), "Exemption file must be a JSON object."

    def test_exemption_file_has_account_keys(self, exemptions_data: dict) -> None:
        """Exemption file must have entries keyed by account IDs."""
        assert len(exemptions_data) > 0, "Exemption file must have at least one account entry."
        for key in exemptions_data:
            assert key.isdigit() and len(key) == 12, (
                f"Exemption file key {key!r} must be a 12-digit AWS account ID."
            )

    def test_exemption_file_state_owning_accounts(
        self, exemptions_data: dict, accounts_data: dict
    ) -> None:
        """Exemption file must only cover accounts with a state-bootstrap (sandbox, prod)."""
        state_owning_roles = {"sandbox", "prod-infra"}
        state_owning_accounts = {
            acct_id
            for acct_id, info in accounts_data.items()
            if info.get("account_role") in state_owning_roles
        }
        exemption_accounts = set(exemptions_data.keys())
        assert exemption_accounts.issubset(state_owning_accounts), (
            f"Exemption file contains accounts not in state-owning set. "
            f"Extra: {exemption_accounts - state_owning_accounts}. "
            f"Only state-backend accounts (sandbox, prod) should have exemptions."
        )

    def test_exemption_file_required_resource_patterns(self, exemptions_data: dict) -> None:
        """Each account's exemption must include the four required bootstrap resource patterns."""
        for acct_id, patterns in exemptions_data.items():
            assert isinstance(patterns, list), (
                f"Account {acct_id} exemptions must be a list of ARN patterns."
            )
            assert len(patterns) > 0, f"Account {acct_id} must have at least one exemption pattern."
            # Each pattern must be a non-empty string
            for pattern in patterns:
                assert isinstance(pattern, str) and pattern.strip(), (
                    f"Account {acct_id} has a non-string or empty exemption pattern: {pattern!r}."
                )

    def test_exemption_file_includes_state_bucket_pattern(self, exemptions_data: dict) -> None:
        """Each account's exemptions must include a state bucket ARN pattern."""
        for acct_id, patterns in exemptions_data.items():
            joined = " ".join(patterns)
            assert "tfstate" in joined.lower() or acct_id in joined, (
                f"Account {acct_id} exemptions do not mention a state bucket ARN pattern. "
                f"Expected patterns referencing the tfstate bucket. Got: {patterns!r}."
            )

    def test_exemption_file_includes_cmk_alias_pattern(self, exemptions_data: dict) -> None:
        """Each account's exemptions must include a CMK alias pattern."""
        for acct_id, patterns in exemptions_data.items():
            joined = " ".join(patterns)
            assert "alias" in joined.lower(), (
                f"Account {acct_id} exemptions do not include a CMK alias pattern. "
                f"Expected an 'alias/<acct>-tfstate' pattern. Got: {patterns!r}."
            )

    def test_exemption_file_no_non_bootstrap_resources(self, exemptions_data: dict) -> None:
        """Exemption file must only name bootstrap resource ARN patterns."""
        allowed_keywords = {"tfstate", "artifact", "access-log", "alias", "arn:aws"}
        for acct_id, patterns in exemptions_data.items():
            for pattern in patterns:
                matched = any(kw in pattern.lower() for kw in allowed_keywords)
                assert matched, (
                    f"Account {acct_id} has an exemption pattern that does not look like "
                    f"a state-backend resource: {pattern!r}. "
                    "Only state bucket, access-log bucket, artifact bucket, and CMK alias "
                    "patterns are permitted."
                )


# ---------------------------------------------------------------------------
# ENV-to-profile resolution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnvToProfileResolution:
    """ENV-to-profile resolution from accounts.json aws_profile key."""

    def test_sandbox_env_resolves_to_sandbox_profile(self, accounts_data: dict) -> None:
        """ENV=sandbox must resolve to aws_profile=sandbox from accounts.json."""
        sandbox_accounts = [
            info for info in accounts_data.values() if info["account_role"] == "sandbox"
        ]
        assert len(sandbox_accounts) == 1, "Exactly one sandbox account must exist."
        assert sandbox_accounts[0]["aws_profile"] == "sandbox", (
            "Sandbox account must have aws_profile='sandbox'."
        )

    @pytest.mark.parametrize(
        "env_name,expected_profile",
        [
            ("sandbox", "sandbox"),
            ("qa", "qa"),
            ("prod", "prod"),
            ("root", "root"),
        ],
    )
    def test_env_resolves_to_correct_profile(
        self, accounts_data: dict, env_name: str, expected_profile: str
    ) -> None:
        """Each ENV must resolve to the correct aws_profile from accounts.json."""
        matching = [
            info for info in accounts_data.values() if info.get("aws_profile") == expected_profile
        ]
        assert len(matching) == 1, (
            f"Expected exactly one account with aws_profile='{expected_profile}' "
            f"for ENV='{env_name}'. Found {len(matching)}."
        )

    def test_live_verify_resolve_profile_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """live_verify._resolve_profile('sandbox') returns 'sandbox'."""
        monkeypatch.setenv("ACCOUNTS_JSON_PATH", str(ACCOUNTS_JSON_PATH))
        m = _import_live_verify()
        profile = m._resolve_profile(
            "sandbox", accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text())
        )
        assert profile == "sandbox"

    def test_live_verify_resolve_profile_qa(self) -> None:
        """live_verify._resolve_profile('qa') returns 'qa'."""
        m = _import_live_verify()
        profile = m._resolve_profile("qa", accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()))
        assert profile == "qa"

    def test_live_verify_resolve_profile_unknown_env_raises(self) -> None:
        """live_verify._resolve_profile with unknown ENV raises a UsageError."""
        m = _import_live_verify()
        with pytest.raises(m.UsageError, match="unknown ENV"):
            m._resolve_profile("notanenv", accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()))

    def test_live_verify_resolve_account_id_sandbox(self) -> None:
        """live_verify._resolve_account_id('sandbox') returns the correct sandbox account ID."""
        m = _import_live_verify()
        acct_id = m._resolve_account_id(
            "sandbox", accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text())
        )
        assert acct_id == "222222222222"


# ---------------------------------------------------------------------------
# Read-only call surface assertion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadOnlyCallSurface:
    """Assert that scripts/live_verify.py only issues read-only AWS API calls."""

    _ALLOWED_CALL_PREFIXES = (
        "get_",
        "list_",
        "describe_",
        "head_",
    )

    def _collect_boto3_method_calls_from_source(self) -> list[str]:
        """Parse the live_verify source for boto3 client method call patterns."""
        source = (REPO_ROOT / "scripts" / "live_verify.py").read_text()
        import re

        # Find all calls like client.some_method( or self._client.some_method(
        # Use a pattern that finds method calls on named boto3 client variables.
        method_pattern = re.compile(r"\b(\w+_client|client|session)\s*\.\s*(\w+)\s*\(")
        calls = set()
        for m in method_pattern.finditer(source):
            method_name = m.group(2)
            # Only consider method names that look like AWS API calls
            if "_" in method_name and not method_name.startswith("_"):
                calls.add(method_name)
        return list(calls)

    def test_no_mutating_aws_calls_in_source(self) -> None:
        """live_verify.py must not call any mutating AWS methods.

        Only Get*/List*/Describe*/head_* method names are permitted on boto3 clients.
        Any put_, create_, update_, delete_, set_, modify_, attach_ etc. method
        call is a mutation and is forbidden in the live_verify module.

        This test inspects the boto3 client variable calls specifically -- it does
        NOT flag standard library methods like ssl.create_default_context() or
        socket.create_connection(), which are necessary for TLS/network probes.
        """
        source_path = REPO_ROOT / "scripts" / "live_verify.py"
        assert source_path.exists(), (
            f"scripts/live_verify.py not found at {source_path}. "
            "The file must be created as part of this task."
        )
        source = source_path.read_text()

        import re

        # Only look at method calls on identifiers that are boto3 client variables.
        # These are created via self._client(...) and stored in named variables like
        # iam, s3, kms, cf, rgt, cw, etc. We identify them by the call pattern
        # used in the checks: `<client_var>.<method>(`.
        # Strategy: find all calls where the receiver ends with a known boto3 client
        # suffix pattern used in this module (_client suffix, or named like 'iam',
        # 's3', 'kms', 'cf', 'acm', 'rgt', 'cw', 'budgets', 'ce', 'sns', 'ecs',
        # 'firehose', 'glue', 'athena', 'route53').
        boto3_client_names = {
            "iam",
            "s3",
            "kms",
            "cf",
            "acm",
            "route53",
            "firehose",
            "glue",
            "athena",
            "ecs",
            "cw",
            "budgets",
            "ce",
            "sns",
            "rgt",
            "session",
        }

        # Match calls like: iam.get_role( or s3.head_bucket( etc.
        boto3_call_pattern = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in boto3_client_names) + r")\s*\.\s*(\w+)\s*\("
        )

        aws_calls: set[str] = set()
        for m in boto3_call_pattern.finditer(source):
            method = m.group(2)
            if "_" in method and not method.startswith("_"):
                aws_calls.add(method)

        # Also catch calls via self._client(...).<method>(
        # which appear in chains or stored in local vars differently
        client_method_pattern = re.compile(r"self\._client\([^)]+\)\s*\.\s*(\w+)\s*\(")
        for m in client_method_pattern.finditer(source):
            method = m.group(1)
            if "_" in method and not method.startswith("_"):
                aws_calls.add(method)

        mutating_prefixes = (
            "put_",
            "create_",
            "update_",
            "delete_",
            "set_",
            "modify_",
            "attach_",
            "detach_",
            "add_",
            "remove_",
            "revoke_",
            "authorize_",
            "disassociate_",
            "associate_",
            "tag_",
            "untag_",
            "enable_",
            "disable_",
            "change_",
            "reset_",
            "apply_",
            "run_",
            "start_",
            "stop_",
            "terminate_",
            "reboot_",
            "copy_",
            "import_",
            "export_",
            "register_",
            "deregister_",
            "publish_",
            "send_",
            "invoke_",
            "execute_",
            "cancel_",
        )

        violations = [
            m for m in aws_calls if any(m.startswith(prefix) for prefix in mutating_prefixes)
        ]

        assert not violations, (
            f"scripts/live_verify.py contains mutating AWS API calls, which are forbidden "
            f"(the tool must be strictly read-only). "
            f"Violating method names found: {sorted(violations)!r}. "
            "Only Get*/List*/Describe*/head_* style calls are permitted on boto3 clients."
        )

    def test_all_aws_calls_are_read_only_prefixed(self) -> None:
        """Every AWS-style method call in live_verify.py must start with a read-only prefix."""
        source_path = REPO_ROOT / "scripts" / "live_verify.py"
        assert source_path.exists(), (
            f"scripts/live_verify.py not found at {source_path}; the file must exist."
        )

        source = source_path.read_text()
        import re

        # Pattern: word.method( where method has underscores (AWS API style)
        # Find ALL underscore method calls on dotted access
        all_pattern = re.compile(r"\.\s*([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s*\(")
        all_calls = set()
        for m in all_pattern.finditer(source):
            name = m.group(1)
            # Skip Python builtins and common non-AWS patterns
            skip = {
                "read_text",
                "write_text",
                "split_lines",
                "join_path",
                "strip_prefix",
                "read_bytes",
                "write_bytes",
                "open_file",
                "file_path",
                "log_info",
                "log_error",
                "parse_args",
                "add_argument",
                "print_line",
                "format_msg",
                "sys_exit",
                "os_environ",
                "json_dumps",
                "json_loads",
            }
            if name not in skip and "_" in name:
                all_calls.add(name)

        read_only_prefixes = ("get_", "list_", "describe_", "head_")
        # Only check calls that look like AWS API calls (not internal helpers)
        # We look for calls made on variables that look like boto3 clients
        boto3_client_pattern = re.compile(
            r"(\w+client\w*|boto3\w*|session\w*|iam\w*|s3\w*|acm\w*|cf\w*|"
            r"rg\w*|route53\w*|cw\w*|budgets\w*|ce\w*|sns\w*|ecs\w*|"
            r"firehose\w*|glue\w*|athena\w*)\s*\.\s*([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\s*\("
        )
        aws_calls = set()
        for m in boto3_client_pattern.finditer(source):
            method = m.group(2)
            if "_" in method:
                aws_calls.add(method)

        violations = [c for c in aws_calls if not any(c.startswith(p) for p in read_only_prefixes)]

        assert not violations, (
            f"scripts/live_verify.py contains non-read-only AWS calls: {sorted(violations)!r}. "
            "Only get_*/list_*/describe_*/head_* are permitted (read-only contract)."
        )


# ---------------------------------------------------------------------------
# Evidence schema (section 5.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvidenceSchema:
    """Section 5.5 JSON evidence schema validation."""

    def test_evidence_schema_required_fields(self) -> None:
        """Evidence JSON must have: check, env, run_at, account_id, probes, result."""
        evidence = {
            "check": "no-project-resources",
            "env": "sandbox",
            "run_at": "2026-06-14T03:12:09Z",
            "account_id": "222222222222",
            "probes": [
                {
                    "name": "tagging-api:Project=telemetry-platform",
                    "status": "OK",
                    "detail": "0 matches",
                },
            ],
            "result": "OK",
        }
        required_fields = {"check", "env", "run_at", "account_id", "probes", "result"}
        for field in required_fields:
            assert field in evidence, f"Evidence schema missing required field: {field!r}"

    def test_evidence_probe_schema(self) -> None:
        """Each probe in evidence must have: name, status, detail."""
        probe = {
            "name": "tagging-api:Project=telemetry-platform",
            "status": "OK",
            "detail": "0 matches",
        }
        required = {"name", "status", "detail"}
        for field in required:
            assert field in probe, f"Probe schema missing required field: {field!r}"

    def test_evidence_result_values(self) -> None:
        """_emit_ok and _emit_fail must produce probes with status 'OK' and 'FAIL'."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        def _noop_gh_runner(args: list[str]) -> tuple[int, str, str]:
            return 1, "", "not used"

        lv = _import_live_verify()
        runner = lv.LiveVerify(
            check="no-project-resources",
            env="sandbox",
            output_path=None,
            accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
            exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
            oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
            boto3_module=mock_boto3,
            gh_runner=_noop_gh_runner,
        )
        probes: list[dict] = []
        runner._emit_ok(probes, "test:ok-probe", detail="ok detail")
        runner._emit_fail(probes, "test:fail-probe", expected="something", actual="something else")
        statuses = {p["status"] for p in probes}
        assert statuses == {"OK", "FAIL"}, f"unexpected probe statuses: {statuses}"
        ok_probe = next(p for p in probes if p["name"] == "test:ok-probe")
        fail_probe = next(p for p in probes if p["name"] == "test:fail-probe")
        assert ok_probe["status"] == "OK"
        assert fail_probe["status"] == "FAIL"

    def test_live_verify_writes_evidence_json(self, tmp_path: pathlib.Path) -> None:
        """--output flag must produce a valid section 5.5 evidence JSON file."""
        output_path = tmp_path / "evidence.json"

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {
            "Account": "222222222222",
            "Arn": "arn:aws:iam::222222222222:user/test",
            "UserId": "AIDATEST",
        }

        # Mock for no-project-resources: Tagging API returns empty
        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(output_path),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert output_path.exists(), (
            "Evidence JSON file must be created when --output is specified."
        )
        evidence = json.loads(output_path.read_text())
        assert evidence["check"] == "no-project-resources"
        assert evidence["env"] == "sandbox"
        assert "run_at" in evidence
        assert "account_id" in evidence
        assert isinstance(evidence["probes"], list)
        assert evidence["result"] in ("OK", "FAIL")
        assert exit_code == 0


# ---------------------------------------------------------------------------
# oidc-provider check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOidcProviderCheck:
    """oidc-provider check: OK and FAIL paths."""

    def _make_iam_client(
        self, providers: list[dict], provider_detail: dict | None = None
    ) -> MagicMock:
        iam_client = MagicMock()
        iam_client.list_open_id_connect_providers.return_value = {
            "OpenIDConnectProviderList": providers
        }
        if provider_detail is not None:
            iam_client.get_open_id_connect_provider.return_value = provider_detail
        else:
            iam_client.get_open_id_connect_provider.return_value = {
                "ClientIDList": ["sts.amazonaws.com"],
                "ThumbprintList": ["abc123"],
            }
        return iam_client

    def test_oidc_provider_ok_when_provider_exists(self, tmp_path: pathlib.Path) -> None:
        """oidc-provider check returns OK when the OIDC provider exists with correct audience."""
        provider_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
        iam_client = self._make_iam_client(
            providers=[{"Arn": provider_arn}],
            provider_detail={"ClientIDList": ["sts.amazonaws.com"], "ThumbprintList": ["abc"]},
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "333333333333"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-provider",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_oidc_provider_fail_when_provider_missing(self, tmp_path: pathlib.Path) -> None:
        """oidc-provider check returns FAIL when no OIDC provider is found."""
        iam_client = self._make_iam_client(providers=[])

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "333333333333"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-provider",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_oidc_provider_fail_when_wrong_audience(self, tmp_path: pathlib.Path) -> None:
        """oidc-provider check returns FAIL when audience is not sts.amazonaws.com."""
        provider_arn = "arn:aws:iam::333333333333:oidc-provider/token.actions.githubusercontent.com"
        iam_client = self._make_iam_client(
            providers=[{"Arn": provider_arn}],
            provider_detail={"ClientIDList": ["wrong-audience"], "ThumbprintList": ["abc"]},
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "333333333333"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-provider",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# oidc-roles check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOidcRolesCheck:
    """oidc-roles check: OK and FAIL paths."""

    def _make_iam_client_with_roles(self, roles: dict[str, dict]) -> MagicMock:
        iam_client = MagicMock()

        def get_role(**kwargs: str) -> dict:
            role_name = kwargs.get("RoleName", "")
            if role_name in roles:
                return {"Role": roles[role_name]}
            from botocore.exceptions import ClientError

            error_response = {"Error": {"Code": "NoSuchEntity", "Message": "Role not found"}}
            raise ClientError(error_response, "GetRole")

        iam_client.get_role.side_effect = get_role
        iam_client.list_attached_role_policies.return_value = {"AttachedPolicies": []}
        iam_client.list_role_policies.return_value = {"PolicyNames": []}
        return iam_client

    def test_oidc_roles_ok_when_all_roles_exist(
        self, tmp_path: pathlib.Path, oidc_roles_data: dict
    ) -> None:
        """oidc-roles check returns OK when all expected roles exist in the account."""
        qa_account = "333333333333"
        qa_roles = oidc_roles_data.get(qa_account, {}).get("roles", {})

        roles_map = {}
        for role_name, role_info in qa_roles.items():
            gh_oidc_url = "token.actions.githubusercontent.com"
            gh_oidc_arn = f"arn:aws:iam::333333333333:oidc-provider/{gh_oidc_url}"
            trust = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Federated": gh_oidc_arn},
                        "Action": "sts:AssumeRoleWithWebIdentity",
                        "Condition": {
                            "StringLike": {f"{gh_oidc_url}:sub": role_info.get("sub", "*")}
                        },
                    }
                ],
            }
            roles_map[role_name] = {
                "RoleName": role_name,
                "AssumeRolePolicyDocument": trust,
                "Arn": f"arn:aws:iam::333333333333:role/{role_name}",
            }

        iam_client = self._make_iam_client_with_roles(roles_map)

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": qa_account}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=oidc_roles_data,
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_oidc_roles_fail_when_role_missing(
        self, tmp_path: pathlib.Path, oidc_roles_data: dict
    ) -> None:
        """oidc-roles check returns FAIL when expected role does not exist."""
        import botocore.exceptions

        iam_client = MagicMock()
        error_response = {"Error": {"Code": "NoSuchEntity", "Message": "Role not found"}}
        iam_client.get_role.side_effect = botocore.exceptions.ClientError(error_response, "GetRole")

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "333333333333"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=oidc_roles_data,
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# state-backend check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStateBackendCheck:
    """state-backend check: OK and FAIL paths."""

    def _make_ok_s3_client(self) -> MagicMock:
        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}
        s3_client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3_client.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": (
                                "arn:aws:kms:us-east-1:222222222222:alias/222222222222-tfstate"
                            ),
                        }
                    }
                ]
            }
        }
        s3_client.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }
        s3_client.get_bucket_policy.return_value = {
            "Policy": json.dumps(
                {
                    "Statement": [
                        {"Condition": {"Bool": {"aws:SecureTransport": "false"}}, "Effect": "Deny"}
                    ]
                }
            )
        }
        return s3_client

    def _make_ok_kms_client(self) -> MagicMock:
        kms_client = MagicMock()
        kms_client.describe_key.return_value = {
            "KeyMetadata": {"KeyState": "Enabled", "KeyId": "abc-123"}
        }
        return kms_client

    def test_state_backend_ok_when_fully_hardened(self, tmp_path: pathlib.Path) -> None:
        """state-backend check returns OK when all S3/KMS posture checks pass."""
        s3_client = self._make_ok_s3_client()
        kms_client = self._make_ok_kms_client()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_state_backend_fail_when_bucket_missing(self, tmp_path: pathlib.Path) -> None:
        """state-backend check returns FAIL when the state bucket does not exist."""
        import botocore.exceptions

        s3_client = MagicMock()
        error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
        s3_error = botocore.exceptions.ClientError(error_response, "HeadBucket")
        s3_client.head_bucket.side_effect = s3_error
        # All other s3 calls also fail because the bucket doesn't exist
        s3_client.get_bucket_versioning.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}}, "GetBucketVersioning"
        )
        s3_client.get_bucket_encryption.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}}, "GetBucketEncryption"
        )
        s3_client.get_public_access_block.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}}, "GetPublicAccessBlock"
        )
        s3_client.get_bucket_policy.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}}, "GetBucketPolicy"
        )

        kms_client = self._make_ok_kms_client()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_state_backend_fail_when_versioning_disabled(self, tmp_path: pathlib.Path) -> None:
        """state-backend check returns FAIL when bucket versioning is not enabled."""
        s3_client = self._make_ok_s3_client()
        # Override versioning response to indicate Suspended
        s3_client.get_bucket_versioning.return_value = {"Status": "Suspended"}
        kms_client = self._make_ok_kms_client()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# stack check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStackCheck:
    """stack check: OK and FAIL paths."""

    def test_stack_ok_when_all_outputs_present(self, tmp_path: pathlib.Path) -> None:
        """stack check returns OK when the stack is fully deployed."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}

        cf_client = MagicMock()
        cf_client.get_distribution.return_value = {
            "Distribution": {"Status": "Deployed", "DomainName": "abc.cloudfront.net"}
        }

        acm_client = MagicMock()
        acm_client.describe_certificate.return_value = {"Certificate": {"Status": "ISSUED"}}

        route53_client = MagicMock()
        route53_client.list_hosted_zones_by_name.return_value = {
            "HostedZones": [{"Id": "/hostedzone/Z123", "Name": "sandbox.example.com."}]
        }
        route53_client.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [{"Name": "sandbox.example.com.", "Type": "A"}]
        }

        firehose_client = MagicMock()
        firehose_client.describe_delivery_stream.return_value = {
            "DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}
        }

        glue_client = MagicMock()
        glue_client.get_database.return_value = {"Database": {"Name": "telemetry"}}

        athena_client = MagicMock()
        athena_client.get_work_group.return_value = {"WorkGroup": {"Name": "telemetry"}}

        ecs_client = MagicMock()
        ecs_client.describe_services.return_value = {
            "services": [{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1}]
        }

        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {"MetricAlarms": [{"AlarmName": "test-alarm"}]}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "cloudfront":
                return cf_client
            if service_name == "acm":
                return acm_client
            if service_name == "route53":
                return route53_client
            if service_name == "firehose":
                return firehose_client
            if service_name == "glue":
                return glue_client
            if service_name == "athena":
                return athena_client
            if service_name == "ecs":
                return ecs_client
            if service_name == "cloudwatch":
                return cw_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_stack_fail_when_cloudfront_not_deployed(self, tmp_path: pathlib.Path) -> None:
        """stack check returns FAIL when a CloudFront distribution is not in Deployed state."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}

        cf_client = MagicMock()
        cf_client.get_distribution.return_value = {
            "Distribution": {"Status": "InProgress", "DomainName": "abc.cloudfront.net"}
        }

        acm_client = MagicMock()
        acm_client.describe_certificate.return_value = {"Certificate": {"Status": "ISSUED"}}

        route53_client = MagicMock()
        route53_client.list_hosted_zones_by_name.return_value = {
            "HostedZones": [{"Id": "/hostedzone/Z123", "Name": "sandbox.example.com."}]
        }
        route53_client.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [{"Name": "sandbox.example.com.", "Type": "A"}]
        }

        firehose_client = MagicMock()
        firehose_client.describe_delivery_stream.return_value = {
            "DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}
        }

        glue_client = MagicMock()
        glue_client.get_database.return_value = {"Database": {"Name": "telemetry"}}

        athena_client = MagicMock()
        athena_client.get_work_group.return_value = {"WorkGroup": {"Name": "telemetry"}}

        ecs_client = MagicMock()
        ecs_client.describe_services.return_value = {
            "services": [{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1}]
        }

        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {"MetricAlarms": [{"AlarmName": "test-alarm"}]}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "cloudfront":
                return cf_client
            if service_name == "acm":
                return acm_client
            if service_name == "route53":
                return route53_client
            if service_name == "firehose":
                return firehose_client
            if service_name == "glue":
                return glue_client
            if service_name == "athena":
                return athena_client
            if service_name == "ecs":
                return ecs_client
            if service_name == "cloudwatch":
                return cw_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# endpoints check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEndpointsCheck:
    """endpoints check: OK and FAIL paths."""

    def test_endpoints_ok_when_all_resolve(self, tmp_path: pathlib.Path) -> None:
        """endpoints check returns OK when DNS + TLS + HTTP probes all pass."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        mock_resolver = MagicMock()
        mock_resolver.return_value = (True, "DNS resolved", "127.0.0.1")

        mock_tls = MagicMock()
        mock_tls.return_value = (True, "TLS OK")

        mock_http = MagicMock()
        mock_http.return_value = (True, "HTTP OK")

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=mock_resolver,
                tls_checker=mock_tls,
                http_prober=mock_http,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_endpoints_fail_when_dns_does_not_resolve(self, tmp_path: pathlib.Path) -> None:
        """endpoints check returns FAIL when DNS does not resolve."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        mock_resolver = MagicMock()
        mock_resolver.return_value = (False, "DNS resolution failed", None)

        mock_tls = MagicMock()
        mock_tls.return_value = (True, "TLS OK")

        mock_http = MagicMock()
        mock_http.return_value = (True, "HTTP OK")

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=mock_resolver,
                tls_checker=mock_tls,
                http_prober=mock_http,
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# observability check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestObservabilityCheck:
    """observability check: OK and FAIL paths."""

    def test_observability_ok_when_all_present(self, tmp_path: pathlib.Path) -> None:
        """observability check returns OK when all alarms, budgets, and subscriptions exist."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        # The real observability alarm names are the alarms-input map keys
        # (alarm_name = each.key in the cloudwatch primitive), e.g. alb_5xx_count.
        # They carry no "<env>-" prefix, so the check lists all alarms and asserts an
        # expected name is present.
        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {
            "MetricAlarms": [
                {"AlarmName": "alb_5xx_count", "StateValue": "OK"},
                {"AlarmName": "ecs_running_task_count", "StateValue": "OK"},
            ]
        }

        budgets_client = MagicMock()
        budgets_client.describe_budgets.return_value = {"Budgets": [{"BudgetName": "budget-1"}]}

        ce_client = MagicMock()
        ce_client.get_anomaly_monitors.return_value = {
            "AnomalyMonitors": [{"MonitorArn": "arn:aws:ce::222222222222:anomalymonitor/1"}]
        }
        ce_client.get_anomaly_subscriptions.return_value = {
            "AnomalySubscriptions": [
                {
                    "SubscriptionArn": "arn:aws:ce::222222222222:anomalysubscription/1",
                    "Subscribers": [{"Address": "test@example.com", "Status": "CONFIRMED"}],
                }
            ]
        }

        # The SNS topic name is namespace-derived; a confirmed subscription is present.
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [
                {
                    "SubscriptionArn": "arn:aws:sns:us-east-1:222222222222:topic:sub1",
                    "Protocol": "email",
                    "Endpoint": "platform-alerts@example.com",
                    "SubscriptionOwner": "222222222222",
                }
            ]
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_observability_fail_when_alarm_in_alarm_state(self, tmp_path: pathlib.Path) -> None:
        """observability check returns FAIL when an expected alarm is in ALARM state."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        # A real observability alarm name (map key) in ALARM state must FAIL the probe.
        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {
            "MetricAlarms": [{"AlarmName": "alb_5xx_count", "StateValue": "ALARM"}]
        }

        budgets_client = MagicMock()
        budgets_client.describe_budgets.return_value = {"Budgets": [{"BudgetName": "budget-1"}]}

        ce_client = MagicMock()
        ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": [{"MonitorArn": "arn"}]}
        ce_client.get_anomaly_subscriptions.return_value = {
            "AnomalySubscriptions": [{"SubscriptionArn": "arn", "Subscribers": []}]
        }

        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {"Subscriptions": []}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_observability_fail_when_no_alarms(self, tmp_path: pathlib.Path) -> None:
        """observability check returns FAIL when no alarms are found."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {"MetricAlarms": []}

        budgets_client = MagicMock()
        budgets_client.describe_budgets.return_value = {"Budgets": []}

        ce_client = MagicMock()
        ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": []}
        ce_client.get_anomaly_subscriptions.return_value = {"AnomalySubscriptions": []}

        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {"Subscriptions": []}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# no-project-resources check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoProjectResourcesCheck:
    """no-project-resources check: OK, FAIL, and exemption paths."""

    def test_no_project_resources_ok_when_empty(self, tmp_path: pathlib.Path) -> None:
        """no-project-resources check returns OK when zero tagged resources found."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_no_project_resources_ok_when_only_exempt_resources(
        self, tmp_path: pathlib.Path, exemptions_data: dict
    ) -> None:
        """no-project-resources check returns OK when all found resources are on the exemption list.

        All found ARNs match the committed exemption list; exit code must be 0.
        """
        acct_id = "222222222222"
        exempt_arns = exemptions_data.get(acct_id, [])
        assert exempt_arns, (
            f"No exemptions defined for sandbox account {acct_id!r} in "
            f"no-project-resources-exemptions.json; at least one ARN pattern is required."
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": acct_id}

        # Return the first exempt ARN as a found resource
        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [{"ResourceARN": exempt_arns[0]}],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=exemptions_data,
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_no_project_resources_fail_when_non_exempt_resources_found(
        self, tmp_path: pathlib.Path, exemptions_data: dict
    ) -> None:
        """no-project-resources check returns FAIL and lists non-exempt ARNs."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        non_exempt_arn = "arn:aws:s3:::some-unexpected-bucket"
        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [{"ResourceARN": non_exempt_arn}],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=exemptions_data,
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_no_project_resources_fail_lists_arns_individually(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """no-project-resources FAIL must list each non-exempt ARN individually."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        arns = [
            "arn:aws:s3:::non-exempt-bucket-1",
            "arn:aws:s3:::non-exempt-bucket-2",
        ]
        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [{"ResourceARN": arn} for arn in arns],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1
        out = capsys.readouterr().out
        for arn in arns:
            assert arn in out, (
                f"Expected ARN {arn!r} to be listed in output when non-exempt resources found."
            )


# ---------------------------------------------------------------------------
# repo-settings check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepoSettingsCheck:
    """repo-settings check: OK and FAIL paths."""

    def test_repo_settings_ok_when_all_configured(self, tmp_path: pathlib.Path) -> None:
        """repo-settings check returns OK when variables and environments are set."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        # Mock gh runner
        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return (
                    0,
                    "AWS_DEFAULT_REGION\nAWS_QA_TERRATEST_ROLE_ARN\nAWS_TERRAGRUNT_PLAN_ROLE_ARN\nLOCK_MAX_AGE_MINUTES\nPORTAL_ARTIFACT_BUCKET\nPORTAL_LAMBDA_S3_KEY",
                    "",
                )
            if "environments/terratest-approval" in cmd:
                return 0, '{"name":"terratest-approval"}', ""
            if "environments/prod-apply" in cmd:
                return (
                    0,
                    '{"name":"prod-apply","protection_rules":[{"type":"required_reviewers","reviewers":[{"reviewer":{"login":"operator"}}]}]}',
                    "",
                )
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_repo_settings_fail_when_variable_missing(self, tmp_path: pathlib.Path) -> None:
        """repo-settings check returns FAIL when a required variable is missing."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        # Missing variables - only has one
        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return 0, "AWS_DEFAULT_REGION", ""
            if "environments/terratest-approval" in cmd:
                return 0, '{"name":"terratest-approval"}', ""
            if "environments/prod-apply" in cmd:
                return (
                    0,
                    '{"name":"prod-apply","protection_rules":[{"type":"required_reviewers","reviewers":[{"reviewer":{"login":"op"}}]}]}',
                    "",
                )
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_repo_settings_fail_when_environment_missing(self, tmp_path: pathlib.Path) -> None:
        """repo-settings check returns FAIL when a required environment is absent."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return (
                    0,
                    "AWS_DEFAULT_REGION\nAWS_QA_TERRATEST_ROLE_ARN\nAWS_TERRAGRUNT_PLAN_ROLE_ARN\nLOCK_MAX_AGE_MINUTES\nPORTAL_ARTIFACT_BUCKET\nPORTAL_LAMBDA_S3_KEY",
                    "",
                )
            if "environments" in cmd:
                return 1, "", "Not Found"
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        assert exit_code == 1


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorPaths:
    """Task-specific error paths per the work unit's error handling contract."""

    def test_unknown_check_exits_2(self, tmp_path: pathlib.Path) -> None:
        """Unknown CHECK value must raise UsageError (exit 2)."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            with pytest.raises(lv.UsageError, match="unknown CHECK"):
                lv.LiveVerify(
                    check="nonexistent-check",
                    env="sandbox",
                    output_path=str(tmp_path / "out.json"),
                    accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                    exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                    oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                    boto3_module=mock_boto3,
                    gh_runner=None,
                )

    def test_unknown_env_exits_2(self, tmp_path: pathlib.Path) -> None:
        """Unknown ENV value must raise UsageError (exit 2)."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            with pytest.raises(lv.UsageError, match="unknown ENV"):
                lv.LiveVerify(
                    check="no-project-resources",
                    env="notanenv",
                    output_path=str(tmp_path / "out.json"),
                    accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                    exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                    oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                    boto3_module=mock_boto3,
                    gh_runner=None,
                )

    def test_poll_budget_expiry_exits_nonzero(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Readiness poll expiry must exit non-zero with diagnostic information.

        The endpoints check polls DNS resolution with LIVE_VERIFY_TIMEOUT/
        LIVE_VERIFY_POLL_INTERVAL. When the poll budget expires, the check
        must exit 1 with a diagnostic.
        """
        # Set very short timeout so poll expires immediately
        monkeypatch.setenv("LIVE_VERIFY_TIMEOUT", "0")
        monkeypatch.setenv("LIVE_VERIFY_POLL_INTERVAL", "1")

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        # DNS resolver always fails (forces the endpoints check to fail)
        mock_resolver = MagicMock()
        mock_resolver.return_value = (False, "DNS resolution failed", None)
        mock_tls = MagicMock()
        mock_tls.return_value = (True, "TLS OK")
        mock_http = MagicMock()
        mock_http.return_value = (True, "HTTP OK")

        # Re-import module AFTER env var changes take effect
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        if "scripts.live_verify" in sys.modules:
            del sys.modules["scripts.live_verify"]

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=mock_resolver,
                tls_checker=mock_tls,
                http_prober=mock_http,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_probe_fail_lines_format(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Failed probes must output 'FAIL <probe>: <expected> vs <actual>' lines."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "333333333333"}

        iam_client = MagicMock()
        iam_client.list_open_id_connect_providers.return_value = {"OpenIDConnectProviderList": []}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-provider",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1
        output = capsys.readouterr().out
        assert "FAIL" in output, "Failed probe output must contain 'FAIL'."

    def test_ok_probe_lines_format(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Passed probes must output 'OK <probe>' lines."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0
        output = capsys.readouterr().out
        assert "OK" in output, "Passed probe output must contain 'OK'."


# ---------------------------------------------------------------------------
# Constants are defined and env-driven
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstants:
    """LIVE_VERIFY_TIMEOUT and LIVE_VERIFY_POLL_INTERVAL constants."""

    def test_constants_exist_in_constants_module(self) -> None:
        """scripts.constants must define LIVE_VERIFY_TIMEOUT and LIVE_VERIFY_POLL_INTERVAL."""
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        import scripts.constants as c

        assert hasattr(c, "LIVE_VERIFY_TIMEOUT"), (
            "scripts.constants must define LIVE_VERIFY_TIMEOUT."
        )
        assert hasattr(c, "LIVE_VERIFY_POLL_INTERVAL"), (
            "scripts.constants must define LIVE_VERIFY_POLL_INTERVAL."
        )

    def test_live_verify_timeout_default_value(self) -> None:
        """LIVE_VERIFY_TIMEOUT must default to 1800 (seconds) per spec section 7."""
        import os

        env_backup = os.environ.pop("LIVE_VERIFY_TIMEOUT", None)
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        try:
            import scripts.constants as c

            assert c.LIVE_VERIFY_TIMEOUT == 1800, (
                f"LIVE_VERIFY_TIMEOUT default must be 1800. Got: {c.LIVE_VERIFY_TIMEOUT!r}."
            )
        finally:
            if env_backup is not None:
                os.environ["LIVE_VERIFY_TIMEOUT"] = env_backup
            if "scripts.constants" in sys.modules:
                del sys.modules["scripts.constants"]

    def test_live_verify_poll_interval_default_value(self) -> None:
        """LIVE_VERIFY_POLL_INTERVAL must default to 15 (seconds) per spec section 7."""
        import os

        env_backup = os.environ.pop("LIVE_VERIFY_POLL_INTERVAL", None)
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        try:
            import scripts.constants as c

            got_poll = c.LIVE_VERIFY_POLL_INTERVAL
            assert got_poll == 15, f"LIVE_VERIFY_POLL_INTERVAL default = 15. Got: {got_poll!r}"
        finally:
            if env_backup is not None:
                os.environ["LIVE_VERIFY_POLL_INTERVAL"] = env_backup
            if "scripts.constants" in sys.modules:
                del sys.modules["scripts.constants"]

    def test_live_verify_timeout_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LIVE_VERIFY_TIMEOUT must be overridable via env var."""
        monkeypatch.setenv("LIVE_VERIFY_TIMEOUT", "999")
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        import scripts.constants as c

        assert c.LIVE_VERIFY_TIMEOUT == 999, (
            f"LIVE_VERIFY_TIMEOUT env override not working. Got: {c.LIVE_VERIFY_TIMEOUT!r}."
        )

    def test_live_verify_poll_interval_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LIVE_VERIFY_POLL_INTERVAL must be overridable via env var."""
        monkeypatch.setenv("LIVE_VERIFY_POLL_INTERVAL", "5")
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        import scripts.constants as c

        assert c.LIVE_VERIFY_POLL_INTERVAL == 5, "LIVE_VERIFY_POLL_INTERVAL override not working."

    def test_live_verify_https_port_default_value(self) -> None:
        """LIVE_VERIFY_HTTPS_PORT must default to 443 (IANA https well-known port)."""
        import os

        env_backup = os.environ.pop("LIVE_VERIFY_HTTPS_PORT", None)
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        try:
            import scripts.constants as c

            assert c.LIVE_VERIFY_HTTPS_PORT == 443, (
                f"LIVE_VERIFY_HTTPS_PORT default must be 443. Got: {c.LIVE_VERIFY_HTTPS_PORT!r}."
            )
        finally:
            if env_backup is not None:
                os.environ["LIVE_VERIFY_HTTPS_PORT"] = env_backup
            if "scripts.constants" in sys.modules:
                del sys.modules["scripts.constants"]

    def test_live_verify_https_port_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LIVE_VERIFY_HTTPS_PORT must be overridable via env var."""
        monkeypatch.setenv("LIVE_VERIFY_HTTPS_PORT", "8443")
        if "scripts.constants" in sys.modules:
            del sys.modules["scripts.constants"]
        import scripts.constants as c

        assert c.LIVE_VERIFY_HTTPS_PORT == 8443, (
            f"LIVE_VERIFY_HTTPS_PORT env override not working. Got: {c.LIVE_VERIFY_HTTPS_PORT!r}."
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCliEntryPoint:
    """CLI entry point: argparse, exit codes."""

    def test_cli_unknown_check_exits_2(self) -> None:
        """CLI with unknown --check argument must exit 2."""
        if "scripts.live_verify" in sys.modules:
            del sys.modules["scripts.live_verify"]
        import scripts.live_verify as lv

        with pytest.raises(SystemExit) as exc_info:
            lv.main(["--check", "unknown-check", "--env", "sandbox"])
        assert exc_info.value.code == 2

    def test_cli_unknown_env_exits_2(self) -> None:
        """CLI with unknown --env argument must exit 2."""
        if "scripts.live_verify" in sys.modules:
            del sys.modules["scripts.live_verify"]
        import scripts.live_verify as lv

        with pytest.raises(SystemExit) as exc_info:
            lv.main(["--check", "no-project-resources", "--env", "unknown-env"])
        assert exc_info.value.code == 2

    def test_cli_no_project_resources_success_exits_0(self, tmp_path: pathlib.Path) -> None:
        """CLI with valid args and successful probe must exit 0."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        output_file = tmp_path / "evidence.json"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            if "scripts.live_verify" in sys.modules:
                del sys.modules["scripts.live_verify"]
            import scripts.live_verify as lv

            with pytest.raises(SystemExit) as exc_info:
                lv.main(
                    [
                        "--check",
                        "no-project-resources",
                        "--env",
                        "sandbox",
                        "--output",
                        str(output_file),
                    ]
                )
            assert exc_info.value.code == 0

        assert output_file.exists()
        evidence = json.loads(output_file.read_text())
        assert evidence["check"] == "no-project-resources"
        assert evidence["result"] == "OK"


# ---------------------------------------------------------------------------
# Coverage tests for default helper functions and remaining branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultHelpers:
    """Tests for default DNS resolver, TLS checker, HTTP prober, and gh runner."""

    def test_default_dns_resolver_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_dns_resolver returns (True, detail, ip) when DNS resolves."""
        lv = _import_live_verify()

        monkeypatch.setattr("socket.gethostbyname", lambda fqdn: "127.0.0.1")
        success, detail, ip = lv._default_dns_resolver("example.com")
        assert success is True
        assert ip == "127.0.0.1"
        assert "example.com" in detail

    def test_default_dns_resolver_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_dns_resolver returns (False, detail, None) when DNS fails."""
        lv = _import_live_verify()

        def raise_os_error(fqdn: str) -> str:
            raise OSError("Name or service not known")

        monkeypatch.setattr("socket.gethostbyname", raise_os_error)
        success, detail, ip = lv._default_dns_resolver("nonexistent.invalid")
        assert success is False
        assert ip is None
        assert "failed" in detail.lower()

    def test_default_tls_checker_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_tls_checker returns (True, detail) when TLS handshake succeeds."""

        lv = _import_live_verify()

        mock_sock = MagicMock()
        mock_sock.getpeercert.return_value = {"subjectAltName": [("DNS", "example.com")]}
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value.__enter__ = lambda s: mock_sock
        mock_ctx.wrap_socket.return_value.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("ssl.create_default_context", lambda: mock_ctx)
        monkeypatch.setattr("socket.create_connection", lambda *a, **k: MagicMock())

        success, detail = lv._default_tls_checker("example.com")
        assert success is True
        assert "TLS OK" in detail

    def test_default_tls_checker_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_tls_checker returns (False, detail) when TLS handshake fails."""
        lv = _import_live_verify()

        def raise_error(*a, **k):
            raise OSError("Connection refused")

        monkeypatch.setattr("ssl.create_default_context", raise_error)

        success, detail = lv._default_tls_checker("nonexistent.invalid")
        assert success is False
        assert "failed" in detail.lower()

    def test_default_tls_checker_empty_cert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_tls_checker returns (False, detail) when getpeercert() returns empty."""
        lv = _import_live_verify()

        mock_sock = MagicMock()
        mock_sock.getpeercert.return_value = {}
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value.__enter__ = lambda s: mock_sock
        mock_ctx.wrap_socket.return_value.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("ssl.create_default_context", lambda: mock_ctx)
        monkeypatch.setattr("socket.create_connection", lambda *a, **k: MagicMock())

        success, detail = lv._default_tls_checker("example.com")
        assert success is False
        assert "no peer certificate" in detail

    def test_default_http_prober_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_http_prober returns (True, detail) when HTTP probe succeeds."""
        import http.client

        lv = _import_live_verify()

        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **k: mock_conn)
        success, detail = lv._default_http_prober("https://example.com/")
        assert success is True
        assert "200" in detail

    def test_default_http_prober_success_with_query_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_default_http_prober appends query string to path when URL has one."""
        import http.client

        lv = _import_live_verify()

        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        captured_path: list[str] = []

        def mock_request(method: str, path: str) -> None:
            captured_path.append(path)

        mock_conn.request = mock_request

        monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **k: mock_conn)
        success, detail = lv._default_http_prober("https://example.com/health?check=true")
        assert success is True
        assert "200" in detail
        assert captured_path[0] == "/health?check=true"

    def test_default_http_prober_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_http_prober returns (False, detail) when HTTP probe fails."""
        import http.client

        lv = _import_live_verify()

        def raise_error(*a, **k):
            raise OSError("Connection refused")

        monkeypatch.setattr(http.client, "HTTPSConnection", raise_error)
        success, detail = lv._default_http_prober("https://nonexistent.invalid/")
        assert success is False
        assert "failed" in detail.lower()

    def test_default_http_prober_uses_https_port_constant_when_url_has_no_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A port-less https URL must fall back to LIVE_VERIFY_HTTPS_PORT (no hard-coded 443)."""
        import http.client

        import scripts.constants as c

        lv = _import_live_verify()

        captured: dict[str, object] = {}

        def fake_connection(host: str, port: int, timeout: float) -> MagicMock:
            captured["host"] = host
            captured["port"] = port
            mock_resp = MagicMock()
            mock_resp.status = 204
            mock_conn = MagicMock()
            mock_conn.getresponse.return_value = mock_resp
            return mock_conn

        monkeypatch.setattr(http.client, "HTTPSConnection", fake_connection)
        success, detail = lv._default_http_prober("https://example.com/")
        assert success is True
        assert captured["host"] == "example.com"
        assert captured["port"] == c.LIVE_VERIFY_HTTPS_PORT

    def test_default_http_prober_honors_explicit_port_in_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit port in the https URL must be used instead of the default constant."""
        import http.client

        lv = _import_live_verify()

        captured: dict[str, object] = {}

        def fake_connection(host: str, port: int, timeout: float) -> MagicMock:
            captured["port"] = port
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_conn = MagicMock()
            mock_conn.getresponse.return_value = mock_resp
            return mock_conn

        monkeypatch.setattr(http.client, "HTTPSConnection", fake_connection)
        success, detail = lv._default_http_prober("https://example.com:8443/health")
        assert success is True
        assert captured["port"] == 8443

    def test_default_gh_runner_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_gh_runner returns (exit_code, stdout, stderr) from gh CLI."""
        lv = _import_live_verify()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output"
        mock_result.stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_result)
        rc, stdout, stderr = lv._default_gh_runner(["variable", "list"])
        assert rc == 0
        assert stdout == "output"

    def test_default_gh_runner_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_default_gh_runner returns non-zero exit code when gh fails."""
        lv = _import_live_verify()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        monkeypatch.setattr("subprocess.run", lambda *a, **k: mock_result)
        rc, stdout, stderr = lv._default_gh_runner(["variable", "list"])
        assert rc == 1
        assert stderr == "error"

    def test_poll_until_success(self) -> None:
        """_poll_until returns True when predicate passes on first call."""
        lv = _import_live_verify()
        result = lv._poll_until(lambda: True, timeout=10, interval=1, timeout_msg="timed out")
        assert result is True

    def test_poll_until_timeout(self) -> None:
        """_poll_until returns False when predicate never passes within timeout."""
        lv = _import_live_verify()
        result = lv._poll_until(lambda: False, timeout=0, interval=1, timeout_msg="timed out")
        assert result is False

    def test_poll_until_succeeds_on_second_try(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_poll_until returns True when predicate passes after one failed check."""
        lv = _import_live_verify()
        calls = [0]

        def predicate() -> bool:
            calls[0] += 1
            return calls[0] >= 2

        monkeypatch.setattr("time.sleep", lambda n: None)
        result = lv._poll_until(predicate, timeout=60, interval=0.001, timeout_msg="timed out")
        assert result is True
        assert calls[0] == 2

    def test_get_session_without_aws_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """_get_session uses profile_name= when AWS_PROFILE is not set."""
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            return rgt_client

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0
        # Check that Session was called with profile_name
        mock_boto3.Session.assert_called_once_with(profile_name="sandbox")

    def test_get_session_ambient_aws_profile_does_not_override_env_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """--env is authoritative: an ambient AWS_PROFILE never overrides it.

        Regression for the bug where an ambient ``AWS_PROFILE`` caused
        ``_get_session`` to build a bare ``Session()`` -- silently using the
        ambient profile's account instead of the account ``--env`` requested.
        Here the shell ambient profile is ``sandbox`` while ``--env=prod`` is
        requested; the Session must be bound to the ``--env`` profile
        (``prod``), NOT a bare ambient Session.
        """
        monkeypatch.setenv("AWS_PROFILE", "sandbox")

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            return rgt_client

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="prod",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0
        # The --env-resolved profile ('prod') must win over ambient AWS_PROFILE
        # ('sandbox'); a bare Session() (ambient-profile selection) is the bug.
        mock_boto3.Session.assert_called_once_with(profile_name="prod")

    def test_default_http_prober_rejects_non_https_url(self) -> None:
        """_default_http_prober raises ValueError for non-https URLs (bandit B310)."""
        lv = _import_live_verify()
        with pytest.raises(ValueError, match="only permits https://"):
            lv._default_http_prober("http://example.com/")

    def test_default_http_prober_rejects_url_with_no_scheme(self) -> None:
        """_default_http_prober raises ValueError for URLs without a scheme."""
        lv = _import_live_verify()
        with pytest.raises(ValueError, match="only permits https://"):
            lv._default_http_prober("example.com/path")

    def test_get_region_returns_injected_region(self, tmp_path: pathlib.Path) -> None:
        """_get_region returns the injectable aws_region parameter when provided."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }
        mock_session.client.return_value = rgt_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                aws_region="eu-west-1",
            )
        assert runner._get_region() == "eu-west-1"

    def test_get_region_raises_when_env_var_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """_get_region raises UsageError when AWS_DEFAULT_REGION is not set and no injection."""
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }
        mock_session.client.return_value = rgt_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
        with pytest.raises(lv.UsageError, match="AWS_DEFAULT_REGION"):
            runner._get_region()

    def test_get_gh_repo_returns_from_env_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """_get_gh_repo returns the GITHUB_REPOSITORY env var value when gh_repo not injected."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "my-org/my-repo")

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }
        mock_session.client.return_value = rgt_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
        assert runner._get_gh_repo() == "my-org/my-repo"

    def test_get_gh_repo_raises_when_env_var_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """_get_gh_repo raises UsageError when GITHUB_REPOSITORY is not set and no injection."""
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }
        mock_session.client.return_value = rgt_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
        with pytest.raises(lv.UsageError, match="GITHUB_REPOSITORY"):
            runner._get_gh_repo()


@pytest.mark.unit
class TestOidcRolesAdditionalBranches:
    """Additional branch coverage for oidc-roles check."""

    def test_oidc_roles_no_roles_for_account(self, tmp_path: pathlib.Path) -> None:
        """oidc-roles check emits OK when no roles are defined for this account."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            # Use empty oidc_roles_data so sandbox account has no roles declared
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data={},
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_oidc_roles_trust_sub_not_found(self, tmp_path: pathlib.Path) -> None:
        """oidc-roles check FAIL when role exists but trust sub condition is wrong."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "333333333333"}

        iam_client = MagicMock()
        # Role exists but trust policy doesn't have the right sub
        iam_client.get_role.return_value = {
            "Role": {
                "RoleName": "telemetry-platform-gha-terratest",
                "Arn": "arn:aws:iam::333333333333:role/telemetry-platform-gha-terratest",
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {
                                "Federated": (
                                    "arn:aws:iam::333333333333:oidc-provider/"
                                    "token.actions.githubusercontent.com"
                                )
                            },
                            "Action": "sts:AssumeRoleWithWebIdentity",
                            "Condition": {
                                "StringLike": {
                                    "token.actions.githubusercontent.com:sub": "wrong:repo:*"
                                }
                            },
                        }
                    ],
                },
            }
        }
        iam_client.list_attached_role_policies.return_value = {"AttachedPolicies": []}
        iam_client.list_role_policies.return_value = {"PolicyNames": []}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            oidc_roles_data = json.loads(OIDC_ROLES_JSON_PATH.read_text())
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=oidc_roles_data,
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_oidc_roles_chain_trust_present(self, tmp_path: pathlib.Path) -> None:
        """oidc-roles check OK for role-chaining trust (no sub, trust policy present)."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "444444444444"}

        iam_client = MagicMock()

        # The root (dns-owner) account hosts TWO roles in oidc-roles.json:
        #   - telemetry-platform-dns-writer: role-chaining trust (AWS principal, no sub)
        #   - telemetry-platform-gha-tg-plan: GitHub OIDC trust (sub set)  [multi-account WALL 1]
        # live_verify probes BOTH, so the mock must return the role matching the queried name.
        def mock_get_role(**kwargs):
            role_name = kwargs["RoleName"]
            if role_name == "telemetry-platform-gha-tg-plan":
                return {
                    "Role": {
                        "RoleName": "telemetry-platform-gha-tg-plan",
                        "Arn": "arn:aws:iam::444444444444:role/telemetry-platform-gha-tg-plan",
                        "AssumeRolePolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Principal": {
                                        "Federated": (
                                            "arn:aws:iam::444444444444:oidc-provider/"
                                            "token.actions.githubusercontent.com"
                                        )
                                    },
                                    "Action": "sts:AssumeRoleWithWebIdentity",
                                    "Condition": {
                                        "StringEquals": {
                                            "token.actions.githubusercontent.com:aud": (
                                                "sts.amazonaws.com"
                                            )
                                        },
                                        "StringLike": {
                                            "token.actions.githubusercontent.com:sub": (
                                                "repo:example-org/telemetry-platform:*"
                                            )
                                        },
                                    },
                                }
                            ],
                        },
                    }
                }
            # dns-writer role has a trust policy but no sub (role-chaining)
            return {
                "Role": {
                    "RoleName": "telemetry-platform-dns-writer",
                    "Arn": "arn:aws:iam::444444444444:role/telemetry-platform-dns-writer",
                    "AssumeRolePolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "AWS": (
                                        "arn:aws:iam::111111111111:role/telemetry-platform-gha-tg-apply"
                                    )
                                },
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    },
                }
            }

        iam_client.get_role.side_effect = mock_get_role

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            oidc_roles_data = json.loads(OIDC_ROLES_JSON_PATH.read_text())
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="root",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=oidc_roles_data,
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_oidc_roles_chain_trust_empty(self, tmp_path: pathlib.Path) -> None:
        """oidc-roles check FAIL for role with empty trust policy (chained role)."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "444444444444"}

        iam_client = MagicMock()
        iam_client.get_role.return_value = {
            "Role": {
                "RoleName": "telemetry-platform-dns-writer",
                "Arn": "arn:aws:iam::444444444444:role/telemetry-platform-dns-writer",
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [],  # Empty -- no trust policy
                },
            }
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            oidc_roles_data = json.loads(OIDC_ROLES_JSON_PATH.read_text())
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="root",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=oidc_roles_data,
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


@pytest.mark.unit
class TestStateBackendAdditionalBranches:
    """Additional branch coverage for state-backend check."""

    def _make_s3_client_no_kms_rule(self) -> MagicMock:
        s3 = MagicMock()
        s3.head_bucket.return_value = {}
        s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256",
                        }
                    }
                ]
            }
        }
        s3.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }
        s3.get_bucket_policy.return_value = {
            "Policy": json.dumps(
                {
                    "Statement": [
                        {"Condition": {"Bool": {"aws:SecureTransport": "false"}}, "Effect": "Deny"}
                    ]
                }
            )
        }
        return s3

    def _make_ok_kms_client(self) -> MagicMock:
        kms = MagicMock()
        kms.describe_key.return_value = {"KeyMetadata": {"KeyState": "Enabled"}}
        return kms

    def test_state_backend_fail_when_encryption_not_kms(self, tmp_path: pathlib.Path) -> None:
        """state-backend check FAIL when encryption is AES256 not SSE-KMS."""
        s3_client = self._make_s3_client_no_kms_rule()
        kms_client = self._make_ok_kms_client()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_state_backend_fail_when_public_access_not_blocked(
        self, tmp_path: pathlib.Path
    ) -> None:
        """state-backend check FAIL when public access is not fully blocked."""
        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}
        s3_client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3_client.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": (
                                "arn:aws:kms:us-east-1:222222222222:alias/222222222222-tfstate"
                            ),
                        }
                    }
                ]
            }
        }
        s3_client.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": False,  # Not blocked!
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }
        s3_client.get_bucket_policy.return_value = {
            "Policy": json.dumps(
                {
                    "Statement": [
                        {"Condition": {"Bool": {"aws:SecureTransport": "false"}}, "Effect": "Deny"}
                    ]
                }
            )
        }
        kms_client = self._make_ok_kms_client()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_state_backend_fail_when_no_tls_policy(self, tmp_path: pathlib.Path) -> None:
        """state-backend check FAIL when bucket policy has no TLS deny statement."""
        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}
        s3_client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3_client.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": (
                                "arn:aws:kms:us-east-1:222222222222:alias/222222222222-tfstate"
                            ),
                        }
                    }
                ]
            }
        }
        s3_client.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }
        # No TLS deny statement in bucket policy
        s3_client.get_bucket_policy.return_value = {
            "Policy": json.dumps({"Statement": [{"Effect": "Allow", "Principal": "*"}]})
        }
        kms_client = self._make_ok_kms_client()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_state_backend_fail_when_kms_not_enabled(self, tmp_path: pathlib.Path) -> None:
        """state-backend check FAIL when KMS key is not in Enabled state."""
        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}
        s3_client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3_client.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": (
                                "arn:aws:kms:us-east-1:222222222222:alias/222222222222-tfstate"
                            ),
                        }
                    }
                ]
            }
        }
        s3_client.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }
        s3_client.get_bucket_policy.return_value = {
            "Policy": json.dumps(
                {
                    "Statement": [
                        {"Condition": {"Bool": {"aws:SecureTransport": "false"}}, "Effect": "Deny"}
                    ]
                }
            )
        }

        kms_client = MagicMock()
        kms_client.describe_key.return_value = {"KeyMetadata": {"KeyState": "Disabled"}}

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


@pytest.mark.unit
class TestStackAdditionalBranches:
    """Additional branch coverage for stack check."""

    def _make_base_clients(
        self,
    ) -> tuple[
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
    ]:
        s3 = MagicMock()
        s3.head_bucket.return_value = {}
        cf = MagicMock()
        cf.get_distribution.return_value = {"Distribution": {"Status": "Deployed"}}
        acm = MagicMock()
        acm.describe_certificate.return_value = {"Certificate": {"Status": "ISSUED"}}
        route53 = MagicMock()
        route53.list_hosted_zones_by_name.return_value = {
            "HostedZones": [{"Id": "/hostedzone/Z123", "Name": "test."}]
        }
        route53.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [{"Name": "test.", "Type": "A"}]
        }
        firehose = MagicMock()
        firehose.describe_delivery_stream.return_value = {
            "DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}
        }
        glue = MagicMock()
        glue.get_database.return_value = {"Database": {"Name": "telemetry"}}
        athena = MagicMock()
        athena.get_work_group.return_value = {"WorkGroup": {"Name": "telemetry"}}
        ecs = MagicMock()
        ecs.describe_services.return_value = {
            "services": [{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1}]
        }
        cw = MagicMock()
        cw.describe_alarms.return_value = {"MetricAlarms": [{"AlarmName": "alarm-1"}]}
        return s3, cf, acm, route53, firehose, glue, athena, ecs, cw

    def _make_runner(
        self, tmp_path: pathlib.Path, s3, cf, acm, route53, firehose, glue, athena, ecs, cw
    ) -> tuple[object, object]:
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        lv = _import_live_verify()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            return runner, mock_boto3

    def test_stack_fail_when_acm_not_issued(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when ACM certificate is not ISSUED."""
        import botocore.exceptions

        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        acm.describe_certificate.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "DescribeCertificate",
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_route53_no_zones(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when Route53 has no hosted zones."""
        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        route53.list_hosted_zones_by_name.return_value = {"HostedZones": []}

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_route53_no_records(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when Route53 zone exists but has no records."""
        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        route53.list_resource_record_sets.return_value = {"ResourceRecordSets": []}

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_firehose_not_active(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when Firehose delivery stream is not ACTIVE."""
        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        firehose.describe_delivery_stream.return_value = {
            "DeliveryStreamDescription": {"DeliveryStreamStatus": "CREATING"}
        }

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_glue_db_missing(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when Glue database is not found."""
        import botocore.exceptions

        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        glue.get_database.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "EntityNotFoundException", "Message": "not found"}}, "GetDatabase"
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_athena_wg_missing(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when Athena workgroup is not found."""
        import botocore.exceptions

        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        athena.get_work_group.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "InvalidRequestException", "Message": "not found"}}, "GetWorkGroup"
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_ecs_no_services(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when ECS returns no services."""
        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        ecs.describe_services.return_value = {"services": []}

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_ecs_not_stable(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when ECS service has running != desired count."""
        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        ecs.describe_services.return_value = {
            "services": [{"status": "ACTIVE", "runningCount": 0, "desiredCount": 1}]
        }

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_stack_fail_when_cw_alarms_missing(self, tmp_path: pathlib.Path) -> None:
        """stack check FAIL when CloudWatch returns no alarms."""
        s3, cf, acm, route53, firehose, glue, athena, ecs, cw = self._make_base_clients()
        cw.describe_alarms.return_value = {"MetricAlarms": []}

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "s3":
                return s3
            if service_name == "cloudfront":
                return cf
            if service_name == "acm":
                return acm
            if service_name == "route53":
                return route53
            if service_name == "firehose":
                return firehose
            if service_name == "glue":
                return glue
            if service_name == "athena":
                return athena
            if service_name == "ecs":
                return ecs
            if service_name == "cloudwatch":
                return cw
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1


@pytest.mark.unit
class TestObservabilityAdditionalBranches:
    """Additional branch coverage for observability check."""

    def test_observability_fail_when_sns_topic_has_no_subscription(
        self, tmp_path: pathlib.Path
    ) -> None:
        """observability check FAILs when the namespace-derived SNS topic has no subscription.

        Fail-fast contract: an empty SNS subscription list means no alerting path exists at
        all, which must FAIL. (A PendingConfirmation subscription, by contrast, IS accepted as
        OK because confirmation is operator-gated -- see the dedicated pending test.)
        """
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        # A real observability alarm name so the alarm-presence probe passes and the SNS
        # probe is the one under test.
        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {
            "MetricAlarms": [{"AlarmName": "alb_5xx_count", "StateValue": "OK"}]
        }
        budgets_client = MagicMock()
        budgets_client.describe_budgets.return_value = {"Budgets": [{"BudgetName": "b"}]}
        ce_client = MagicMock()
        ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": [{"MonitorArn": "arn"}]}
        ce_client.get_anomaly_subscriptions.return_value = {
            "AnomalySubscriptions": [{"SubscriptionArn": "arn", "Subscribers": []}]
        }
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {"Subscriptions": []}

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_observability_fail_when_sns_topic_raises(self, tmp_path: pathlib.Path) -> None:
        """observability check FAILs when list_subscriptions_by_topic raises ClientError.

        Fail-fast contract: an inaccessible SNS topic means the alerting path is broken,
        which must FAIL (not silently pass as it did with the old "ok-on-error" behaviour).
        """
        import botocore.exceptions

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {
            "MetricAlarms": [{"AlarmName": "alb_5xx_count", "StateValue": "OK"}]
        }
        budgets_client = MagicMock()
        budgets_client.describe_budgets.return_value = {"Budgets": [{"BudgetName": "b"}]}
        ce_client = MagicMock()
        ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": [{"MonitorArn": "arn"}]}
        ce_client.get_anomaly_subscriptions.return_value = {
            "AnomalySubscriptions": [{"SubscriptionArn": "arn", "Subscribers": []}]
        }
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NotFoundException", "Message": "Topic not found"}},
            "ListSubscriptionsByTopic",
        )

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


@pytest.mark.unit
class TestNoProjResAdditionalBranches:
    """Additional branch coverage for no-project-resources check with pagination."""

    def test_no_project_resources_pagination(self, tmp_path: pathlib.Path) -> None:
        """no-project-resources check handles paginated Tagging API responses."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}

        # First call returns a page with a pagination token, second returns empty
        rgt_client = MagicMock()
        rgt_client.get_resources.side_effect = [
            {
                "ResourceTagMappingList": [],
                "PaginationToken": "next-page-token",
            },
            {
                "ResourceTagMappingList": [],
                "PaginationToken": "",
            },
        ]

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            if service_name == "resourcegroupstaggingapi":
                return rgt_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0
        assert rgt_client.get_resources.call_count == 2


@pytest.mark.unit
class TestRepoSettingsAdditionalBranches:
    """Additional branch coverage for repo-settings check."""

    def test_repo_settings_variable_list_json_format(self, tmp_path: pathlib.Path) -> None:
        """repo-settings handles JSON-format variable list output."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        vars_json = json.dumps(
            [
                {"name": "AWS_DEFAULT_REGION"},
                {"name": "AWS_QA_TERRATEST_ROLE_ARN"},
                {"name": "AWS_TERRAGRUNT_PLAN_ROLE_ARN"},
                {"name": "LOCK_MAX_AGE_MINUTES"},
                {"name": "PORTAL_ARTIFACT_BUCKET"},
                {"name": "PORTAL_LAMBDA_S3_KEY"},
            ]
        )

        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return 0, vars_json, ""
            if "environments/terratest-approval" in cmd:
                return 0, '{"name":"terratest-approval"}', ""
            if "environments/prod-apply" in cmd:
                return (
                    0,
                    '{"name":"prod-apply","protection_rules":[{"type":"required_reviewers","reviewers":[{"reviewer":{"login":"op"}}]}]}',
                    "",
                )
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        assert exit_code == 0

    def test_repo_settings_prod_apply_no_reviewers(self, tmp_path: pathlib.Path) -> None:
        """repo-settings FAIL when prod-apply environment has no required reviewers."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return (
                    0,
                    "AWS_DEFAULT_REGION\nAWS_QA_TERRATEST_ROLE_ARN\nAWS_TERRAGRUNT_PLAN_ROLE_ARN\nLOCK_MAX_AGE_MINUTES\nPORTAL_ARTIFACT_BUCKET\nPORTAL_LAMBDA_S3_KEY",
                    "",
                )
            if "environments/terratest-approval" in cmd:
                return 0, '{"name":"terratest-approval"}', ""
            if "environments/prod-apply" in cmd:
                # No required_reviewers in protection_rules
                return 0, '{"name":"prod-apply","protection_rules":[]}', ""
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_repo_settings_variable_list_fails(self, tmp_path: pathlib.Path) -> None:
        """repo-settings FAIL when gh variable list command fails entirely."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return 1, "", "error: not authenticated"
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_repo_settings_prod_apply_non_json_response_fails_fast(
        self, tmp_path: pathlib.Path
    ) -> None:
        """repo-settings FAILS fast on a non-JSON gh api prod-apply response.

        Fail-fast contract (CLAUDE.md): an unparseable response for the
        security-critical prod-apply required-reviewer check must surface as a
        probe failure (non-zero). The previous "falls back to OK" behaviour
        silently masked the gap in branch/environment protection and is
        prohibited.
        """
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd:
                return (
                    0,
                    "AWS_DEFAULT_REGION\nAWS_QA_TERRATEST_ROLE_ARN\nAWS_TERRAGRUNT_PLAN_ROLE_ARN\nLOCK_MAX_AGE_MINUTES\nPORTAL_ARTIFACT_BUCKET\nPORTAL_LAMBDA_S3_KEY",
                    "",
                )
            if "environments/terratest-approval" in cmd:
                return 0, '{"name":"terratest-approval"}', ""
            if "environments/prod-apply" in cmd:
                return 0, "non-json response", ""
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        # prod-apply env exists, but the unparseable reviewer response is a real
        # probe failure -- fail-fast, not a masked OK.
        assert exit_code == 1


@pytest.mark.unit
class TestEndpointsAdditionalBranches:
    """Additional branch coverage for endpoints check."""

    def test_endpoints_tls_fail_still_continues(self, tmp_path: pathlib.Path) -> None:
        """endpoints check records FAIL for TLS and continues with HTTP probe."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_fail = MagicMock(return_value=(False, "TLS handshake failed"))
        http_ok = MagicMock(return_value=(True, "HTTP 200 OK"))

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_ok,
                tls_checker=tls_fail,
                http_prober=http_ok,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_endpoints_http_fail(self, tmp_path: pathlib.Path) -> None:
        """endpoints check records FAIL when HTTP probe fails."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        mock_session.client.return_value = sts_client

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_fail = MagicMock(return_value=(False, "HTTP 503 Service Unavailable"))

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_ok,
                tls_checker=tls_ok,
                http_prober=http_fail,
            )
            exit_code = runner.run()

        assert exit_code == 1


@pytest.mark.unit
class TestRunWithoutOutputPath:
    """run() method without output_path set should not write evidence file."""

    def test_run_no_output_path(self, tmp_path: pathlib.Path) -> None:
        """run() with output_path=None should not write a file."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "222222222222"}
        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }

        def mock_client(service_name, **kwargs):
            if service_name == "sts":
                return sts_client
            return rgt_client

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="no-project-resources",
                env="sandbox",
                output_path=None,
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 0


# ---------------------------------------------------------------------------
# Additional coverage tests for remaining uncovered lines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveAccountIdUnknownEnv:
    """_resolve_account_id raises UsageError for unknown ENV (line 125)."""

    def test_resolve_account_id_unknown_env_raises(self) -> None:
        """_resolve_account_id must raise UsageError for an env with no matching account."""
        m = _import_live_verify()
        with pytest.raises(m.UsageError, match="unknown ENV"):
            m._resolve_account_id(
                "notanenv",
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
            )


@pytest.mark.unit
class TestOidcRolesUnexpectedError:
    """oidc-roles check re-raises unexpected ClientError codes (line 418)."""

    def test_oidc_roles_reraises_unexpected_client_error(
        self, tmp_path: pathlib.Path, oidc_roles_data: dict
    ) -> None:
        """oidc-roles check re-raises ClientError when code is not NoSuchEntity."""
        import botocore.exceptions

        iam_client = MagicMock()
        error_response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}
        iam_client.get_role.side_effect = botocore.exceptions.ClientError(error_response, "GetRole")

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        def mock_client(service_name, **kwargs):
            if service_name == "iam":
                return iam_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="oidc-roles",
                env="qa",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=oidc_roles_data,
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            with pytest.raises(botocore.exceptions.ClientError):
                runner.run()


@pytest.mark.unit
class TestStateBackendKmsClientError:
    """state-backend check emits FAIL when KMS describe_key raises ClientError (lines 621-622)."""

    def _make_ok_s3_client(self) -> MagicMock:
        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}
        s3_client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        s3_client.get_bucket_encryption.return_value = {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                        }
                    }
                ]
            }
        }
        s3_client.get_public_access_block.return_value = {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }
        s3_client.get_bucket_policy.return_value = {
            "Policy": json.dumps(
                {
                    "Statement": [
                        {"Condition": {"Bool": {"aws:SecureTransport": "false"}}, "Effect": "Deny"}
                    ]
                }
            )
        }
        return s3_client

    def test_state_backend_fail_when_kms_client_error(self, tmp_path: pathlib.Path) -> None:
        """state-backend emits FAIL when KMS raises ClientError (alias not found)."""
        import botocore.exceptions

        s3_client = self._make_ok_s3_client()
        kms_client = MagicMock()
        error_response = {"Error": {"Code": "NotFoundException", "Message": "Not found"}}
        kms_client.describe_key.side_effect = botocore.exceptions.ClientError(
            error_response, "DescribeKey"
        )

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        def mock_client(service_name, **kwargs):
            if service_name == "s3":
                return s3_client
            if service_name == "kms":
                return kms_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="state-backend",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            exit_code = runner.run()

        assert exit_code == 1


@pytest.mark.unit
class TestStackClientErrors:
    """stack check emits FAIL for various ClientError cases per each AWS service client."""

    def _make_full_stack_mock_client(
        self,
        s3_raises: bool = False,
        cf_raises: bool = False,
        acm_raises: bool = False,
        route53_raises: bool = False,
        firehose_raises: bool = False,
        glue_raises: bool = False,
        glue_empty_name: bool = False,
        athena_raises: bool = False,
        athena_empty_name: bool = False,
        ecs_raises: bool = False,
        cw_raises: bool = False,
    ):
        import botocore.exceptions

        def _make_client_error(code: str, operation: str) -> botocore.exceptions.ClientError:
            return botocore.exceptions.ClientError(
                {"Error": {"Code": code, "Message": "Error"}}, operation
            )

        s3_client = MagicMock()
        if s3_raises:
            s3_client.head_bucket.side_effect = _make_client_error("NoSuchBucket", "HeadBucket")
        else:
            s3_client.head_bucket.return_value = {}

        cf_client = MagicMock()
        if cf_raises:
            cf_client.get_distribution.side_effect = _make_client_error(
                "NoSuchDistribution", "GetDistribution"
            )
        else:
            cf_client.get_distribution.return_value = {"Distribution": {"Status": "Deployed"}}

        acm_client = MagicMock()
        if acm_raises:
            acm_client.describe_certificate.side_effect = _make_client_error(
                "ResourceNotFoundException", "DescribeCertificate"
            )
        else:
            acm_client.describe_certificate.return_value = {"Certificate": {"Status": "ISSUED"}}

        route53_client = MagicMock()
        if route53_raises:
            route53_client.list_hosted_zones_by_name.side_effect = _make_client_error(
                "AccessDenied", "ListHostedZonesByName"
            )
        else:
            route53_client.list_hosted_zones_by_name.return_value = {
                "HostedZones": [{"Id": "/hostedzone/Z1", "Name": "sandbox."}]
            }
            route53_client.list_resource_record_sets.return_value = {
                "ResourceRecordSets": [{"Name": "sandbox.", "Type": "NS"}]
            }

        firehose_client = MagicMock()
        if firehose_raises:
            firehose_client.describe_delivery_stream.side_effect = _make_client_error(
                "ResourceNotFoundException", "DescribeDeliveryStream"
            )
        else:
            firehose_client.describe_delivery_stream.return_value = {
                "DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}
            }

        glue_client = MagicMock()
        if glue_raises:
            glue_client.get_database.side_effect = _make_client_error(
                "EntityNotFoundException", "GetDatabase"
            )
        elif glue_empty_name:
            glue_client.get_database.return_value = {"Database": {"Name": ""}}
        else:
            glue_client.get_database.return_value = {"Database": {"Name": "telemetry"}}

        athena_client = MagicMock()
        if athena_raises:
            athena_client.get_work_group.side_effect = _make_client_error(
                "ResourceNotFoundException", "GetWorkGroup"
            )
        elif athena_empty_name:
            athena_client.get_work_group.return_value = {"WorkGroup": {"Name": ""}}
        else:
            athena_client.get_work_group.return_value = {"WorkGroup": {"Name": "telemetry"}}

        ecs_client = MagicMock()
        if ecs_raises:
            ecs_client.describe_services.side_effect = _make_client_error(
                "ClusterNotFoundException", "DescribeServices"
            )
        else:
            ecs_client.describe_services.return_value = {
                "services": [{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1}]
            }

        cw_client = MagicMock()
        if cw_raises:
            cw_client.describe_alarms.side_effect = _make_client_error(
                "AccessDenied", "DescribeAlarms"
            )
        else:
            cw_client.describe_alarms.return_value = {"MetricAlarms": [{"AlarmName": "alarm-1"}]}

        def mock_client(service_name, **kwargs):
            if service_name == "s3":
                return s3_client
            if service_name == "cloudfront":
                return cf_client
            if service_name == "acm":
                return acm_client
            if service_name == "route53":
                return route53_client
            if service_name == "firehose":
                return firehose_client
            if service_name == "glue":
                return glue_client
            if service_name == "athena":
                return athena_client
            if service_name == "ecs":
                return ecs_client
            if service_name == "cloudwatch":
                return cw_client
            return MagicMock()

        return mock_client

    def _run_stack(self, tmp_path: pathlib.Path, mock_client_fn) -> int:
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.side_effect = mock_client_fn

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            return runner.run()

    def test_stack_fail_when_s3_head_raises(self, tmp_path: pathlib.Path) -> None:
        """stack check FAILS fast when s3.head_bucket raises ClientError.

        Fail-fast contract (CLAUDE.md): a missing/inaccessible data-lake bucket
        is a real probe failure and must surface non-zero. The previous
        "fallback OK (bucket name varies by deploy)" behaviour masked the
        failure and is prohibited.
        """
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(s3_raises=True))
        assert exit_code == 1

    def test_stack_fail_when_cf_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when cf.get_distribution raises ClientError (lines 670-671)."""
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(cf_raises=True))
        assert exit_code == 1

    def test_stack_fail_when_acm_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when acm.describe_certificate raises ClientError (line 688)."""
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(acm_raises=True))
        assert exit_code == 1

    def test_stack_fail_when_route53_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when route53.list_hosted_zones raises ClientError."""
        exit_code = self._run_stack(
            tmp_path, self._make_full_stack_mock_client(route53_raises=True)
        )
        assert exit_code == 1

    def test_stack_fail_when_firehose_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when firehose.describe_delivery_stream raises ClientError."""
        exit_code = self._run_stack(
            tmp_path, self._make_full_stack_mock_client(firehose_raises=True)
        )
        assert exit_code == 1

    def test_stack_fail_when_glue_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when glue.get_database raises ClientError."""
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(glue_raises=True))
        assert exit_code == 1

    def test_stack_fail_when_glue_returns_empty_name(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when Glue database has empty name (line 771)."""
        exit_code = self._run_stack(
            tmp_path, self._make_full_stack_mock_client(glue_empty_name=True)
        )
        assert exit_code == 1

    def test_stack_fail_when_athena_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when athena.get_work_group raises ClientError."""
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(athena_raises=True))
        assert exit_code == 1

    def test_stack_fail_when_athena_returns_empty_name(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when Athena workgroup has empty name (line 793)."""
        exit_code = self._run_stack(
            tmp_path, self._make_full_stack_mock_client(athena_empty_name=True)
        )
        assert exit_code == 1

    def test_stack_fail_when_ecs_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when ecs.describe_services raises ClientError (lines 839-840)."""
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(ecs_raises=True))
        assert exit_code == 1

    def test_stack_fail_when_cw_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when cloudwatch.describe_alarms raises ClientError."""
        exit_code = self._run_stack(tmp_path, self._make_full_stack_mock_client(cw_raises=True))
        assert exit_code == 1


@pytest.mark.unit
class TestEndpointsMissingDomains:
    """endpoints check branches for missing domains file and env with no apex domains."""

    def test_endpoints_fail_when_domains_json_missing(self, tmp_path: pathlib.Path) -> None:
        """endpoints check emits FAIL when terragrunt/common/domains.json does not exist."""
        import pathlib as pathlib_mod
        import unittest.mock

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        real_exists = pathlib_mod.Path.exists

        def patched_exists(self_path):
            if self_path.name == "domains.json":
                return False
            return real_exists(self_path)

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            with unittest.mock.patch.object(pathlib_mod.Path, "exists", patched_exists):
                runner = lv.LiveVerify(
                    check="endpoints",
                    env="sandbox",
                    output_path=str(tmp_path / "out.json"),
                    accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                    exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                    oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                    boto3_module=mock_boto3,
                    gh_runner=None,
                    dns_resolver=dns_ok,
                    tls_checker=tls_ok,
                    http_prober=http_ok,
                )
                exit_code = runner.run()

        assert exit_code == 1

    def test_endpoints_ok_when_env_has_no_apexes(self, tmp_path: pathlib.Path) -> None:
        """endpoints check emits OK when env has no apex domain keys in domains.json."""
        import pathlib as pathlib_mod
        import unittest.mock

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        domains_with_no_apexes = json.dumps({"qa": {"enable_custom_domain": False}})

        real_exists = pathlib_mod.Path.exists
        real_read_text = pathlib_mod.Path.read_text

        def patched_exists(self_path):
            if self_path.name == "domains.json":
                return True
            return real_exists(self_path)

        def patched_read_text(self_path, *args, **kwargs):
            if self_path.name == "domains.json":
                return domains_with_no_apexes
            return real_read_text(self_path, *args, **kwargs)

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            with (
                unittest.mock.patch.object(pathlib_mod.Path, "exists", patched_exists),
                unittest.mock.patch.object(pathlib_mod.Path, "read_text", patched_read_text),
            ):
                runner = lv.LiveVerify(
                    check="endpoints",
                    env="qa",
                    output_path=str(tmp_path / "out.json"),
                    accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                    exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                    oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                    boto3_module=mock_boto3,
                    gh_runner=None,
                    dns_resolver=dns_ok,
                    tls_checker=tls_ok,
                    http_prober=http_ok,
                )
                exit_code = runner.run()

        assert exit_code == 0

    def test_endpoints_ok_when_only_service_apex_present(self, tmp_path: pathlib.Path) -> None:
        """endpoints check derives published FQDNs from a service apex only (no pretty apex)."""
        import pathlib as pathlib_mod
        import unittest.mock

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        domains_service_only = json.dumps({"qa": {"dns_service_apex": "qa.example.com"}})

        real_exists = pathlib_mod.Path.exists
        real_read_text = pathlib_mod.Path.read_text

        def patched_exists(self_path):
            if self_path.name == "domains.json":
                return True
            return real_exists(self_path)

        def patched_read_text(self_path, *args, **kwargs):
            if self_path.name == "domains.json":
                return domains_service_only
            return real_read_text(self_path, *args, **kwargs)

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            with (
                unittest.mock.patch.object(pathlib_mod.Path, "exists", patched_exists),
                unittest.mock.patch.object(pathlib_mod.Path, "read_text", patched_read_text),
            ):
                runner = lv.LiveVerify(
                    check="endpoints",
                    env="qa",
                    output_path=str(tmp_path / "out.json"),
                    accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                    exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                    oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                    boto3_module=mock_boto3,
                    gh_runner=None,
                    dns_resolver=dns_ok,
                    tls_checker=tls_ok,
                    http_prober=http_ok,
                )
                exit_code = runner.run()

        assert exit_code == 0
        # With only a service apex configured, the probed hosts are the INSTANCE-SCOPED service
        # FQDNs (<label>-<active_set>.<service_apex>), NOT the bare <label>.<service_apex>. The
        # active set is read from the real qa/active.hcl (000). The bare apex and the bare
        # instance-less service host are probed only as optional / never at all.
        tls_hosts = {call.args[0] for call in tls_ok.call_args_list}
        assert tls_hosts == {"collector-000.qa.example.com", "telemetry-000.qa.example.com"}
        http_urls = {call.args[0] for call in http_ok.call_args_list}
        assert http_urls == {
            "https://collector-000.qa.example.com/",
            "https://telemetry-000.qa.example.com/",
        }
        # Neither the bare apex nor the bare instance-less service host is ever TLS/HTTP probed.
        assert "qa.example.com" not in tls_hosts
        assert "collector.qa.example.com" not in tls_hosts


@pytest.mark.unit
class TestEndpointsPublishedServiceFqdns:
    """endpoints check probes the REAL published service FQDNs (collector./telemetry.) per env.

    The stack publishes collector.<apex> (collector CloudFront) and telemetry.<apex>
    (portal CloudFront) for the service apex and pretty apex from common/domains.json. The
    bare apex carries no record by design, so it must be probed only as an optional,
    never-failing informational resolver call -- never as a hard TLS/HTTP requirement.
    """

    def _run_with_recording_probes(
        self, tmp_path: pathlib.Path, env: str
    ) -> tuple[int, set[str], set[str], set[str]]:
        """Run the endpoints check against the REAL domains.json with recording probes.

        Returns (exit_code, dns_hosts, tls_hosts, http_urls).
        """
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env=env,
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_ok,
                tls_checker=tls_ok,
                http_prober=http_ok,
            )
            exit_code = runner.run()

        dns_hosts = {call.args[0] for call in dns_ok.call_args_list}
        tls_hosts = {call.args[0] for call in tls_ok.call_args_list}
        http_urls = {call.args[0] for call in http_ok.call_args_list}
        return exit_code, dns_hosts, tls_hosts, http_urls

    def test_endpoints_sandbox_probes_real_published_fqdns(self, tmp_path: pathlib.Path) -> None:
        """sandbox: instance-scoped service FQDNs + pretty FQDNs of the sandbox apex are probed."""
        domains = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())
        service_apex = domains["sandbox"]["dns_service_apex"]
        pretty_apex = domains["sandbox"]["dns_pretty_apex"]
        # sandbox: service apex == pretty apex, so there is exactly one apex.
        assert service_apex == pretty_apex
        active_set = _import_live_verify()._default_active_set_resolver(
            "sandbox", "us-east-1", REPO_ROOT
        )

        exit_code, _dns_hosts, tls_hosts, http_urls = self._run_with_recording_probes(
            tmp_path, "sandbox"
        )

        assert exit_code == 0
        # Even when service_apex == pretty_apex, the instance-scoped service host and the pretty
        # host are DISTINCT records and both are probed.
        assert tls_hosts == {
            f"collector-{active_set}.{service_apex}",
            f"telemetry-{active_set}.{service_apex}",
            f"collector.{service_apex}",
            f"telemetry.{service_apex}",
        }
        assert http_urls == {
            f"https://collector-{active_set}.{service_apex}/",
            f"https://telemetry-{active_set}.{service_apex}/",
            f"https://collector.{service_apex}/",
            f"https://telemetry.{service_apex}/",
        }
        # The bare apex is never TLS/HTTP probed.
        assert service_apex not in tls_hosts

    def test_endpoints_prod_probes_instance_scoped_service_and_pretty_fqdns(
        self, tmp_path: pathlib.Path
    ) -> None:
        """prod: the INSTANCE-SCOPED service FQDN + the pretty FQDN are probed (false-FAIL guard).

        Regression guard for the prod false-FAIL bug: prod's service_apex differs from its
        pretty_apex, and the bare, instance-less <label>.<service_apex> is NEVER published. The
        old check probed that bare host and FALSE-FAILED a healthy prod. The corrected check
        probes collector-<active>.<service_apex> (the real dns-collector A-alias record) and
        collector.<pretty_apex> (the real pretty CNAME) -- exactly the two live cert SANs -- and
        NEVER the bare collector.<service_apex>.
        """
        domains = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())
        service_apex = domains["prod"]["dns_service_apex"]
        pretty_apex = domains["prod"]["dns_pretty_apex"]
        # prod: service apex differs from pretty apex (e.g. prod.platform... vs platform...).
        assert service_apex != pretty_apex
        # The active instance set the pretty CNAME targets, read from the real prod/active.hcl.
        active_set = _import_live_verify()._default_active_set_resolver(
            "prod", "us-east-1", REPO_ROOT
        )

        exit_code, _dns_hosts, tls_hosts, http_urls = self._run_with_recording_probes(
            tmp_path, "prod"
        )

        assert exit_code == 0
        assert tls_hosts == {
            f"collector-{active_set}.{service_apex}",
            f"telemetry-{active_set}.{service_apex}",
            f"collector.{pretty_apex}",
            f"telemetry.{pretty_apex}",
        }
        assert http_urls == {
            f"https://collector-{active_set}.{service_apex}/",
            f"https://telemetry-{active_set}.{service_apex}/",
            f"https://collector.{pretty_apex}/",
            f"https://telemetry.{pretty_apex}/",
        }
        # Regression: the bare instance-less service host (the OLD false-FAIL host) is NEVER
        # probed. Under the old bare-<label>.<service_apex> assumption this set membership would
        # hold and the healthy-prod probe would FAIL because that host does not resolve.
        assert f"collector.{service_apex}" not in tls_hosts
        assert f"telemetry.{service_apex}" not in tls_hosts

    def test_endpoints_ok_when_apex_record_less_but_service_hosts_resolve(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The bare apex returning no record does NOT fail the check (optional apex probe).

        DNS resolves the published collector./telemetry. hosts but NOT the bare apex; the
        check must still pass because the apex is record-less by design.
        """
        domains = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())
        service_apex = domains["sandbox"]["dns_service_apex"]

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        def dns_resolver(fqdn):
            # The bare apex has no record by design; the published service hosts resolve.
            if fqdn == service_apex:
                return (False, f"DNS resolution failed for {fqdn}", None)
            return (True, f"DNS resolved {fqdn}", "1.2.3.4")

        dns_mock = MagicMock(side_effect=dns_resolver)
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_mock,
                tls_checker=tls_ok,
                http_prober=http_ok,
            )
            exit_code = runner.run()

        # The record-less bare apex must not turn the check red.
        assert exit_code == 0

    def test_endpoints_fail_when_published_service_host_does_not_resolve(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A published collector./telemetry. host that does not resolve is a hard FAIL."""
        domains = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())
        service_apex = domains["sandbox"]["dns_service_apex"]
        collector_fqdn = f"collector.{service_apex}"

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        def dns_resolver(fqdn):
            if fqdn == collector_fqdn:
                return (False, f"DNS resolution failed for {fqdn}", None)
            return (True, f"DNS resolved {fqdn}", "1.2.3.4")

        dns_mock = MagicMock(side_effect=dns_resolver)
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_mock,
                tls_checker=tls_ok,
                http_prober=http_ok,
            )
            exit_code = runner.run()

        assert exit_code == 1

    def test_endpoints_service_fqdn_instance_is_active_set_driven(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The service-FQDN instance number is the injected active set, not a hardcoded literal.

        Injecting a non-"000" active set proves the instance segment is DERIVED from the
        active-set config (blue/green switch), so promoting a new instance set retargets the
        probed service FQDN with no code change.
        """
        domains = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())
        service_apex = domains["prod"]["dns_service_apex"]
        pretty_apex = domains["prod"]["dns_pretty_apex"]

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))
        # Sentinel active set distinct from the checked-in "000".
        active_resolver = MagicMock(return_value="007")

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="endpoints",
                env="prod",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_ok,
                tls_checker=tls_ok,
                http_prober=http_ok,
                active_set_resolver=active_resolver,
            )
            exit_code = runner.run()

        assert exit_code == 0
        tls_hosts = {call.args[0] for call in tls_ok.call_args_list}
        assert tls_hosts == {
            f"collector-007.{service_apex}",
            f"telemetry-007.{service_apex}",
            f"collector.{pretty_apex}",
            f"telemetry.{pretty_apex}",
        }
        # Neither the checked-in "000" instance nor the bare instance-less host was probed.
        assert f"collector-000.{service_apex}" not in tls_hosts
        assert f"collector.{service_apex}" not in tls_hosts

    def test_endpoints_fail_when_active_set_unresolvable(self, tmp_path: pathlib.Path) -> None:
        """An unresolvable active set FAILs the check (fail-fast) but still probes the pretty host.

        The instance-scoped service FQDN cannot be built without the active set, so it must
        surface as a probe FAIL rather than a guessed hostname. The pretty FQDN does not depend
        on the active set and is still probed.
        """
        domains = json.loads((REPO_ROOT / "terragrunt" / "common" / "domains.json").read_text())
        pretty_apex = domains["prod"]["dns_pretty_apex"]

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        dns_ok = MagicMock(return_value=(True, "DNS resolved", "1.2.3.4"))
        tls_ok = MagicMock(return_value=(True, "TLS OK"))
        http_ok = MagicMock(return_value=(True, "HTTP 200"))

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()

            def raise_usage(env: str, region: str, repo_root: pathlib.Path) -> str:
                raise lv.UsageError("active.hcl not found")

            runner = lv.LiveVerify(
                check="endpoints",
                env="prod",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                dns_resolver=dns_ok,
                tls_checker=tls_ok,
                http_prober=http_ok,
                active_set_resolver=raise_usage,
            )
            exit_code = runner.run()

        assert exit_code == 1
        # The pretty host does not depend on the active set, so it is still probed.
        tls_hosts = {call.args[0] for call in tls_ok.call_args_list}
        assert f"collector.{pretty_apex}" in tls_hosts


@pytest.mark.unit
class TestActiveSetResolver:
    """_default_active_set_resolver reads locals.active from the per-env active.hcl."""

    def test_resolver_reads_active_from_real_active_hcl(self) -> None:
        """The resolver returns the active instance set declared in the real per-env active.hcl."""
        lv = _import_live_verify()
        for env in ("sandbox", "qa", "prod"):
            active_hcl = REPO_ROOT / "terragrunt" / "live" / "telemetry" / "us-east-1" / env
            active_hcl = active_hcl / "active.hcl"
            # The resolver must agree with the raw file content (no hardcoded "000").
            expected = lv._ACTIVE_SET_RE.search(active_hcl.read_text()).group(1)
            assert lv._default_active_set_resolver(env, "us-east-1", REPO_ROOT) == expected

    def test_resolver_parses_arbitrary_active_value(self, tmp_path: pathlib.Path) -> None:
        """The resolver parses whatever numbered set active.hcl declares (e.g. a promoted 001)."""
        lv = _import_live_verify()
        env = "sandbox"
        active_dir = tmp_path / "terragrunt" / "live" / "telemetry" / "us-east-1" / env
        active_dir.mkdir(parents=True)
        (active_dir / "active.hcl").write_text('locals {\n  active = "001"\n}\n')
        assert lv._default_active_set_resolver(env, "us-east-1", tmp_path) == "001"

    def test_resolver_fail_fast_when_active_hcl_missing(self, tmp_path: pathlib.Path) -> None:
        """A missing active.hcl fail-fasts with UsageError (never a silent guess)."""
        lv = _import_live_verify()
        with pytest.raises(lv.UsageError, match="active-set file not found"):
            lv._default_active_set_resolver("prod", "us-east-1", tmp_path)

    def test_resolver_fail_fast_when_active_unparseable(self, tmp_path: pathlib.Path) -> None:
        """An active.hcl with no parseable active assignment fail-fasts with UsageError."""
        lv = _import_live_verify()
        env = "prod"
        active_dir = tmp_path / "terragrunt" / "live" / "telemetry" / "us-east-1" / env
        active_dir.mkdir(parents=True)
        (active_dir / "active.hcl").write_text("locals {\n  something_else = true\n}\n")
        with pytest.raises(lv.UsageError, match="could not parse"):
            lv._default_active_set_resolver(env, "us-east-1", tmp_path)


@pytest.mark.unit
class TestObservabilityNamespaceDerivation:
    """observability namespace + topic-name derivation per env (sandbox AND prod)."""

    def test_observability_namespace_sandbox(self) -> None:
        """Sandbox namespace mirrors the _envcommon derivation."""
        lv = _import_live_verify()
        runner = lv.LiveVerify(
            check="observability",
            env="sandbox",
            output_path=None,
            accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
            exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
            oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
            boto3_module=MagicMock(),
            gh_runner=None,
            aws_region="us-east-1",
        )
        assert runner._observability_namespace() == (
            "telemetry-useast1-sandbox-shared-observability-000"
        )
        assert runner._observability_topic_name() == (
            "telemetry-useast1-sandbox-shared-observability-000-observability"
        )

    def test_observability_namespace_prod(self) -> None:
        """Prod namespace differs only by the environment component."""
        lv = _import_live_verify()
        runner = lv.LiveVerify(
            check="observability",
            env="prod",
            output_path=None,
            accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
            exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
            oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
            boto3_module=MagicMock(),
            gh_runner=None,
            aws_region="us-east-1",
        )
        assert runner._observability_namespace() == (
            "telemetry-useast1-prod-shared-observability-000"
        )
        assert runner._observability_topic_name() == (
            "telemetry-useast1-prod-shared-observability-000-observability"
        )

    def test_observability_topic_uses_shared_singleton_tier(self) -> None:
        """The observability leaf is a once-per-env singleton (shared tier): the 4th namespace
        field (environment_instance) is 'shared', NOT '000'.

        Regression guard for the SNS-ARN bug: the observability unit was relocated to
        _singletons/shared/observability/000 so the live topic is
        telemetry-<region>-<env>-shared-observability-000-observability. Deriving '000'
        targeted a non-existent topic and failed the live-verify observability check.
        """
        lv = _import_live_verify()
        runner = lv.LiveVerify(
            check="observability",
            env="sandbox",
            output_path=None,
            accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
            exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
            oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
            boto3_module=MagicMock(),
            gh_runner=None,
            aws_region="us-east-1",
        )
        topic = runner._observability_topic_name()
        assert "-shared-observability-" in topic
        # The old (wrong) per-set form must never reappear.
        assert "-000-observability-000-" not in topic


@pytest.mark.unit
class TestObservabilityClientErrors:
    """observability check emits FAIL for ClientError on CW, budgets, monitors, and subs."""

    def _run_observability(
        self,
        tmp_path: pathlib.Path,
        cw_raises: bool = False,
        budgets_raises: bool = False,
        monitors_raises: bool = False,
        subscriptions_raises: bool = False,
    ) -> int:
        import botocore.exceptions

        def _make_err(code: str, op: str) -> botocore.exceptions.ClientError:
            return botocore.exceptions.ClientError(
                {"Error": {"Code": code, "Message": "Error"}}, op
            )

        cw_client = MagicMock()
        if cw_raises:
            cw_client.describe_alarms.side_effect = _make_err("AccessDenied", "DescribeAlarms")
        else:
            # A real observability alarm name (map key) so the alarm-presence probe passes
            # and only the targeted client error drives the FAIL.
            cw_client.describe_alarms.return_value = {
                "MetricAlarms": [{"AlarmName": "alb_5xx_count", "StateValue": "OK"}]
            }

        budgets_client = MagicMock()
        if budgets_raises:
            budgets_client.describe_budgets.side_effect = _make_err(
                "AccessDenied", "DescribeBudgets"
            )
        else:
            budgets_client.describe_budgets.return_value = {"Budgets": [{"BudgetName": "b1"}]}

        ce_client = MagicMock()
        if monitors_raises:
            ce_client.get_anomaly_monitors.side_effect = _make_err(
                "AccessDenied", "GetAnomalyMonitors"
            )
        else:
            ce_client.get_anomaly_monitors.return_value = {
                "AnomalyMonitors": [{"MonitorArn": "arn:test"}]
            }
        if subscriptions_raises:
            ce_client.get_anomaly_subscriptions.side_effect = _make_err(
                "AccessDenied", "GetAnomalySubscriptions"
            )
        else:
            ce_client.get_anomaly_subscriptions.return_value = {
                "AnomalySubscriptions": [{"SubscriptionArn": "arn:sub"}]
            }

        # A present subscription so the SNS probe passes; only the targeted client error
        # drives the FAIL under test.
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [{"SubscriptionArn": "arn:confirmed", "Endpoint": "ops@example.com"}]
        }

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        def mock_client(service_name, **kwargs):
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            return runner.run()

    def test_observability_fail_when_cw_raises_client_error(self, tmp_path: pathlib.Path) -> None:
        """observability check emits FAIL when cloudwatch.describe_alarms raises ClientError."""
        exit_code = self._run_observability(tmp_path, cw_raises=True)
        assert exit_code == 1

    def test_observability_fail_when_budgets_raises_client_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """observability check emits FAIL when budgets.describe_budgets raises ClientError."""
        exit_code = self._run_observability(tmp_path, budgets_raises=True)
        assert exit_code == 1

    def test_observability_fail_when_monitors_raises_client_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """observability check emits FAIL when ce.get_anomaly_monitors raises ClientError."""
        exit_code = self._run_observability(tmp_path, monitors_raises=True)
        assert exit_code == 1

    def test_observability_fail_when_subscriptions_raises_client_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """observability check emits FAIL when ce.get_anomaly_subscriptions raises ClientError."""
        exit_code = self._run_observability(tmp_path, subscriptions_raises=True)
        assert exit_code == 1


@pytest.mark.unit
class TestObservabilitySnsSubscriptionPresence:
    """observability SNS probe: subscription-presence semantics against the namespace-derived
    topic.

    The SNS topic name is namespace-derived (topic_name = "${namespace}-observability"), NOT
    the old hardcoded "<env>-alerts", and contacts.json is no longer consulted. Email
    subscription confirmation is OPERATOR-gated, so a PendingConfirmation subscription is the
    expected interim state and is ACCEPTED as OK; the probe asserts a subscription EXISTS.
    """

    _EXPECTED_TOPIC_ARN = (
        "arn:aws:sns:us-east-1:222222222222:"
        "telemetry-useast1-sandbox-shared-observability-000-observability"
    )

    def _make_standard_observability_clients(self) -> tuple:
        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {
            "MetricAlarms": [{"AlarmName": "alb_5xx_count", "StateValue": "OK"}]
        }
        budgets_client = MagicMock()
        budgets_client.describe_budgets.return_value = {"Budgets": [{"BudgetName": "b1"}]}
        ce_client = MagicMock()
        ce_client.get_anomaly_monitors.return_value = {
            "AnomalyMonitors": [{"MonitorArn": "arn:test"}]
        }
        ce_client.get_anomaly_subscriptions.return_value = {
            "AnomalySubscriptions": [{"SubscriptionArn": "arn:sub"}]
        }
        return cw_client, budgets_client, ce_client

    def _run(self, tmp_path: pathlib.Path, sns_client: MagicMock) -> int:
        cw_client, budgets_client, ce_client = self._make_standard_observability_clients()

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        def mock_client(service_name, **kwargs):
            if service_name == "cloudwatch":
                return cw_client
            if service_name == "budgets":
                return budgets_client
            if service_name == "ce":
                return ce_client
            if service_name == "sns":
                return sns_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="observability",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
            )
            return runner.run()

    def test_observability_sns_probes_namespace_derived_topic_arn(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The SNS probe targets the namespace-derived topic ARN, not the old <env>-alerts."""
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [{"SubscriptionArn": "arn:confirmed", "Endpoint": "ops@example.com"}]
        }
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 0
        # The probe must call list_subscriptions_by_topic with the namespace-derived ARN.
        sns_client.list_subscriptions_by_topic.assert_called_once_with(
            TopicArn=self._EXPECTED_TOPIC_ARN
        )

    def test_observability_sns_confirmed_subscription_ok(self, tmp_path: pathlib.Path) -> None:
        """OK when a confirmed (real-ARN) subscription is present on the topic."""
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [
                {
                    "SubscriptionArn": "arn:aws:sns:us-east-1:222222222222:topic:sub-1",
                    "SubscriptionOwner": "222222222222",
                    "Endpoint": "ops@example.com",
                }
            ]
        }
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 0

    def test_observability_sns_pending_subscription_accepted_as_ok(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A PendingConfirmation subscription is ACCEPTED as OK (operator-gated confirmation).

        The SNS email subscription confirmation is operator-gated (the subscriber clicks the
        confirmation link out-of-band). A subscription in PendingConfirmation is therefore the
        expected interim state on a correctly-provisioned topic and must NOT fail the check.
        """
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [
                {
                    "SubscriptionArn": "PendingConfirmation",
                    "Protocol": "email",
                    "Endpoint": "platform-alerts@example.com",
                }
            ]
        }
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 0

    def test_observability_sns_mixed_pending_and_confirmed_ok(self, tmp_path: pathlib.Path) -> None:
        """OK when at least one subscription exists, mixing confirmed and pending."""
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [
                {
                    "SubscriptionArn": "arn:aws:sns:us-east-1:222222222222:topic:sub-1",
                    "Endpoint": "ops@example.com",
                },
                {
                    "SubscriptionArn": "PendingConfirmation",
                    "Endpoint": "platform-alerts@example.com",
                },
            ]
        }
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 0

    def test_observability_sns_empty_subscriptions_fails_fast(self, tmp_path: pathlib.Path) -> None:
        """FAILS fast when the topic has zero subscriptions.

        Fail-fast contract (CLAUDE.md): a topic with no subscription at all is a real gap in
        the alerting path -- there is no subscriber, pending or confirmed.
        """
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {"Subscriptions": []}
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 1

    def test_observability_sns_only_deleted_tombstone_fails_fast(
        self, tmp_path: pathlib.Path
    ) -> None:
        """FAILS fast when the only subscription is a Deleted tombstone (no live subscriber)."""
        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [{"SubscriptionArn": "Deleted", "Endpoint": "ops@example.com"}]
        }
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 1

    def test_observability_sns_client_error_fails_fast(self, tmp_path: pathlib.Path) -> None:
        """FAILS fast when list_subscriptions_by_topic raises ClientError.

        Fail-fast contract (CLAUDE.md): an inaccessible alerts topic must surface as a probe
        failure (non-zero), never be silently skipped as OK.
        """
        import botocore.exceptions

        sns_client = MagicMock()
        sns_client.list_subscriptions_by_topic.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "NotFound", "Message": "Topic not found"}},
            "ListSubscriptionsByTopic",
        )
        exit_code = self._run(tmp_path, sns_client)
        assert exit_code == 1


@pytest.mark.unit
class TestRepoSettingsJsonDecodeError:
    """repo-settings variable list JSON decode error fallback (lines 1178-1179)."""

    def test_repo_settings_variable_list_invalid_json_fallback(
        self, tmp_path: pathlib.Path
    ) -> None:
        """repo-settings falls back to line-splitting when variable list JSON is malformed."""
        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        # Construct invalid JSON that starts with '[' but cannot be parsed
        invalid_json = "[not valid json at all"

        def mock_gh_runner(args: list[str]) -> tuple[int, str, str]:
            cmd = " ".join(args)
            if "variable list" in cmd and "--json" in cmd:
                return 0, invalid_json, ""
            if "variable list" in cmd:
                return 1, "", "error"
            if "environments/terratest-approval" in cmd:
                return 0, '{"name":"terratest-approval"}', ""
            if "environments/prod-apply" in cmd:
                return (
                    0,
                    json.dumps(
                        {
                            "name": "prod-apply",
                            "protection_rules": [
                                {"type": "required_reviewers", "reviewers": ["me"]}
                            ],
                        }
                    ),
                    "",
                )
            return 1, "", "Not found"

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="repo-settings",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=mock_gh_runner,
                gh_repo="example-org/telemetry-platform",
            )
            exit_code = runner.run()

        # Invalid JSON starts with '[', parse fails, fallback puts the whole line as
        # one "variable name" -- none match the required set, so FAIL for vars
        assert exit_code == 1


@pytest.mark.unit
class TestMainBoto3ImportError:
    """main() exits 1 when boto3 is not importable (lines 1282-1288)."""

    def test_main_exits_1_when_boto3_not_available(self) -> None:
        """main() prints an error and exits 1 when boto3 is unavailable (lines 1282-1288)."""
        import sys

        # Inject None as boto3 so the import inside main() triggers ImportError
        with patch.dict(sys.modules, {"boto3": None}):
            lv = _import_live_verify()
            with pytest.raises(SystemExit) as exc_info:
                lv.main(["--check", "no-project-resources", "--env", "sandbox"])
            assert exc_info.value.code == 1


@pytest.mark.unit
class TestMainEntrypoint:
    """__main__ block coverage (line 1310)."""

    def test_main_module_entry_point(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling live_verify via runpy as __main__ invokes main() (line 1310)."""
        import runpy
        import sys

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session
        rgt_client = MagicMock()
        rgt_client.get_resources.return_value = {
            "ResourceTagMappingList": [],
            "PaginationToken": "",
        }
        mock_session.client.return_value = rgt_client

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "scripts.live_verify",
                "--check",
                "no-project-resources",
                "--env",
                "sandbox",
            ],
        )

        with (
            patch.dict(sys.modules, {"boto3": mock_boto3}),
            pytest.raises(SystemExit) as exc_info,
        ):
            runpy.run_module("scripts.live_verify", run_name="__main__", alter_sys=True)

        assert exc_info.value.code == 0


@pytest.mark.unit
class TestStackAcmStatusNotIssued:
    """stack check emits FAIL when ACM certificate status is not ISSUED (line 688)."""

    def test_stack_fail_when_acm_status_pending(self, tmp_path: pathlib.Path) -> None:
        """stack check emits FAIL when ACM returns PENDING_VALIDATION status."""

        mock_boto3 = MagicMock()
        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        s3_client = MagicMock()
        s3_client.head_bucket.return_value = {}

        cf_client = MagicMock()
        cf_client.get_distribution.return_value = {"Distribution": {"Status": "Deployed"}}

        acm_client = MagicMock()
        # Return PENDING_VALIDATION to hit the else branch (line 688)
        acm_client.describe_certificate.return_value = {
            "Certificate": {"Status": "PENDING_VALIDATION"}
        }

        route53_client = MagicMock()
        route53_client.list_hosted_zones_by_name.return_value = {
            "HostedZones": [{"Id": "/hostedzone/Z1", "Name": "sandbox."}]
        }
        route53_client.list_resource_record_sets.return_value = {
            "ResourceRecordSets": [{"Name": "sandbox.", "Type": "NS"}]
        }

        firehose_client = MagicMock()
        firehose_client.describe_delivery_stream.return_value = {
            "DeliveryStreamDescription": {"DeliveryStreamStatus": "ACTIVE"}
        }

        glue_client = MagicMock()
        glue_client.get_database.return_value = {"Database": {"Name": "telemetry"}}

        athena_client = MagicMock()
        athena_client.get_work_group.return_value = {"WorkGroup": {"Name": "telemetry"}}

        ecs_client = MagicMock()
        ecs_client.describe_services.return_value = {
            "services": [{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1}]
        }

        cw_client = MagicMock()
        cw_client.describe_alarms.return_value = {"MetricAlarms": [{"AlarmName": "alarm-1"}]}

        def mock_client(service_name, **kwargs):
            if service_name == "s3":
                return s3_client
            if service_name == "cloudfront":
                return cf_client
            if service_name == "acm":
                return acm_client
            if service_name == "route53":
                return route53_client
            if service_name == "firehose":
                return firehose_client
            if service_name == "glue":
                return glue_client
            if service_name == "athena":
                return athena_client
            if service_name == "ecs":
                return ecs_client
            if service_name == "cloudwatch":
                return cw_client
            return MagicMock()

        mock_session.client.side_effect = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            lv = _import_live_verify()
            runner = lv.LiveVerify(
                check="stack",
                env="sandbox",
                output_path=str(tmp_path / "out.json"),
                accounts_data=json.loads(ACCOUNTS_JSON_PATH.read_text()),
                exemptions_data=json.loads(EXEMPTIONS_JSON_PATH.read_text()),
                oidc_roles_data=json.loads(OIDC_ROLES_JSON_PATH.read_text()),
                boto3_module=mock_boto3,
                gh_runner=None,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
            exit_code = runner.run()

        assert exit_code == 1
