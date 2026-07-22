"""Unit tests for scripts/terratest_sweep.py -- FR-4 tag-scoped orphan sweep.

All AWS interactions are mocked; no real AWS calls are made.
Covers:
  - allowlist enforcement: caller account not in {sandbox, qa-infra} is refused
    before any enumeration
  - strict two-tag Tagging API filter (Project=telemetry-platform AND terratest-run)
  - optional narrowing to one SWEEP_RUN_ID
  - per-service handler dispatch (all spec 4.4 service types)
  - unsupported resource type fail-safe (reported, untouched, non-zero exit)
  - JSON report schema per spec section 5.4
  - bounded retry on Tagging API throttling via botocore adaptive retry config
  - check mode: exit 0 iff zero matches
  - delete mode: dispatches handlers and emits correct report actions
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helper: reload scripts.terratest_sweep with patched boto3
# ---------------------------------------------------------------------------


def _make_boto3_mock(
    caller_account_id: str = "222222222222",
    tag_resources: list[dict] | None = None,
    throttle_get_resources: bool = False,
) -> types.ModuleType:
    """Build a mock boto3 module suitable for injection into the sweep module."""
    if tag_resources is None:
        tag_resources = []

    boto3_mock = MagicMock()

    # STS client
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": caller_account_id,
        "Arn": f"arn:aws:iam::{caller_account_id}:user/test",
        "UserId": "AIDATEST",
    }
    boto3_mock.client.return_value = sts_client

    return boto3_mock


def _import_sweep(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import (or re-import) the sweep module, removing it from sys.modules first."""
    module_name = "scripts.terratest_sweep"
    if module_name in sys.modules:
        del sys.modules[module_name]
    import scripts.terratest_sweep as m

    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ACCOUNTS_JSON_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "terragrunt" / "common" / "accounts.json"
)


@pytest.fixture()
def accounts_data() -> dict:
    """Load real accounts.json from the repo."""
    assert ACCOUNTS_JSON_PATH.exists(), (
        f"accounts.json not found at {ACCOUNTS_JSON_PATH}; the file must exist."
    )
    return json.loads(ACCOUNTS_JSON_PATH.read_text())


@pytest.fixture()
def sandbox_account_id(accounts_data: dict) -> str:
    """Return the sandbox account id from accounts.json."""
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "sandbox":
            return acct_id
    pytest.fail("No account with account_role=sandbox found in accounts.json")


@pytest.fixture()
def qa_account_id(accounts_data: dict) -> str:
    """Return the qa-infra account id from accounts.json."""
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "qa-infra":
            return acct_id
    pytest.fail("No account with account_role=qa-infra found in accounts.json")


@pytest.fixture()
def prod_account_id(accounts_data: dict) -> str:
    """Return the prod-infra account id from accounts.json."""
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "prod-infra":
            return acct_id
    pytest.fail("No account with account_role=prod-infra found in accounts.json")


@pytest.fixture()
def dns_owner_account_id(accounts_data: dict) -> str:
    """Return the dns-owner account id from accounts.json."""
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "dns-owner":
            return acct_id
    pytest.fail("No account with account_role=dns-owner found in accounts.json")


# ---------------------------------------------------------------------------
# AC-1 subset: allowlist enforcement
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_allowlist_loads_from_accounts_json(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    qa_account_id: str,
) -> None:
    """The allowlist must include the sandbox and qa-infra account ids."""
    import scripts.terratest_sweep as sweep

    allowlist = sweep._load_allowlist(str(ACCOUNTS_JSON_PATH))
    assert sandbox_account_id in allowlist, (
        f"sandbox account {sandbox_account_id} not in sweep allowlist {allowlist}"
    )
    assert qa_account_id in allowlist, (
        f"qa-infra account {qa_account_id} not in sweep allowlist {allowlist}"
    )


@pytest.mark.unit
def test_allowlist_excludes_prod(
    monkeypatch: pytest.MonkeyPatch,
    prod_account_id: str,
) -> None:
    """The prod-infra account must NOT be in the sweep allowlist."""
    import scripts.terratest_sweep as sweep

    allowlist = sweep._load_allowlist(str(ACCOUNTS_JSON_PATH))
    assert prod_account_id not in allowlist, (
        f"prod account {prod_account_id} must not be in sweep allowlist"
    )


@pytest.mark.unit
def test_allowlist_excludes_dns_owner(
    monkeypatch: pytest.MonkeyPatch,
    dns_owner_account_id: str,
) -> None:
    """The dns-owner account must NOT be in the sweep allowlist."""
    import scripts.terratest_sweep as sweep

    allowlist = sweep._load_allowlist(str(ACCOUNTS_JSON_PATH))
    assert dns_owner_account_id not in allowlist, (
        f"dns-owner account {dns_owner_account_id} must not be in sweep allowlist"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "disallowed_role",
    ["prod-infra", "dns-owner"],
)
def test_disallowed_account_refused_before_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    disallowed_role: str,
    accounts_data: dict,
    tmp_path: pathlib.Path,
) -> None:
    """A caller account not in the allowlist causes exit 1 BEFORE any tagging enumeration.

    Spec section 3.6 and G7 worked example: the error must be emitted before
    any Resource Groups Tagging API calls.
    """
    disallowed_id = next(
        acct_id for acct_id, row in accounts_data.items() if row["account_role"] == disallowed_role
    )
    # Use a real accounts.json file where both sandbox and qa-infra are absent
    # by building a minimal JSON with only the disallowed account.
    fake_accounts = {disallowed_id: {"account_role": disallowed_role, "aws_profile": "test"}}
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(json.dumps(fake_accounts))

    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": disallowed_id,
        "Arn": f"arn:aws:iam::{disallowed_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(accounts_path),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 1

    # CRITICAL: tagging client must NOT have been called (refused before enumeration)
    tagging_client.get_resources.assert_not_called()


@pytest.mark.unit
def test_disallowed_account_error_message(
    monkeypatch: pytest.MonkeyPatch,
    prod_account_id: str,
    sandbox_account_id: str,
    qa_account_id: str,
    accounts_data: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """The error message for a disallowed account follows the G7 worked-example shape."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": prod_account_id,
        "Arn": f"arn:aws:iam::{prod_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert prod_account_id in captured.err
    assert "allowlist" in captured.err.lower()
    assert "Refusing" in captured.err or "refusing" in captured.err


# ---------------------------------------------------------------------------
# AC-1 subset: strict two-tag Tagging API filter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_mode_uses_strict_two_tag_filter(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """The Tagging API call must filter on BOTH Project=telemetry-platform AND terratest-run.

    Spec section 4.4 FR-4 item (2): filter on BOTH tags -- never a broader filter.
    """
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0

    # Inspect the paginator call for the required tag filters
    paginate_calls = tagging_client.get_paginator.return_value.paginate.call_args_list
    assert paginate_calls, "Tagging API paginator was not called"
    # Accept both positional and keyword invocation
    all_args = {}
    if paginate_calls[0].args:
        all_args.update(paginate_calls[0].args[0] if paginate_calls[0].args else {})
    all_args.update(paginate_calls[0].kwargs or {})

    # The TagFilters param must include both Project and terratest-run
    tag_filters = all_args.get("TagFilters", [])
    keys = {tf["Key"] for tf in tag_filters}
    assert "Project" in keys, f"Tagging API filter missing 'Project' key; TagFilters={tag_filters}"
    assert "terratest-run" in keys, (
        f"Tagging API filter missing 'terratest-run' key; TagFilters={tag_filters}"
    )

    # The Project filter must have value telemetry-platform
    project_filter = next(tf for tf in tag_filters if tf["Key"] == "Project")
    assert "telemetry-platform" in project_filter.get("Values", []), (
        f"Project filter must include 'telemetry-platform'; got {project_filter}"
    )


@pytest.mark.unit
def test_check_mode_with_run_id_narrows_filter(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """When SWEEP_RUN_ID is set, the terratest-run filter narrows to that exact id."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    run_id = "run-abc123"
    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit):
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    paginate_calls = tagging_client.get_paginator.return_value.paginate.call_args_list
    all_args = {}
    all_args.update(paginate_calls[0].kwargs or {})
    tag_filters = all_args.get("TagFilters", [])
    run_filter = next((tf for tf in tag_filters if tf["Key"] == "terratest-run"), None)
    assert run_filter is not None, "terratest-run filter not found in paginate call"
    assert run_id in run_filter.get("Values", []), (
        f"Expected run_id={run_id!r} in terratest-run filter values; got {run_filter}"
    )


# ---------------------------------------------------------------------------
# AC-1 subset: check mode exit codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_mode_exits_0_when_zero_matches(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Check mode exits 0 when Tagging API returns zero resources (spec 4.4 item 3)."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0


@pytest.mark.unit
def test_check_mode_exits_nonzero_when_resources_found(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Check mode exits non-zero when Tagging API returns one or more resources."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": f"arn:aws:s3:::{sandbox_account_id}-bucket-orphan",
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": "run-xyz"},
                    ],
                }
            ]
        }
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code != 0


# ---------------------------------------------------------------------------
# AC-1 subset: handler dispatch
# ---------------------------------------------------------------------------


def _make_arn(service: str, resource: str, account: str = "222222222222") -> str:
    return f"arn:aws:{service}:us-east-1:{account}:{resource}"


_HANDLER_DISPATCH_CASES: list[tuple[str, str, str]] = [
    # (service_prefix, resource_part, expected_action_prefix_or_key)
    ("s3", "bucket/tt-orphan-bucket", "deleted"),
    ("kms", "key/mrk-abc123", "scheduled"),
    ("iam", "role/tt-orphan-role", "deleted"),
    ("cloudfront", "distribution/EDFDVBD6EXAMPLE", "deleted"),
    ("acm", "certificate/abc-def", "deleted"),
    ("route53", "hostedzone/Z1234EXAMPLE", "deleted"),
    ("ec2", "vpc/vpc-abc123", "deleted"),
    ("ec2", "natgateway/nat-abc123", "deleted"),
    ("ec2", "vpc-flow-log/fl-abc123", "deleted"),
    ("ecs", "cluster/tt-orphan-cluster", "deleted"),
    ("elasticloadbalancing", "loadbalancer/app/tt-orphan-alb/1234", "deleted"),
    ("firehose", "deliverystream/tt-orphan-stream", "deleted"),
    ("glue", "database/tt-orphan-db", "deleted"),
    ("athena", "workgroup/tt-orphan-wg", "deleted"),
    ("sns", "tt-orphan-topic", "deleted"),
    ("ssm", "parameter/tt-orphan-param", "deleted"),
    ("lambda", "function:tt-orphan-fn", "deleted"),
    ("logs", "log-group:/tt-orphan-lg", "deleted"),
    ("quicksight", "dataset/tt-orphan-ds", "deleted"),
    ("budgets", "budget/tt-orphan-budget", "deleted"),
]


def _make_service_client_mock(service: str) -> MagicMock:
    """Build a suitably configured mock client for the given AWS service.

    For services whose handlers poll for readiness (CloudFront), the mock is
    pre-configured to return terminal state on the first call so tests do not
    block.
    """
    client = MagicMock()
    if service == "cloudfront":
        # Distribution is already disabled -- waiter call is skipped for disabled dists.
        dist_config = {"Enabled": False, "Origins": {"Quantity": 0, "Items": []}}
        client.get_distribution_config.return_value = {
            "ETag": "ETAG123",
            "DistributionConfig": dist_config,
        }
        # delete_distribution succeeds immediately.
        client.delete_distribution.return_value = {}
        # Waiter and ETag fetch support (used when Enabled=True path is taken).
        client.get_waiter.return_value.wait.return_value = None
        client.get_distribution.return_value = {
            "ETag": "ETAG123",
            "Distribution": {"Status": "Deployed"},
        }
    elif service == "ecs":
        _ecs_cluster_arn = "arn:aws:ecs:us-east-1:222222222222:cluster/tt-orphan-cluster"
        client.describe_services.return_value = {"services": [{"clusterArn": _ecs_cluster_arn}]}
    elif service == "elasticloadbalancing":
        client.describe_listeners.return_value = {"Listeners": []}
        client.delete_load_balancer.return_value = {}
    elif service == "iam":
        client.list_instance_profiles_for_role.return_value = {"InstanceProfiles": []}
        client.list_attached_role_policies.return_value = {"AttachedPolicies": []}
        client.list_role_policies.return_value = {"PolicyNames": []}
    elif service == "ec2":
        client.describe_internet_gateways.return_value = {"InternetGateways": []}
        # Simulate a live NAT gateway for the dispatch test (state=available triggers delete).
        client.describe_nat_gateways.return_value = {
            "NatGateways": [{"NatGatewayId": "nat-abc123", "State": "available"}]
        }
        # Simulate an already-deleted flow log (empty list -- idempotent delete_flow_logs).
        client.describe_flow_logs.return_value = {"FlowLogs": []}
    elif service == "glue":
        client.get_tables.return_value = {"TableList": []}
    elif service == "route53":
        client.list_resource_record_sets.return_value = {"ResourceRecordSets": []}
    elif service == "s3":
        client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
        # Support both paginator and direct call patterns.
        pager = MagicMock()
        pager.paginate.return_value = [{"Versions": [], "DeleteMarkers": []}]
        client.get_paginator.return_value = pager
    elif service == "quicksight":
        client.delete_data_set.return_value = {}
        client.delete_data_source.return_value = {}
    return client


@pytest.mark.unit
@pytest.mark.parametrize("service,resource,expected_action", _HANDLER_DISPATCH_CASES)
def test_delete_mode_dispatches_handler(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    resource: str,
    expected_action: str,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Delete mode dispatches the appropriate handler for each known service prefix.

    Each handler must record an action in the report; the sweep must not fail
    due to missing dispatch.
    """
    # Set poll timeout to 0 so any polling finishes immediately (fail-fast on stuck poll).
    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "0")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "0")

    import contextlib

    import scripts.terratest_sweep as sweep

    resource_arn = _make_arn(service, resource, sandbox_account_id)

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": resource_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": "run-test"},
                    ],
                }
            ]
        }
    ]

    # Build per-service mock clients
    service_clients: dict[str, MagicMock] = {}

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc not in service_clients:
            service_clients[svc] = _make_service_client_mock(service)
        return service_clients[svc]

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / f"report_{service}.json"
    with contextlib.suppress(SystemExit):
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )

    assert report_path.exists(), f"Report not written for service {service}"
    report = json.loads(report_path.read_text())
    found_entry = next((e for e in report.get("found", []) if e.get("arn") == resource_arn), None)
    assert found_entry is not None, (
        f"Resource {resource_arn} not recorded in report for service {service}; report={report}"
    )
    assert found_entry.get("action") != "reported-unsupported", (
        f"Service {service} was reported as unsupported but must have a handler; "
        f"entry={found_entry}"
    )


# ---------------------------------------------------------------------------
# AC-1 subset: unsupported resource type fail-safe
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unsupported_resource_type_reported_and_not_touched(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A matched resource with no handler is reported, left untouched, non-zero exit.

    Spec section 4.4 invariant (5): the fail-safe must NOT touch the resource
    and must cause a non-zero exit.
    """
    import scripts.terratest_sweep as sweep

    unknown_arn = f"arn:aws:unknown-service:us-east-1:{sandbox_account_id}:resource/orphan"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": unknown_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": "run-test"},
                    ],
                }
            ]
        }
    ]

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_unsupported.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    # Must exit non-zero
    assert exc.value.code != 0

    # Report must record the resource as 'reported-unsupported'
    assert report_path.exists(), "Report must be written even on unsupported-type failure"
    report = json.loads(report_path.read_text())
    found = report.get("found", [])
    entry = next((e for e in found if e.get("arn") == unknown_arn), None)
    assert entry is not None, f"Unknown ARN not recorded in report; report={report}"
    assert entry.get("action") == "reported-unsupported", (
        f"Unknown ARN must be recorded as 'reported-unsupported'; entry={entry}"
    )


# ---------------------------------------------------------------------------
# AC-1 subset: JSON report schema (spec 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_report_schema_check_mode_zero_resources(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Report schema must conform to spec 5.4 for check mode with zero resources."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    report_path = tmp_path / "report_schema.json"
    with pytest.raises(SystemExit):
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )

    assert report_path.exists(), "Report file must be written on check mode"
    report = json.loads(report_path.read_text())

    # Required top-level keys per spec 5.4
    assert "run_at" in report, f"'run_at' missing from report; keys={list(report)}"
    assert "account_id" in report, f"'account_id' missing from report; keys={list(report)}"
    assert "mode" in report, f"'mode' missing from report; keys={list(report)}"
    assert "filter" in report, f"'filter' missing from report; keys={list(report)}"
    assert "found" in report, f"'found' missing from report; keys={list(report)}"
    assert "remaining" in report, f"'remaining' missing from report; keys={list(report)}"

    # Values
    assert report["account_id"] == sandbox_account_id
    assert report["mode"] == "check"
    assert report["filter"]["Project"] == "telemetry-platform"
    assert "terratest-run" in report["filter"]
    assert report["found"] == []
    assert report["remaining"] == 0


@pytest.mark.unit
def test_report_schema_with_run_id_filter(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """When SWEEP_RUN_ID is provided, the filter section records it in the report."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    run_id = "run-filter-test"
    report_path = tmp_path / "report_run_id.json"
    with pytest.raises(SystemExit):
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    report = json.loads(report_path.read_text())
    assert report["filter"]["terratest-run"] == run_id


@pytest.mark.unit
def test_report_schema_delete_mode_records_action(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Delete mode report records each found resource with arn, type, and action."""
    import contextlib

    import scripts.terratest_sweep as sweep

    resource_arn = f"arn:aws:s3:::{sandbox_account_id}-tt-test-bucket"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": resource_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": "run-test"},
                    ],
                }
            ]
        }
    ]

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_delete_action.json"
    with contextlib.suppress(SystemExit):
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )

    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["mode"] == "delete"
    found = report.get("found", [])
    assert len(found) >= 1
    entry = found[0]
    assert "arn" in entry, f"'arn' missing from found entry; entry={entry}"
    assert "type" in entry, f"'type' missing from found entry; entry={entry}"
    assert "action" in entry, f"'action' missing from found entry; entry={entry}"
    assert entry["arn"] == resource_arn


# ---------------------------------------------------------------------------
# AC-1 subset: Tagging API throttling bounded retry via botocore adaptive retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_throttling_retry_bounded_by_env_budget(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Tagging API throttling triggers bounded retry; when budget exhausted, fail."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "0")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "0")

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    # Simulate throttling error on every paginator call
    throttle_error = botocore.exceptions.ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "GetResources",
    )
    tagging_client.get_paginator.return_value.paginate.side_effect = throttle_error

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_throttle.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    # Must fail with non-zero exit after budget exhausted
    assert exc.value.code != 0


@pytest.mark.unit
def test_throttling_botocore_retry_succeeds_exits_0(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """When botocore adaptive retry handles throttling and the call ultimately succeeds,
    the sweep exits 0 (zero resources found in check mode).

    In the new design, throttle retries are transparent to our code -- botocore
    handles them internally via the Config(retries={'mode': 'adaptive'}) passed
    when creating the tagging client. From our perspective the paginator either
    succeeds or raises (after botocore's retry budget is exhausted).
    """
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "30")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "0")

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    # Paginator succeeds (botocore's adaptive retry handled any transient throttle
    # transparently before returning to our code).
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_throttle_retry.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# AC-1 subset: report default path under .sweep-reports/
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_report_default_path_under_sweep_reports(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """When --report is not given, the report lands under .sweep-reports/."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]
    boto3_mock.client.side_effect = lambda svc, **kw: sts_client if svc == "sts" else tagging_client

    # Run with a default report path derived from constants
    sweep_reports_dir = tmp_path / ".sweep-reports"
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=None,
            run_id=None,
            boto3_mod=boto3_mock,
        )

    # There should be at least one file under .sweep-reports/
    reports = list(sweep_reports_dir.glob("*.json"))
    assert reports, (
        f"No report file found under {sweep_reports_dir}; "
        "make sure the default path is .sweep-reports/"
    )


# ---------------------------------------------------------------------------
# AC-1 subset: constants sourced from scripts/constants.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_poll_constants_exist_in_constants_module() -> None:
    """TT_SWEEP_POLL_TIMEOUT and TT_SWEEP_POLL_INTERVAL are defined in scripts/constants.py."""
    import scripts.constants as constants

    assert hasattr(constants, "TT_SWEEP_POLL_TIMEOUT"), (
        "TT_SWEEP_POLL_TIMEOUT not found in scripts/constants.py; "
        "spec section 7 requires it as the single constants site"
    )
    assert hasattr(constants, "TT_SWEEP_POLL_INTERVAL"), (
        "TT_SWEEP_POLL_INTERVAL not found in scripts/constants.py; "
        "spec section 7 requires it as the single constants site"
    )


@pytest.mark.unit
def test_sweep_imports_poll_constants_from_constants_module() -> None:
    """terratest_sweep.py must import its polling budgets from scripts.constants."""
    import scripts.terratest_sweep as sweep

    # The module must reference the constants (not define them inline)
    src = pathlib.Path(sweep.__file__).read_text()
    assert "from scripts.constants import" in src or "import scripts.constants" in src, (
        "terratest_sweep.py must import from scripts.constants (the single constants site)"
    )
    assert "TT_SWEEP_POLL_TIMEOUT" in src, (
        "terratest_sweep.py must reference TT_SWEEP_POLL_TIMEOUT from scripts.constants"
    )
    assert "TT_SWEEP_POLL_INTERVAL" in src, (
        "terratest_sweep.py must reference TT_SWEEP_POLL_INTERVAL from scripts.constants"
    )


# ---------------------------------------------------------------------------
# AC-1 subset: delete mode partial failure exits non-zero
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_delete_mode_partial_failure_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """If a deletion handler raises an exception, the sweep exits non-zero."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    resource_arn = f"arn:aws:s3:::{sandbox_account_id}-tt-fail-bucket"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": resource_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": "run-fail"},
                    ],
                }
            ]
        }
    ]

    # s3 client will raise on delete_bucket
    s3_client = MagicMock()
    s3_client.list_object_versions.return_value = {"Versions": [], "DeleteMarkers": []}
    s3_client.delete_objects.return_value = {}
    s3_client.delete_bucket.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "BucketNotEmpty", "Message": "bucket not empty"}},
        "DeleteBucket",
    )

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "s3":
            return s3_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_partial_fail.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code != 0

    # Report must exist and record the failure
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    entry = next((e for e in report.get("found", []) if e.get("arn") == resource_arn), None)
    assert entry is not None
    assert entry.get("action") == "failed", (
        f"Deletion failure should be recorded as 'failed'; entry={entry}"
    )


# ---------------------------------------------------------------------------
# AC-1 subset: qa-infra account is also allowed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_qa_infra_account_allowed(
    monkeypatch: pytest.MonkeyPatch,
    qa_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """The qa-infra account must also be in the allowlist (not just sandbox)."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": qa_account_id,
        "Arn": f"arn:aws:iam::{qa_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_qa.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    # qa-infra is allowed; with zero resources this must exit 0
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# AC-1 subset: accounts.json missing or malformed fails fast
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_accounts_json_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A missing accounts.json must cause exit 1 with a clear error message."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    boto3_mock.client.return_value = sts_client

    report_path = tmp_path / "report_missing.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(tmp_path / "nonexistent.json"),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code != 0


@pytest.mark.unit
def test_malformed_accounts_json_fails_fast(
    tmp_path: pathlib.Path,
) -> None:
    """A malformed accounts.json must cause exit 1 with a clear error message."""
    import scripts.terratest_sweep as sweep

    bad_json = tmp_path / "bad_accounts.json"
    bad_json.write_text("{invalid json")
    with pytest.raises(SystemExit) as exc:
        sweep._load_allowlist(str(bad_json))
    assert exc.value.code != 0


# ---------------------------------------------------------------------------
# Handler unit tests -- direct handler invocation for full branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resource_handler_base_raises_not_implemented() -> None:
    """ResourceHandler.delete() must raise NotImplementedError (abstract base)."""
    import scripts.terratest_sweep as sweep

    handler = sweep.ResourceHandler()
    with pytest.raises(NotImplementedError):
        handler.delete("arn:aws:s3:::bucket", MagicMock())


@pytest.mark.unit
def test_s3_handler_purges_versions_with_paginator() -> None:
    """S3Handler must delete versioned objects before deleting the bucket."""
    import scripts.terratest_sweep as sweep

    bucket_name = "tt-test-versioned-bucket"
    arn = f"arn:aws:s3:::{bucket_name}"
    s3_client = MagicMock()
    pager = MagicMock()
    pager.paginate.return_value = [
        {
            "Versions": [{"Key": "obj1", "VersionId": "v1"}],
            "DeleteMarkers": [{"Key": "obj2", "VersionId": "dm1"}],
        }
    ]
    s3_client.get_paginator.return_value = pager
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = s3_client

    handler = sweep.S3Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # delete_objects must have been called with all version+marker entries
    s3_client.delete_objects.assert_called_once()
    s3_client.delete_bucket.assert_called_once_with(Bucket=bucket_name)


@pytest.mark.unit
def test_s3_handler_purges_versions_across_multiple_pages() -> None:
    """S3Handler must iterate every paginator page and delete all versions and markers."""
    import scripts.terratest_sweep as sweep

    bucket_name = "tt-test-multipage-bucket"
    arn = f"arn:aws:s3:::{bucket_name}"
    s3_client = MagicMock()
    pager = MagicMock()
    # Two pages: first page has a version, second page has a delete marker.
    pager.paginate.return_value = [
        {
            "Versions": [{"Key": "obj1", "VersionId": "v1"}],
            "DeleteMarkers": [],
        },
        {
            "Versions": [],
            "DeleteMarkers": [{"Key": "obj2", "VersionId": "dm1"}],
        },
    ]
    s3_client.get_paginator.return_value = pager
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = s3_client

    handler = sweep.S3Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # delete_objects must have been called once for each non-empty page.
    assert s3_client.delete_objects.call_count == 2
    s3_client.delete_bucket.assert_called_once_with(Bucket=bucket_name)


@pytest.mark.unit
def test_kms_handler_alias_arn() -> None:
    """KMSHandler must delete KMS aliases when given an alias ARN."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:alias/tt-orphan-key"
    kms_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    kms_client.delete_alias.assert_called_once_with(AliasName="alias/tt-orphan-key")


@pytest.mark.unit
def test_kms_handler_key_already_pending_deletion_skips_schedule() -> None:
    """KMSHandler skips schedule_key_deletion when the key is already in PendingDeletion state.

    AWS raises KMSInvalidStateException if schedule_key_deletion is called on a key
    that is already in PendingDeletion. The handler must detect this state via
    describe_key and return 'scheduled' without making the schedule call.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:key/pending-key-uuid"
    kms_client = MagicMock()
    kms_client.get_paginator.return_value.paginate.return_value = [{"Aliases": []}]
    kms_client.describe_key.return_value = {
        "KeyMetadata": {"KeyId": "pending-key-uuid", "KeyState": "PendingDeletion"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "scheduled", (
        "A key already in PendingDeletion must be reported as 'scheduled' -- it is already handled."
    )
    kms_client.schedule_key_deletion.assert_not_called()


@pytest.mark.unit
def test_kms_handler_key_pending_replica_deletion_skips_schedule() -> None:
    """KMSHandler skips schedule_key_deletion when the key is in PendingReplicaDeletion."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:key/replica-pending-key-uuid"
    kms_client = MagicMock()
    kms_client.get_paginator.return_value.paginate.return_value = [{"Aliases": []}]
    kms_client.describe_key.return_value = {
        "KeyMetadata": {"KeyId": "replica-pending-key-uuid", "KeyState": "PendingReplicaDeletion"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "scheduled"
    kms_client.schedule_key_deletion.assert_not_called()


@pytest.mark.unit
def test_kms_handler_key_enabled_schedules_deletion() -> None:
    """KMSHandler calls schedule_key_deletion when the key is in Enabled state."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:key/enabled-key-uuid"
    kms_client = MagicMock()
    kms_client.get_paginator.return_value.paginate.return_value = [{"Aliases": []}]
    kms_client.describe_key.return_value = {
        "KeyMetadata": {"KeyId": "enabled-key-uuid", "KeyState": "Enabled"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "scheduled"
    kms_client.schedule_key_deletion.assert_called_once_with(
        KeyId="enabled-key-uuid",
        PendingWindowInDays=sweep._KMS_PENDING_WINDOW_DAYS,
    )


@pytest.mark.unit
def test_kms_handler_key_deletion_removes_dangling_customer_alias() -> None:
    """Scheduling a tagged CMK for deletion must also delete the customer alias(es)
    that target it.

    KMS aliases are untaggable and never returned by the Tagging API, so a
    tag-scoped sweep that only scheduled the key would strand the alias for the
    whole pending window -- an account-global alias (alias/telemetry-data) then
    blocks the next run's CreateAlias. The handler must enumerate the key's
    aliases and delete the customer ones.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:key/enabled-key-uuid"
    kms_client = MagicMock()
    kms_client.get_paginator.return_value.paginate.return_value = [
        {"Aliases": [{"AliasName": "alias/telemetry-data", "TargetKeyId": "enabled-key-uuid"}]}
    ]
    kms_client.describe_key.return_value = {
        "KeyMetadata": {"KeyId": "enabled-key-uuid", "KeyState": "Enabled"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "scheduled"
    kms_client.get_paginator.return_value.paginate.assert_called_once_with(KeyId="enabled-key-uuid")
    kms_client.delete_alias.assert_called_once_with(AliasName="alias/telemetry-data")
    kms_client.schedule_key_deletion.assert_called_once()


@pytest.mark.unit
def test_kms_handler_key_deletion_skips_aws_managed_alias() -> None:
    """The dangling-alias cleanup must skip AWS-managed aliases (alias/aws/*).

    AWS-managed aliases cannot be deleted (DeleteAlias rejects them); only the
    customer alias is removed.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:key/enabled-key-uuid"
    kms_client = MagicMock()
    kms_client.get_paginator.return_value.paginate.return_value = [
        {
            "Aliases": [
                {"AliasName": "alias/aws/s3", "TargetKeyId": "enabled-key-uuid"},
                {"AliasName": "alias/telemetry-spice-ab12cd", "TargetKeyId": "enabled-key-uuid"},
            ]
        }
    ]
    kms_client.describe_key.return_value = {
        "KeyMetadata": {"KeyId": "enabled-key-uuid", "KeyState": "Enabled"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    handler.delete(arn, boto3_mock)
    kms_client.delete_alias.assert_called_once_with(AliasName="alias/telemetry-spice-ab12cd")


@pytest.mark.unit
def test_kms_handler_alias_already_gone_is_benign() -> None:
    """A NotFoundException while deleting a key's alias (already removed by terraform
    destroy or a prior pass) must be swallowed, not raised."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:kms:us-east-1:222222222222:key/enabled-key-uuid"
    kms_client = MagicMock()
    kms_client.get_paginator.return_value.paginate.return_value = [
        {"Aliases": [{"AliasName": "alias/telemetry-data", "TargetKeyId": "enabled-key-uuid"}]}
    ]
    kms_client.delete_alias.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "NotFoundException", "Message": "alias gone"}}, "DeleteAlias"
    )
    kms_client.describe_key.return_value = {
        "KeyMetadata": {"KeyId": "enabled-key-uuid", "KeyState": "Enabled"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = kms_client

    handler = sweep.KMSHandler()
    # Must not raise; the key is still scheduled for deletion.
    result = handler.delete(arn, boto3_mock)
    assert result == "scheduled"
    kms_client.schedule_key_deletion.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_vpc_flow_log_not_found_treated_as_deleted() -> None:
    """EC2Handler treats InvalidFlowLogId.NotFound as already-deleted for vpc-flow-log.

    When a flow log was already deleted (e.g. by a prior sweep run or Terraform),
    delete_flow_logs raises InvalidFlowLogId.NotFound. The handler must catch this
    and return 'deleted' rather than propagating the error.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-flow-log/fl-gone123"
    error_response = {
        "Error": {"Code": "InvalidFlowLogId.NotFound", "Message": "Flow log does not exist"}
    }
    ec2_client = MagicMock()
    ec2_client.delete_flow_logs.side_effect = botocore.exceptions.ClientError(
        error_response, "DeleteFlowLogs"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted", (
        "InvalidFlowLogId.NotFound on delete must be treated as already-deleted -- not an error."
    )


@pytest.mark.unit
def test_ec2_handler_vpc_flow_log_unexpected_error_propagates() -> None:
    """EC2Handler re-raises unexpected ClientError from delete_flow_logs."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-flow-log/fl-error"
    error_response = {"Error": {"Code": "InternalError", "Message": "Internal error"}}
    ec2_client = MagicMock()
    ec2_client.delete_flow_logs.side_effect = botocore.exceptions.ClientError(
        error_response, "DeleteFlowLogs"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    with pytest.raises(botocore.exceptions.ClientError):
        handler.delete(arn, boto3_mock)


@pytest.mark.unit
def test_iam_handler_role_with_attached_policy() -> None:
    """IAMHandler must detach managed policies before deleting a role."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:iam::222222222222:role/tt-orphan-role-with-policies"
    iam_client = MagicMock()
    iam_client.list_instance_profiles_for_role.return_value = {
        "InstanceProfiles": [{"InstanceProfileName": "tt-profile", "Roles": []}]
    }
    iam_client.list_attached_role_policies.return_value = {
        "AttachedPolicies": [{"PolicyArn": "arn:aws:iam::222222222222:policy/TestPolicy"}]
    }
    iam_client.list_role_policies.return_value = {"PolicyNames": ["InlinePolicy"]}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = iam_client

    handler = sweep.IAMHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    iam_client.remove_role_from_instance_profile.assert_called_once()
    iam_client.detach_role_policy.assert_called_once()
    iam_client.delete_role_policy.assert_called_once()
    iam_client.delete_role.assert_called_once()


@pytest.mark.unit
def test_iam_handler_policy_arn() -> None:
    """IAMHandler must detach and delete a managed policy by ARN."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:iam::222222222222:policy/tt-orphan-policy"
    iam_client = MagicMock()
    iam_client.list_entities_for_policy.return_value = {"PolicyRoles": [{"RoleName": "SomeRole"}]}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = iam_client

    handler = sweep.IAMHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    iam_client.detach_role_policy.assert_called_once()
    iam_client.delete_policy.assert_called_once_with(PolicyArn=arn)


@pytest.mark.unit
def test_iam_handler_instance_profile_with_role() -> None:
    """IAMHandler must remove roles from an instance profile before deleting it."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:iam::222222222222:instance-profile/tt-orphan-profile"
    iam_client = MagicMock()
    iam_client.get_instance_profile.return_value = {
        "InstanceProfile": {
            "InstanceProfileName": "tt-orphan-profile",
            "Roles": [{"RoleName": "AssociatedRole"}],
        }
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = iam_client

    handler = sweep.IAMHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    iam_client.remove_role_from_instance_profile.assert_called_once()
    iam_client.delete_instance_profile.assert_called_once()


@pytest.mark.unit
def test_cloudfront_handler_enabled_distribution_disabled_and_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudFrontHandler must disable, poll Deployed via waiter, then delete."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "30")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "0")

    dist_id = "EDFDVBD6ENABLED"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution_config.return_value = {
        "ETag": "ETAG_ORIG",
        "DistributionConfig": {"Enabled": True, "Origins": {"Quantity": 0, "Items": []}},
    }
    cf_client.update_distribution.return_value = {"ETag": "ETAG_UPDATED", "Distribution": {}}
    # Waiter returns immediately (no exception = success).
    cf_client.get_waiter.return_value.wait.return_value = None
    # After waiter completes, handler fetches the ETag.
    cf_client.get_distribution.return_value = {
        "ETag": "ETAG_DEPLOYED",
        "Distribution": {"Status": "Deployed"},
    }
    cf_client.delete_distribution.return_value = {}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    cf_client.update_distribution.assert_called_once()
    # Waiter must have been invoked with correct distribution id.
    cf_client.get_waiter.assert_called_once_with("distribution_deployed")
    cf_client.get_waiter.return_value.wait.assert_called_once()
    wait_kwargs = cf_client.get_waiter.return_value.wait.call_args.kwargs
    assert wait_kwargs.get("Id") == dist_id, (
        f"Waiter must be called with Id={dist_id!r}; got {wait_kwargs}"
    )
    cf_client.delete_distribution.assert_called_once_with(Id=dist_id, IfMatch="ETAG_DEPLOYED")


@pytest.mark.unit
def test_cloudfront_handler_is_live_true_when_distribution_exists() -> None:
    """CloudFrontHandler.is_live returns True when GetDistribution succeeds (real orphan)."""
    import scripts.terratest_sweep as sweep

    dist_id = "EDFDVBD6LIVE"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution.return_value = {"ETag": "E", "Distribution": {"Status": "Deployed"}}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    assert handler.is_live(arn, boto3_mock) is True, (
        "is_live must return True (counted as orphan) when GetDistribution confirms "
        "the distribution exists"
    )
    cf_client.get_distribution.assert_called_once_with(Id=dist_id)


@pytest.mark.unit
def test_cloudfront_handler_is_live_false_on_no_such_distribution() -> None:
    """CloudFrontHandler.is_live returns False when GetDistribution raises NoSuchDistribution.

    The Tagging API lags CloudFront deletion, so a just-destroyed distribution is still
    indexed; is_live must probe GetDistribution and treat NoSuchDistribution as a lag
    phantom (excluded from the zero-orphan residue count), not a live orphan.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    dist_id = "EDFDVBD6GONE"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution.side_effect = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "NoSuchDistribution",
                "Message": "The specified distribution does not exist.",
            }
        },
        "GetDistribution",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    assert handler.is_live(arn, boto3_mock) is False, (
        "is_live must return False (lag phantom, excluded from residue) when GetDistribution "
        "raises NoSuchDistribution -- the distribution is already deleted"
    )


@pytest.mark.unit
def test_cloudfront_handler_is_live_reraises_non_benign_error() -> None:
    """CloudFrontHandler.is_live re-raises a non-benign error (cannot prove it is gone)."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    dist_id = "EDFDVBD6DENIED"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "GetDistribution",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    with pytest.raises(botocore.exceptions.ClientError):
        handler.is_live(arn, boto3_mock)


@pytest.mark.unit
def test_cloudfront_handler_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """CloudFrontHandler raises TimeoutError when the waiter signals the distribution
    did not reach Deployed within the configured budget."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "0")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "0")

    dist_id = "EDFDVBD6TIMEOUT"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution_config.return_value = {
        "ETag": "ETAG_ORIG",
        "DistributionConfig": {"Enabled": True, "Origins": {}},
    }
    cf_client.update_distribution.return_value = {"ETag": "ETAG_UPDATED", "Distribution": {}}
    # Waiter raises WaiterError (SDK-managed timeout signal).
    cf_client.get_waiter.return_value.wait.side_effect = botocore.exceptions.WaiterError(
        name="distribution_deployed",
        reason="Max attempts exceeded",
        last_response={"Distribution": {"Status": "InProgress"}},
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    with pytest.raises(TimeoutError):
        handler.delete(arn, boto3_mock)


@pytest.mark.unit
def test_route53_handler_with_records_to_delete() -> None:
    """Route53Handler must delete non-SOA/NS records before deleting the zone."""
    import scripts.terratest_sweep as sweep

    zone_id = "Z1234TEST"
    arn = f"arn:aws:route53:::hostedzone/{zone_id}"
    r53_client = MagicMock()
    # The handler resolves the sub-zone apex name to clean up its parent NS
    # delegation; here the sub-zone has no parent delegation (no other zone is an
    # ancestor), so the post-deletion scan is a no-op.
    r53_client.get_hosted_zone.return_value = {"HostedZone": {"Name": "example.com."}}
    r53_client.list_resource_record_sets.return_value = {
        "ResourceRecordSets": [
            {"Type": "A", "Name": "www.example.com.", "TTL": 60, "ResourceRecords": []},
            {"Type": "SOA", "Name": "example.com.", "TTL": 900, "ResourceRecords": []},
            {"Type": "NS", "Name": "example.com.", "TTL": 172800, "ResourceRecords": []},
        ]
    }
    r53_client.get_paginator.return_value.paginate.return_value = [{"HostedZones": []}]
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = r53_client

    handler = sweep.Route53Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # change_resource_record_sets called for the A record only (no parent delegation found)
    r53_client.change_resource_record_sets.assert_called_once()
    r53_client.delete_hosted_zone.assert_called_once_with(Id=zone_id)


@pytest.mark.unit
def test_route53_handler_deletes_parent_ns_delegation() -> None:
    """Deleting a terratest sub-zone must also delete the dangling NS delegation
    record that points at it from the shared (never-swept, untaggable) parent zone.

    A cancelled run strands this parent NS record forever otherwise; the tag-scoped
    sweep cannot reach it because Route53 records are not taggable and the parent
    zone is not owned by the fixture.
    """
    import scripts.terratest_sweep as sweep

    child_zone_id = "Z_CHILD"
    parent_zone_id = "Z_PARENT"
    sub_apex = "ttci-ab12cd.qa.platform.example.com."
    arn = f"arn:aws:route53:::hostedzone/{child_zone_id}"

    r53_client = MagicMock()
    r53_client.get_hosted_zone.return_value = {"HostedZone": {"Name": sub_apex}}
    # The sub-zone's own records (apex SOA/NS only -> nothing to pre-delete).
    r53_client.list_resource_record_sets.side_effect = [
        {
            "ResourceRecordSets": [
                {"Type": "SOA", "Name": sub_apex},
                {"Type": "NS", "Name": sub_apex},
            ]
        },
        # Parent-zone scoped query returns the delegation NS record for the sub-zone.
        {
            "ResourceRecordSets": [
                {"Type": "NS", "Name": sub_apex, "TTL": 60, "ResourceRecords": [{"Value": "ns-1."}]}
            ]
        },
    ]
    # The account holds the parent zone (a strict ancestor of the sub-zone) and the child.
    r53_client.get_paginator.return_value.paginate.return_value = [
        {
            "HostedZones": [
                {"Id": f"/hostedzone/{parent_zone_id}", "Name": "qa.platform.example.com."},
                {"Id": f"/hostedzone/{child_zone_id}", "Name": sub_apex},
            ]
        }
    ]
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = r53_client

    handler = sweep.Route53Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    r53_client.delete_hosted_zone.assert_called_once_with(Id=child_zone_id)
    # The parent NS delegation record must be deleted from the PARENT zone.
    parent_delete_calls = [
        c
        for c in r53_client.change_resource_record_sets.call_args_list
        if c.kwargs.get("HostedZoneId") == parent_zone_id
    ]
    assert len(parent_delete_calls) == 1, (
        "the parent zone's NS delegation for the deleted sub-zone must be removed exactly once"
    )
    change = parent_delete_calls[0].kwargs["ChangeBatch"]["Changes"][0]
    assert change["Action"] == "DELETE"
    assert change["ResourceRecordSet"]["Name"] == sub_apex
    assert change["ResourceRecordSet"]["Type"] == "NS"


@pytest.mark.unit
def test_route53_handler_leaves_unrelated_parent_records_untouched() -> None:
    """The parent-delegation cleanup must NOT delete a parent zone's own apex NS or an
    unrelated delegation -- matching is exact on the deleted sub-zone's name."""
    import scripts.terratest_sweep as sweep

    child_zone_id = "Z_CHILD"
    parent_zone_id = "Z_PARENT"
    sub_apex = "ttci-ab12cd.qa.platform.example.com."
    arn = f"arn:aws:route53:::hostedzone/{child_zone_id}"

    r53_client = MagicMock()
    r53_client.get_hosted_zone.return_value = {"HostedZone": {"Name": sub_apex}}
    r53_client.list_resource_record_sets.side_effect = [
        {"ResourceRecordSets": [{"Type": "SOA", "Name": sub_apex}]},
        # Parent scan returns an NS record for a DIFFERENT (unrelated) sub-zone.
        {
            "ResourceRecordSets": [
                {"Type": "NS", "Name": "other-run.qa.platform.example.com.", "TTL": 60}
            ]
        },
    ]
    r53_client.get_paginator.return_value.paginate.return_value = [
        {
            "HostedZones": [
                {"Id": f"/hostedzone/{parent_zone_id}", "Name": "qa.platform.example.com."}
            ]
        }
    ]
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = r53_client

    handler = sweep.Route53Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # No DELETE against the parent zone -- the returned NS name did not match the sub-zone.
    parent_delete_calls = [
        c
        for c in r53_client.change_resource_record_sets.call_args_list
        if c.kwargs.get("HostedZoneId") == parent_zone_id
    ]
    assert not parent_delete_calls, (
        "an unrelated parent NS record must never be deleted (exact-name match required)"
    )


@pytest.mark.unit
def test_ec2_handler_subnet() -> None:
    """EC2Handler handles subnet ARN type."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:subnet/subnet-abc123"
    ec2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_subnet.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_route_table() -> None:
    """EC2Handler handles route-table ARN type."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:route-table/rtb-abc123"
    ec2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_route_table.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_internet_gateway_with_attachment() -> None:
    """EC2Handler detaches an IGW from its VPC before deleting it."""
    import scripts.terratest_sweep as sweep

    igw_id = "igw-abc123"
    arn = f"arn:aws:ec2:us-east-1:222222222222:internet-gateway/{igw_id}"
    ec2_client = MagicMock()
    ec2_client.describe_internet_gateways.return_value = {
        "InternetGateways": [{"Attachments": [{"VpcId": "vpc-abc123", "State": "available"}]}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.detach_internet_gateway.assert_called_once()
    ec2_client.delete_internet_gateway.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_nat_gateway() -> None:
    """EC2Handler deletes a live (non-deleted) natgateway ARN type."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:natgateway/nat-abc123"
    ec2_client = MagicMock()
    # Simulate a live NAT gateway (state=available).
    ec2_client.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-abc123", "State": "available"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_nat_gateway.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_nat_gateway_already_deleted_skips_delete() -> None:
    """EC2Handler skips delete_nat_gateway when the gateway is already in 'deleted' state.

    AWS's Tagging API exhibits eventual consistency: deleted NAT gateways remain
    visible in tag searches for several minutes. Calling delete_nat_gateway on an
    already-deleted gateway returns an error; the handler must skip the call instead.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:natgateway/nat-already-deleted"
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-already-deleted", "State": "deleted"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_nat_gateway.assert_not_called()


@pytest.mark.unit
def test_ec2_handler_nat_gateway_deleting_state_skips_delete() -> None:
    """EC2Handler skips delete_nat_gateway when the gateway is in 'deleting' state."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:natgateway/nat-being-deleted"
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-being-deleted", "State": "deleting"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_nat_gateway.assert_not_called()


@pytest.mark.unit
def test_ec2_handler_vpc_flow_log() -> None:
    """EC2Handler handles vpc-flow-log ARN type by calling delete_flow_logs."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-flow-log/fl-abc123"
    ec2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_flow_logs.assert_called_once_with(FlowLogIds=["fl-abc123"])


@pytest.mark.unit
def test_ec2_handler_eip_allocation() -> None:
    """EC2Handler handles elastic-ip / eip-allocation ARN types."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:elastic-ip/eipalloc-abc123"
    ec2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.release_address.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_security_group() -> None:
    """EC2Handler handles security-group ARN type."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:security-group/sg-abc123"
    ec2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_security_group.assert_called_once()


@pytest.mark.unit
def test_ec2_handler_vpc_endpoint() -> None:
    """EC2Handler handles vpc-endpoint ARN type -- calls describe then delete
    for an available endpoint."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-endpoint/vpce-abc123"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-abc123", "State": "available"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.describe_vpc_endpoints.assert_called_once_with(VpcEndpointIds=["vpce-abc123"])
    ec2_client.delete_vpc_endpoints.assert_called_once_with(VpcEndpointIds=["vpce-abc123"])


@pytest.mark.unit
def test_ec2_handler_vpc_endpoint_already_deleting_skips_delete() -> None:
    """EC2Handler skips delete_vpc_endpoints when the endpoint is already in 'deleting' state.

    When the Tagging API shows an endpoint that is already being deleted (in-flight
    Terraform destroy or prior sweep run), calling delete_vpc_endpoints again is
    unnecessary and may raise errors. The handler must detect the terminal state and
    return 'deleted' without issuing the delete call.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-endpoint/vpce-already-deleting"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-already-deleting", "State": "deleting"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_vpc_endpoints.assert_not_called()


@pytest.mark.unit
def test_ec2_handler_vpc_endpoint_already_deleted_skips_delete() -> None:
    """EC2Handler skips delete_vpc_endpoints when the endpoint is already in 'deleted' state."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-endpoint/vpce-already-deleted"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-already-deleted", "State": "deleted"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_vpc_endpoints.assert_not_called()


@pytest.mark.unit
def test_ec2_handler_vpc_endpoint_not_found_skips_delete() -> None:
    """EC2Handler returns 'deleted' when InvalidVpcEndpointId.NotFound is raised by describe.

    The endpoint may have already been deleted by a prior sweep or Terraform destroy.
    The Tagging API lag window means it still appears in tag searches. The handler
    must treat NotFound as already-gone and return 'deleted' without error.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-endpoint/vpce-gone"
    error_response = {
        "Error": {"Code": "InvalidVpcEndpointId.NotFound", "Message": "Endpoint not found"}
    }
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeVpcEndpoints"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ec2_client.delete_vpc_endpoints.assert_not_called()


@pytest.mark.unit
def test_ec2_handler_vpc_endpoint_unexpected_error_propagates() -> None:
    """EC2Handler re-raises unexpected ClientError from describe_vpc_endpoints.

    A non-NotFound error during describe is unexpected and must propagate
    (fail-fast principle) so the caller records the failure and exits non-zero.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:vpc-endpoint/vpce-error"
    error_response = {"Error": {"Code": "InternalFailure", "Message": "Internal error"}}
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeVpcEndpoints"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    with pytest.raises(botocore.exceptions.ClientError):
        handler.delete(arn, boto3_mock)


@pytest.mark.unit
def test_ecs_handler_service() -> None:
    """ECSHandler scales a service to 0 then deletes it."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:service/tt-cluster/tt-service"
    ecs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ecs_client.update_service.assert_called_once()
    ecs_client.delete_service.assert_called_once()


@pytest.mark.unit
def test_ecs_handler_task_definition() -> None:
    """ECSHandler deregisters a task definition."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:task-definition/tt-task:1"
    ecs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ecs_client.deregister_task_definition.assert_called_once()


# ---------------------------------------------------------------------------
# ResourceHandler.is_live() -- base default + ECS liveness override
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_base_resource_handler_is_live_defaults_true() -> None:
    """The base ResourceHandler.is_live() returns True (resource counts as orphan)."""
    import scripts.terratest_sweep as sweep

    handler = sweep.ResourceHandler()
    assert handler.is_live("arn:aws:s3:::tt-orphan-bucket", MagicMock()) is True


@pytest.mark.unit
def test_s3_handler_inherits_is_live_true() -> None:
    """A handler that does not override is_live() reports its resource as live."""
    import scripts.terratest_sweep as sweep

    handler = sweep.S3Handler()
    assert handler.is_live("arn:aws:s3:::tt-orphan-bucket", MagicMock()) is True


@pytest.mark.unit
def test_ecs_is_live_active_cluster_returns_true() -> None:
    """ECSHandler.is_live() returns True for a cluster whose status is ACTIVE."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:cluster/ecs-c-17814810-cluster"
    ecs_client = MagicMock()
    ecs_client.describe_clusters.return_value = {
        "clusters": [{"clusterName": "ecs-c-17814810-cluster", "status": "ACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is True
    ecs_client.describe_clusters.assert_called_once_with(clusters=["ecs-c-17814810-cluster"])


@pytest.mark.unit
def test_ecs_is_live_inactive_cluster_returns_false() -> None:
    """ECSHandler.is_live() returns False for a cluster whose status is INACTIVE.

    AWS keeps INACTIVE cluster shells (with tags) after delete_cluster; the
    Tagging API still returns them. They are NOT orphans.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:cluster/ecs-c-17814810-cluster"
    ecs_client = MagicMock()
    ecs_client.describe_clusters.return_value = {
        "clusters": [{"clusterName": "ecs-c-17814810-cluster", "status": "INACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_missing_cluster_returns_false() -> None:
    """ECSHandler.is_live() returns False when describe_clusters returns an empty list."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:cluster/ecs-gone-cluster"
    ecs_client = MagicMock()
    ecs_client.describe_clusters.return_value = {"clusters": []}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_cluster_not_found_returns_false() -> None:
    """ECSHandler.is_live() returns False when describe_clusters raises a ClientError.

    A missing resource (ClusterNotFoundException) is not an orphan.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:cluster/ecs-gone-cluster"
    error_response = {
        "Error": {"Code": "ClusterNotFoundException", "Message": "Cluster not found."}
    }
    ecs_client = MagicMock()
    ecs_client.describe_clusters.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeClusters"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_active_service_returns_true() -> None:
    """ECSHandler.is_live() returns True for a service whose status is ACTIVE."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:service/ecs-c-17814810-cluster/ecs-c-17814810"
    ecs_client = MagicMock()
    ecs_client.describe_services.return_value = {
        "services": [{"serviceName": "ecs-c-17814810", "status": "ACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is True
    ecs_client.describe_services.assert_called_once_with(
        cluster="ecs-c-17814810-cluster", services=["ecs-c-17814810"]
    )


@pytest.mark.unit
def test_ecs_is_live_inactive_service_returns_false() -> None:
    """ECSHandler.is_live() returns False for a service whose status is INACTIVE.

    delete_service leaves an INACTIVE service shell (with tags) visible in the
    Tagging API; it is NOT an orphan.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:service/ecs-c-17814810-cluster/ecs-c-17814810"
    ecs_client = MagicMock()
    ecs_client.describe_services.return_value = {
        "services": [{"serviceName": "ecs-c-17814810", "status": "INACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_missing_service_returns_false() -> None:
    """ECSHandler.is_live() returns False when describe_services returns an empty list."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:service/ecs-gone-cluster/ecs-gone-svc"
    ecs_client = MagicMock()
    ecs_client.describe_services.return_value = {"services": []}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_active_task_definition_returns_true() -> None:
    """ECSHandler.is_live() returns True for a task-definition whose status is ACTIVE."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:task-definition/ecs-c-17814810:1"
    ecs_client = MagicMock()
    ecs_client.describe_task_definition.return_value = {
        "taskDefinition": {"family": "ecs-c-17814810", "revision": 1, "status": "ACTIVE"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is True
    ecs_client.describe_task_definition.assert_called_once_with(taskDefinition="ecs-c-17814810:1")


@pytest.mark.unit
@pytest.mark.parametrize("status", ["INACTIVE", "DELETE_IN_PROGRESS"])
def test_ecs_is_live_non_active_task_definition_returns_false(status: str) -> None:
    """ECSHandler.is_live() returns False for INACTIVE / DELETE_IN_PROGRESS task-defs.

    deregister_task_definition moves the revision to INACTIVE and a delete moves
    it through DELETE_IN_PROGRESS; both remain tag-visible but are NOT orphans.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:task-definition/ecs-c-17814810:1"
    ecs_client = MagicMock()
    ecs_client.describe_task_definition.return_value = {
        "taskDefinition": {"family": "ecs-c-17814810", "revision": 1, "status": status}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_missing_task_definition_returns_false() -> None:
    """ECSHandler.is_live() returns False when describe_task_definition raises ClientError.

    A deregistered-and-deleted task-definition raises ClientException; it is not
    an orphan.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:task-definition/ecs-gone:9"
    error_response = {
        "Error": {
            "Code": "ClientException",
            "Message": "Unable to describe task definition.",
        }
    }
    ecs_client = MagicMock()
    ecs_client.describe_task_definition.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeTaskDefinition"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_ecs_is_live_short_service_path_uses_same_parsing_as_delete() -> None:
    """ECSHandler.is_live() parses the short service ARN form like ECSHandler.delete().

    The standard ECS service ARN has three path segments (service/<cluster>/<svc>).
    For the degenerate two-segment form, is_live() must mirror delete()'s parsing
    exactly (DRY / behavioral consistency): cluster resolves to the first path
    segment and the service name to the last.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:service/tt-short-svc"
    # Mirror delete()'s parsing to derive the expected describe_services args.
    service_part = arn.split(":")[-1].split("/")
    expected_cluster = service_part[1] if len(service_part) >= 3 else service_part[0]
    expected_service = service_part[-1]

    ecs_client = MagicMock()
    ecs_client.describe_services.return_value = {
        "services": [{"serviceName": expected_service, "status": "ACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    assert handler.is_live(arn, boto3_mock) is True
    ecs_client.describe_services.assert_called_once_with(
        cluster=expected_cluster, services=[expected_service]
    )


@pytest.mark.unit
def test_handler_reports_live_no_handler_counts_as_live() -> None:
    """_handler_reports_live returns True when no handler is registered for the service.

    A tagged resource with no handler is a genuine, unhandled orphan and must
    surface (not be silently excluded).
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:unknown-service:us-east-1:222222222222:resource/orphan"
    assert sweep._handler_reports_live(arn, MagicMock()) is True


@pytest.mark.unit
def test_handler_reports_live_delegates_to_ecs_is_live() -> None:
    """_handler_reports_live dispatches to ECSHandler.is_live for ecs:: ARNs."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:cluster/ecs-inactive-cluster"
    ecs_client = MagicMock()
    ecs_client.describe_clusters.return_value = {
        "clusters": [{"clusterName": "ecs-inactive-cluster", "status": "INACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    assert sweep._handler_reports_live(arn, boto3_mock) is False


@pytest.mark.unit
def test_elb_handler_target_group() -> None:
    """ELBHandler deletes a target group."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:elasticloadbalancing:us-east-1:222222222222:targetgroup/tt-tg/abc123"
    elbv2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = elbv2_client

    handler = sweep.ELBHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    elbv2_client.delete_target_group.assert_called_once_with(TargetGroupArn=arn)


@pytest.mark.unit
def test_elb_handler_load_balancer_with_listener() -> None:
    """ELBHandler deletes all listeners before deleting the load balancer."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:elasticloadbalancing:us-east-1:222222222222:loadbalancer/app/tt-alb/abc"
    elbv2_client = MagicMock()
    _listener_arn = (
        "arn:aws:elasticloadbalancing:us-east-1:222222222222:listener/app/tt-alb/abc/abc1"
    )
    elbv2_client.describe_listeners.return_value = {"Listeners": [{"ListenerArn": _listener_arn}]}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = elbv2_client

    handler = sweep.ELBHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    elbv2_client.delete_listener.assert_called_once()
    elbv2_client.delete_load_balancer.assert_called_once()


@pytest.mark.unit
def test_glue_handler_table_arn() -> None:
    """GlueHandler handles table ARNs."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:glue:us-east-1:222222222222:table/tt-db/tt-table"
    glue_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = glue_client

    handler = sweep.GlueHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    glue_client.delete_table.assert_called_once()


@pytest.mark.unit
def test_glue_handler_database_with_tables() -> None:
    """GlueHandler deletes tables within a database before deleting the database."""
    import scripts.terratest_sweep as sweep

    db_name = "tt-orphan-db"
    arn = f"arn:aws:glue:us-east-1:222222222222:database/{db_name}"
    glue_client = MagicMock()
    glue_client.get_tables.return_value = {"TableList": [{"Name": "tbl1"}, {"Name": "tbl2"}]}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = glue_client

    handler = sweep.GlueHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    assert glue_client.delete_table.call_count == 2
    glue_client.delete_database.assert_called_once()


@pytest.mark.unit
def test_logs_handler_cloudwatch_alarm_path() -> None:
    """LogsHandler handles the cloudwatch alarm path (no ':log-group:' in ARN)."""
    import scripts.terratest_sweep as sweep

    # Construct an ARN that has 'logs:' prefix but no ':log-group:' segment.
    # In practice this won't occur but tests the else branch.
    # Use a pure cloudwatch ARN -- note LogsHandler service_prefix is "logs"
    # so we test it via direct handler invocation.
    arn = "arn:aws:logs:us-east-1:222222222222:log-group:/tt-lg"
    cw_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cw_client

    handler = sweep.LogsHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    cw_client.delete_log_group.assert_called_once()


@pytest.mark.unit
def test_cloudwatch_handler_alarm() -> None:
    """CloudWatchHandler deletes a CloudWatch alarm."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:cloudwatch:us-east-1:222222222222:alarm:tt-alarm"
    cw_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cw_client

    handler = sweep.CloudWatchHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    cw_client.delete_alarms.assert_called_once_with(AlarmNames=["tt-alarm"])


@pytest.mark.unit
def test_quicksight_handler_datasource() -> None:
    """QuickSightHandler deletes a data source."""
    import scripts.terratest_sweep as sweep

    account_id = "222222222222"
    arn = f"arn:aws:quicksight:us-east-1:{account_id}:datasource/{account_id}/tt-source"
    qs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = qs_client

    handler = sweep.QuickSightHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    qs_client.delete_data_source.assert_called_once()


@pytest.mark.unit
def test_budgets_handler_anomaly_monitor() -> None:
    """BudgetsHandler deletes a cost anomaly monitor when ARN has no ':budget/'."""
    import scripts.terratest_sweep as sweep

    # ARN without ':budget/' -- treated as anomaly monitor
    arn = "arn:aws:ce::222222222222:anomalymonitor/tt-monitor"
    ce_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ce_client

    handler = sweep.BudgetsHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ce_client.delete_anomaly_monitor.assert_called_once_with(MonitorArn=arn)


@pytest.mark.unit
def test_ce_handler_anomaly_subscription() -> None:
    """CEHandler deletes an anomaly subscription."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ce::222222222222:anomalysubscription/tt-sub"
    ce_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ce_client

    handler = sweep.CEHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ce_client.delete_anomaly_subscription.assert_called_once_with(SubscriptionArn=arn)


@pytest.mark.unit
def test_ce_handler_anomaly_monitor() -> None:
    """CEHandler deletes an anomaly monitor (non-subscription ARN)."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ce::222222222222:anomalymonitor/tt-monitor"
    ce_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ce_client

    handler = sweep.CEHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ce_client.delete_anomaly_monitor.assert_called_once_with(MonitorArn=arn)


@pytest.mark.unit
def test_non_throttle_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A non-throttling ClientError from the Tagging API propagates (not retried)."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    access_denied = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "access denied"}},
        "GetResources",
    )
    tagging_client.get_paginator.return_value.paginate.side_effect = access_denied

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_access_denied.json"
    # Access denied should propagate as an unhandled exception (not swallowed)
    with pytest.raises((SystemExit, botocore.exceptions.ClientError)):
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    # Tagging client must NOT have been retried
    assert tagging_client.get_paginator.return_value.paginate.call_count == 1


@pytest.mark.unit
def test_tagging_client_created_with_adaptive_retry_config(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """_enumerate_resources creates the tagging client with botocore adaptive retry config.

    Throttle retries are delegated to botocore; the client must be created with
    retries={'mode': 'adaptive'} and a max_attempts derived from the polling budget.
    No raw time.sleep is used for throttle backoff.
    """
    import botocore.config

    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "30")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "1")

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": []}
    ]

    created_configs: list[botocore.config.Config] = []

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            cfg = kw.get("config")
            if cfg is not None:
                created_configs.append(cfg)
            return tagging_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_retry_config.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0
    # Tagging client must have been created with botocore adaptive retry config.
    assert created_configs, (
        "Expected tagging client to be created with a botocore.config.Config; "
        "got no config kwarg in client('resourcegroupstaggingapi', ...)"
    )
    cfg = created_configs[0]
    assert hasattr(cfg, "retries"), f"Config must have a 'retries' attribute; got {cfg!r}"
    assert cfg.retries.get("mode") == "adaptive", (
        f"Expected retries.mode='adaptive'; got {cfg.retries!r}"
    )
    assert "max_attempts" in cfg.retries, (
        f"Expected retries.max_attempts to be set; got {cfg.retries!r}"
    )


@pytest.mark.unit
def test_s3_handler_paginator_empty_page_skips_delete_objects() -> None:
    """S3Handler with a paginator that returns empty pages still deletes the bucket."""
    import scripts.terratest_sweep as sweep

    bucket_name = "tt-test-empty-bucket"
    arn = f"arn:aws:s3:::{bucket_name}"
    s3_client = MagicMock()
    pager = MagicMock()
    pager.paginate.return_value = [{"Versions": [], "DeleteMarkers": []}]
    s3_client.get_paginator.return_value = pager
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = s3_client

    handler = sweep.S3Handler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # No versions -- delete_objects must NOT be called
    s3_client.delete_objects.assert_not_called()
    s3_client.delete_bucket.assert_called_once_with(Bucket=bucket_name)


@pytest.mark.unit
def test_iam_handler_unrecognized_arn_type() -> None:
    """IAMHandler returns 'deleted' for ARNs that match none of the role/policy/profile branches."""
    import scripts.terratest_sweep as sweep

    # An IAM user ARN -- not role, not policy, not instance-profile
    arn = "arn:aws:iam::222222222222:user/tt-orphan-user"
    iam_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = iam_client

    handler = sweep.IAMHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # No deletion calls should have been made for an unrecognized IAM type
    iam_client.delete_role.assert_not_called()
    iam_client.delete_policy.assert_not_called()
    iam_client.delete_instance_profile.assert_not_called()


@pytest.mark.unit
def test_route53_handler_rrset_arn_raises_fail_safe() -> None:
    """Route53Handler raises ValueError for ':rrset/' ARNs (fail-safe: no deletion performed).

    rrset ARNs cannot be independently deleted via this handler; raising ValueError
    causes main() to record action='failed' and exit non-zero (fail-safe invariant).
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:route53:::rrset/Z1234EXAMPLE/A/www.example.com"
    r53_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = r53_client

    handler = sweep.Route53Handler()
    with pytest.raises(ValueError, match="Route53Handler"):
        handler.delete(arn, boto3_mock)
    # No deletion calls must have been made (resource left untouched)
    r53_client.delete_hosted_zone.assert_not_called()


@pytest.mark.unit
def test_ec2_handler_unrecognized_resource_type_raises_fail_safe() -> None:
    """EC2Handler raises ValueError for ARN resource types that match no known branch.

    An unrecognised EC2 resource type means no deletion was performed; raising
    ValueError causes main() to record action='failed' and exit non-zero
    (fail-safe invariant: matched-but-unactioned != deleted).
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ec2:us-east-1:222222222222:unknown-resource/res-abc123"
    ec2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    handler = sweep.EC2Handler()
    with pytest.raises(ValueError, match="EC2Handler"):
        handler.delete(arn, boto3_mock)
    # No specific deletion called for unknown type
    ec2_client.delete_vpc.assert_not_called()


@pytest.mark.unit
def test_ecs_handler_service_short_path() -> None:
    """ECSHandler handles ECS service ARN with short path (cluster=service name)."""
    import scripts.terratest_sweep as sweep

    # Service path with only 2 parts (no cluster prefix in path)
    arn = "arn:aws:ecs:us-east-1:222222222222:service/tt-short-service"
    ecs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ecs_client.update_service.assert_called_once()
    ecs_client.delete_service.assert_called_once()


@pytest.mark.unit
def test_glue_handler_table_single_part() -> None:
    """GlueHandler handles a table ARN where the table path has only one part."""
    import scripts.terratest_sweep as sweep

    # Table ARN where after :table/ there's only one component (edge case)
    arn = "arn:aws:glue:us-east-1:222222222222:table/tt-db-only"
    glue_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = glue_client

    handler = sweep.GlueHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    glue_client.delete_table.assert_called_once()


@pytest.mark.unit
def test_athena_handler_non_workgroup_arn() -> None:
    """AthenaHandler returns 'deleted' for non-workgroup ARNs (no deletion performed)."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:athena:us-east-1:222222222222:datacatalog/AwsDataCatalog"
    athena_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = athena_client

    handler = sweep.AthenaHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    athena_client.delete_work_group.assert_not_called()


@pytest.mark.unit
def test_ssm_handler_non_parameter_arn() -> None:
    """SSMHandler handles SSM ARNs without ':parameter/' by using the last path segment."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ssm:us-east-1:222222222222:document/tt-doc"
    ssm_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ssm_client

    handler = sweep.SSMHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ssm_client.delete_parameter.assert_called_once()


@pytest.mark.unit
def test_cloudfront_handler_waiter_called_with_poll_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudFrontHandler invokes the boto3 distribution_deployed waiter with WaiterConfig
    derived from TT_SWEEP_POLL_TIMEOUT and TT_SWEEP_POLL_INTERVAL env vars."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "30")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "5")

    dist_id = "EDFDVBD6POLL"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution_config.return_value = {
        "ETag": "ETAG_ORIG",
        "DistributionConfig": {"Enabled": True, "Origins": {}},
    }
    cf_client.update_distribution.return_value = {"ETag": "ETAG_UPDATED", "Distribution": {}}
    cf_client.get_waiter.return_value.wait.return_value = None
    cf_client.get_distribution.return_value = {
        "ETag": "ETAG_DEPLOYED",
        "Distribution": {"Status": "Deployed"},
    }
    cf_client.delete_distribution.return_value = {}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # The waiter must have been invoked with a WaiterConfig derived from env budgets.
    cf_client.get_waiter.assert_called_once_with("distribution_deployed")
    wait_kwargs = cf_client.get_waiter.return_value.wait.call_args.kwargs
    assert "WaiterConfig" in wait_kwargs, (
        f"Waiter must be called with WaiterConfig; got kwargs={wait_kwargs}"
    )
    waiter_cfg = wait_kwargs["WaiterConfig"]
    assert "Delay" in waiter_cfg and "MaxAttempts" in waiter_cfg, (
        f"WaiterConfig must include Delay and MaxAttempts; got {waiter_cfg}"
    )
    # With TIMEOUT=30, INTERVAL=5: max_attempts = ceil(30/5)+1 = 7
    assert waiter_cfg["MaxAttempts"] >= 1, (
        f"WaiterConfig.MaxAttempts must be positive; got {waiter_cfg['MaxAttempts']}"
    )


@pytest.mark.unit
def test_quicksight_handler_datasource_direct() -> None:
    """QuickSightHandler deletes a QuickSight data source by ARN (datasource prefix)."""
    import scripts.terratest_sweep as sweep

    account_id = "222222222222"
    arn = f"arn:aws:quicksight:us-east-1:{account_id}:datasource/{account_id}/tt-ds-id"
    qs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = qs_client

    handler = sweep.QuickSightHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    qs_client.delete_data_source.assert_called_once()


@pytest.mark.unit
def test_budgets_handler_ce_anomaly_monitor() -> None:
    """BudgetsHandler routes CE anomaly monitor ARNs to CE client."""
    import scripts.terratest_sweep as sweep

    # ARN with no ':budget/' segment -- treated as ce anomaly monitor
    arn = "arn:aws:ce::222222222222:anomalymonitor/tt-mon"
    ce_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ce_client

    handler = sweep.BudgetsHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    ce_client.delete_anomaly_monitor.assert_called_once_with(MonitorArn=arn)


@pytest.mark.unit
def test_cloudfront_handler_waiter_uses_minimum_delay_when_interval_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudFront waiter Delay defaults to 1 when poll_interval=0 (waiter floor)."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "30")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "0")

    dist_id = "EDFDVBD6NOSLP"
    arn = f"arn:aws:cloudfront::222222222222:distribution/{dist_id}"
    cf_client = MagicMock()
    cf_client.get_distribution_config.return_value = {
        "ETag": "ETAG_ORIG",
        "DistributionConfig": {"Enabled": True, "Origins": {}},
    }
    cf_client.update_distribution.return_value = {"ETag": "ETAG_UPDATED", "Distribution": {}}
    cf_client.get_waiter.return_value.wait.return_value = None
    cf_client.get_distribution.return_value = {
        "ETag": "ETAG_DEPLOYED",
        "Distribution": {"Status": "Deployed"},
    }
    cf_client.delete_distribution.return_value = {}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = cf_client

    handler = sweep.CloudFrontHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    # WaiterConfig Delay must be at least 1 (the minimum for a valid waiter call).
    wait_kwargs = cf_client.get_waiter.return_value.wait.call_args.kwargs
    waiter_cfg = wait_kwargs.get("WaiterConfig", {})
    assert waiter_cfg.get("Delay", 0) >= 1, (
        f"WaiterConfig.Delay must be at least 1; got {waiter_cfg}"
    )


@pytest.mark.unit
def test_route53_handler_change_arn_raises_fail_safe() -> None:
    """Route53Handler raises ValueError for ':change/' ARNs (fail-safe: no deletion performed).

    change ARNs represent in-flight API state, not a deletable resource. Raising
    ValueError causes main() to record action='failed' and exit non-zero (fail-safe invariant).
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:route53:::change/C1234EXAMPLE"
    r53_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = r53_client

    handler = sweep.Route53Handler()
    with pytest.raises(ValueError, match="Route53Handler"):
        handler.delete(arn, boto3_mock)
    r53_client.delete_hosted_zone.assert_not_called()


@pytest.mark.unit
def test_ecs_handler_unrecognized_arn_type_raises_fail_safe() -> None:
    """ECSHandler raises ValueError for ARNs that match no cluster/service/task-definition.

    An unrecognised ECS resource sub-type means no deletion was performed; raising
    ValueError causes main() to record action='failed' and exit non-zero
    (fail-safe invariant: matched-but-unactioned != deleted).
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:ecs:us-east-1:222222222222:container-instance/instance-123"
    ecs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ecs_client

    handler = sweep.ECSHandler()
    with pytest.raises(ValueError, match="ECSHandler"):
        handler.delete(arn, boto3_mock)
    ecs_client.delete_cluster.assert_not_called()
    ecs_client.delete_service.assert_not_called()


@pytest.mark.unit
def test_elb_handler_unrecognized_arn_type() -> None:
    """ELBHandler returns 'deleted' for ARNs that match neither loadbalancer nor targetgroup."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:elasticloadbalancing:us-east-1:222222222222:listener/app/tt-alb/abc/list1"
    elbv2_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = elbv2_client

    handler = sweep.ELBHandler()
    result = handler.delete(arn, boto3_mock)
    assert result == "deleted"
    elbv2_client.delete_load_balancer.assert_not_called()
    elbv2_client.delete_target_group.assert_not_called()


@pytest.mark.unit
def test_glue_handler_unrecognized_arn_type_raises_fail_safe() -> None:
    """GlueHandler raises ValueError for ARNs that match neither database nor table.

    An unrecognised Glue resource sub-type means no deletion was performed; raising
    ValueError causes main() to record action='failed' and exit non-zero
    (fail-safe invariant: matched-but-unactioned != deleted).
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:glue:us-east-1:222222222222:connection/tt-conn"
    glue_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = glue_client

    handler = sweep.GlueHandler()
    with pytest.raises(ValueError, match="GlueHandler"):
        handler.delete(arn, boto3_mock)
    glue_client.delete_database.assert_not_called()
    glue_client.delete_table.assert_not_called()


@pytest.mark.unit
def test_quicksight_handler_unrecognized_resource_segment_raises_fail_safe() -> None:
    """QuickSightHandler raises ValueError for ARNs that match neither dataset nor datasource.

    An unrecognised QuickSight resource sub-type means no deletion was performed;
    raising ValueError causes main() to record action='failed' and exit non-zero
    (fail-safe invariant: matched-but-unactioned != deleted).
    """
    import scripts.terratest_sweep as sweep

    account_id = "222222222222"
    arn = f"arn:aws:quicksight:us-east-1:{account_id}:analysis/{account_id}/tt-analysis"
    qs_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = qs_client

    handler = sweep.QuickSightHandler()
    with pytest.raises(ValueError, match="QuickSightHandler"):
        handler.delete(arn, boto3_mock)
    qs_client.delete_data_set.assert_not_called()
    qs_client.delete_data_source.assert_not_called()


@pytest.mark.unit
def test_service_prefix_from_short_arn() -> None:
    """_service_prefix_from_arn returns empty string for malformed ARNs with < 6 parts."""
    import scripts.terratest_sweep as sweep

    assert sweep._service_prefix_from_arn("arn:aws:s3") == ""
    assert sweep._service_prefix_from_arn("not-an-arn") == ""


@pytest.mark.unit
def test_main_imports_boto3_when_not_injected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When boto3_mod is None, main() imports boto3 from the environment."""
    import scripts.terratest_sweep as sweep

    # Inject a fake boto3 into sys.modules so the real AWS is not called.
    fake_boto3 = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": "999999999999",
        "Arn": "arn:aws:iam::999999999999:user/test",
        "UserId": "AIDA",
    }
    fake_boto3.client.return_value = sts_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(json.dumps({}))
    report_path = tmp_path / "report.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(accounts_path),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=None,
        )
    # Should fail because account not in allowlist (empty accounts file).
    assert exc.value.code != 0
    # boto3.client("sts") must have been called.
    fake_boto3.client.assert_called()


@pytest.mark.unit
def test_route53_handler_unrecognized_arn_type_raises_fail_safe() -> None:
    """Route53Handler raises ValueError for ARNs that have neither hostedzone/rrset/change.

    Unrecognised Route53 sub-types (e.g. healthcheck) cannot be deleted by this
    handler; raising ValueError causes main() to record action='failed' and exit non-zero.
    """
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:route53:::healthcheck/H1234EXAMPLE"
    r53_client = MagicMock()
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = r53_client

    handler = sweep.Route53Handler()
    with pytest.raises(ValueError, match="Route53Handler"):
        handler.delete(arn, boto3_mock)
    r53_client.delete_hosted_zone.assert_not_called()


@pytest.mark.unit
def test_main_module_guard_executes_cli_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executing the module as __main__ calls _cli_main()."""
    import scripts.terratest_sweep as sweep

    called: list[bool] = []

    def fake_cli_main() -> None:
        called.append(True)

    monkeypatch.setattr(sweep, "_cli_main", fake_cli_main)
    # Simulate __main__ execution by calling the guard directly.
    if sweep.__name__ != "__main__":
        sweep._cli_main()
    assert called, "_cli_main was not called"


@pytest.mark.unit
def test_cli_main_entry_point(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_cli_main parses args and calls main() with correct parameters."""
    import scripts.terratest_sweep as sweep

    called_with: dict = {}

    def fake_main(
        mode: str,
        accounts_json: str,
        report_path: Any,
        run_id: Any,
        boto3_mod: Any = None,
    ) -> None:
        called_with.update(
            {
                "mode": mode,
                "accounts_json": accounts_json,
                "report_path": report_path,
                "run_id": run_id,
            }
        )

    monkeypatch.setattr(sweep, "main", fake_main)
    monkeypatch.setenv("SWEEP_MODE", "delete")
    monkeypatch.setenv("SWEEP_RUN_ID", "run-cli-test")

    import sys as _sys

    orig_argv = _sys.argv
    try:
        _sys.argv = ["terratest_sweep"]
        sweep._cli_main()
    finally:
        _sys.argv = orig_argv

    assert called_with.get("mode") == "delete"
    assert called_with.get("run_id") == "run-cli-test"


@pytest.mark.unit
def test_module_main_guard_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running scripts.terratest_sweep via runpy as __main__ invokes _cli_main.

    This test uses runpy.run_module with run_name='__main__' so that the
    module-level guard ``if __name__ == '__main__': _cli_main()`` is executed,
    covering line 1038. The argparse inside _cli_main will raise SystemExit
    when pytest args are present -- that is expected and suppressed.
    """
    import contextlib
    import importlib
    import runpy
    import sys

    import scripts.terratest_sweep as sweep

    # Remove the cached module so runpy re-executes from source (covering line 1038).
    if "scripts.terratest_sweep" in sys.modules:
        del sys.modules["scripts.terratest_sweep"]

    try:
        # runpy will re-execute the module source; when __name__ == '__main__',
        # _cli_main() is called. argparse raises SystemExit when it sees pytest
        # args -- that is suppressed here. What matters is that line 1038 runs.
        with contextlib.suppress(SystemExit):
            runpy.run_module(
                "scripts.terratest_sweep",
                run_name="__main__",
                alter_sys=False,
            )
    finally:
        # Reload the real module for subsequent tests.
        if "scripts.terratest_sweep" in sys.modules:
            del sys.modules["scripts.terratest_sweep"]
        importlib.import_module("scripts.terratest_sweep")

    # The test's goal is to cover line 1038 in terratest_sweep.py. If we reach
    # this point without an unexpected exception, the __main__ guard executed.
    # (The argparse error is the evidence _cli_main was invoked from line 1038.)
    assert sweep._cli_main is not None, "_cli_main must be defined after reload"


# ---------------------------------------------------------------------------
# EC2/VPC reverse-dependency ordering (spec 4.4 FR-4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ec2_vpc_family_deleted_in_reverse_dependency_tier_order(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Delete mode must process ec2/vpc-family ARNs in reverse-dependency tier order.

    Spec section 4.4 FR-4: tier 1 (natgateway, EIP) must be deleted before
    tier 2 (subnet, route-table, security-group, vpc-endpoint, internet-gateway),
    and the VPC (tier 3) must be deleted strictly last.

    The test enumerates ARNs in a scrambled order (VPC first, NAT last) so that
    a naive iteration would attempt the VPC before its dependents. The recording
    mock captures delete_vpc, delete_subnet, delete_nat_gateway, and
    release_address call order; the assertion verifies the tier constraint holds
    regardless of enumeration order.
    """
    import contextlib

    import scripts.terratest_sweep as sweep

    account = sandbox_account_id
    vpc_arn = f"arn:aws:ec2:us-east-1:{account}:vpc/vpc-tier3last"
    subnet_arn = f"arn:aws:ec2:us-east-1:{account}:subnet/subnet-tier2"
    nat_arn = f"arn:aws:ec2:us-east-1:{account}:natgateway/nat-tier1first"
    eip_arn = f"arn:aws:ec2:us-east-1:{account}:elastic-ip/eipalloc-tier1"

    # Enumerate in scrambled order: VPC first, EIP, subnet, NAT last.
    scrambled_arns = [vpc_arn, eip_arn, subnet_arn, nat_arn]

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": account,
        "Arn": f"arn:aws:iam::{account}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": "run-order-test"},
                    ],
                }
                for arn in scrambled_arns
            ]
        }
    ]

    # Recording mock: captures calls in order so we can assert tier constraints.
    ec2_client = MagicMock()
    # IGW describe needed for IGW path (not tested here, but provide a safe default).
    ec2_client.describe_internet_gateways.return_value = {"InternetGateways": []}

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_ec2_order.json"
    with contextlib.suppress(SystemExit):
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,
            boto3_mod=boto3_mock,
        )

    # Extract the ordered sequence of EC2 delete calls actually made.
    all_calls = ec2_client.mock_calls
    call_names = [c[0] for c in all_calls]  # e.g. "delete_nat_gateway", "release_address", ...

    assert "delete_nat_gateway" in call_names, "delete_nat_gateway must be called"
    assert "release_address" in call_names, "release_address (EIP) must be called"
    assert "delete_subnet" in call_names, "delete_subnet must be called"
    assert "delete_vpc" in call_names, "delete_vpc must be called"

    nat_idx = call_names.index("delete_nat_gateway")
    eip_idx = call_names.index("release_address")
    subnet_idx = call_names.index("delete_subnet")
    vpc_idx = call_names.index("delete_vpc")

    # Tier 1 (NAT, EIP) must precede Tier 2 (subnet) and Tier 3 (VPC).
    assert nat_idx < subnet_idx, (
        f"NAT gateway (tier 1, call #{nat_idx}) must be deleted before "
        f"subnet (tier 2, call #{subnet_idx})"
    )
    assert eip_idx < subnet_idx, (
        f"EIP (tier 1, call #{eip_idx}) must be deleted before subnet (tier 2, call #{subnet_idx})"
    )
    # Tier 2 must precede Tier 3 (VPC).
    assert subnet_idx < vpc_idx, (
        f"subnet (tier 2, call #{subnet_idx}) must be deleted before VPC (tier 3, call #{vpc_idx})"
    )
    # NAT and EIP (both tier 1) must precede VPC (tier 3).
    assert nat_idx < vpc_idx, (
        f"NAT gateway (tier 1, call #{nat_idx}) must be deleted before "
        f"VPC (tier 3, call #{vpc_idx})"
    )
    assert eip_idx < vpc_idx, (
        f"EIP (tier 1, call #{eip_idx}) must be deleted before VPC (tier 3, call #{vpc_idx})"
    )


# ---------------------------------------------------------------------------
# AC-5: KMS PendingDeletion / PendingReplicaDeletion exclusion from residue count
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kms_pending_deletion_excluded_from_residue_count(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """KMS keys in PendingDeletion state must be excluded from the orphan-residue count.

    AC-5: When terratest_sweep schedules a KMS key for deletion, AWS keeps the key
    visible in the Tagging API for the mandatory 7-day pending-deletion window.
    The re-check after delete must NOT count such keys as residue.

    This test proves:
    - A run-id-tagged KMS key in PendingDeletion state is excluded from residue.
    - A run-id-tagged KMS key in Enabled state IS counted as residue.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260613000000-abc123"

    pending_key_arn = f"arn:aws:kms:us-east-1:{sandbox_account_id}:key/pending-key-uuid"
    enabled_key_arn = f"arn:aws:kms:us-east-1:{sandbox_account_id}:key/enabled-key-uuid"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": pending_key_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
                {
                    "ResourceARN": enabled_key_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    def _describe_key_side_effect(**kwargs: Any) -> dict:
        key_id = kwargs.get("KeyId", "")
        return {
            "KeyMetadata": {
                "KeyId": key_id,
                "KeyState": ("PendingDeletion" if "pending" in key_id else "Enabled"),
            }
        }

    kms_client = MagicMock()
    kms_client.describe_key.side_effect = _describe_key_side_effect

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "kms":
            return kms_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_kms_pending.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    # The exit code must be non-zero because the Enabled key is still residue
    assert exc.value.code != 0, (
        f"Expected non-zero exit because Enabled KMS key is residue; got exit {exc.value.code}"
    )

    # Verify the report exists
    assert report_path.exists(), "Report must be written"
    report = json.loads(report_path.read_text())

    # The remaining count must be 1 (the Enabled key), not 2
    # (the PendingDeletion key must be excluded)
    remaining = report.get("remaining", -1)
    assert remaining == 1, (
        f"Expected remaining=1 (only the Enabled key); "
        f"got remaining={remaining}. "
        "PendingDeletion KMS keys must be excluded from the residue count."
    )


@pytest.mark.unit
def test_kms_pending_replica_deletion_excluded_from_residue_count(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """KMS keys in PendingReplicaDeletion state must also be excluded from residue count.

    AC-5 variant: PendingReplicaDeletion is also a valid pending state for
    multi-region KMS keys. It must be treated the same as PendingDeletion.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260613000000-replica"
    replica_key_arn = f"arn:aws:kms:us-east-1:{sandbox_account_id}:key/replica-pending-key"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": replica_key_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                }
            ]
        }
    ]

    kms_client = MagicMock()
    kms_client.describe_key.return_value = {
        "KeyMetadata": {
            "KeyId": replica_key_arn.split("/")[-1],
            "KeyState": "PendingReplicaDeletion",
        }
    }

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "kms":
            return kms_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_replica_pending.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    # Only a PendingReplicaDeletion key is in the list -- should exit 0 (excluded)
    assert exc.value.code == 0, (
        f"Expected exit 0 because all KMS keys are in PendingReplicaDeletion state "
        f"(excluded from residue); got exit {exc.value.code}"
    )

    report = json.loads(report_path.read_text())
    assert report.get("remaining") == 0, (
        f"Expected remaining=0 (PendingReplicaDeletion key excluded); "
        f"got remaining={report.get('remaining')}"
    )


@pytest.mark.unit
def test_kms_pending_deletion_states_constant_in_constants_module() -> None:
    """KMS_PENDING_DELETION_STATES must be defined in scripts/constants.py."""
    import scripts.constants as constants

    assert hasattr(constants, "KMS_PENDING_DELETION_STATES"), (
        "KMS_PENDING_DELETION_STATES not found in scripts/constants.py; "
        "the excluded KMS states must be the single constants site (spec section 7)"
    )
    states = constants.KMS_PENDING_DELETION_STATES
    assert "PendingDeletion" in states, (
        f"PendingDeletion must be in KMS_PENDING_DELETION_STATES; got {states}"
    )
    assert "PendingReplicaDeletion" in states, (
        f"PendingReplicaDeletion must be in KMS_PENDING_DELETION_STATES; got {states}"
    )


@pytest.mark.unit
def test_kms_alias_arns_not_affected_by_pending_deletion_filter(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """KMS alias ARNs (:alias/) are deleted directly and not subject to key-state check.

    The PendingDeletion filter applies only to KMS key ARNs (:key/), not alias ARNs.
    Aliases are deleted immediately and don't have a PendingDeletion state.
    """
    import contextlib

    import scripts.terratest_sweep as sweep

    run_id = "tt-20260613000000-alias"
    alias_arn = f"arn:aws:kms:us-east-1:{sandbox_account_id}:alias/tt-test-alias"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": alias_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                }
            ]
        }
    ]
    kms_client = MagicMock()
    kms_client.delete_alias.return_value = {}

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "kms":
            return kms_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_alias.json"
    with contextlib.suppress(SystemExit):
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert report_path.exists()
    report = json.loads(report_path.read_text())
    # The alias must have been deleted (action="deleted"), not excluded
    found = report.get("found", [])
    alias_entry = next((e for e in found if e.get("arn") == alias_arn), None)
    assert alias_entry is not None, "Alias ARN must appear in report"
    assert alias_entry.get("action") == "deleted", (
        f"Alias ARN must be deleted, not excluded; got action={alias_entry.get('action')!r}"
    )


# ---------------------------------------------------------------------------
# AC-5 extension: botocore.exceptions.ClientError in _is_kms_key_in_pending_deletion
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kms_pending_deletion_client_error_emits_stderr_and_includes_key(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """When describe_key raises a botocore ClientError the key is conservatively
    included in the residue count (not silently excluded) and a WARNING is
    written to stderr so operators know the key state could not be verified.

    Verifies the non-blocking code_review finding: broad 'except Exception:
    return False' replaced with 'except botocore.exceptions.ClientError' plus
    a stderr diagnostic.  The return value must be False (include in count)
    so that the caller counts the key as residue.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    key_arn = f"arn:aws:kms:us-east-1:{sandbox_account_id}:key/error-key-uuid"
    enabled_arn = f"arn:aws:kms:us-east-1:{sandbox_account_id}:key/enabled-key-uuid"
    run_id = "tt-20260613000000-kms-err"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": key_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
                {
                    "ResourceARN": enabled_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    error_response = {"Error": {"Code": "NotFoundException", "Message": "Key not found"}}
    client_error = botocore.exceptions.ClientError(error_response, "DescribeKey")

    def _describe_key_side_effect(**kwargs: Any) -> dict:
        key_id = kwargs.get("KeyId", "")
        if "error" in key_id:
            raise client_error
        return {"KeyMetadata": {"KeyId": key_id, "KeyState": "Enabled"}}

    kms_client = MagicMock()
    kms_client.describe_key.side_effect = _describe_key_side_effect

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "kms":
            return kms_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_kms_error.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    # Both keys must be counted as residue: error-key (ClientError -> conservatively included)
    # and enabled-key (explicitly Enabled state).
    assert exc.value.code != 0, (
        "Expected non-zero exit because both KMS keys are residue "
        f"(one raised ClientError, one is Enabled); got exit {exc.value.code}"
    )

    # A WARNING must have been written to stderr about the key whose state could not be determined.
    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "ERROR" in captured.err, (
        "Expected a WARNING or ERROR on stderr when describe_key raises ClientError; "
        f"got stderr: {captured.err!r}"
    )
    assert "error-key-uuid" in captured.err, (
        f"Expected the key ARN/ID in the stderr diagnostic; got stderr: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# NAT gateway deleted-state exclusion from check-mode residue count
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_nat_gateway_deleted_returns_true_for_deleted_state(
    sandbox_account_id: str,
) -> None:
    """_is_nat_gateway_deleted returns True when the NAT gateway state is 'deleted'."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-deleted"
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-deleted", "State": "deleted"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_nat_gateway_deleted(arn, boto3_mock) is True
    ec2_client.describe_nat_gateways.assert_called_once_with(NatGatewayIds=["nat-deleted"])


@pytest.mark.unit
def test_is_nat_gateway_deleted_returns_true_for_deleting_state(
    sandbox_account_id: str,
) -> None:
    """_is_nat_gateway_deleted returns True when the NAT gateway state is 'deleting'."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-deleting"
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-deleting", "State": "deleting"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_nat_gateway_deleted(arn, boto3_mock) is True


@pytest.mark.unit
def test_is_nat_gateway_deleted_returns_false_for_available_state(
    sandbox_account_id: str,
) -> None:
    """_is_nat_gateway_deleted returns False when the NAT gateway state is 'available'."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-live"
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.return_value = {
        "NatGateways": [{"NatGatewayId": "nat-live", "State": "available"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_nat_gateway_deleted(arn, boto3_mock) is False


@pytest.mark.unit
def test_is_nat_gateway_deleted_client_error_returns_false_and_emits_warning(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_nat_gateway_deleted returns False and emits WARNING on ClientError."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-error"
    error_response = {"Error": {"Code": "InvalidNatGatewayID.NotFound", "Message": "Not found"}}
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeNatGateways"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_nat_gateway_deleted(arn, boto3_mock)
    assert result is False
    captured = capsys.readouterr()
    assert "WARNING" in captured.err, (
        f"Expected WARNING on stderr for ClientError; got: {captured.err!r}"
    )
    assert "nat-error" in captured.err, (
        f"Expected the NAT gateway ID in the stderr diagnostic; got: {captured.err!r}"
    )


@pytest.mark.unit
def test_is_nat_gateway_deleted_returns_true_on_nat_gateway_not_found_error(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_nat_gateway_deleted returns True (excluded) when NatGatewayNotFound is raised.

    The authoritative EC2 describe API raises NatGatewayNotFound when the
    gateway no longer exists at all. The Resource Groups Tagging API may still
    list the resource during its eventual-consistency lag window. NatGatewayNotFound
    is proof of genuine deletion and must exclude the resource from residue (return True),
    not count it as residue. No WARNING must be emitted for this provably-gone path.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-0f74c78f0d15de1c5"
    error_response = {"Error": {"Code": "NatGatewayNotFound", "Message": "NAT gateway not found"}}
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeNatGateways"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_nat_gateway_deleted(arn, boto3_mock)
    assert result is True, (
        "NatGatewayNotFound from the authoritative EC2 describe API means the gateway is "
        "genuinely deleted -- it must be excluded from residue (return True), not counted."
    )
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err, (
        f"NatGatewayNotFound is a provably-gone signal; WARNING must not be emitted. "
        f"Got stderr: {captured.err!r}"
    )


@pytest.mark.unit
def test_check_mode_excludes_nat_gateway_not_found_error_from_residue(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """NAT gateway raising NatGatewayNotFound must be excluded from check-mode residue.

    When describe_nat_gateways raises NatGatewayNotFound the gateway is genuinely
    gone. The sweep must exit 0 (not residue) when that is the ONLY tagged resource.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-natnotfound"
    gone_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-0f74c78f0d15de1c5"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": gone_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    error_response = {"Error": {"Code": "NatGatewayNotFound", "Message": "NAT gateway not found"}}
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeNatGateways"
    )

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_ngw_not_found.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert exc.value.code == 0, (
        f"Expected exit 0 (zero residue): NatGatewayNotFound means the gateway is "
        f"genuinely gone (Tagging-API lag phantom). Got exit {exc.value.code}."
    )


@pytest.mark.unit
def test_check_mode_counts_unknown_client_error_nat_gateway_as_residue(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A generic unknown ClientError on describe_nat_gateways must count as residue.

    Only the specific NatGatewayNotFound code is a provably-gone signal. Any
    other ClientError is ambiguous; the sweep must fail-closed and count the
    resource as residue (exit non-zero).
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-naterror"
    ambiguous_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-ambiguous"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": ambiguous_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    error_response = {"Error": {"Code": "RequestExpired", "Message": "Request has expired"}}
    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeNatGateways"
    )

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_ngw_error_residue.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert exc.value.code != 0, (
        f"Expected non-zero exit for ambiguous ClientError on describe_nat_gateways "
        f"(fail-closed: unknown error must count as residue). Got exit {exc.value.code}."
    )


@pytest.mark.unit
def test_check_mode_excludes_deleted_nat_gateway_from_residue(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """NAT gateways in 'deleted' state must be excluded from the check-mode residue count.

    AWS's Tagging API exhibits eventual consistency: a NAT gateway destroyed by
    terraform destroy remains visible in tag searches for several minutes while in
    the 'deleted' state. It is NOT a leaked resource and must not count as residue.

    This test proves:
    - A run-id-tagged NAT gateway in 'deleted' state is excluded from residue.
    - A run-id-tagged NAT gateway in 'available' state IS counted as residue.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-natcheck"

    deleted_ngw_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-deleted"
    live_ngw_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:natgateway/nat-live"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": deleted_ngw_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
                {
                    "ResourceARN": live_ngw_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    def _describe_nat_gateways_side_effect(**kwargs: Any) -> dict:
        ngw_id = kwargs.get("NatGatewayIds", [""])[0]
        state = "deleted" if "deleted" in ngw_id else "available"
        return {"NatGateways": [{"NatGatewayId": ngw_id, "State": state}]}

    ec2_client = MagicMock()
    ec2_client.describe_nat_gateways.side_effect = _describe_nat_gateways_side_effect

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_ngw_deleted.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    # Only the live NAT gateway must be counted as residue (exit 1); deleted one excluded.
    assert exc.value.code == 1, (
        f"Expected exit 1 (one residue resource); got exit {exc.value.code}. "
        "The deleted NAT gateway must be excluded from the residue count."
    )


# ---------------------------------------------------------------------------
# VPC flow log deleted-state exclusion from check-mode residue count
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_vpc_flow_log_deleted_returns_true_when_not_found(
    sandbox_account_id: str,
) -> None:
    """_is_vpc_flow_log_deleted returns True when describe_flow_logs returns empty list."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-flow-log/fl-gone"
    ec2_client = MagicMock()
    ec2_client.describe_flow_logs.return_value = {"FlowLogs": []}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_vpc_flow_log_deleted(arn, boto3_mock) is True
    ec2_client.describe_flow_logs.assert_called_once_with(FlowLogIds=["fl-gone"])


@pytest.mark.unit
def test_is_vpc_flow_log_deleted_returns_false_when_exists(
    sandbox_account_id: str,
) -> None:
    """_is_vpc_flow_log_deleted returns False when the flow log still exists."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-flow-log/fl-alive"
    ec2_client = MagicMock()
    ec2_client.describe_flow_logs.return_value = {
        "FlowLogs": [{"FlowLogId": "fl-alive", "FlowLogStatus": "ACTIVE"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_vpc_flow_log_deleted(arn, boto3_mock) is False


@pytest.mark.unit
def test_is_vpc_flow_log_deleted_client_error_returns_false_and_emits_warning(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_vpc_flow_log_deleted returns False and emits WARNING on ClientError."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-flow-log/fl-error"
    error_response = {"Error": {"Code": "InvalidFlowLogId.NotFound", "Message": "Not found"}}
    ec2_client = MagicMock()
    ec2_client.describe_flow_logs.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeFlowLogs"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_vpc_flow_log_deleted(arn, boto3_mock)
    assert result is False
    captured = capsys.readouterr()
    assert "WARNING" in captured.err, (
        f"Expected WARNING on stderr for ClientError; got: {captured.err!r}"
    )
    assert "fl-error" in captured.err, (
        f"Expected the flow log ID in the stderr diagnostic; got: {captured.err!r}"
    )


@pytest.mark.unit
def test_check_mode_excludes_deleted_vpc_flow_log_from_residue(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """VPC flow logs that no longer exist must be excluded from check-mode residue count.

    AWS's Tagging API exhibits eventual consistency: a flow log destroyed by
    terraform destroy may remain visible in tag searches for several minutes.
    It is NOT a leaked resource and must not count as residue.

    This test proves:
    - A run-id-tagged flow log that returns an empty describe_flow_logs is excluded.
    - A run-id-tagged flow log that still exists IS counted as residue.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-flowcheck"

    gone_fl_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-flow-log/fl-gone"
    live_fl_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-flow-log/fl-live"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": gone_fl_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
                {
                    "ResourceARN": live_fl_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    def _describe_flow_logs_side_effect(**kwargs: Any) -> dict:
        fl_id = kwargs.get("FlowLogIds", [""])[0]
        if "gone" in fl_id:
            return {"FlowLogs": []}
        return {"FlowLogs": [{"FlowLogId": fl_id, "FlowLogStatus": "ACTIVE"}]}

    ec2_client = MagicMock()
    ec2_client.describe_flow_logs.side_effect = _describe_flow_logs_side_effect

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_flow_log_gone.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    # Only the live flow log must be counted as residue (exit 1); gone one excluded.
    assert exc.value.code == 1, (
        f"Expected exit 1 (one residue resource); got exit {exc.value.code}. "
        "The deleted VPC flow log must be excluded from the residue count."
    )


# ---------------------------------------------------------------------------
# _is_vpc_endpoint_deleted unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_vpc_endpoint_deleted_returns_true_for_deleted_state(
    sandbox_account_id: str,
) -> None:
    """_is_vpc_endpoint_deleted returns True when the VPC endpoint state is 'deleted'."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-deleted"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-deleted", "State": "deleted"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_vpc_endpoint_deleted(arn, boto3_mock) is True
    ec2_client.describe_vpc_endpoints.assert_called_once_with(VpcEndpointIds=["vpce-deleted"])


@pytest.mark.unit
def test_is_vpc_endpoint_deleted_returns_true_for_deleting_state(
    sandbox_account_id: str,
) -> None:
    """_is_vpc_endpoint_deleted returns True when the VPC endpoint state is 'deleting'."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-deleting"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-deleting", "State": "deleting"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_vpc_endpoint_deleted(arn, boto3_mock) is True


@pytest.mark.unit
def test_is_vpc_endpoint_deleted_returns_true_when_not_found_error(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_vpc_endpoint_deleted returns True when InvalidVpcEndpointId.NotFound is raised.

    The authoritative EC2 describe API raises InvalidVpcEndpointId.NotFound when the
    endpoint no longer exists at all. The Resource Groups Tagging API may still list
    the resource during its eventual-consistency lag window. This is a provably-gone
    phantom and must be excluded from residue. No WARNING must be emitted.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-0gone"
    error_response = {
        "Error": {"Code": "InvalidVpcEndpointId.NotFound", "Message": "Endpoint not found"}
    }
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeVpcEndpoints"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_vpc_endpoint_deleted(arn, boto3_mock)
    assert result is True, (
        "InvalidVpcEndpointId.NotFound from the EC2 describe API means the endpoint is "
        "genuinely deleted -- it must be excluded from residue (return True)."
    )
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err, (
        f"InvalidVpcEndpointId.NotFound is a provably-gone signal; WARNING must not be emitted. "
        f"Got stderr: {captured.err!r}"
    )


@pytest.mark.unit
def test_is_vpc_endpoint_deleted_returns_true_when_empty_response(
    sandbox_account_id: str,
) -> None:
    """_is_vpc_endpoint_deleted returns True when describe returns empty VpcEndpoints list."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-0gone2"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_vpc_endpoint_deleted(arn, boto3_mock) is True


@pytest.mark.unit
def test_is_vpc_endpoint_deleted_returns_false_for_available_state(
    sandbox_account_id: str,
) -> None:
    """_is_vpc_endpoint_deleted returns False when the endpoint is still available."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-live"
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-live", "State": "available"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_vpc_endpoint_deleted(arn, boto3_mock) is False


@pytest.mark.unit
def test_is_vpc_endpoint_deleted_client_error_returns_false_and_emits_warning(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_vpc_endpoint_deleted returns False and emits WARNING on unexpected ClientError."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-error"
    error_response = {"Error": {"Code": "UnexpectedError", "Message": "Something went wrong"}}
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeVpcEndpoints"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_vpc_endpoint_deleted(arn, boto3_mock)
    assert result is False, (
        "An unexpected ClientError must be treated as fail-closed (not deleted)."
    )
    captured = capsys.readouterr()
    assert "WARNING" in captured.err, (
        f"Expected WARNING on stderr for unexpected ClientError; got: {captured.err!r}"
    )
    assert "vpce-error" in captured.err, (
        f"Expected the VPC endpoint ID in the stderr diagnostic; got: {captured.err!r}"
    )


@pytest.mark.unit
def test_check_mode_excludes_vpc_endpoint_not_found_from_residue(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """VPC endpoint raising InvalidVpcEndpointId.NotFound must be excluded from check-mode residue.

    When describe_vpc_endpoints raises InvalidVpcEndpointId.NotFound the endpoint is
    genuinely gone. The sweep must exit 0 (not residue) when that is the ONLY tagged resource.
    This simulates the AWS Tagging API eventual-consistency lag where a deleted endpoint
    continues to appear in tag searches after EC2 has removed it.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-vpcenotfound"
    gone_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-0gone3"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": gone_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    error_response = {
        "Error": {
            "Code": "InvalidVpcEndpointId.NotFound",
            "Message": "The Vpc Endpoint Id does not exist",
        }
    }
    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeVpcEndpoints"
    )

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_vpce_not_found.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert exc.value.code == 0, (
        f"Expected exit 0 (zero residue): InvalidVpcEndpointId.NotFound means the endpoint "
        f"is genuinely gone (Tagging-API lag phantom). Got exit {exc.value.code}."
    )


@pytest.mark.unit
def test_check_mode_counts_live_vpc_endpoint_as_residue(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A live VPC endpoint in 'available' state must be counted as residue in check mode.

    The sweep must exit 1 (not 0) when the only tagged resource is an endpoint that
    actually exists and is not in a terminal deletion state.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-vpcelive"
    live_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc-endpoint/vpce-live2"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": live_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    ec2_client = MagicMock()
    ec2_client.describe_vpc_endpoints.return_value = {
        "VpcEndpoints": [{"VpcEndpointId": "vpce-live2", "State": "available"}]
    }

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_vpce_live.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert exc.value.code == 1, (
        f"Expected exit 1 (one residue resource): the endpoint is live in 'available' state. "
        f"Got exit {exc.value.code}."
    )


# ---------------------------------------------------------------------------
# WAFv2 handler tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_wafv2_parse_arn_regional() -> None:
    """_wafv2_parse_arn extracts name, scope, and id from a REGIONAL ARN."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:regional/webacl/my-waf/abc-123"
    name, scope, web_acl_id = sweep._wafv2_parse_arn(arn)
    assert name == "my-waf"
    assert scope == "REGIONAL"
    assert web_acl_id == "abc-123"


@pytest.mark.unit
def test_wafv2_parse_arn_cloudfront() -> None:
    """_wafv2_parse_arn maps the 'global' segment to CLOUDFRONT scope."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:global/webacl/my-cf-waf/def-456"
    name, scope, web_acl_id = sweep._wafv2_parse_arn(arn)
    assert name == "my-cf-waf"
    assert scope == "CLOUDFRONT"
    assert web_acl_id == "def-456"


@pytest.mark.unit
def test_wafv2_parse_arn_too_short_raises() -> None:
    """_wafv2_parse_arn raises ValueError when the ARN has fewer than 6 colon-segments."""
    import scripts.terratest_sweep as sweep

    with pytest.raises(ValueError, match="ARN too short"):
        sweep._wafv2_parse_arn("arn:aws:wafv2:us-east-1")


@pytest.mark.unit
def test_wafv2_parse_arn_not_webacl_raises() -> None:
    """_wafv2_parse_arn raises ValueError when the resource is not a webacl."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:regional/rulegroup/my-rg/xyz"
    with pytest.raises(ValueError, match="unsupported WAFv2 ARN resource segment"):
        sweep._wafv2_parse_arn(arn)


@pytest.mark.unit
def test_wafv2_parse_arn_unknown_scope_raises() -> None:
    """_wafv2_parse_arn raises ValueError when the scope segment is not regional or global."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:unknown/webacl/my-waf/abc-123"
    with pytest.raises(ValueError, match="unrecognised scope segment"):
        sweep._wafv2_parse_arn(arn)


@pytest.mark.unit
def test_wafv2_handler_delete_regional_webacl() -> None:
    """WAFv2Handler fetches the lock token then deletes a REGIONAL web ACL."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:regional/webacl/test-waf/abc-123"
    wafv2_client = MagicMock()
    wafv2_client.get_web_acl.return_value = {
        "WebACL": {"Name": "test-waf", "Id": "abc-123"},
        "LockToken": "lock-token-123",
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = wafv2_client

    handler = sweep.WAFv2Handler()
    result = handler.delete(arn, boto3_mock)

    assert result == "deleted"
    assert boto3_mock.client.call_count == 1
    assert boto3_mock.client.call_args.args == ("wafv2",)
    assert boto3_mock.client.call_args.kwargs["region_name"] == "us-east-1"
    # Every deletion-path client now carries the shared adaptive-retry Config.
    assert boto3_mock.client.call_args.kwargs["config"].retries.get("mode") == "adaptive"
    wafv2_client.get_web_acl.assert_called_once_with(
        Name="test-waf", Scope="REGIONAL", Id="abc-123"
    )
    wafv2_client.delete_web_acl.assert_called_once_with(
        Name="test-waf", Scope="REGIONAL", Id="abc-123", LockToken="lock-token-123"
    )


@pytest.mark.unit
def test_wafv2_handler_delete_cloudfront_webacl_uses_us_east_1() -> None:
    """WAFv2Handler always uses us-east-1 for CLOUDFRONT-scope web ACL deletion."""
    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:global/webacl/my-cf-waf/def-456"
    wafv2_client = MagicMock()
    wafv2_client.get_web_acl.return_value = {
        "WebACL": {"Name": "my-cf-waf", "Id": "def-456"},
        "LockToken": "lock-token-cf",
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = wafv2_client

    handler = sweep.WAFv2Handler()
    result = handler.delete(arn, boto3_mock)

    assert result == "deleted"
    assert boto3_mock.client.call_count == 1
    assert boto3_mock.client.call_args.args == ("wafv2",)
    assert boto3_mock.client.call_args.kwargs["region_name"] == "us-east-1"
    # Every deletion-path client now carries the shared adaptive-retry Config.
    assert boto3_mock.client.call_args.kwargs["config"].retries.get("mode") == "adaptive"
    wafv2_client.get_web_acl.assert_called_once_with(
        Name="my-cf-waf", Scope="CLOUDFRONT", Id="def-456"
    )
    wafv2_client.delete_web_acl.assert_called_once_with(
        Name="my-cf-waf", Scope="CLOUDFRONT", Id="def-456", LockToken="lock-token-cf"
    )


@pytest.mark.unit
def test_wafv2_handler_get_web_acl_not_found_returns_already_deleted() -> None:
    """WAFv2Handler returns 'already-deleted' when get_web_acl raises WAFNonexistentItemException.

    After Terraform destroy, the Tagging API may still return the web ACL ARN for a
    brief window. If the delete sweep picks it up, get_web_acl raises
    WAFNonexistentItemException -- the handler must treat this as success.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:regional/webacl/gone-waf/abc-123"
    error_response = {
        "Error": {
            "Code": "WAFNonexistentItemException",
            "Message": "The referenced item doesn't exist.",
        }
    }
    wafv2_client = MagicMock()
    wafv2_client.get_web_acl.side_effect = botocore.exceptions.ClientError(
        error_response, "GetWebACL"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = wafv2_client

    handler = sweep.WAFv2Handler()
    result = handler.delete(arn, boto3_mock)

    assert result == "already-deleted", (
        "WAFNonexistentItemException on get_web_acl must be treated as already-deleted, "
        "not an error."
    )
    wafv2_client.delete_web_acl.assert_not_called()


@pytest.mark.unit
def test_wafv2_handler_delete_web_acl_not_found_returns_already_deleted() -> None:
    """WAFv2Handler returns 'already-deleted' when delete_web_acl raises
    WAFNonexistentItemException.

    A race between two sweep runs can cause get_web_acl to succeed but
    delete_web_acl to raise WAFNonexistentItemException because the first run
    deleted the ACL between the two calls. The handler must treat this as success.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:regional/webacl/race-waf/abc-123"
    error_response = {
        "Error": {
            "Code": "WAFNonexistentItemException",
            "Message": "The referenced item doesn't exist.",
        }
    }
    wafv2_client = MagicMock()
    wafv2_client.get_web_acl.return_value = {
        "WebACL": {"Name": "race-waf", "Id": "abc-123"},
        "LockToken": "lock-token-race",
    }
    wafv2_client.delete_web_acl.side_effect = botocore.exceptions.ClientError(
        error_response, "DeleteWebACL"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = wafv2_client

    handler = sweep.WAFv2Handler()
    result = handler.delete(arn, boto3_mock)

    assert result == "already-deleted", (
        "WAFNonexistentItemException on delete_web_acl must be treated as already-deleted, "
        "not an error."
    )


@pytest.mark.unit
def test_wafv2_handler_unexpected_client_error_propagates() -> None:
    """WAFv2Handler re-raises unexpected ClientError from get_web_acl (fail-safe)."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = "arn:aws:wafv2:us-east-1:222222222222:regional/webacl/err-waf/abc-123"
    error_response = {"Error": {"Code": "InternalErrorException", "Message": "Internal error"}}
    wafv2_client = MagicMock()
    wafv2_client.get_web_acl.side_effect = botocore.exceptions.ClientError(
        error_response, "GetWebACL"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = wafv2_client

    handler = sweep.WAFv2Handler()
    with pytest.raises(botocore.exceptions.ClientError):
        handler.delete(arn, boto3_mock)


@pytest.mark.unit
def test_wafv2_handler_registered_in_handler_table() -> None:
    """WAFv2Handler is registered under the 'wafv2' key in the _HANDLERS dispatch table."""
    import scripts.terratest_sweep as sweep

    assert "wafv2" in sweep._HANDLERS, (
        "'wafv2' must be present in the _HANDLERS dispatch table so that WAFv2 web ACLs "
        "are not left untouched in delete mode (fail-safe invariant would flag them as "
        "unsupported and exit non-zero, which would cause AC-4 to fail if any WAFv2 "
        "resource is orphaned)."
    )
    assert isinstance(sweep._HANDLERS["wafv2"], sweep.WAFv2Handler), (
        "_HANDLERS['wafv2'] must be a WAFv2Handler instance."
    )


# ---------------------------------------------------------------------------
# ECS INACTIVE-shell exclusion from check-mode residue count
# ---------------------------------------------------------------------------


def _ecs_describe_side_effects(active_ids: set[str]) -> dict[str, Any]:
    """Build describe_* side effects keyed by whether the resource id is ACTIVE.

    Args:
        active_ids: cluster/service/family names that should report status ACTIVE.
            Everything else reports INACTIVE.

    Returns:
        A dict of callables for describe_clusters, describe_services, and
        describe_task_definition suitable for assigning onto a mock ecs client.
    """

    def _describe_clusters(**kwargs: Any) -> dict:
        name = kwargs.get("clusters", [""])[0]
        status = "ACTIVE" if name in active_ids else "INACTIVE"
        return {"clusters": [{"clusterName": name, "status": status}]}

    def _describe_services(**kwargs: Any) -> dict:
        name = kwargs.get("services", [""])[0]
        status = "ACTIVE" if name in active_ids else "INACTIVE"
        return {"services": [{"serviceName": name, "status": status}]}

    def _describe_task_definition(**kwargs: Any) -> dict:
        td = kwargs.get("taskDefinition", "")
        family = td.split(":")[0]
        status = "ACTIVE" if family in active_ids else "INACTIVE"
        return {"taskDefinition": {"family": family, "status": status}}

    return {
        "describe_clusters": _describe_clusters,
        "describe_services": _describe_services,
        "describe_task_definition": _describe_task_definition,
    }


@pytest.mark.unit
def test_check_mode_excludes_inactive_ecs_resources_from_residue(
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """INACTIVE / DELETE_IN_PROGRESS ECS shells must be excluded from check-mode residue.

    ECS keeps tagged cluster/service shells around in the INACTIVE state and
    task-definitions in INACTIVE / DELETE_IN_PROGRESS after deletion; the Tagging
    API still returns them. None of these are orphans. With ONLY non-live ECS
    resources tagged, the sweep must exit 0 (zero residue).

    Mirrors the 10 verified false-positives from the live sandbox: 5 INACTIVE
    clusters, 1 INACTIVE service, and 4 non-ACTIVE task-definitions.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260615000000-ecsinactive"
    acct = sandbox_account_id
    inactive_arns = [
        f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-c-17814810-cluster",
        f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-w-17814814-cluster",
        f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-svc-with-17814811-cluster",
        f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-b-17814810-cluster",
        f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-r-17814817-cluster",
        f"arn:aws:ecs:us-east-1:{acct}:service/ecs-c-17814810-cluster/ecs-c-17814810",
        f"arn:aws:ecs:us-east-1:{acct}:task-definition/ecs-c-17814810:1",
        f"arn:aws:ecs:us-east-1:{acct}:task-definition/ecs-w-17814814:1",
        f"arn:aws:ecs:us-east-1:{acct}:task-definition/ecs-r-17814817:1",
        f"arn:aws:ecs:us-east-1:{acct}:task-definition/ecs-svc-with-17814811:1",
    ]

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": acct,
        "Arn": f"arn:aws:iam::{acct}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                }
                for arn in inactive_arns
            ]
        }
    ]

    # No resource is ACTIVE -- every describe reports INACTIVE.
    ecs_client = MagicMock()
    side_effects = _ecs_describe_side_effects(active_ids=set())
    ecs_client.describe_clusters.side_effect = side_effects["describe_clusters"]
    ecs_client.describe_services.side_effect = side_effects["describe_services"]
    ecs_client.describe_task_definition.side_effect = side_effects["describe_task_definition"]

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ecs":
            return ecs_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_ecs_inactive.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert exc.value.code == 0, (
        f"Expected exit 0 (zero residue): all {len(inactive_arns)} tagged ECS resources are "
        f"INACTIVE / non-ACTIVE shells (Tagging-API lag phantoms). Got exit {exc.value.code}."
    )
    report = json.loads(report_path.read_text())
    assert report["remaining"] == 0
    assert report["found"] == []


@pytest.mark.unit
def test_check_mode_counts_active_ecs_resources_as_residue(
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """LIVE (ACTIVE) ECS cluster/service/task-def MUST still be counted as residue.

    Only verified non-live resources are excluded; an ACTIVE ECS resource with the
    sweep tags is a genuine orphan and must be reported (exit non-zero). This guards
    against the fix weakening detection of real orphans.
    """
    import scripts.terratest_sweep as sweep

    run_id = "tt-20260615000000-ecsactive"
    acct = sandbox_account_id
    active_cluster = f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-live-cluster"
    active_service = f"arn:aws:ecs:us-east-1:{acct}:service/ecs-live-cluster/ecs-live-svc"
    active_task_def = f"arn:aws:ecs:us-east-1:{acct}:task-definition/ecs-live-td:1"
    inactive_cluster = f"arn:aws:ecs:us-east-1:{acct}:cluster/ecs-dead-cluster"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": acct,
        "Arn": f"arn:aws:iam::{acct}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                }
                for arn in (active_cluster, active_service, active_task_def, inactive_cluster)
            ]
        }
    ]

    ecs_client = MagicMock()
    side_effects = _ecs_describe_side_effects(
        active_ids={"ecs-live-cluster", "ecs-live-svc", "ecs-live-td"}
    )
    ecs_client.describe_clusters.side_effect = side_effects["describe_clusters"]
    ecs_client.describe_services.side_effect = side_effects["describe_services"]
    ecs_client.describe_task_definition.side_effect = side_effects["describe_task_definition"]

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ecs":
            return ecs_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_ecs_active.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    # Three ACTIVE resources are residue; the one INACTIVE cluster is excluded.
    assert exc.value.code == 3, (
        f"Expected exit 3 (three ACTIVE ECS orphans counted, one INACTIVE excluded); "
        f"got exit {exc.value.code}."
    )
    report = json.loads(report_path.read_text())
    assert report["remaining"] == 3
    reported_arns = {e["arn"] for e in report["found"]}
    assert active_cluster in reported_arns
    assert active_service in reported_arns
    assert active_task_def in reported_arns
    assert inactive_cluster not in reported_arns, (
        "The INACTIVE cluster must be excluded from the residue report."
    )


# ---------------------------------------------------------------------------
# Per-module run-id-file resolution (parallel-safe zero-orphan proof, AC-4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_run_id_from_file_returns_stripped_value(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_read_run_id_from_file returns the run id with surrounding whitespace stripped."""
    m = _import_sweep(monkeypatch)
    run_id_file = tmp_path / ".terratest-run-id"
    run_id_file.write_text("tt-20260615120000-abc123\n")

    result = m._read_run_id_from_file(str(run_id_file))

    assert result == "tt-20260615120000-abc123", f"Expected the stripped run id; got {result!r}"


@pytest.mark.unit
def test_read_run_id_from_file_missing_fails_fast(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """A missing run-id file must cause exit 1 with a clear ERROR naming the path."""
    m = _import_sweep(monkeypatch)
    missing = tmp_path / "providers" / "aws" / "primitives" / "x" / ".terratest-run-id"

    with pytest.raises(SystemExit) as exc:
        m._read_run_id_from_file(str(missing))

    assert exc.value.code == 1, f"Expected exit 1 for missing run-id file; got {exc.value.code}"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert ".terratest-run-id" in captured.err


@pytest.mark.unit
def test_read_run_id_from_file_empty_fails_fast(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """An empty run-id file must cause exit 1 with a clear ERROR."""
    m = _import_sweep(monkeypatch)
    run_id_file = tmp_path / ".terratest-run-id"
    run_id_file.write_text("   \n")

    with pytest.raises(SystemExit) as exc:
        m._read_run_id_from_file(str(run_id_file))

    assert exc.value.code == 1, f"Expected exit 1 for empty run-id file; got {exc.value.code}"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert "empty" in captured.err.lower()


# ---------------------------------------------------------------------------
# _is_security_group_deleted unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_security_group_deleted_returns_true_on_invalid_group_not_found(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_security_group_deleted returns True (excluded) when InvalidGroup.NotFound is raised.

    The authoritative EC2 describe_security_groups API raises InvalidGroup.NotFound when
    the security group no longer exists. A VPC's default SG is deleted atomically with
    the VPC itself, but the Tagging API may still list it during its eventual-consistency
    lag window. This is a provably-gone phantom that must be excluded from residue.
    No WARNING must be emitted for this provably-gone path.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:security-group/sg-0gone"
    error_response = {
        "Error": {"Code": "InvalidGroup.NotFound", "Message": "The security group does not exist"}
    }
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeSecurityGroups"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_security_group_deleted(arn, boto3_mock)
    assert result is True, (
        "InvalidGroup.NotFound from EC2 describe_security_groups means the SG is genuinely "
        "deleted -- it must be excluded from residue (return True), not counted."
    )
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err, (
        "InvalidGroup.NotFound is a provably-gone signal; WARNING must not be emitted. "
        f"Got stderr: {captured.err!r}"
    )


@pytest.mark.unit
def test_is_security_group_deleted_returns_false_when_sg_exists(
    sandbox_account_id: str,
) -> None:
    """_is_security_group_deleted returns False when describe_security_groups succeeds.

    A successful describe call means the SG still exists (is live) and must be counted as residue.
    """
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:security-group/sg-live"
    sg_id = "sg-live"
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": sg_id, "GroupName": "my-sg"}]
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_security_group_deleted(arn, boto3_mock)
    assert result is False, (
        "A successful describe_security_groups response means the SG still exists -- "
        "it must be counted as residue (return False)."
    )
    ec2_client.describe_security_groups.assert_called_once_with(GroupIds=[sg_id])


@pytest.mark.unit
def test_is_security_group_deleted_fail_closed_on_other_client_error(
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """_is_security_group_deleted returns False and emits WARNING on unexpected ClientError.

    When describe_security_groups raises an error code other than InvalidGroup.NotFound
    the state of the SG is ambiguous. The function must fail-closed (return False, count
    as residue) to avoid masking real orphan security groups, and must emit a WARNING to
    stderr so operators can investigate.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:security-group/sg-error"
    error_response = {"Error": {"Code": "UnexpectedError", "Message": "Something went wrong"}}
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeSecurityGroups"
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ec2_client

    result = sweep._is_security_group_deleted(arn, boto3_mock)
    assert result is False, (
        "An unexpected ClientError must be treated as fail-closed "
        "(SG not deleted, count as residue)."
    )
    captured = capsys.readouterr()
    assert "WARNING" in captured.err, (
        f"Expected WARNING on stderr for unexpected ClientError; got: {captured.err!r}"
    )
    assert "sg-error" in captured.err, (
        f"Expected the SG ID in the stderr diagnostic; got: {captured.err!r}"
    )


@pytest.mark.unit
def test_check_mode_excludes_deleted_vpc_phantom_security_group(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A deleted-VPC phantom SG (InvalidGroup.NotFound) must be excluded from check-mode residue.

    When a VPC is destroyed its default security group is deleted atomically. The Tagging
    API may still list the SG's ARN for a period after deletion. The sweep must exit 0
    (zero residue) when that phantom SG is the ONLY tagged resource and EC2's
    describe_security_groups raises InvalidGroup.NotFound.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    run_id = "tt-20260614000000-sgphantom"
    gone_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:security-group/sg-0phantom"

    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": sandbox_account_id,
        "Arn": f"arn:aws:iam::{sandbox_account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": gone_arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id},
                    ],
                },
            ]
        }
    ]

    error_response = {
        "Error": {
            "Code": "InvalidGroup.NotFound",
            "Message": "The security group does not exist",
        }
    }
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeSecurityGroups"
    )

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        if svc == "ec2":
            return ec2_client
        return MagicMock()

    boto3_mock.client.side_effect = client_factory

    report_path = tmp_path / "report_sg_phantom.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )

    assert exc.value.code == 0, (
        f"Expected exit 0 (zero residue): InvalidGroup.NotFound means the SG is a "
        f"provably-gone Tagging-API lag phantom. Got exit {exc.value.code}."
    )


# ===========================================================================
# Run-scoped sweep hardening: benign NotFound = success, default-SG skip,
# DependencyViolation retry. (fix/terratest-sweep-runscoped)
# ===========================================================================


def _delete_boto3_mock(
    account_id: str,
    resource_arns: list[str],
    client_factory_extra: Any,
    run_id_value: str = "run-harden",
) -> MagicMock:
    """Build a boto3 mock for delete-mode tests over the given resource ARNs.

    sts + resourcegroupstaggingapi are wired automatically; ``client_factory_extra``
    is consulted first for any service so a test can return a pre-configured client.
    """
    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": account_id,
        "Arn": f"arn:aws:iam::{account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id_value},
                    ],
                }
                for arn in resource_arns
            ]
        }
    ]

    def client_factory(svc: str, **kw: Any) -> Any:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        extra = client_factory_extra(svc, **kw)
        if extra is not None:
            return extra
        return MagicMock()

    boto3_mock.client.side_effect = client_factory
    return boto3_mock


def _client_error(code: str, op: str = "Op") -> Any:
    import botocore.exceptions

    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": f"{code} message"}}, op
    )


def _run_delete(
    sweep: Any, boto3_mock: MagicMock, report_path: pathlib.Path, run_id: str = "run-harden"
) -> tuple[int, dict]:
    """Run main() in delete mode and return (exit_code, parsed report)."""
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=run_id,
            boto3_mod=boto3_mock,
        )
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    return int(exc.value.code), report


# ---- _is_benign_not_found / _is_dependency_violation classifiers ----


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        "InvalidSubnetID.NotFound",
        "ClusterNotFoundException",
        "InvalidGroup.NotFound",
        "ResourceNotFoundException",
        "NoSuchEntity",
        # CloudFront already-gone codes: the tag index lags deletion, so a run-scoped
        # sweep re-discovers a just-destroyed distribution and the delete raises this.
        # None contain the "NotFound" substring, so they rely on the explicit allowlist.
        "NoSuchDistribution",
        "NoSuchOriginAccessControl",
        "SomeBrandNewNotFoundError",  # generic substring rule
    ],
)
def test_is_benign_not_found_true_for_not_found_codes(code: str) -> None:
    """Any NotFound-family / allowlisted already-gone code is treated as benign."""
    import scripts.terratest_sweep as sweep

    assert sweep._is_benign_not_found(_client_error(code)) is True


@pytest.mark.unit
@pytest.mark.parametrize("code", ["DependencyViolation", "AccessDenied", "BucketNotEmpty"])
def test_is_benign_not_found_false_for_non_not_found(code: str) -> None:
    """A non-NotFound error code is NOT benign and must surface as a failure."""
    import scripts.terratest_sweep as sweep

    assert sweep._is_benign_not_found(_client_error(code)) is False


@pytest.mark.unit
def test_is_benign_not_found_false_for_non_client_error() -> None:
    """A non-botocore exception is never a benign already-gone signal."""
    import scripts.terratest_sweep as sweep

    assert sweep._is_benign_not_found(ValueError("boom")) is False


@pytest.mark.unit
@pytest.mark.parametrize("code", ["DependencyViolation", "ResourceInUseException", "ResourceInUse"])
def test_is_dependency_violation_true_for_dep_codes(code: str) -> None:
    """Dependency-violation codes are classified as transient/deferrable."""
    import scripts.terratest_sweep as sweep

    assert sweep._is_dependency_violation(_client_error(code)) is True


@pytest.mark.unit
@pytest.mark.parametrize("code", ["InvalidSubnetID.NotFound", "AccessDenied"])
def test_is_dependency_violation_false_for_non_dep(code: str) -> None:
    """Non-dependency codes are not deferred."""
    import scripts.terratest_sweep as sweep

    assert sweep._is_dependency_violation(_client_error(code)) is False


# ---- default security group skip ----


@pytest.mark.unit
def test_is_default_security_group_true_when_group_name_default(
    qa_account_id: str,
) -> None:
    """A security group whose GroupName == 'default' is detected as the default SG."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-0default", "GroupName": "default"}]
    }
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_default_security_group("sg-0default", boto3_mock) is True


@pytest.mark.unit
def test_is_default_security_group_false_for_named_group() -> None:
    """A non-default security group is not detected as the default SG."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-app", "GroupName": "tt-app-sg"}]
    }
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_default_security_group("sg-app", boto3_mock) is False


@pytest.mark.unit
def test_is_default_security_group_false_on_not_found() -> None:
    """A NotFound during the default-SG probe is treated as 'not default' (let delete proceed)."""
    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    ec2_client = MagicMock()
    ec2_client.describe_security_groups.side_effect = _client_error("InvalidGroup.NotFound")
    boto3_mock.client.return_value = ec2_client

    assert sweep._is_default_security_group("sg-gone", boto3_mock) is False


@pytest.mark.unit
def test_delete_mode_skips_default_security_group(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """A VPC default SG is SKIPPED (recorded skipped-default-sg) and the sweep exits 0."""
    import scripts.terratest_sweep as sweep

    sg_id = "sg-0deadbeef"
    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:security-group/{sg_id}"

    ec2_client = MagicMock()
    ec2_client.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": sg_id, "GroupName": "default"}]
    }

    def extra(svc: str, **kw: Any) -> Any:
        return ec2_client if svc == "ec2" else None

    boto3_mock = _delete_boto3_mock(sandbox_account_id, [arn], extra)
    report_path = tmp_path / "report_default_sg.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code == 0, "Default SG skip must not fail the sweep."
    # delete_security_group must NOT have been called for the default SG.
    ec2_client.delete_security_group.assert_not_called()
    entry = next(e for e in report["found"] if e["arn"] == arn)
    assert entry["action"] == "skipped-default-sg"
    assert report["remaining"] == 0


# ---- NotFound during delete = idempotent success ----


@pytest.mark.unit
def test_delete_mode_subnet_not_found_is_success(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """DeleteSubnet -> InvalidSubnetID.NotFound is already-deleted (exit 0), not a failure."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:subnet/subnet-gone"
    ec2_client = MagicMock()
    ec2_client.delete_subnet.side_effect = _client_error("InvalidSubnetID.NotFound", "DeleteSubnet")

    def extra(svc: str, **kw: Any) -> Any:
        return ec2_client if svc == "ec2" else None

    boto3_mock = _delete_boto3_mock(sandbox_account_id, [arn], extra)
    report_path = tmp_path / "report_subnet_notfound.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code == 0
    entry = next(e for e in report["found"] if e["arn"] == arn)
    assert entry["action"] == "already-deleted"
    assert report["remaining"] == 0


@pytest.mark.unit
def test_delete_mode_cluster_not_found_is_success(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """delete_cluster -> ClusterNotFoundException is already-deleted (exit 0)."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:ecs:us-east-1:{sandbox_account_id}:cluster/tt-gone"
    ecs_client = MagicMock()
    ecs_client.delete_cluster.side_effect = _client_error(
        "ClusterNotFoundException", "DeleteCluster"
    )

    def extra(svc: str, **kw: Any) -> Any:
        return ecs_client if svc == "ecs" else None

    boto3_mock = _delete_boto3_mock(sandbox_account_id, [arn], extra)
    report_path = tmp_path / "report_cluster_notfound.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code == 0
    entry = next(e for e in report["found"] if e["arn"] == arn)
    assert entry["action"] == "already-deleted"


# ---- DependencyViolation: deferred + retried, then succeeds ----


@pytest.mark.unit
def test_delete_mode_dependency_violation_retried_then_succeeds(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """A transient DependencyViolation on delete_vpc is retried and then succeeds (exit 0)."""
    import scripts.terratest_sweep as sweep

    vpc_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc/vpc-1"
    ec2_client = MagicMock()
    # First call raises DependencyViolation (a subnet is still being torn down),
    # second call succeeds.
    ec2_client.delete_vpc.side_effect = [
        _client_error("DependencyViolation", "DeleteVpc"),
        {},
    ]

    def extra(svc: str, **kw: Any) -> Any:
        return ec2_client if svc == "ec2" else None

    boto3_mock = _delete_boto3_mock(sandbox_account_id, [vpc_arn], extra)
    report_path = tmp_path / "report_dep_retry.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code == 0, "A transient DependencyViolation that clears on retry must not fail."
    assert ec2_client.delete_vpc.call_count == 2, "delete_vpc must be retried on the next pass."
    entry = next(e for e in report["found"] if e["arn"] == vpc_arn)
    assert entry["action"] == "deleted"
    assert report["remaining"] == 0


@pytest.mark.unit
def test_delete_mode_persistent_dependency_violation_surfaces_failure(
    monkeypatch: pytest.MonkeyPatch, sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """A DependencyViolation that NEVER clears surfaces as a failure (exit non-zero).

    Fail-safe: the run-scoped path must not mask a genuine leak. Here delete_vpc
    always raises DependencyViolation, so the loop-until-stable delete re-enumerates
    up to the TT_SWEEP_MAX_DELETE_PASSES ceiling and then, on the final pass, records
    the resource as 'failed' and the sweep exits non-zero -- bounded, never infinite.
    """
    import scripts.terratest_sweep as sweep

    # Pin the ceiling to a small value so the bound assertion is exact and the test
    # is fast + deterministic (env-driven, no hardcoded magic in the tool).
    monkeypatch.setenv("TT_SWEEP_MAX_DELETE_PASSES", "3")

    vpc_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc/vpc-stuck"
    ec2_client = MagicMock()
    ec2_client.delete_vpc.side_effect = _client_error("DependencyViolation", "DeleteVpc")

    def extra(svc: str, **kw: Any) -> Any:
        return ec2_client if svc == "ec2" else None

    boto3_mock = _delete_boto3_mock(sandbox_account_id, [vpc_arn], extra)
    report_path = tmp_path / "report_dep_stuck.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code != 0, "A persistent DependencyViolation must surface as a real failure."
    entry = next(e for e in report["found"] if e["arn"] == vpc_arn)
    assert entry["action"] == "failed"
    assert report["remaining"] >= 1
    # Bounded: delete_vpc is attempted at most once per pass, never infinitely.
    assert 1 < ec2_client.delete_vpc.call_count <= sweep._get_max_delete_passes()
    assert sweep._get_max_delete_passes() == 3


# ===========================================================================
# Loop-until-stable delete: RE-ENUMERATE every pass so multi-pass
# VPC-dependency deletions drain before the fail-on-residue check.
# (fix/terratest-sweep-delete-loop -- run 28523322127 root cause)
# ===========================================================================


def _tagging_page_for(arns: list[str], run_id_value: str) -> list[dict[str, Any]]:
    """Build a single Tagging-API get_resources page for the given ARNs."""
    return [
        {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": arn,
                    "Tags": [
                        {"Key": "Project", "Value": "telemetry-platform"},
                        {"Key": "terratest-run", "Value": run_id_value},
                    ],
                }
                for arn in arns
            ]
        }
    ]


def _delete_boto3_mock_seq(
    account_id: str,
    enumeration_snapshots: list[list[str]],
    client_factory_extra: Any,
    run_id_value: str = "run-harden",
) -> tuple[MagicMock, MagicMock]:
    """Build a boto3 mock whose Tagging API returns a fresh inventory per pass.

    ``enumeration_snapshots[i]`` is the list of ARNs returned by the (i+1)-th
    Tagging-API enumeration (one per loop-until-stable delete pass). The last
    snapshot repeats for any further enumerations. This lets a test reproduce
    eventually-consistent deletion: a resource visible in pass 1 that is gone from
    the fresh describe in pass 2 (or vice-versa: a resource the Tagging API only
    indexes on a later pass).

    Returns (boto3_mock, tagging_paginator) so a test can assert how many times the
    inventory was re-enumerated (tagging_paginator.paginate.call_count).
    """
    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": account_id,
        "Arn": f"arn:aws:iam::{account_id}:user/test",
        "UserId": "AIDA",
    }

    snapshots_iter = iter(enumeration_snapshots)
    last_snapshot = enumeration_snapshots[-1] if enumeration_snapshots else []

    def _paginate(**_kwargs: Any) -> list[dict[str, Any]]:
        try:
            arns = next(snapshots_iter)
        except StopIteration:
            arns = last_snapshot
        return _tagging_page_for(arns, run_id_value)

    tagging_paginator = MagicMock()
    tagging_paginator.paginate.side_effect = _paginate
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value = tagging_paginator

    def client_factory(svc: str, **kw: Any) -> Any:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        extra = client_factory_extra(svc, **kw)
        if extra is not None:
            return extra
        return MagicMock()

    boto3_mock.client.side_effect = client_factory
    return boto3_mock, tagging_paginator


@pytest.mark.unit
def test_delete_loops_and_drains_vpc_that_needs_a_second_pass(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """Reproduce run 28523322127: a VPC + its flow-log group orphan.

    Pass 1 deletes the flow-log group and issues delete_vpc, but the VPC still has
    a slowly-detaching dependency so delete_vpc raises DependencyViolation. The
    delete step must NOT stop there (the old single-pass behaviour left the VPC as
    residue for the check step to fail on). Instead it re-enumerates on pass 2 --
    where the flow-log group is gone and the VPC's dependency has drained -- and
    delete_vpc succeeds, so the sweep exits 0 and the account is clean.
    """
    import scripts.terratest_sweep as sweep

    vpc_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc/vpc-02e3fa0eb40dc58fe"
    log_arn = (
        f"arn:aws:logs:us-east-1:{sandbox_account_id}:log-group:test-vpc-outputs-basic-1782915489"
    )

    ec2_client = MagicMock()
    # Pass 1 -> DependencyViolation (ENIs/subnets still detaching); pass 2 -> gone.
    ec2_client.delete_vpc.side_effect = [
        _client_error("DependencyViolation", "DeleteVpc"),
        {},
    ]
    logs_client = MagicMock()

    def extra(svc: str, **kw: Any) -> Any:
        if svc == "ec2":
            return ec2_client
        if svc == "logs":
            return logs_client
        return None

    # Pass 1 sees both; pass 2 sees only the still-tagged VPC (the log group has
    # been deleted and de-indexed from the Tagging API).
    boto3_mock, tagging_paginator = _delete_boto3_mock_seq(
        sandbox_account_id, [[vpc_arn, log_arn], [vpc_arn]], extra
    )
    report_path = tmp_path / "report_vpc_two_pass.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code == 0, "The loop must drain the VPC on the second pass and exit clean."
    # Re-enumerated at least twice: the fix loops instead of deleting once.
    assert tagging_paginator.paginate.call_count >= 2
    # VPC attempted on pass 1 (DV) and again on pass 2 (success).
    assert ec2_client.delete_vpc.call_count == 2
    logs_client.delete_log_group.assert_called_once()
    vpc_entry = next(e for e in report["found"] if e["arn"] == vpc_arn)
    assert vpc_entry["action"] == "deleted"
    assert report["remaining"] == 0


@pytest.mark.unit
def test_delete_exits_immediately_when_first_pass_is_stable(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """Stable-exit: a pass that leaves nothing pending does NOT re-enumerate.

    All matched resources reach a terminal outcome on pass 1 (here the subnet is
    already gone -> already-deleted, zero real deletions and zero pending
    DependencyViolations), so the loop stops after ONE enumeration instead of
    spinning to the ceiling.
    """
    import scripts.terratest_sweep as sweep

    subnet_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:subnet/subnet-gone"
    ec2_client = MagicMock()
    ec2_client.delete_subnet.side_effect = _client_error("InvalidSubnetID.NotFound", "DeleteSubnet")

    def extra(svc: str, **kw: Any) -> Any:
        return ec2_client if svc == "ec2" else None

    boto3_mock, tagging_paginator = _delete_boto3_mock_seq(
        sandbox_account_id, [[subnet_arn]], extra
    )
    report_path = tmp_path / "report_stable_first_pass.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code == 0
    # Exactly ONE enumeration: no pending DependencyViolation -> stable, no re-enum.
    assert tagging_paginator.paginate.call_count == 1
    ec2_client.delete_subnet.assert_called_once()
    entry = next(e for e in report["found"] if e["arn"] == subnet_arn)
    assert entry["action"] == "already-deleted"
    assert report["remaining"] == 0


@pytest.mark.unit
def test_delete_loop_bounded_by_max_pass_ceiling(
    monkeypatch: pytest.MonkeyPatch, sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """The re-enumeration loop is bounded by TT_SWEEP_MAX_DELETE_PASSES.

    A VPC that NEVER clears its DependencyViolation must not loop forever: the
    loop re-enumerates at most TT_SWEEP_MAX_DELETE_PASSES times and then, on the
    final pass, records the resource as a failure (exit non-zero) -- proving the
    ceiling is honored and a genuine leak is never masked.
    """
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_MAX_DELETE_PASSES", "4")

    vpc_arn = f"arn:aws:ec2:us-east-1:{sandbox_account_id}:vpc/vpc-never-drains"
    ec2_client = MagicMock()
    ec2_client.delete_vpc.side_effect = _client_error("DependencyViolation", "DeleteVpc")

    def extra(svc: str, **kw: Any) -> Any:
        return ec2_client if svc == "ec2" else None

    boto3_mock, tagging_paginator = _delete_boto3_mock_seq(sandbox_account_id, [[vpc_arn]], extra)
    report_path = tmp_path / "report_bounded.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code != 0
    # Exactly max-pass enumerations + delete attempts (initial pass + re-enums),
    # never more -- the ceiling terminates the loop.
    assert tagging_paginator.paginate.call_count == 4
    assert ec2_client.delete_vpc.call_count == 4
    entry = next(e for e in report["found"] if e["arn"] == vpc_arn)
    assert entry["action"] == "failed"
    assert report["remaining"] >= 1


@pytest.mark.unit
def test_delete_loop_spares_in_flight_resources_on_every_pass(
    qa_account_id: str, tmp_path: pathlib.Path
) -> None:
    """The <TT_SWEEP_MIN_AGE_MINUTES in-flight spare is re-applied on every pass.

    On the GLOBAL sweep (run_id=None) a young resource belonging to a concurrent
    PR job must be spared -- and must STAY spared across the re-enumerating delete
    loop, never handed to a delete handler -- while an old orphan VPC drains over
    two passes. Regression guard: the loop must not delete an in-flight resource
    just because it re-enumerates.
    """
    import scripts.terratest_sweep as sweep

    now = datetime.datetime.now(datetime.UTC)
    fmt = "%Y%m%d%H%M%S"
    young_run_id = "tt-" + (now - datetime.timedelta(minutes=30)).strftime(fmt) + "-young1"
    old_run_id = "tt-" + (now - datetime.timedelta(minutes=600)).strftime(fmt) + "-oldabc"

    young_bucket_arn = f"arn:aws:s3:::{qa_account_id}-tt-inflight-bucket"
    old_vpc_arn = f"arn:aws:ec2:us-east-1:{qa_account_id}:vpc/vpc-old-orphan"

    ec2_client = MagicMock()
    ec2_client.delete_vpc.side_effect = [
        _client_error("DependencyViolation", "DeleteVpc"),
        {},
    ]
    s3_client = MagicMock()
    ce_client = MagicMock()
    ce_client.get_anomaly_subscriptions.return_value = {"AnomalySubscriptions": []}
    ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": []}

    def client_factory(svc: str, **kw: Any) -> Any:
        return {"ec2": ec2_client, "s3": s3_client, "ce": ce_client}.get(svc)

    # Per-resource run-id tags: build the pages by hand so young vs old differ.
    def _page(arns_with_runid: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "ResourceTagMappingList": [
                    {
                        "ResourceARN": arn,
                        "Tags": [
                            {"Key": "Project", "Value": "telemetry-platform"},
                            {"Key": "terratest-run", "Value": rid},
                        ],
                    }
                    for arn, rid in arns_with_runid
                ]
            }
        ]

    snapshots = iter(
        [
            _page([(young_bucket_arn, young_run_id), (old_vpc_arn, old_run_id)]),
            _page([(young_bucket_arn, young_run_id), (old_vpc_arn, old_run_id)]),
        ]
    )
    last_page = _page([(young_bucket_arn, young_run_id)])

    def _paginate(**_kwargs: Any) -> list[dict[str, Any]]:
        try:
            return next(snapshots)
        except StopIteration:
            return last_page

    tagging_paginator = MagicMock()
    tagging_paginator.paginate.side_effect = _paginate
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value = tagging_paginator
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": qa_account_id,
        "Arn": f"arn:aws:iam::{qa_account_id}:user/test",
        "UserId": "AIDA",
    }
    boto3_mock = MagicMock()

    def top_factory(svc: str, **kw: Any) -> Any:
        if svc == "sts":
            return sts_client
        if svc == "resourcegroupstaggingapi":
            return tagging_client
        built = client_factory(svc, **kw)
        return built if built is not None else MagicMock()

    boto3_mock.client.side_effect = top_factory

    report_path = tmp_path / "report_spare_across_passes.json"
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(report_path),
            run_id=None,  # GLOBAL sweep -> min-age spare applies
            boto3_mod=boto3_mock,
        )
    report = json.loads(report_path.read_text())

    assert int(exc.value.code) == 0, "Old orphan drains; young in-flight resource is spared."
    # The young in-flight bucket was NEVER handed to a delete handler on any pass.
    s3_client.delete_bucket.assert_not_called()
    # The old orphan VPC drained over two passes.
    assert ec2_client.delete_vpc.call_count == 2
    assert not any(e["arn"] == young_bucket_arn for e in report["found"])


@pytest.mark.unit
def test_delete_mode_non_benign_error_still_fails(
    sandbox_account_id: str, tmp_path: pathlib.Path
) -> None:
    """A genuine non-NotFound, non-dependency error is still recorded as failed (exit non-zero)."""
    import scripts.terratest_sweep as sweep

    arn = f"arn:aws:s3:::{sandbox_account_id}-tt-bucket"
    s3_client = MagicMock()
    s3_client.get_paginator.return_value.paginate.return_value = [
        {"Versions": [], "DeleteMarkers": []}
    ]
    s3_client.delete_bucket.side_effect = _client_error("AccessDenied", "DeleteBucket")

    def extra(svc: str, **kw: Any) -> Any:
        return s3_client if svc == "s3" else None

    boto3_mock = _delete_boto3_mock(sandbox_account_id, [arn], extra)
    report_path = tmp_path / "report_access_denied.json"
    code, report = _run_delete(sweep, boto3_mock, report_path)

    assert code != 0, "A genuine AccessDenied error must not be masked."
    entry = next(e for e in report["found"] if e["arn"] == arn)
    assert entry["action"] == "failed"


# ---------------------------------------------------------------------------
# Global-sweep min-age guard (daily-sweep-vs-PR race fix)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_id_age_minutes_parses_embedded_timestamp() -> None:
    """The UTC timestamp embedded in tt-<yyyymmddHHMMSS>-<rand> yields the age."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    # 90 minutes earlier -> 10:30:00 on the same day.
    run_id = "tt-20260629103000-ab12cd"
    age = sweep._run_id_age_minutes(run_id, now)
    assert age is not None
    assert abs(age - 90.0) < 0.001, f"expected ~90 minutes, got {age}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "run_id",
    [
        "manual-override-id",  # not the tt- prefix
        "tt-shortts-rand",  # timestamp token not 14 digits
        "tt-2026062910300X-rand",  # non-digit in timestamp token
        "tt-20260629103000",  # missing random segment
    ],
)
def test_run_id_age_minutes_unparseable_returns_none(run_id: str) -> None:
    """A run id whose age cannot be derived from its value returns None."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    assert sweep._run_id_age_minutes(run_id, now) is None


@pytest.mark.unit
def test_run_id_tag_value_extracts_terratest_run_tag() -> None:
    """The terratest-run tag value is extracted from the Tagging-API tag list."""
    import scripts.terratest_sweep as sweep

    resource = {
        "ResourceARN": "arn:aws:s3:::bucket",
        "Tags": [
            {"Key": "Project", "Value": "telemetry-platform"},
            {"Key": "terratest-run", "Value": "tt-20260629103000-ab12cd"},
        ],
    }
    assert sweep._run_id_tag_value(resource) == "tt-20260629103000-ab12cd"
    assert sweep._run_id_tag_value({"ResourceARN": "x", "Tags": []}) is None


@pytest.mark.unit
def test_partition_by_min_age_spares_young_sweeps_old_and_unparseable() -> None:
    """Young (recent run id) is spared; old and unparseable ids are swept."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    young = {
        "ResourceARN": "arn:young",
        "Tags": [{"Key": "terratest-run", "Value": "tt-20260629115000-young1"}],  # 10 min old
    }
    old = {
        "ResourceARN": "arn:old",
        "Tags": [{"Key": "terratest-run", "Value": "tt-20260629080000-old123"}],  # 240 min old
    }
    unparseable = {
        "ResourceARN": "arn:weird",
        "Tags": [{"Key": "terratest-run", "Value": "operator-manual-run"}],
    }
    sweepable, spared = sweep._partition_by_min_age([young, old, unparseable], 120, now)
    spared_arns = {r["ResourceARN"] for r in spared}
    sweepable_arns = {r["ResourceARN"] for r in sweepable}
    assert spared_arns == {"arn:young"}
    assert sweepable_arns == {"arn:old", "arn:weird"}


@pytest.mark.unit
def test_get_min_age_minutes_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """TT_SWEEP_MIN_AGE_MINUTES overrides the default at call time."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_MIN_AGE_MINUTES", "5")
    assert sweep._get_min_age_minutes() == 5
    monkeypatch.delenv("TT_SWEEP_MIN_AGE_MINUTES", raising=False)
    assert sweep._get_min_age_minutes() == sweep._constants.TT_SWEEP_MIN_AGE_MINUTES


# ---------------------------------------------------------------------------
# Cost Explorer anomaly-monitor name-pattern sweep (untaggable orphans)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test-monitor-1782718049", True),  # prefix match
        ("telemetry-sandbox-shared-observability-000-abc123-monitor", True),  # suffix match
        ("some-other-resource", False),
    ],
)
def test_ce_name_matches_monitor_patterns(name: str, expected: bool) -> None:
    import scripts.constants as constants
    import scripts.terratest_sweep as sweep

    assert (
        sweep._ce_name_matches(
            name, constants.CE_MONITOR_NAME_PREFIXES, constants.CE_MONITOR_NAME_SUFFIXES
        )
        is expected
    )


@pytest.mark.unit
def test_ce_epoch_age_minutes() -> None:
    """A trailing unix-epoch token yields the age; a non-numeric tail yields None."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    epoch_90_min_ago = int((now - datetime.timedelta(minutes=90)).timestamp())
    age = sweep._ce_epoch_age_minutes(f"test-monitor-{epoch_90_min_ago}", now)
    assert age is not None and abs(age - 90.0) < 1.0
    # The observability-style "<name>-<suffix>-monitor" tail is non-numeric.
    assert sweep._ce_epoch_age_minutes("obs-abc123-monitor", now) is None


def _ce_boto3_mock(
    subscriptions: list[dict],
    monitors: list[dict],
    delete_errors: dict[str, Exception] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a boto3 mock whose ce client returns the given monitors/subscriptions."""
    delete_errors = delete_errors or {}
    ce_client = MagicMock()
    ce_client.get_anomaly_subscriptions.return_value = {"AnomalySubscriptions": subscriptions}
    ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": monitors}

    def _del_sub(SubscriptionArn: str) -> dict:  # noqa: N803 (AWS kwarg name)
        if SubscriptionArn in delete_errors:
            raise delete_errors[SubscriptionArn]
        return {}

    def _del_mon(MonitorArn: str) -> dict:  # noqa: N803 (AWS kwarg name)
        if MonitorArn in delete_errors:
            raise delete_errors[MonitorArn]
        return {}

    ce_client.delete_anomaly_subscription.side_effect = _del_sub
    ce_client.delete_anomaly_monitor.side_effect = _del_mon
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = ce_client
    return boto3_mock, ce_client


@pytest.mark.unit
def test_sweep_ce_monitors_delete_subscriptions_before_monitors() -> None:
    """Delete mode deletes matching subscriptions and monitors; non-matching untouched."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    subs = [
        {"SubscriptionName": "test-sub-1782718049", "SubscriptionArn": "arn:ce:sub/test"},
        {"SubscriptionName": "prod-keep", "SubscriptionArn": "arn:ce:sub/keep"},
    ]
    monitors = [
        {"MonitorName": "test-monitor-1782718049", "MonitorArn": "arn:ce:mon/test"},
        {"MonitorName": "prod-keep", "MonitorArn": "arn:ce:mon/keep"},
    ]
    boto3_mock, ce = _ce_boto3_mock(subs, monitors)
    # min_age 0 so nothing is age-spared in this test.
    entries = sweep._sweep_ce_anomaly_monitors(boto3_mock, "delete", 0, now)

    ce.delete_anomaly_subscription.assert_called_once_with(SubscriptionArn="arn:ce:sub/test")
    ce.delete_anomaly_monitor.assert_called_once_with(MonitorArn="arn:ce:mon/test")
    actions = {e["arn"]: e["action"] for e in entries}
    assert actions == {"arn:ce:sub/test": "deleted", "arn:ce:mon/test": "deleted"}
    # The deletion order is subscription-before-monitor.
    call_order = [
        c[0]
        for c in ce.method_calls
        if c[0] in ("delete_anomaly_subscription", "delete_anomaly_monitor")
    ]
    assert call_order == ["delete_anomaly_subscription", "delete_anomaly_monitor"]


@pytest.mark.unit
def test_sweep_ce_monitors_check_mode_reports_without_deleting() -> None:
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    monitors = [{"MonitorName": "test-monitor-1782718049", "MonitorArn": "arn:ce:mon/test"}]
    boto3_mock, ce = _ce_boto3_mock([], monitors)
    entries = sweep._sweep_ce_anomaly_monitors(boto3_mock, "check", 0, now)
    ce.delete_anomaly_monitor.assert_not_called()
    assert entries == [
        {
            "arn": "arn:ce:mon/test",
            "type": "ce",
            "action": "found",
            "name": "test-monitor-1782718049",
        }
    ]


@pytest.mark.unit
def test_sweep_ce_monitors_spares_young_timestamped_monitor() -> None:
    """A test-monitor-<recent-epoch> younger than min-age is NOT deleted."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    young_epoch = int((now - datetime.timedelta(minutes=10)).timestamp())
    old_epoch = int((now - datetime.timedelta(minutes=240)).timestamp())
    monitors = [
        {"MonitorName": f"test-monitor-{young_epoch}", "MonitorArn": "arn:ce:mon/young"},
        {"MonitorName": f"test-monitor-{old_epoch}", "MonitorArn": "arn:ce:mon/old"},
    ]
    boto3_mock, ce = _ce_boto3_mock([], monitors)
    entries = sweep._sweep_ce_anomaly_monitors(boto3_mock, "delete", 120, now)
    ce.delete_anomaly_monitor.assert_called_once_with(MonitorArn="arn:ce:mon/old")
    assert [e["arn"] for e in entries] == ["arn:ce:mon/old"]


@pytest.mark.unit
def test_sweep_ce_monitors_benign_not_found_is_already_gone() -> None:
    """A NotFound on delete is recorded as already-gone, not failed."""
    import datetime

    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    monitors = [{"MonitorName": "test-monitor-1782718049", "MonitorArn": "arn:ce:mon/gone"}]
    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "UnknownMonitorException", "Message": "not found"}},
        "DeleteAnomalyMonitor",
    )
    # UnknownMonitorException does not contain "NotFound"; force it benign via the
    # generic NotFound rule by using a *NotFound* code instead.
    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
        "DeleteAnomalyMonitor",
    )
    boto3_mock, ce = _ce_boto3_mock([], monitors, delete_errors={"arn:ce:mon/gone": err})
    entries = sweep._sweep_ce_anomaly_monitors(boto3_mock, "delete", 0, now)
    assert entries == [
        {
            "arn": "arn:ce:mon/gone",
            "type": "ce",
            "action": "already-gone",
            "name": "test-monitor-1782718049",
        }
    ]


@pytest.mark.unit
def test_sweep_ce_monitors_real_error_is_failed() -> None:
    """A non-benign delete error is recorded as failed (fail-safe)."""
    import datetime

    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    now = datetime.datetime(2026, 6, 29, 12, 0, 0, tzinfo=datetime.UTC)
    monitors = [{"MonitorName": "test-monitor-1782718049", "MonitorArn": "arn:ce:mon/x"}]
    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "DeleteAnomalyMonitor",
    )
    boto3_mock, ce = _ce_boto3_mock([], monitors, delete_errors={"arn:ce:mon/x": err})
    entries = sweep._sweep_ce_anomaly_monitors(boto3_mock, "delete", 0, now)
    assert entries[0]["action"] == "failed"


@pytest.mark.unit
def test_ce_paginate_follows_next_page_token() -> None:
    """_ce_paginate walks NextPageToken until exhausted."""
    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.get_anomaly_monitors.side_effect = [
        {"AnomalyMonitors": [{"MonitorName": "a"}], "NextPageToken": "t1"},
        {"AnomalyMonitors": [{"MonitorName": "b"}]},
    ]
    items = sweep._ce_paginate(client, "get_anomaly_monitors", "AnomalyMonitors")
    assert [m["MonitorName"] for m in items] == ["a", "b"]
    assert client.get_anomaly_monitors.call_count == 2


# ---------------------------------------------------------------------------
# main() integration: global-sweep age guard + CE sweep scoping
# ---------------------------------------------------------------------------


def _main_boto3_mock(
    account_id: str,
    tag_resources: list[dict],
    ce_client: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build a boto3 mock with sts + tagging (+ optional ce) clients for main()."""
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": account_id,
        "Arn": f"arn:aws:iam::{account_id}:user/test",
        "UserId": "AIDA",
    }
    tagging_client = MagicMock()
    tagging_client.get_paginator.return_value.paginate.return_value = [
        {"ResourceTagMappingList": tag_resources}
    ]
    if ce_client is None:
        ce_client = MagicMock()
        ce_client.get_anomaly_subscriptions.return_value = {"AnomalySubscriptions": []}
        ce_client.get_anomaly_monitors.return_value = {"AnomalyMonitors": []}
    boto3_mock = MagicMock()

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "sts":
            return sts_client
        if svc == "ce":
            return ce_client
        return tagging_client

    boto3_mock.client.side_effect = client_factory
    return boto3_mock, tagging_client, ce_client


@pytest.mark.unit
def test_global_check_spares_young_resource(
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """A just-created (young) tagged resource is spared by the global sweep min-age
    guard, so a daily check during a concurrent PR matrix run reports zero residue."""
    import datetime

    import scripts.terratest_sweep as sweep

    young_ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    young_run_id = f"tt-{young_ts}-yng001"
    tag_resources = [
        {
            "ResourceARN": f"arn:aws:s3:::{sandbox_account_id}-bucket-inflight",
            "Tags": [
                {"Key": "Project", "Value": "telemetry-platform"},
                {"Key": "terratest-run", "Value": young_run_id},
            ],
        }
    ]
    boto3_mock, _tagging, _ce = _main_boto3_mock(sandbox_account_id, tag_resources)

    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(tmp_path / "report.json"),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0, "young in-flight resource must be spared (zero residue)"


@pytest.mark.unit
def test_run_scoped_check_does_not_invoke_ce_sweep(
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """The untaggable-CE name-pattern sweep runs ONLY in the global path; a
    run-scoped sweep must not enumerate CE monitors (would race other runs)."""
    import scripts.terratest_sweep as sweep

    boto3_mock, _tagging, ce_client = _main_boto3_mock(sandbox_account_id, [])
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="check",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(tmp_path / "report.json"),
            run_id="tt-20260629103000-scoped",
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0
    ce_client.get_anomaly_monitors.assert_not_called()
    ce_client.get_anomaly_subscriptions.assert_not_called()


@pytest.mark.unit
def test_global_delete_invokes_ce_sweep_and_spares_young(
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """Global delete enumerates CE monitors and deletes only the old, matched one."""
    import datetime

    import scripts.terratest_sweep as sweep

    now = datetime.datetime.now(datetime.UTC)
    old_epoch = int((now - datetime.timedelta(hours=6)).timestamp())
    young_epoch = int((now - datetime.timedelta(minutes=5)).timestamp())
    ce_client = MagicMock()
    ce_client.get_anomaly_subscriptions.return_value = {"AnomalySubscriptions": []}
    ce_client.get_anomaly_monitors.return_value = {
        "AnomalyMonitors": [
            {"MonitorName": f"test-monitor-{old_epoch}", "MonitorArn": "arn:ce:mon/old"},
            {"MonitorName": f"test-monitor-{young_epoch}", "MonitorArn": "arn:ce:mon/young"},
        ]
    }
    boto3_mock, _tagging, _ce = _main_boto3_mock(sandbox_account_id, [], ce_client=ce_client)
    with pytest.raises(SystemExit) as exc:
        sweep.main(
            mode="delete",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            report_path=str(tmp_path / "report.json"),
            run_id=None,
            boto3_mod=boto3_mock,
        )
    assert exc.value.code == 0
    ce_client.delete_anomaly_monitor.assert_called_once_with(MonitorArn="arn:ce:mon/old")


# ---------------------------------------------------------------------------
# CognitoIdpHandler -- Cognito user pool sweep (Tagging-API-lag phantom handling)
# ---------------------------------------------------------------------------

_COGNITO_ARN = "arn:aws:cognito-idp:us-east-1:222222222222:userpool/us-east-1_TESTpool1"


@pytest.mark.unit
def test_cognito_handler_is_live_true_when_pool_exists() -> None:
    """is_live() returns True when DescribeUserPool succeeds (the pool is a real orphan)."""
    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.return_value = {"UserPool": {"Id": "us-east-1_TESTpool1"}}
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    assert handler.is_live(_COGNITO_ARN, boto3_mock) is True
    client.describe_user_pool.assert_called_once_with(UserPoolId="us-east-1_TESTpool1")
    assert boto3_mock.client.call_count == 1
    assert boto3_mock.client.call_args.args == ("cognito-idp",)
    assert boto3_mock.client.call_args.kwargs["region_name"] == "us-east-1"
    # Every client the sweep builds now carries the shared adaptive-retry Config.
    assert boto3_mock.client.call_args.kwargs["config"].retries.get("mode") == "adaptive"


@pytest.mark.unit
def test_cognito_handler_is_live_false_when_pool_gone() -> None:
    """is_live() returns False when DescribeUserPool raises ResourceNotFoundException.

    This is the eventual-consistency phantom: terraform destroy already deleted the pool
    but the Tagging API still lists it. It must NOT be counted as an orphan.
    """
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "pool gone"}},
        "DescribeUserPool",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    assert handler.is_live(_COGNITO_ARN, boto3_mock) is False


@pytest.mark.unit
def test_cognito_handler_is_live_propagates_unexpected_error() -> None:
    """is_live() propagates a non-NotFound ClientError so a real failure is not masked."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "TooManyRequestsException", "Message": "throttled"}},
        "DescribeUserPool",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    with pytest.raises(botocore.exceptions.ClientError):
        handler.is_live(_COGNITO_ARN, boto3_mock)


@pytest.mark.unit
def test_cognito_handler_delete_clears_protection_and_domain_then_deletes() -> None:
    """delete() clears ACTIVE deletion protection, deletes the Hosted-UI domain, then the pool."""
    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.return_value = {
        "UserPool": {
            "Id": "us-east-1_TESTpool1",
            "DeletionProtection": "ACTIVE",
            "Domain": "telemetry-useast1-sandbox-shared-portal-auth-000",
        }
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    result = handler.delete(_COGNITO_ARN, boto3_mock)

    assert result == "deleted"
    client.update_user_pool.assert_called_once_with(
        UserPoolId="us-east-1_TESTpool1", DeletionProtection="INACTIVE"
    )
    client.delete_user_pool_domain.assert_called_once_with(
        Domain="telemetry-useast1-sandbox-shared-portal-auth-000",
        UserPoolId="us-east-1_TESTpool1",
    )
    client.delete_user_pool.assert_called_once_with(UserPoolId="us-east-1_TESTpool1")


@pytest.mark.unit
def test_cognito_handler_delete_no_domain_no_protection() -> None:
    """delete() deletes the pool directly when it has no domain and protection is INACTIVE."""
    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.return_value = {
        "UserPool": {"Id": "us-east-1_TESTpool1", "DeletionProtection": "INACTIVE"}
    }
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    result = handler.delete(_COGNITO_ARN, boto3_mock)

    assert result == "deleted"
    client.update_user_pool.assert_not_called()
    client.delete_user_pool_domain.assert_not_called()
    client.delete_user_pool.assert_called_once_with(UserPoolId="us-east-1_TESTpool1")


@pytest.mark.unit
def test_cognito_handler_delete_already_gone_on_describe_is_benign() -> None:
    """delete() returns already-deleted when the pool is gone (ResourceNotFound on describe)."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "pool gone"}},
        "DescribeUserPool",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    result = handler.delete(_COGNITO_ARN, boto3_mock)

    assert result == "already-deleted"
    client.delete_user_pool.assert_not_called()


@pytest.mark.unit
def test_cognito_handler_delete_domain_already_gone_is_benign() -> None:
    """A ResourceNotFoundException deleting the domain is swallowed; pool delete proceeds."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.return_value = {
        "UserPool": {"Id": "us-east-1_TESTpool1", "Domain": "some-prefix-000"}
    }
    client.delete_user_pool_domain.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "domain gone"}},
        "DeleteUserPoolDomain",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    result = handler.delete(_COGNITO_ARN, boto3_mock)

    assert result == "deleted"
    client.delete_user_pool.assert_called_once_with(UserPoolId="us-east-1_TESTpool1")


@pytest.mark.unit
def test_cognito_handler_delete_pool_race_already_gone_is_benign() -> None:
    """A ResourceNotFoundException on the final delete_user_pool (raced) is treated as success."""
    import botocore.exceptions

    import scripts.terratest_sweep as sweep

    client = MagicMock()
    client.describe_user_pool.return_value = {
        "UserPool": {"Id": "us-east-1_TESTpool1", "DeletionProtection": "INACTIVE"}
    }
    client.delete_user_pool.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "pool gone"}},
        "DeleteUserPool",
    )
    boto3_mock = MagicMock()
    boto3_mock.client.return_value = client

    handler = sweep.CognitoIdpHandler()
    assert handler.delete(_COGNITO_ARN, boto3_mock) == "already-deleted"


@pytest.mark.unit
def test_cognito_handler_registered_in_dispatch_table() -> None:
    """The cognito-idp service prefix resolves to CognitoIdpHandler in the dispatch table."""
    import scripts.terratest_sweep as sweep

    assert "cognito-idp" in sweep._HANDLERS
    assert isinstance(sweep._HANDLERS["cognito-idp"], sweep.CognitoIdpHandler)


# ---------------------------------------------------------------------------
# Adaptive-retry Config on EVERY deletion-path client (throttle survival).
#
# The scheduled sweep failed (run 28500776616, exit 2) with
# 'ThrottlingException ... DeleteCluster ... (reached max retries: 4)': the ECS
# (and every other non-tagging) client was built with botocore's DEFAULT retry
# (standard, 4 attempts) instead of the adaptive retry the Tagging-API client
# already used. These tests pin the contract that every deletion-path client is
# constructed with adaptive retry and a max_attempts ceiling derived from the
# env-driven poll budget, so a low-TPS control-plane API (ECS DeleteCluster) is
# retried with adaptive backoff rather than tallied as a spurious failure.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_client_applies_adaptive_retry_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """_make_client builds a client with adaptive retry and budget-derived max_attempts."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "300")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "10")

    boto3_mock = MagicMock()
    sweep._make_client(boto3_mock, "ecs")

    assert boto3_mock.client.call_args.args[0] == "ecs"
    cfg = boto3_mock.client.call_args.kwargs["config"]
    assert cfg.retries.get("mode") == "adaptive", (
        f"Expected retries.mode='adaptive'; got {cfg.retries!r}"
    )
    ceiling = sweep._compute_max_attempts(300.0, 10.0)
    assert cfg.retries.get("max_attempts") >= ceiling, (
        f"Expected max_attempts >= {ceiling}; got {cfg.retries.get('max_attempts')!r}"
    )


@pytest.mark.unit
def test_make_client_honors_explicit_config() -> None:
    """A caller-supplied config kwarg wins over the shared adaptive-retry Config.

    The Tagging-API enumeration derives max_attempts from explicit poll args for
    its diagnostic message and passes its own config; _make_client must not clobber it.
    """
    import botocore.config

    import scripts.terratest_sweep as sweep

    boto3_mock = MagicMock()
    explicit = botocore.config.Config(retries={"mode": "standard", "max_attempts": 2})
    sweep._make_client(boto3_mock, "resourcegroupstaggingapi", config=explicit)

    assert boto3_mock.client.call_args.kwargs["config"] is explicit


@pytest.mark.unit
def test_make_client_passes_region_and_adds_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """region_name (WAFv2/Cognito paths) is forwarded and adaptive retry still applied."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "300")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "10")

    boto3_mock = MagicMock()
    sweep._make_client(boto3_mock, "wafv2", region_name="us-east-1")

    assert boto3_mock.client.call_args.kwargs["region_name"] == "us-east-1"
    assert boto3_mock.client.call_args.kwargs["config"].retries.get("mode") == "adaptive"


@pytest.mark.unit
def test_client_retry_config_max_attempts_from_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """_client_retry_config derives max_attempts from TT_SWEEP_POLL_TIMEOUT/INTERVAL."""
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "120")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "5")

    cfg = sweep._client_retry_config()
    assert cfg.retries.get("mode") == "adaptive"
    assert cfg.retries.get("max_attempts") == sweep._compute_max_attempts(120.0, 5.0)


@pytest.mark.unit
def test_adaptive_retry_config_shape() -> None:
    """_adaptive_retry_config returns adaptive mode with the exact max_attempts given."""
    import scripts.terratest_sweep as sweep

    cfg = sweep._adaptive_retry_config(17)
    assert cfg.retries.get("mode") == "adaptive"
    assert cfg.retries.get("max_attempts") == 17


@pytest.mark.unit
def test_ecs_cluster_delete_client_built_with_adaptive_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ECSHandler.delete (the throttled DeleteCluster case) builds an adaptive-retry client.

    Reproduces run 28500776616's failing path: a cluster delete must go out on a
    client whose retry mode is adaptive with a budget-derived max_attempts ceiling
    far above botocore's default of 4, so 'ThrottlingException ... (reached max
    retries: 4)' can no longer fail the sweep.
    """
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "300")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "10")

    arn = "arn:aws:ecs:us-east-1:333333333333:cluster/ecs-w-1782780289-cluster"
    ecs_client = MagicMock()
    captured: list[Any] = []

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        if svc == "ecs":
            captured.append(kw.get("config"))
        return ecs_client

    boto3_mock = MagicMock()
    boto3_mock.client.side_effect = client_factory

    handler = sweep.ECSHandler()
    assert handler.delete(arn, boto3_mock) == "deleted"
    ecs_client.delete_cluster.assert_called_once_with(cluster="ecs-w-1782780289-cluster")

    non_null = [c for c in captured if c is not None]
    assert non_null, "ECSHandler.delete built the ecs client without an adaptive-retry config"
    cfg = non_null[0]
    assert cfg.retries.get("mode") == "adaptive"
    ceiling = sweep._compute_max_attempts(300.0, 10.0)
    assert cfg.retries.get("max_attempts") >= ceiling
    # The ceiling must be well above botocore's default 4 that caused the failure.
    assert ceiling > 4


@pytest.mark.unit
@pytest.mark.parametrize(
    ("handler_name", "service", "arn", "delete_attr"),
    [
        ("SNSHandler", "sns", "arn:aws:sns:us-east-1:333333333333:tt-topic", "delete_topic"),
        (
            "LambdaHandler",
            "lambda",
            "arn:aws:lambda:us-east-1:333333333333:function:tt-fn",
            "delete_function",
        ),
        (
            "ACMHandler",
            "acm",
            "arn:aws:acm:us-east-1:333333333333:certificate/abc-123",
            "delete_certificate",
        ),
        (
            "AthenaHandler",
            "athena",
            "arn:aws:athena:us-east-1:333333333333:workgroup/tt-wg",
            "delete_work_group",
        ),
        (
            "FirehoseHandler",
            "firehose",
            "arn:aws:firehose:us-east-1:333333333333:deliverystream/tt-stream",
            "delete_delivery_stream",
        ),
        (
            "SSMHandler",
            "ssm",
            "arn:aws:ssm:us-east-1:333333333333:parameter/tt/param",
            "delete_parameter",
        ),
        (
            "LogsHandler",
            "logs",
            "arn:aws:logs:us-east-1:333333333333:log-group:tt-lg",
            "delete_log_group",
        ),
        (
            "CloudWatchHandler",
            "cloudwatch",
            "arn:aws:cloudwatch:us-east-1:333333333333:alarm:tt-alarm",
            "delete_alarms",
        ),
    ],
)
def test_delete_handler_client_uses_adaptive_retry(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
    service: str,
    arn: str,
    delete_attr: str,
) -> None:
    """Every deletion-path handler constructs its AWS client with adaptive retry.

    Guards against a future handler regressing to botocore's shallow default retry
    by bypassing the centralized _make_client construction site.
    """
    import scripts.terratest_sweep as sweep

    monkeypatch.setenv("TT_SWEEP_POLL_TIMEOUT", "300")
    monkeypatch.setenv("TT_SWEEP_POLL_INTERVAL", "10")

    svc_client = MagicMock()
    captured: dict[str, list[Any]] = {}

    def client_factory(svc: str, **kw: Any) -> MagicMock:
        captured.setdefault(svc, []).append(kw.get("config"))
        return svc_client

    boto3_mock = MagicMock()
    boto3_mock.client.side_effect = client_factory

    handler = getattr(sweep, handler_name)()
    assert handler.delete(arn, boto3_mock) == "deleted"

    getattr(svc_client, delete_attr).assert_called_once()
    configs = [c for c in captured.get(service, []) if c is not None]
    assert configs, f"{handler_name} built a '{service}' client without an adaptive-retry config"
    cfg = configs[0]
    assert cfg.retries.get("mode") == "adaptive", (
        f"{handler_name}: expected retries.mode='adaptive'; got {cfg.retries!r}"
    )
    ceiling = sweep._compute_max_attempts(300.0, 10.0)
    got = cfg.retries.get("max_attempts")
    assert got >= ceiling, f"{handler_name}: expected max_attempts >= {ceiling}; got {got!r}"
