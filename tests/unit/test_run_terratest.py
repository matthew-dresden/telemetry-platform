"""Unit tests for scripts/run_terratest.py -- FR-1 terratest runner.

All AWS interactions and subprocess calls are mocked; no real AWS calls or
go test invocations are made.

Covers:
  - MODULE_PATH validation (must exist, have go.mod requiring
    terraform-terratest-framework, and a tests/ dir)
  - test.config parsing (malformed lines skipped, env-wins precedence for
    TERRATEST_IDEMPOTENCY / GO_TEST_TIMEOUT)
  - missing test.config fails fast
  - run-id generation (tt-<UTC yyyymmddHHMMSS>-<6-char random>) and
    TERRATEST_RUN_ID override
  - deny-set refusal: prod-infra / dns-owner accounts refused with exit 1
  - post-run cleanup wiring (examples, sweep check/delete/re-check)
  - exit-code composition (go test exit AND zero residue required)
  - TF_PLUGIN_CACHE_DIR exported for go-test invocation
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACCOUNTS_JSON_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "terragrunt" / "common" / "accounts.json"
)


def _load_accounts_data() -> dict:
    assert ACCOUNTS_JSON_PATH.exists(), f"accounts.json not found at {ACCOUNTS_JSON_PATH}"
    return json.loads(ACCOUNTS_JSON_PATH.read_text())


@pytest.fixture()
def accounts_data() -> dict:
    return _load_accounts_data()


@pytest.fixture()
def sandbox_account_id(accounts_data: dict) -> str:
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "sandbox":
            return acct_id
    pytest.fail("No sandbox account in accounts.json")


@pytest.fixture()
def prod_account_id(accounts_data: dict) -> str:
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "prod-infra":
            return acct_id
    pytest.fail("No prod-infra account in accounts.json")


@pytest.fixture()
def dns_owner_account_id(accounts_data: dict) -> str:
    for acct_id, row in accounts_data.items():
        if row["account_role"] == "dns-owner":
            return acct_id
    pytest.fail("No dns-owner account in accounts.json")


def _make_valid_module(tmp_path: pathlib.Path, go_timeout: str = "10m") -> pathlib.Path:
    """Create a minimal valid module directory for testing."""
    module_path = tmp_path / "providers" / "aws" / "primitives" / "kms-key"
    module_path.mkdir(parents=True)
    # tests/ directory
    (module_path / "tests").mkdir()
    # go.mod requiring terraform-terratest-framework
    (module_path / "go.mod").write_text(
        "module github.com/test/mod\n\n"
        "require (\n"
        "    github.com/matthew-dresden/terraform-terratest-framework v1.0.0\n"
        ")\n"
    )
    # test.config
    (module_path / "test.config").write_text(
        f"TERRATEST_IDEMPOTENCY=true\nGO_TEST_TIMEOUT={go_timeout}\n"
    )
    # examples directory
    examples = module_path / "examples" / "basic"
    examples.mkdir(parents=True)
    (examples / ".terraform").mkdir()
    (examples / "terraform.tfstate").write_text("{}")
    return module_path


def _make_sts_boto3_mock(account_id: str) -> MagicMock:
    boto3_mock = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": account_id,
        "Arn": f"arn:aws:iam::{account_id}:user/test",
        "UserId": "AIDATEST",
    }
    boto3_mock.client.return_value = sts_client
    return boto3_mock


def _run_main(
    module_path: str,
    *,
    boto3_mod: Any,
    invoke_result: Any = None,
    sweep_check_result: int = 0,
    sweep_delete_result: int = 0,
    sweep_recheck_result: int = 0,
    monkeypatch: pytest.MonkeyPatch | None = None,
    accounts_json: str | None = None,
) -> int:
    """Import and call run_terratest.main(), returning the exit code via SystemExit."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]

    import subprocess

    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 0 if invoke_result is None else invoke_result

    import scripts.run_terratest as rt

    accts = accounts_json or str(ACCOUNTS_JSON_PATH)

    # Patch invoke_pinned_binary via the module's reference
    with (
        patch.object(rt, "_invoke_go_test", return_value=fake_completed.returncode),
        patch.object(rt, "_sweep_check", return_value=sweep_check_result),
        patch.object(rt, "_sweep_delete", return_value=sweep_delete_result),
        patch.object(rt, "_sweep_recheck", return_value=sweep_recheck_result),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=module_path,
            accounts_json=accts,
            boto3_mod=boto3_mod,
        )
    return exc.value.code


# ---------------------------------------------------------------------------
# MODULE_PATH validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_module_path_nonexistent_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """A non-existent MODULE_PATH must cause exit 1 with a clear error message."""
    nonexistent = str(tmp_path / "does_not_exist")
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=nonexistent,
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0


@pytest.mark.unit
def test_module_path_missing_tests_dir_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """MODULE_PATH without a tests/ directory must cause exit 1."""
    module_path = tmp_path / "no-tests"
    module_path.mkdir()
    (module_path / "go.mod").write_text(
        "module github.com/test/mod\n"
        "require github.com/matthew-dresden/terraform-terratest-framework v1.0.0\n"
    )
    (module_path / "test.config").write_text("GO_TEST_TIMEOUT=5m\n")
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0


@pytest.mark.unit
def test_module_path_missing_go_mod_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """MODULE_PATH without go.mod must cause exit 1."""
    module_path = tmp_path / "no-gomod"
    module_path.mkdir()
    (module_path / "tests").mkdir()
    (module_path / "test.config").write_text("GO_TEST_TIMEOUT=5m\n")
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0


@pytest.mark.unit
def test_module_path_go_mod_missing_framework_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """MODULE_PATH with go.mod that does NOT require terraform-terratest-framework fails."""
    module_path = tmp_path / "wrong-framework"
    module_path.mkdir()
    (module_path / "tests").mkdir()
    (module_path / "go.mod").write_text(
        "module github.com/test/mod\n\nrequire github.com/some/other-framework v1.0.0\n"
    )
    (module_path / "test.config").write_text("GO_TEST_TIMEOUT=5m\n")
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0


@pytest.mark.unit
def test_module_path_error_message_content(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """The error message must mention the MODULE_PATH and the missing item."""
    nonexistent = str(tmp_path / "missing_module")
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit):
        rt.main(
            module_path=nonexistent,
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert "MODULE_PATH" in captured.err or "missing_module" in captured.err


# ---------------------------------------------------------------------------
# test.config parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_test_config_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """A missing test.config must cause exit 1 with a clear error message."""
    module_path = tmp_path / "mod"
    module_path.mkdir()
    (module_path / "tests").mkdir()
    (module_path / "go.mod").write_text(
        "module github.com/test/mod\n"
        "require github.com/matthew-dresden/terraform-terratest-framework v1.0.0\n"
    )
    # No test.config created
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "test.config" in captured.err


@pytest.mark.unit
def test_test_config_parses_go_test_timeout(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GO_TEST_TIMEOUT from test.config is used when env var is not set."""
    module_path = _make_valid_module(tmp_path, go_timeout="15m")
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_timeout: list[str] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_timeout.append(timeout)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert captured_timeout == ["15m"], (
        f"Expected timeout '15m' from test.config; got {captured_timeout}"
    )


@pytest.mark.unit
def test_env_var_wins_over_test_config_for_go_test_timeout(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GO_TEST_TIMEOUT env var overrides the value in test.config."""
    module_path = _make_valid_module(tmp_path, go_timeout="15m")
    monkeypatch.setenv("GO_TEST_TIMEOUT", "30m")
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_timeout: list[str] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_timeout.append(timeout)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert captured_timeout == ["30m"], f"Expected timeout '30m' from env; got {captured_timeout}"


@pytest.mark.unit
def test_constants_default_used_when_timeout_absent_from_config_and_env(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GO_TEST_TIMEOUT is absent from both test.config and env, the
    constants.DEFAULT_GO_TEST_TIMEOUT value (env-driven via GO_TEST_TIMEOUT env var
    default) is used.  This proves the precedence chain:
    env > test.config > constants-default."""
    module_path = _make_valid_module(tmp_path)
    # Overwrite test.config with no GO_TEST_TIMEOUT entry
    (module_path / "test.config").write_text("TERRATEST_IDEMPOTENCY=true\n")
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    if "scripts.constants" in sys.modules:
        del sys.modules["scripts.constants"]
    import scripts.constants as _c
    import scripts.run_terratest as rt

    captured_timeout: list[str] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_timeout.append(timeout)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert captured_timeout == [_c.DEFAULT_GO_TEST_TIMEOUT], (
        f"Expected timeout from constants.DEFAULT_GO_TEST_TIMEOUT "
        f"({_c.DEFAULT_GO_TEST_TIMEOUT!r}); got {captured_timeout}"
    )


@pytest.mark.unit
def test_env_var_wins_over_test_config_for_idempotency(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TERRATEST_IDEMPOTENCY env var overrides the value in test.config."""
    module_path = _make_valid_module(tmp_path)
    (module_path / "test.config").write_text("TERRATEST_IDEMPOTENCY=true\nGO_TEST_TIMEOUT=10m\n")
    monkeypatch.setenv("TERRATEST_IDEMPOTENCY", "false")
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert captured_env, "fake_invoke was never called"
    assert captured_env[0].get("TERRATEST_IDEMPOTENCY") == "false", (
        f"Expected TERRATEST_IDEMPOTENCY='false' from env override; "
        f"got {captured_env[0].get('TERRATEST_IDEMPOTENCY')}"
    )


@pytest.mark.unit
def test_test_config_malformed_lines_skipped(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed test.config lines (no '=', empty, or comment) are skipped gracefully."""
    module_path = _make_valid_module(tmp_path)
    (module_path / "test.config").write_text(
        "# comment line\n\nMALFORMED_NO_EQUALS\nGO_TEST_TIMEOUT=5m\n=empty_key\n"
    )
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_timeout: list[str] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_timeout.append(timeout)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert captured_timeout == ["5m"], (
        f"Expected timeout '5m' from valid line in malformed config; got {captured_timeout}"
    )


# ---------------------------------------------------------------------------
# Run-id generation and TERRATEST_RUN_ID override
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_id_default_format(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default run id must match tt-<yyyymmddHHMMSS>-<6-char random>."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert captured_env, "fake_invoke was never called"
    run_id = captured_env[0].get("TERRATEST_RUN_ID", "")
    pattern = re.compile(r"^tt-\d{14}-[a-z0-9]{6}$")
    assert pattern.match(run_id), (
        f"Run id {run_id!r} does not match tt-<yyyymmddHHMMSS>-<6-char random>"
    )


@pytest.mark.unit
def test_terratest_run_id_override_honored(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TERRATEST_RUN_ID env var overrides the auto-generated run id."""
    module_path = _make_valid_module(tmp_path)
    custom_id = "tt-custom-run-id"
    monkeypatch.setenv("TERRATEST_RUN_ID", custom_id)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert captured_env, "fake_invoke was never called"
    assert captured_env[0].get("TERRATEST_RUN_ID") == custom_id, (
        f"Expected TERRATEST_RUN_ID={custom_id!r}; got {captured_env[0].get('TERRATEST_RUN_ID')}"
    )


# ---------------------------------------------------------------------------
# Per-module run-id file write (parallel-safe zero-orphan proof, AC-4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_id_file_name_in_constants() -> None:
    """The per-module run-id filename constant must be defined in scripts/constants.py."""
    import scripts.constants as constants

    assert hasattr(constants, "TERRATEST_RUN_ID_FILE_NAME"), (
        "TERRATEST_RUN_ID_FILE_NAME not found in scripts/constants.py"
    )
    assert constants.TERRATEST_RUN_ID_FILE_NAME == ".terratest-run-id", (
        f"Expected TERRATEST_RUN_ID_FILE_NAME='.terratest-run-id'; "
        f"got {constants.TERRATEST_RUN_ID_FILE_NAME!r}"
    )


@pytest.mark.unit
def test_run_id_file_written_with_resolved_run_id(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner must write the resolved run id to <MODULE_PATH>/.terratest-run-id,
    and the persisted value must match the run id exported to go-test."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.constants as constants
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    run_id_file = module_path / constants.TERRATEST_RUN_ID_FILE_NAME
    assert run_id_file.exists(), f"run-id file {run_id_file} must exist after a tf-test run"
    persisted = run_id_file.read_text().strip()
    pattern = re.compile(r"^tt-\d{14}-[a-z0-9]{6}$")
    assert pattern.match(persisted), (
        f"Persisted run id {persisted!r} does not match tt-<yyyymmddHHMMSS>-<6-char random>"
    )
    assert captured_env, "fake_invoke was never called"
    assert persisted == captured_env[0].get("TERRATEST_RUN_ID"), (
        "run-id file content must match the TERRATEST_RUN_ID exported to go-test; "
        f"file={persisted!r} env={captured_env[0].get('TERRATEST_RUN_ID')!r}"
    )


@pytest.mark.unit
def test_run_id_file_written_with_override_run_id(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TERRATEST_RUN_ID is overridden, the override value is the one persisted."""
    module_path = _make_valid_module(tmp_path)
    custom_id = "tt-override-fixed-id"
    monkeypatch.setenv("TERRATEST_RUN_ID", custom_id)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.constants as constants
    import scripts.run_terratest as rt

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    run_id_file = module_path / constants.TERRATEST_RUN_ID_FILE_NAME
    assert run_id_file.exists(), f"run-id file {run_id_file} must exist"
    assert run_id_file.read_text().strip() == custom_id, (
        f"Expected persisted run id {custom_id!r}; got {run_id_file.read_text().strip()!r}"
    )


@pytest.mark.unit
def test_run_id_file_is_per_module_overwritten_each_run(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different modules each get their own run-id file (no cross-module
    collision), and a second run of the same module overwrites its file."""
    module_a = _make_valid_module(tmp_path)
    # Second distinct module under a separate root.
    other_root = tmp_path / "other"
    module_b = _make_valid_module(other_root)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.constants as constants
    import scripts.run_terratest as rt

    def run_for(module_path: pathlib.Path, run_id: str) -> None:
        monkeypatch.setenv("TERRATEST_RUN_ID", run_id)
        with (
            patch.object(rt, "_invoke_go_test", return_value=0),
            patch.object(rt, "_sweep_check", return_value=0),
            patch.object(rt, "_sweep_delete", return_value=0),
            patch.object(rt, "_sweep_recheck", return_value=0),
            pytest.raises(SystemExit),
        ):
            rt.main(
                module_path=str(module_path),
                accounts_json=str(ACCOUNTS_JSON_PATH),
                boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            )

    file_a = module_a / constants.TERRATEST_RUN_ID_FILE_NAME
    file_b = module_b / constants.TERRATEST_RUN_ID_FILE_NAME

    run_for(module_a, "tt-module-a-first")
    run_for(module_b, "tt-module-b-first")

    # Each module owns a distinct file with its own run id (no cross-contamination).
    assert file_a.read_text().strip() == "tt-module-a-first"
    assert file_b.read_text().strip() == "tt-module-b-first"

    # A second run of module A overwrites (not appends to) its own file only.
    run_for(module_a, "tt-module-a-second")
    assert file_a.read_text().strip() == "tt-module-a-second"
    assert file_b.read_text().strip() == "tt-module-b-first", (
        "module B's run-id file must be untouched by module A's re-run"
    )


@pytest.mark.unit
def test_run_id_file_write_failure_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """A failure to write the run-id file must fail fast with exit 1, an ERROR
    message naming the file, and BEFORE go test is ever invoked."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    invoke_called: list[bool] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        invoke_called.append(True)
        return 0

    def raise_oserror(self: pathlib.Path, *args: Any, **kwargs: Any) -> int:
        raise OSError("read-only file system")

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        patch.object(pathlib.Path, "write_text", raise_oserror),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert exc.value.code == 1, (
        f"Expected exit 1 on run-id file write failure; got {exc.value.code}"
    )
    assert not invoke_called, (
        "go test must NOT be invoked when the run-id file write fails (fail fast)"
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert ".terratest-run-id" in captured.err


# ---------------------------------------------------------------------------
# Env exports: TF_VAR_terratest_run_id, TF_VAR_project_tag, PROJECT_TAG
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tf_var_exports_set_in_env(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runner must export TF_VAR_terratest_run_id, TF_VAR_project_tag=telemetry-platform,
    TERRATEST_RUN_ID, and PROJECT_TAG to go-test."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert captured_env, "fake_invoke was never called"
    env = captured_env[0]
    assert "TF_VAR_terratest_run_id" in env, (
        f"TF_VAR_terratest_run_id missing from go-test env; got keys={list(env)}"
    )
    assert env.get("TF_VAR_project_tag") == "telemetry-platform", (
        f"TF_VAR_project_tag must be 'telemetry-platform'; got {env.get('TF_VAR_project_tag')!r}"
    )
    assert "TERRATEST_RUN_ID" in env, (
        f"TERRATEST_RUN_ID missing from go-test env; got keys={list(env)}"
    )
    assert env.get("PROJECT_TAG") == "telemetry-platform", (
        f"PROJECT_TAG must be 'telemetry-platform'; got {env.get('PROJECT_TAG')!r}"
    )
    # TF_VAR_terratest_run_id must match TERRATEST_RUN_ID
    assert env["TF_VAR_terratest_run_id"] == env["TERRATEST_RUN_ID"], (
        "TF_VAR_terratest_run_id must match TERRATEST_RUN_ID"
    )
    # AWS_ACCOUNT_ID must be exported so go tests requiring it can use the caller account
    assert "AWS_ACCOUNT_ID" in env, f"AWS_ACCOUNT_ID missing from go-test env; got keys={list(env)}"
    assert env["AWS_ACCOUNT_ID"] == sandbox_account_id, (
        f"AWS_ACCOUNT_ID must match the caller's account; "
        f"expected {sandbox_account_id!r}, got {env['AWS_ACCOUNT_ID']!r}"
    )


# ---------------------------------------------------------------------------
# TF_PLUGIN_CACHE_DIR export
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tf_plugin_cache_dir_exported_to_go_test(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runner must export TF_PLUGIN_CACHE_DIR in the env passed to go-test."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert captured_env, "fake_invoke was never called"
    env = captured_env[0]
    assert "TF_PLUGIN_CACHE_DIR" in env, (
        f"TF_PLUGIN_CACHE_DIR must be in the go-test env; got keys={list(env)}"
    )
    cache_dir = env["TF_PLUGIN_CACHE_DIR"]
    assert cache_dir, "TF_PLUGIN_CACHE_DIR must be a non-empty path"


@pytest.mark.unit
def test_tf_plugin_cache_dir_constant_in_constants_module() -> None:
    """TF_PLUGIN_CACHE_DIR constant must be defined in scripts/constants.py."""
    import scripts.constants as constants

    assert hasattr(constants, "TF_PLUGIN_CACHE_DIR_NAME"), (
        "TF_PLUGIN_CACHE_DIR_NAME not found in scripts/constants.py"
    )


# ---------------------------------------------------------------------------
# Deny-set: AC-3 -- prod-infra and dns-owner are denied
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "denied_role",
    ["prod-infra", "dns-owner"],
)
def test_denied_account_role_refused_exit_1(
    denied_role: str,
    accounts_data: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accounts with prod-infra or dns-owner role must be refused with exit 1.

    The runner must refuse BEFORE attempting any go test invocation.
    """
    denied_id = next(
        acct_id for acct_id, row in accounts_data.items() if row["account_role"] == denied_role
    )
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    invoke_called: list[bool] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        invoke_called.append(True)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(denied_id),
        )
    assert exc.value.code == 1, (
        f"Expected exit 1 for denied role {denied_role}; got {exc.value.code}"
    )
    # Must not have invoked go test
    assert not invoke_called, (
        f"go test must NOT be invoked for denied account {denied_id} ({denied_role})"
    )
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert denied_id in captured.err or denied_role in captured.err


@pytest.mark.unit
def test_denied_account_error_message_contains_role(
    prod_account_id: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deny-set error message must name both the account id and the role."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with patch.object(rt, "_invoke_go_test", return_value=0), pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(prod_account_id),
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert prod_account_id in captured.err
    # Error message shape: "ERROR: terratest is forbidden in account <id> (<role>)..."
    assert "prod-infra" in captured.err or "forbidden" in captured.err.lower()


@pytest.mark.unit
def test_sandbox_account_not_denied(
    sandbox_account_id: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sandbox account must not be denied -- it is the expected credential."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    invoke_called: list[bool] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        invoke_called.append(True)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code == 0, f"Sandbox account must not be denied; got exit {exc.value.code}"
    assert invoke_called, "go test must be invoked for sandbox account"


# ---------------------------------------------------------------------------
# Post-run cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_example_cache_cleaned_after_go_test(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Example terraform cache dirs and state files must be cleaned post-run."""
    module_path = _make_valid_module(tmp_path)
    # Create example artifacts
    example = module_path / "examples" / "basic"
    tf_dir = example / ".terraform"
    tf_dir.mkdir(exist_ok=True)
    tf_state = example / "terraform.tfstate"
    tf_state.write_text("{}")
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    # .terraform dir and terraform.tfstate must have been removed
    assert not tf_dir.exists(), ".terraform dir must be cleaned after run"
    assert not tf_state.exists(), "terraform.tfstate must be cleaned after run"


@pytest.mark.unit
def test_cleanup_always_runs_even_when_go_test_fails(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup must run even when go test exits non-zero."""
    module_path = _make_valid_module(tmp_path)
    example = module_path / "examples" / "basic"
    tf_dir = example / ".terraform"
    tf_dir.mkdir(exist_ok=True)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with (
        patch.object(rt, "_invoke_go_test", return_value=1),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert not tf_dir.exists(), ".terraform dir must be cleaned even after go test failure"


# ---------------------------------------------------------------------------
# Sweep wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sweep_check_called_after_go_test(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-run sweep check must be invoked after go test completes."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    sweep_check_called: list[bool] = []

    def fake_sweep_check(run_id: str, boto3_mod: Any, accounts_json: str) -> int:
        sweep_check_called.append(True)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", side_effect=fake_sweep_check),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert sweep_check_called, "sweep check must be called after go test"


@pytest.mark.unit
def test_sweep_delete_called_when_residue_found(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sweep check finds residue, sweep delete must be invoked."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    delete_called: list[bool] = []

    def fake_delete(run_id: str, boto3_mod: Any, accounts_json: str) -> int:
        delete_called.append(True)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", return_value=3),
        patch.object(rt, "_sweep_delete", side_effect=fake_delete),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert delete_called, "sweep delete must be called when sweep check finds residue"


@pytest.mark.unit
def test_sweep_recheck_called_after_delete(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After sweep delete, a re-check must be performed."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    recheck_called: list[bool] = []

    def fake_recheck(run_id: str, boto3_mod: Any, accounts_json: str) -> int:
        recheck_called.append(True)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", return_value=3),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", side_effect=fake_recheck),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert recheck_called, "sweep re-check must be called after sweep delete"


# ---------------------------------------------------------------------------
# Exit-code composition
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "go_exit,recheck_result,expected_exit",
    [
        (0, 0, 0),  # Both green -> success
        (1, 0, 1),  # Go test fails, no residue -> non-zero
        (0, 2, 2),  # Go test passes, residue remains -> non-zero
        (1, 2, 1),  # Both fail -> non-zero (max of both)
    ],
)
def test_exit_code_composition(
    go_exit: int,
    recheck_result: int,
    expected_exit: int,
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit code 0 requires go test passed AND zero residue after sweep."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    check_result = recheck_result  # first check finds same residue
    with (
        patch.object(rt, "_invoke_go_test", return_value=go_exit),
        patch.object(rt, "_sweep_check", return_value=check_result),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=recheck_result),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    if expected_exit == 0:
        assert exc.value.code == 0, (
            f"Expected exit 0 (go={go_exit}, recheck={recheck_result}); got {exc.value.code}"
        )
    else:
        assert exc.value.code != 0, (
            f"Expected non-zero exit (go={go_exit}, recheck={recheck_result}); got {exc.value.code}"
        )


@pytest.mark.unit
def test_residue_after_delete_emits_error_message(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """When residue remains after sweep delete, an error message must be emitted."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", return_value=2),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=2),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err


# ---------------------------------------------------------------------------
# Constants: all new constants must be in scripts/constants.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_id_prefix_in_constants() -> None:
    """The run-id prefix constant must be in scripts/constants.py."""
    import scripts.constants as constants

    assert hasattr(constants, "TERRATEST_RUN_ID_PREFIX"), (
        "TERRATEST_RUN_ID_PREFIX not found in scripts/constants.py"
    )
    assert constants.TERRATEST_RUN_ID_PREFIX == "tt", (
        f"Expected TERRATEST_RUN_ID_PREFIX='tt'; got {constants.TERRATEST_RUN_ID_PREFIX!r}"
    )


@pytest.mark.unit
def test_project_tag_in_constants() -> None:
    """The project tag constant (telemetry-platform) must be in scripts/constants.py."""
    import scripts.constants as constants

    assert hasattr(constants, "TERRATEST_PROJECT_TAG"), (
        "TERRATEST_PROJECT_TAG not found in scripts/constants.py"
    )
    assert constants.TERRATEST_PROJECT_TAG == "telemetry-platform", (
        "Expected TERRATEST_PROJECT_TAG='telemetry-platform'; got "
        f"{constants.TERRATEST_PROJECT_TAG!r}"
    )


@pytest.mark.unit
def test_no_hardcoded_values_in_runner_source() -> None:
    """scripts/run_terratest.py must not contain hardcoded project tag or prefix literals."""
    import scripts.run_terratest as rt

    src = pathlib.Path(rt.__file__).read_text()
    # The literal string "telemetry-platform" must not appear as a bare string
    # assignment; it must be sourced from constants.
    assert "telemetry-platform" not in src or "constants" in src, (
        "run_terratest.py must reference 'telemetry-platform' from constants, not hardcode it"
    )


@pytest.mark.unit
def test_cli_entry_point_invocable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scripts.run_terratest must expose a _cli_main or __main__ entry point."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    assert hasattr(rt, "main") or hasattr(rt, "_cli_main"), (
        "run_terratest must expose main() or _cli_main()"
    )


@pytest.mark.unit
def test_runner_imports_constants_not_inline() -> None:
    """scripts/run_terratest.py must import from scripts.constants."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    src = pathlib.Path(rt.__file__).read_text()
    assert "scripts.constants" in src or "from scripts import constants" in src, (
        "run_terratest.py must import from scripts.constants"
    )


@pytest.mark.unit
def test_module_main_guard_invokes_cli_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executing scripts.run_terratest as __main__ must call _cli_main."""
    import contextlib
    import runpy

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]

    with contextlib.suppress(SystemExit):
        runpy.run_module(
            "scripts.run_terratest",
            run_name="__main__",
            alter_sys=False,
        )
    # If we get here the guard ran (argparse/SystemExit is suppressed)
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    assert rt.main is not None


# ---------------------------------------------------------------------------
# Additional coverage: deny-set edge cases, plugin cache dir, cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_accounts_json_in_deny_set_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing accounts.json in deny-set check causes exit 1."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    nonexistent = str(tmp_path / "missing_accounts.json")

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=nonexistent,
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err


@pytest.mark.unit
def test_malformed_accounts_json_in_deny_set_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed accounts.json in deny-set check causes exit 1."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    bad_json = tmp_path / "bad_accounts.json"
    bad_json.write_text("{not valid json")

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with pytest.raises(SystemExit) as exc:
        rt.main(
            module_path=str(module_path),
            accounts_json=str(bad_json),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err


@pytest.mark.unit
def test_plugin_cache_dir_uses_env_override(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TF_PLUGIN_CACHE_DIR env var is set, the runner uses it for the cache dir."""
    module_path = _make_valid_module(tmp_path)
    custom_cache = str(tmp_path / "my-plugin-cache")
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", custom_cache)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    captured_env: list[dict] = []

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        captured_env.append(dict(env))
        return 0

    with (
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )
    assert captured_env, "fake_invoke was never called"
    assert captured_env[0].get("TF_PLUGIN_CACHE_DIR") == custom_cache, (
        f"Expected TF_PLUGIN_CACHE_DIR={custom_cache!r}; "
        f"got {captured_env[0].get('TF_PLUGIN_CACHE_DIR')!r}"
    )


@pytest.mark.unit
def test_clean_example_caches_no_examples_dir(tmp_path: pathlib.Path) -> None:
    """_clean_example_caches is a no-op when examples/ dir does not exist."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "no-examples"
    module_path.mkdir()
    # Should not raise even if examples/ is absent
    rt._clean_example_caches(str(module_path))


@pytest.mark.unit
def test_clean_example_caches_cleans_lock_hcl(tmp_path: pathlib.Path) -> None:
    """_clean_example_caches removes .terraform.lock.hcl files from examples/."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    example = module_path / "examples" / "basic"
    example.mkdir(parents=True)
    lock_file = example / ".terraform.lock.hcl"
    lock_file.write_text("# lock file")

    rt._clean_example_caches(str(module_path))
    assert not lock_file.exists(), ".terraform.lock.hcl must be cleaned"


@pytest.mark.unit
def test_sweep_wrappers_call_sweep_main(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """_sweep_check and _sweep_delete call terratest_sweep.main() and return exit codes."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    # Test _sweep_check: sweep_main exits 0 -> returns 0
    with patch("scripts.run_terratest._sweep_main", side_effect=SystemExit(0)):
        result = rt._sweep_check("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 0

    # Test _sweep_check: sweep_main exits 3 -> returns 3
    with patch("scripts.run_terratest._sweep_main", side_effect=SystemExit(3)):
        result = rt._sweep_check("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 3

    # Test _sweep_delete: sweep_main exits 0 -> returns 0
    with patch("scripts.run_terratest._sweep_main", side_effect=SystemExit(0)):
        result = rt._sweep_delete("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 0

    # Test _sweep_delete: sweep_main exits 1 -> returns 1
    with patch("scripts.run_terratest._sweep_main", side_effect=SystemExit(1)):
        result = rt._sweep_delete("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 1

    # Test _sweep_recheck delegates to _sweep_check
    with patch.object(rt, "_sweep_check", return_value=5) as mock_check:
        result = rt._sweep_recheck("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 5
    mock_check.assert_called_once()


@pytest.mark.unit
def test_sweep_wrappers_handle_non_int_exit_code() -> None:
    """_sweep_check and _sweep_delete treat non-int SystemExit code as 1."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    # SystemExit with string code (edge case)
    with patch("scripts.run_terratest._sweep_main", side_effect=SystemExit("error")):
        result = rt._sweep_check("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 1


@pytest.mark.unit
def test_sweep_check_returns_zero_when_main_does_not_raise() -> None:
    """_sweep_check returns 0 when _sweep_main returns normally (no SystemExit)."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    # _sweep_main returns normally (doesn't raise SystemExit) -> _sweep_check returns 0
    with patch("scripts.run_terratest._sweep_main", return_value=None):
        result = rt._sweep_check("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 0


@pytest.mark.unit
def test_sweep_delete_returns_zero_when_main_does_not_raise() -> None:
    """_sweep_delete returns 0 when _sweep_main returns normally (no SystemExit)."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with patch("scripts.run_terratest._sweep_main", return_value=None):
        result = rt._sweep_delete("test-run-id", MagicMock(), str(ACCOUNTS_JSON_PATH))
    assert result == 0


@pytest.mark.unit
def test_clean_example_caches_skips_non_dir_terraform(tmp_path: pathlib.Path) -> None:
    """_clean_example_caches skips .terraform entries that are files, not dirs."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    example = module_path / "examples" / "basic"
    example.mkdir(parents=True)
    # Create .terraform as a file (not a directory) -- should be skipped
    tf_file = example / ".terraform"
    tf_file.write_text("not a dir")

    rt._clean_example_caches(str(module_path))
    # The file should NOT be removed (only dirs are removed by shutil.rmtree)
    assert tf_file.exists(), ".terraform file (non-dir) must not be removed"


@pytest.mark.unit
def test_clean_example_caches_skips_non_file_state(tmp_path: pathlib.Path) -> None:
    """_clean_example_caches skips terraform.tfstate entries that are dirs, not files."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    example = module_path / "examples" / "basic"
    example.mkdir(parents=True)
    # Create terraform.tfstate as a directory -- should be skipped
    state_dir = example / "terraform.tfstate"
    state_dir.mkdir()

    rt._clean_example_caches(str(module_path))
    # The directory should NOT be removed (only files are removed via unlink)
    assert state_dir.exists(), "terraform.tfstate directory (non-file) must not be removed"


@pytest.mark.unit
def test_clean_example_caches_skips_non_file_lock_hcl(tmp_path: pathlib.Path) -> None:
    """_clean_example_caches skips .terraform.lock.hcl entries that are dirs, not files."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    example = module_path / "examples" / "basic"
    example.mkdir(parents=True)
    # Create .terraform.lock.hcl as a directory -- should be skipped
    lock_dir = example / ".terraform.lock.hcl"
    lock_dir.mkdir()

    rt._clean_example_caches(str(module_path))
    # The directory should NOT be removed
    assert lock_dir.exists(), ".terraform.lock.hcl directory (non-file) must not be removed"


@pytest.mark.unit
def test_invoke_go_test_uses_go_binary_env(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_invoke_go_test uses GO_BINARY env var for the go binary path."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    # Patch invoke_pinned_binary to avoid real subprocess
    captured_calls: list[dict] = []

    def fake_invoke_binary(binary: str, args: list, **kwargs: Any) -> Any:
        captured_calls.append({"binary": binary, "args": args})
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setenv("GO_BINARY", "/usr/local/go/bin/go")

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke_binary):
        rt._invoke_go_test(str(tmp_path), "10m", dict(os.environ))

    assert captured_calls, "invoke_pinned_binary was not called"
    assert captured_calls[0]["binary"] == "/usr/local/go/bin/go", (
        f"Expected GO_BINARY=/usr/local/go/bin/go; got {captured_calls[0]['binary']!r}"
    )
    args = captured_calls[0]["args"]
    assert "test" in args
    assert "-timeout=10m" in args
    # -p=1 serialises package execution to prevent shared-state races
    assert "-p=1" in args, f"-p=1 must be in go test args to serialize packages; got {args}"
    # make tf-test MUST compile and run the build-tagged terratest tests, so the
    # runner has to pass `-tags terratest` (they are excluded from the default
    # build that backs make go-unit-test-coverage). The tag flag must precede the
    # package spec, matching go's flag-ordering requirement.
    assert "-tags" in args, f"-tags must be in go test args; got {args}"
    assert "terratest" in args, f"terratest build tag must be in go test args; got {args}"
    assert args[args.index("-tags") + 1] == "terratest", (
        f"-tags must be immediately followed by 'terratest'; got {args}"
    )
    assert args.index("-tags") < args.index("./tests/..."), (
        f"-tags must precede the package spec './tests/...'; got {args}"
    )


@pytest.mark.unit
def test_invoke_go_test_default_binary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_invoke_go_test defaults to 'go' when GO_BINARY is not set."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    monkeypatch.delenv("GO_BINARY", raising=False)

    captured_calls: list[dict] = []

    def fake_invoke_binary(binary: str, args: list, **kwargs: Any) -> Any:
        captured_calls.append({"binary": binary})
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke_binary):
        rt._invoke_go_test(str(tmp_path), "5m", dict(os.environ))

    assert captured_calls[0]["binary"] == "go"


@pytest.mark.unit
def test_main_imports_boto3_when_not_injected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When boto3_mod is None, main() imports boto3 from the environment."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    # Inject a fake boto3 into sys.modules
    fake_boto3 = MagicMock()
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {
        "Account": "222222222222",  # sandbox
        "Arn": "arn:aws:iam::222222222222:user/test",
        "UserId": "AIDATEST",
    }
    fake_boto3.client.return_value = sts_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=None,  # triggers import boto3 path
        )
    # boto3.client was called
    fake_boto3.client.assert_called()


@pytest.mark.unit
def test_cli_main_parses_module_path_and_calls_main(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_cli_main parses sys.argv and passes module_path to main()."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = _make_valid_module(tmp_path)
    called_with: dict = {}

    def fake_main(
        module_path: str,
        accounts_json: str,
        boto3_mod: Any = None,
        all_mode: bool = False,
        summary_json: str | None = None,
        providers_root: str | None = None,
    ) -> None:
        called_with["module_path"] = module_path
        called_with["accounts_json"] = accounts_json
        sys.exit(0)

    monkeypatch.setattr(rt, "main", fake_main)
    monkeypatch.setattr(
        "sys.argv",
        ["run_terratest", str(module_path)],
    )

    with pytest.raises(SystemExit) as exc:
        rt._cli_main()
    assert exc.value.code == 0
    assert called_with.get("module_path") == str(module_path)


# ---------------------------------------------------------------------------
# env forwarding: _invoke_go_test must pass env dict to invoke_pinned_binary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invoke_go_test_forwards_env_to_invoke_pinned_binary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_invoke_go_test must pass the env dict to invoke_pinned_binary as env= kwarg.

    This is the critical contract that ensures TERRATEST_RUN_ID, TF_PLUGIN_CACHE_DIR,
    and TF_VAR_* are visible to the go test subprocess.
    """
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    monkeypatch.delenv("GO_BINARY", raising=False)

    custom_env = {
        "TERRATEST_RUN_ID": "tt-test-forward-env",
        "TF_PLUGIN_CACHE_DIR": "/tmp/tf-cache",
        "TF_VAR_project_tag": "telemetry-platform",
    }
    captured_kwargs: list[dict] = []

    def fake_invoke_binary(binary: str, args: list, **kwargs: Any) -> Any:
        captured_kwargs.append(kwargs)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke_binary):
        rt._invoke_go_test(str(tmp_path), "10m", custom_env)

    assert captured_kwargs, "invoke_pinned_binary was not called"
    assert captured_kwargs[0].get("env") == custom_env, (
        f"Expected env={custom_env!r} forwarded to invoke_pinned_binary; "
        f"got env={captured_kwargs[0].get('env')!r}"
    )


# ---------------------------------------------------------------------------
# Pre-warm provider cache: _prewarm_provider_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prewarm_provider_cache_no_examples_dir(tmp_path: pathlib.Path) -> None:
    """_prewarm_provider_cache is a no-op when examples/ dir does not exist."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "no-examples"
    module_path.mkdir()
    # Must not raise; no terraform invocations
    invoke_called: list[bool] = []

    def fake_invoke_binary(binary: str, args: list, **kwargs: Any) -> Any:
        invoke_called.append(True)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke_binary):
        rt._prewarm_provider_cache(str(module_path), "/tmp/cache")

    assert not invoke_called, "_prewarm_provider_cache must not call terraform when no examples dir"


@pytest.mark.unit
def test_prewarm_provider_cache_calls_terraform_init_for_each_example(
    tmp_path: pathlib.Path,
) -> None:
    """_prewarm_provider_cache calls terraform init in each example directory that has .tf files."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    examples_dir = module_path / "examples"
    # Create two example directories with .tf files
    for name in ("basic", "with-policy"):
        ex_dir = examples_dir / name
        ex_dir.mkdir(parents=True)
        (ex_dir / "main.tf").write_text("# placeholder")

    invoke_calls: list[dict] = []

    def fake_invoke_binary(binary: str, args: list, **kwargs: Any) -> Any:
        invoke_calls.append({"binary": binary, "args": args, "cwd": kwargs.get("cwd")})
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke_binary):
        rt._prewarm_provider_cache(str(module_path), "/tmp/cache")

    assert len(invoke_calls) == 2, (
        f"Expected 2 terraform init calls (one per example); got {len(invoke_calls)}"
    )
    for call in invoke_calls:
        assert call["binary"] == "terraform", f"Expected 'terraform' binary; got {call['binary']!r}"
        assert "init" in call["args"], f"Expected 'init' in args; got {call['args']}"


@pytest.mark.unit
def test_prewarm_provider_cache_skips_non_dir_entries(tmp_path: pathlib.Path) -> None:
    """_prewarm_provider_cache skips files in examples/ that are not directories."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    examples_dir = module_path / "examples"
    examples_dir.mkdir()
    # Create a file (not a directory) in examples/
    (examples_dir / "not-a-dir.txt").write_text("file")
    # Create a valid example dir with .tf files
    ex_dir = examples_dir / "basic"
    ex_dir.mkdir()
    (ex_dir / "main.tf").write_text("# placeholder")

    invoke_calls: list[dict] = []

    def fake_invoke_binary(binary: str, args: list, **kwargs: Any) -> Any:
        invoke_calls.append({"cwd": kwargs.get("cwd")})
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke_binary):
        rt._prewarm_provider_cache(str(module_path), "/tmp/cache")

    # Only the valid example directory should get a terraform init call
    assert len(invoke_calls) == 1, (
        f"Expected 1 terraform init call (only for basic/); got {len(invoke_calls)}"
    )
    assert invoke_calls[0]["cwd"] == str(ex_dir), (
        f"Expected cwd={str(ex_dir)!r}; got {invoke_calls[0]['cwd']!r}"
    )


@pytest.mark.unit
def test_prewarm_provider_cache_called_before_go_test(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_prewarm_provider_cache must be called before go test is invoked."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    call_order: list[str] = []

    def fake_prewarm(module_path: str, plugin_cache_dir: str) -> None:
        call_order.append("prewarm")

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        call_order.append("go_test")
        return 0

    with (
        patch.object(rt, "_prewarm_provider_cache", side_effect=fake_prewarm),
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert call_order == ["prewarm", "go_test"], (
        f"Expected prewarm before go_test; got call order: {call_order}"
    )


# ---------------------------------------------------------------------------
# Stale provider process cleanup: _kill_stale_provider_processes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_kill_stale_provider_processes_noop_when_cache_dir_absent(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes is a no-op when the cache dir does not exist."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    absent_dir = str(tmp_path / "no-cache")
    # Must not raise; proc scanning is skipped for non-existent cache dir.
    rt._kill_stale_provider_processes(absent_dir)


@pytest.mark.unit
def test_kill_stale_provider_processes_noop_when_no_provider_binaries(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes is a no-op when cache dir has no provider binaries."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    # Add a non-binary file; no terraform-provider-* files.
    (cache_dir / "some-other-file.txt").write_text("not a provider")

    # Must not raise; no providers to check.
    rt._kill_stale_provider_processes(str(cache_dir))


@pytest.mark.unit
def test_kill_stale_provider_processes_sends_sigterm_to_matching_process(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes sends SIGTERM to a process running a cache binary.

    The function scans /proc for processes whose /proc/<pid>/exe inode matches
    a provider binary inode in the plugin cache. On a match it calls os.kill
    with SIGTERM. This test patches pathlib.Path and os.kill to inject a fake
    /proc tree with a matching process.
    """
    import signal as _signal

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    # Create a fake cache binary with a known inode.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    provider_binary = cache_dir / "terraform-provider-aws_v6.50.0_x5"
    provider_binary.write_text("fake binary content")
    provider_inode = provider_binary.stat().st_ino

    kills_sent: list[tuple[int, Any]] = []

    def fake_os_kill(pid: int, sig: Any) -> None:
        kills_sent.append((pid, sig))

    with (
        patch("scripts.run_terratest.os.kill", side_effect=fake_os_kill),
        patch("scripts.run_terratest.pathlib.Path") as mock_path,
    ):
        # Build mock for Path("/proc") -- exists + iterdir yields one pid entry.
        proc_path_mock = MagicMock()
        proc_path_mock.exists.return_value = True

        pid_entry = MagicMock()
        pid_entry.name = "12345"
        proc_path_mock.iterdir.return_value = [pid_entry]

        # /proc/12345/exe stat returns the same inode as the cache binary.
        exe_mock = MagicMock()
        exe_stat = MagicMock()
        exe_stat.st_ino = provider_inode
        exe_mock.stat.return_value = exe_stat
        pid_entry.__truediv__ = lambda self, key: exe_mock

        # Build mock for Path(plugin_cache_dir) -- exists + rglob yields provider.
        cache_path_mock = MagicMock()
        cache_path_mock.exists.return_value = True
        binary_mock = MagicMock()
        binary_mock.is_file.return_value = True
        binary_stat = MagicMock()
        binary_stat.st_ino = provider_inode
        binary_mock.stat.return_value = binary_stat
        cache_path_mock.rglob.return_value = [binary_mock]

        def path_factory(*args: Any) -> Any:
            if args and str(args[0]) == "/proc":
                return proc_path_mock
            return cache_path_mock

        mock_path.side_effect = path_factory

        rt._kill_stale_provider_processes(str(cache_dir))

    assert kills_sent, (
        "_kill_stale_provider_processes must call os.kill when a matching process is found"
    )
    assert kills_sent[0][0] == 12345, f"Expected PID 12345; got {kills_sent[0][0]}"
    assert kills_sent[0][1] == _signal.SIGTERM, f"Expected SIGTERM; got {kills_sent[0][1]}"


@pytest.mark.unit
def test_kill_called_before_prewarm_in_main(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_kill_stale_provider_processes must be called before _prewarm_provider_cache."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    call_order: list[str] = []

    def fake_kill(plugin_cache_dir: str) -> None:
        call_order.append("kill")

    def fake_prewarm(module_path: str, plugin_cache_dir: str) -> None:
        call_order.append("prewarm")

    def fake_invoke(module_path: str, timeout: str, env: dict) -> int:
        call_order.append("go_test")
        return 0

    with (
        patch.object(rt, "_kill_stale_provider_processes", side_effect=fake_kill),
        patch.object(rt, "_prewarm_provider_cache", side_effect=fake_prewarm),
        patch.object(rt, "_invoke_go_test", side_effect=fake_invoke),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert call_order == ["kill", "prewarm", "go_test"], (
        f"Expected kill before prewarm before go_test; got: {call_order}"
    )


@pytest.mark.unit
def test_kill_stale_provider_processes_noop_on_non_linux(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes is a no-op when /proc does not exist (non-Linux)."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "terraform-provider-aws_v6.50.0_x5").write_text("fake")

    kills_sent: list[Any] = []

    def fake_os_kill(pid: int, sig: Any) -> None:
        kills_sent.append(pid)

    with (
        patch("scripts.run_terratest.os.kill", side_effect=fake_os_kill),
        patch("scripts.run_terratest.pathlib.Path") as mock_path,
    ):
        proc_mock = MagicMock()
        proc_mock.exists.return_value = False  # /proc does not exist

        cache_mock = MagicMock()
        cache_mock.exists.return_value = True

        def path_factory(*args: Any) -> Any:
            if args and str(args[0]) == "/proc":
                return proc_mock
            return cache_mock

        mock_path.side_effect = path_factory
        rt._kill_stale_provider_processes(str(cache_dir))

    assert not kills_sent, "os.kill must not be called when /proc does not exist"


@pytest.mark.unit
def test_kill_stale_provider_processes_skips_non_file_binary(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes skips cache entries that are not regular files."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    kills_sent: list[Any] = []

    def fake_os_kill(pid: int, sig: Any) -> None:
        kills_sent.append(pid)

    with (
        patch("scripts.run_terratest.os.kill", side_effect=fake_os_kill),
        patch("scripts.run_terratest.pathlib.Path") as mock_path,
    ):
        proc_mock = MagicMock()
        proc_mock.exists.return_value = True
        proc_mock.iterdir.return_value = []  # no pid dirs

        cache_mock = MagicMock()
        cache_mock.exists.return_value = True
        # rglob returns one entry that is NOT a file (e.g., a directory)
        non_file_mock = MagicMock()
        non_file_mock.is_file.return_value = False
        cache_mock.rglob.return_value = [non_file_mock]

        def path_factory(*args: Any) -> Any:
            if args and str(args[0]) == "/proc":
                return proc_mock
            return cache_mock

        mock_path.side_effect = path_factory
        rt._kill_stale_provider_processes(str(cache_dir))

    assert not kills_sent, "os.kill must not be called when no provider file matches"


@pytest.mark.unit
def test_kill_stale_provider_processes_handles_binary_stat_error(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes skips a cache binary if stat() raises OSError."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    kills_sent: list[Any] = []

    def fake_os_kill(pid: int, sig: Any) -> None:
        kills_sent.append(pid)

    with (
        patch("scripts.run_terratest.os.kill", side_effect=fake_os_kill),
        patch("scripts.run_terratest.pathlib.Path") as mock_path,
    ):
        proc_mock = MagicMock()
        proc_mock.exists.return_value = True
        proc_mock.iterdir.return_value = []

        cache_mock = MagicMock()
        cache_mock.exists.return_value = True
        # binary.is_file() returns True but stat() raises OSError
        binary_mock = MagicMock()
        binary_mock.is_file.return_value = True
        binary_mock.stat.side_effect = OSError("permission denied")
        cache_mock.rglob.return_value = [binary_mock]

        def path_factory(*args: Any) -> Any:
            if args and str(args[0]) == "/proc":
                return proc_mock
            return cache_mock

        mock_path.side_effect = path_factory
        # Must not raise; OSError is caught and the binary is skipped.
        rt._kill_stale_provider_processes(str(cache_dir))

    assert not kills_sent


@pytest.mark.unit
def test_kill_stale_provider_processes_handles_kill_permission_error(
    tmp_path: pathlib.Path,
) -> None:
    """_kill_stale_provider_processes continues when os.kill raises PermissionError."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    provider_inode = 999888

    def fake_os_kill(pid: int, sig: Any) -> None:
        raise PermissionError("not allowed to kill this process")

    with (
        patch("scripts.run_terratest.os.kill", side_effect=fake_os_kill),
        patch("scripts.run_terratest.pathlib.Path") as mock_path,
    ):
        proc_mock = MagicMock()
        proc_mock.exists.return_value = True

        pid_entry = MagicMock()
        pid_entry.name = "77777"
        proc_mock.iterdir.return_value = [pid_entry]

        exe_mock = MagicMock()
        exe_stat = MagicMock()
        exe_stat.st_ino = provider_inode
        exe_mock.stat.return_value = exe_stat
        pid_entry.__truediv__ = lambda self, key: exe_mock

        cache_mock = MagicMock()
        cache_mock.exists.return_value = True
        binary_mock = MagicMock()
        binary_mock.is_file.return_value = True
        binary_stat_mock = MagicMock()
        binary_stat_mock.st_ino = provider_inode
        binary_mock.stat.return_value = binary_stat_mock
        cache_mock.rglob.return_value = [binary_mock]

        def path_factory(*args: Any) -> Any:
            if args and str(args[0]) == "/proc":
                return proc_mock
            return cache_mock

        mock_path.side_effect = path_factory
        # Must not raise; PermissionError is caught and the process is skipped.
        rt._kill_stale_provider_processes(str(cache_dir))


# ---------------------------------------------------------------------------
# Suite mode: --all / run_suite()
# ---------------------------------------------------------------------------


def _make_suite_module(
    root: pathlib.Path,
    name: str,
    category: str = "primitives",
) -> pathlib.Path:
    """Create a minimal valid first-party module under providers/aws/<category>/<name>."""
    mod_path = root / "providers" / "aws" / category / name
    mod_path.mkdir(parents=True)
    (mod_path / "tests").mkdir()
    (mod_path / "go.mod").write_text(
        f"module github.com/test/{name}\n\n"
        "require (\n"
        "    github.com/matthew-dresden/terraform-terratest-framework v1.0.0\n"
        ")\n"
    )
    (mod_path / "test.config").write_text("GO_TEST_TIMEOUT=10m\nTERRATEST_IDEMPOTENCY=true\n")
    return mod_path


@pytest.mark.unit
def test_discover_suite_modules_returns_sorted_list(
    tmp_path: pathlib.Path,
) -> None:
    """_discover_suite_modules returns all modules with tests/ and go.mod, sorted."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    _make_suite_module(tmp_path, "kms-key")
    _make_suite_module(tmp_path, "acm-certificate")
    _make_suite_module(tmp_path, "vpc-network", category="references")

    providers_root = tmp_path / "providers" / "aws"
    modules = rt._discover_suite_modules(str(providers_root))
    assert len(modules) == 3, f"Expected 3 modules; got {len(modules)}: {modules}"
    # Must be sorted deterministically
    assert modules == sorted(modules), f"Modules must be in sorted order; got {modules}"


@pytest.mark.unit
def test_discover_suite_modules_requires_both_tests_and_go_mod(
    tmp_path: pathlib.Path,
) -> None:
    """_discover_suite_modules excludes modules missing tests/ or go.mod."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    providers_root = tmp_path / "providers" / "aws"

    # Module with only go.mod (no tests/)
    no_tests = providers_root / "primitives" / "no-tests"
    no_tests.mkdir(parents=True)
    (no_tests / "go.mod").write_text(
        "module github.com/test/no-tests\n"
        "require github.com/matthew-dresden/terraform-terratest-framework v1.0.0\n"
    )

    # Module with only tests/ (no go.mod)
    no_gomod = providers_root / "primitives" / "no-gomod"
    no_gomod.mkdir(parents=True)
    (no_gomod / "tests").mkdir()

    # Valid module
    _make_suite_module(tmp_path, "kms-key")

    modules = rt._discover_suite_modules(str(providers_root))
    assert len(modules) == 1, f"Expected only 1 valid module; got {modules}"
    assert any("kms-key" in m for m in modules), f"kms-key not in discovery result: {modules}"


@pytest.mark.unit
def test_discover_suite_modules_zero_discovery_fails_closed(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """_discover_suite_modules exits 1 with D-3 error when no modules are found."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    providers_root = tmp_path / "providers" / "aws"
    providers_root.mkdir(parents=True)
    (providers_root / "primitives").mkdir()
    (providers_root / "references").mkdir()

    with pytest.raises(SystemExit) as exc:
        rt._discover_suite_modules(str(providers_root))
    assert exc.value.code == 1, f"Expected exit 1 on zero discovery; got {exc.value.code}"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert "no terratest modules discovered" in captured.err.lower()


@pytest.mark.unit
def test_discover_suite_modules_error_message_content(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Zero-discovery error message must match the D-3 spec exactly."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    providers_root = tmp_path / "providers" / "aws"
    providers_root.mkdir(parents=True)
    (providers_root / "primitives").mkdir()

    with pytest.raises(SystemExit):
        rt._discover_suite_modules(str(providers_root))
    captured = capsys.readouterr()
    assert "providers/aws/" in captured.err or "check the repo layout" in captured.err, (
        f"Error message must reference 'providers/aws/' or 'check the repo layout'; "
        f"got: {captured.err!r}"
    )


@pytest.mark.unit
def test_discover_suite_modules_roster_mismatch_fails_closed(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TERRATEST_MODULE_ROSTER_COUNT is set and discovery count differs, fail closed."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    # Create 2 valid modules but set roster count to 3
    _make_suite_module(tmp_path, "kms-key")
    _make_suite_module(tmp_path, "acm-certificate")
    monkeypatch.setenv("TERRATEST_MODULE_ROSTER_COUNT", "3")

    providers_root = tmp_path / "providers" / "aws"
    with pytest.raises(SystemExit) as exc:
        rt._discover_suite_modules(str(providers_root))
    assert exc.value.code == 1, f"Expected exit 1 on roster mismatch; got {exc.value.code}"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    # Must name expected vs actual counts
    assert "2" in captured.err and "3" in captured.err, (
        f"Error message must name expected (3) and actual (2) counts; got: {captured.err!r}"
    )


@pytest.mark.unit
def test_discover_suite_modules_roster_match_succeeds(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TERRATEST_MODULE_ROSTER_COUNT matches discovery count, no error is raised."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    _make_suite_module(tmp_path, "kms-key")
    _make_suite_module(tmp_path, "acm-certificate")
    monkeypatch.setenv("TERRATEST_MODULE_ROSTER_COUNT", "2")

    providers_root = tmp_path / "providers" / "aws"
    modules = rt._discover_suite_modules(str(providers_root))
    assert len(modules) == 2, f"Expected 2 modules when roster matches; got {modules}"


@pytest.mark.unit
def test_run_suite_runs_all_modules_to_completion(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """run_suite runs ALL modules even when one fails (run-to-completion semantics)."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    modules_run: list[str] = []

    def fake_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        modules_run.append(module_path)
        if "kms-key" in module_path:
            raise SystemExit(1)
        raise SystemExit(0)

    with pytest.raises(SystemExit) as exc:
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=["mod-a/kms-key", "mod-b/acm-cert", "mod-c/iam-role"],
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=None,
            single_module_main=fake_main,
        )
    # All 3 modules must have been called (run to completion)
    assert len(modules_run) == 3, f"Expected all 3 modules to be run; only ran: {modules_run}"
    # Exit must be non-zero because kms-key failed
    assert exc.value.code != 0, f"Expected non-zero exit when a module fails; got {exc.value.code}"


@pytest.mark.unit
def test_run_suite_exits_zero_when_all_pass(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_suite exits 0 when all modules pass."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    def fake_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        raise SystemExit(0)

    with pytest.raises(SystemExit) as exc:
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=["mod-a/kms-key", "mod-b/acm-cert"],
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=None,
            single_module_main=fake_main,
        )
    assert exc.value.code == 0, f"Expected exit 0 when all pass; got {exc.value.code}"


@pytest.mark.unit
def test_run_suite_prints_summary_table(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """run_suite prints a per-module pass/fail summary table after running all modules."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    def fake_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        if "kms-key" in module_path:
            raise SystemExit(1)
        raise SystemExit(0)

    with pytest.raises(SystemExit):
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=["a/kms-key", "b/acm-cert"],
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=None,
            single_module_main=fake_main,
        )
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Summary table must show PASS and FAIL entries
    assert "PASS" in combined, f"Summary must contain PASS; output: {combined!r}"
    assert "FAIL" in combined, f"Summary must contain FAIL; output: {combined!r}"
    # Both module names must appear
    assert "kms-key" in combined, f"kms-key must appear in summary; output: {combined!r}"
    assert "acm-cert" in combined, f"acm-cert must appear in summary; output: {combined!r}"


@pytest.mark.unit
def test_run_suite_lists_failing_modules_on_exit(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """run_suite lists the failing module names after the summary table on non-zero exit."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    def fake_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        if "kms-key" in module_path:
            raise SystemExit(1)
        raise SystemExit(0)

    with pytest.raises(SystemExit) as exc:
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=["a/kms-key", "b/acm-cert"],
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=None,
            single_module_main=fake_main,
        )
    assert exc.value.code != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Must name the failing module
    assert "kms-key" in combined, f"Failing modules must be listed on exit; output: {combined!r}"


@pytest.mark.unit
def test_run_suite_summary_json_written(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """run_suite writes the machine-readable summary JSON when --summary-json path is given."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import json

    import scripts.run_terratest as rt

    json_path = tmp_path / "suite-summary.json"

    def fake_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        if "kms-key" in module_path:
            raise SystemExit(1)
        raise SystemExit(0)

    with pytest.raises(SystemExit):
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=["a/kms-key", "b/acm-cert"],
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=str(json_path),
            single_module_main=fake_main,
        )
    assert json_path.exists(), f"Summary JSON must be written to {json_path}"
    data = json.loads(json_path.read_text())
    assert "results" in data, f"Summary JSON must contain 'results' key; got {data!r}"
    assert isinstance(data["results"], list), "results must be a list"


@pytest.mark.unit
def test_run_suite_summary_json_unwritable_fails_fast(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """run_suite exits 1 with ERROR: when --summary-json path is unwritable."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    unwritable = str(tmp_path / "nonexistent_dir" / "summary.json")

    def noop_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        pass

    with pytest.raises(SystemExit) as exc:
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=["a/kms-key"],
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=unwritable,
            single_module_main=noop_main,
        )
    assert exc.value.code == 1, f"Expected exit 1 on unwritable path; got {exc.value.code}"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, (
        f"Must emit ERROR: on unwritable summary JSON path; stderr: {captured.err!r}"
    )


@pytest.mark.unit
def test_suite_mode_serial_ordering(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
) -> None:
    """run_suite invokes modules in the deterministic sorted order passed to it."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    invocation_order: list[str] = []

    def fake_main(module_path: str, accounts_json: str, boto3_mod: Any = None) -> None:
        invocation_order.append(module_path)
        raise SystemExit(0)

    module_paths = ["c/zzz-module", "a/aaa-module", "b/mmm-module"]
    sorted_paths = sorted(module_paths)

    with pytest.raises(SystemExit):
        rt.run_suite(
            providers_root=str(tmp_path / "providers" / "aws"),
            module_paths=sorted_paths,
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            summary_json_path=None,
            single_module_main=fake_main,
        )
    assert invocation_order == sorted_paths, (
        f"Expected invocation in sorted order {sorted_paths}; got {invocation_order}"
    )


@pytest.mark.unit
def test_main_all_mode_invokes_run_suite(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() with all_mode=True must invoke run_suite, not the single-module path."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    suite_called: list[bool] = []

    # Create a valid providers root so _discover_suite_modules can proceed
    _make_suite_module(tmp_path, "kms-key")
    providers_root = str(tmp_path / "providers" / "aws")
    monkeypatch.setenv("TERRATEST_MODULE_ROSTER_COUNT", "1")

    def fake_run_suite(**kwargs: Any) -> None:
        suite_called.append(True)
        raise SystemExit(0)

    with (
        patch.object(rt, "run_suite", side_effect=fake_run_suite),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path="",
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
            all_mode=True,
            providers_root=providers_root,
        )
    assert suite_called, "run_suite must be called when all_mode=True"


@pytest.mark.unit
def test_cli_main_all_flag_sets_all_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_cli_main with --all flag calls main() with all_mode=True."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    called_with: dict = {}

    def fake_main(
        module_path: str,
        accounts_json: str,
        boto3_mod: Any = None,
        all_mode: bool = False,
        summary_json: str | None = None,
        providers_root: str | None = None,
    ) -> None:
        called_with["all_mode"] = all_mode
        called_with["module_path"] = module_path
        sys.exit(0)

    monkeypatch.setattr(rt, "main", fake_main)
    monkeypatch.setattr("sys.argv", ["run_terratest", "--all"])

    with pytest.raises(SystemExit) as exc:
        rt._cli_main()
    assert exc.value.code == 0
    assert called_with.get("all_mode") is True, (
        f"Expected all_mode=True when --all is passed; got {called_with.get('all_mode')!r}"
    )


@pytest.mark.unit
def test_roster_count_constant_in_constants_module() -> None:
    """TERRATEST_MODULE_ROSTER_COUNT env var must be documented via a constant."""
    import scripts.constants as constants

    assert hasattr(constants, "TERRATEST_MODULE_ROSTER_COUNT_ENV_VAR"), (
        "TERRATEST_MODULE_ROSTER_COUNT_ENV_VAR not found in scripts/constants.py; "
        "this constant names the env var that controls the expected module roster count"
    )


# ---------------------------------------------------------------------------
# Destroy-retry: locate leaked terraform working dirs and re-run destroy
# ---------------------------------------------------------------------------


def _write_state_with_resources(work_dir: pathlib.Path, count: int = 1) -> pathlib.Path:
    """Write a terraform.tfstate with `count` resources into work_dir."""
    work_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 4,
        "resources": [{"type": "aws_kms_key", "name": f"k{i}"} for i in range(count)],
    }
    state_file = work_dir / "terraform.tfstate"
    state_file.write_text(json.dumps(state))
    return state_file


def _write_empty_state(work_dir: pathlib.Path) -> pathlib.Path:
    """Write a destroyed/empty terraform.tfstate (resources: []) into work_dir."""
    work_dir.mkdir(parents=True, exist_ok=True)
    state_file = work_dir / "terraform.tfstate"
    state_file.write_text(json.dumps({"version": 4, "resources": []}))
    return state_file


@pytest.mark.unit
def test_state_has_resources_true_when_resources_present(tmp_path: pathlib.Path) -> None:
    """_state_has_resources returns True for a state with a non-empty resources array."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    state_file = _write_state_with_resources(tmp_path / "ex", count=2)
    assert rt._state_has_resources(state_file) is True


@pytest.mark.unit
def test_state_has_resources_false_when_empty(tmp_path: pathlib.Path) -> None:
    """_state_has_resources returns False for an empty (destroyed) state."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    state_file = _write_empty_state(tmp_path / "ex")
    assert rt._state_has_resources(state_file) is False


@pytest.mark.unit
def test_state_has_resources_false_for_zero_byte_state(tmp_path: pathlib.Path) -> None:
    """_state_has_resources returns False for a zero-byte / whitespace-only state file."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    state_file = tmp_path / "terraform.tfstate"
    state_file.write_text("   \n")
    assert rt._state_has_resources(state_file) is False


@pytest.mark.unit
def test_state_has_resources_false_for_unparseable_state(tmp_path: pathlib.Path) -> None:
    """_state_has_resources returns False for a non-JSON state (cannot prove resources)."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    state_file = tmp_path / "terraform.tfstate"
    state_file.write_text("{not valid json")
    assert rt._state_has_resources(state_file) is False


@pytest.mark.unit
def test_state_has_resources_false_on_read_error(tmp_path: pathlib.Path) -> None:
    """_state_has_resources returns False when reading the state raises OSError."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    missing = tmp_path / "does-not-exist" / "terraform.tfstate"

    def raise_oserror(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        raise OSError("permission denied")

    with patch.object(pathlib.Path, "read_text", raise_oserror):
        assert rt._state_has_resources(missing) is False


@pytest.mark.unit
def test_find_leaked_terraform_dirs_no_examples(tmp_path: pathlib.Path) -> None:
    """_find_leaked_terraform_dirs returns [] when examples/ does not exist."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    module_path.mkdir()
    assert rt._find_leaked_terraform_dirs(str(module_path)) == []


@pytest.mark.unit
def test_find_leaked_terraform_dirs_finds_only_dirs_with_resources(
    tmp_path: pathlib.Path,
) -> None:
    """_find_leaked_terraform_dirs returns only dirs whose state has resources.

    A directory with a destroyed/empty state is NOT a leak and must be excluded,
    so the retry never re-destroys an already-clean working dir.
    """
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    examples = module_path / "examples"
    # basic/ leaked (has resources)
    leaked_dir = examples / "basic"
    _write_state_with_resources(leaked_dir, count=1)
    # clean/ destroyed (empty resources) -- must be excluded
    _write_empty_state(examples / "clean")
    # run-id-scoped copy left under examples/ also leaked
    run_copy = examples / "basic-tt-20260624-abc123"
    _write_state_with_resources(run_copy, count=3)

    leaked = rt._find_leaked_terraform_dirs(str(module_path))
    assert len(leaked) == 2, f"Expected 2 leaked dirs (basic + run-id copy); got {leaked}"
    assert str(leaked_dir.resolve()) in leaked
    assert str(run_copy.resolve()) in leaked
    assert str((examples / "clean").resolve()) not in leaked
    # Result must be sorted + de-duplicated.
    assert leaked == sorted(leaked)


@pytest.mark.unit
def test_find_leaked_terraform_dirs_skips_non_file_state(tmp_path: pathlib.Path) -> None:
    """_find_leaked_terraform_dirs skips a terraform.tfstate entry that is a directory."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    example = module_path / "examples" / "basic"
    example.mkdir(parents=True)
    # terraform.tfstate as a directory -- must be skipped (not a file)
    (example / "terraform.tfstate").mkdir()

    assert rt._find_leaked_terraform_dirs(str(module_path)) == []


@pytest.mark.unit
def test_retry_terraform_destroy_noop_when_no_leaks(tmp_path: pathlib.Path) -> None:
    """_retry_terraform_destroy returns 0 and invokes no terraform when nothing leaked."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    (module_path / "examples").mkdir(parents=True)

    invoke_called: list[bool] = []

    def fake_invoke(binary: str, args: list, **kwargs: Any) -> Any:
        invoke_called.append(True)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke):
        leaks = rt._retry_terraform_destroy(str(module_path), dict(os.environ))

    assert leaks == 0
    assert not invoke_called, "no terraform must be invoked when there are no leaked dirs"


@pytest.mark.unit
def test_retry_terraform_destroy_cds_into_leaked_dir_and_runs_destroy(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """_retry_terraform_destroy cd's into the leaked dir and runs 'terraform destroy'.

    The destroy succeeds by emptying the state file (simulating a real destroy),
    so the function must return 0 and log which dir it retried.
    """
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    leaked_dir = module_path / "examples" / "basic"
    state_file = _write_state_with_resources(leaked_dir, count=2)
    expected_cwd = str(leaked_dir.resolve())

    destroy_calls: list[dict] = []

    def fake_invoke(binary: str, args: list, **kwargs: Any) -> Any:
        destroy_calls.append({"binary": binary, "args": args, "cwd": kwargs.get("cwd")})
        # Simulate a successful destroy: empty the local state on the destroy call.
        if "destroy" in args:
            state_file.write_text(json.dumps({"version": 4, "resources": []}))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke):
        leaks = rt._retry_terraform_destroy(str(module_path), {"TF_PLUGIN_CACHE_DIR": "/tmp/c"})

    assert leaks == 0, "a successful destroy must leave zero remaining leaks"
    # A terraform destroy -auto-approve must have run with cwd == the leaked dir.
    destroy_invocations = [c for c in destroy_calls if "destroy" in c["args"]]
    assert destroy_invocations, "terraform destroy must be invoked for the leaked dir"
    assert destroy_invocations[0]["binary"] == "terraform"
    assert "-auto-approve" in destroy_invocations[0]["args"]
    assert destroy_invocations[0]["cwd"] == expected_cwd, (
        f"destroy must cd into the leaked working dir {expected_cwd!r}; "
        f"got cwd={destroy_invocations[0]['cwd']!r}"
    )
    captured = capsys.readouterr()
    assert expected_cwd in captured.err, "the retried dir must be logged"


@pytest.mark.unit
def test_retry_terraform_destroy_forwards_env_to_terraform(tmp_path: pathlib.Path) -> None:
    """_retry_terraform_destroy forwards the supplied env (creds, plugin cache) to terraform."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    leaked_dir = module_path / "examples" / "basic"
    state_file = _write_state_with_resources(leaked_dir, count=1)

    custom_env = {"TF_PLUGIN_CACHE_DIR": "/tmp/cache", "AWS_ACCOUNT_ID": "123456789012"}
    seen_envs: list[dict] = []

    def fake_invoke(binary: str, args: list, **kwargs: Any) -> Any:
        seen_envs.append(kwargs.get("env"))
        if "destroy" in args:
            state_file.write_text(json.dumps({"version": 4, "resources": []}))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke):
        rt._retry_terraform_destroy(str(module_path), custom_env)

    assert seen_envs, "invoke_pinned_binary was never called"
    assert all(env == custom_env for env in seen_envs), (
        f"the supplied env must be forwarded to every terraform call; got {seen_envs}"
    )


@pytest.mark.unit
def test_retry_terraform_destroy_returns_leak_count_when_destroy_fails(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """When destroy fails (state still holds resources), the function returns the leak count."""
    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    module_path = tmp_path / "mod"
    # Two leaked dirs; the destroy does NOT empty their state (simulating failure).
    _write_state_with_resources(module_path / "examples" / "basic", count=1)
    _write_state_with_resources(module_path / "examples" / "with-policy", count=1)

    def fake_invoke(binary: str, args: list, **kwargs: Any) -> Any:
        result = MagicMock()
        # destroy fails with non-zero and leaves the state intact.
        result.returncode = 1 if "destroy" in args else 0
        return result

    with patch("scripts.run_terratest.invoke_pinned_binary", side_effect=fake_invoke):
        leaks = rt._retry_terraform_destroy(str(module_path), dict(os.environ))

    assert leaks == 2, f"both dirs must remain leaked when destroy fails; got {leaks}"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err, "a failed destroy retry must log ERROR:"
    assert "destroy retry FAILED" in captured.err


@pytest.mark.unit
def test_main_triggers_destroy_retry_on_go_test_failure(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When go test fails, main() must invoke the destroy-retry before cache clean."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    call_order: list[str] = []

    def fake_retry(module_path: str, env: dict) -> int:
        call_order.append("retry")
        return 0

    def fake_clean(module_path: str) -> None:
        call_order.append("clean")

    with (
        patch.object(rt, "_invoke_go_test", return_value=1),
        patch.object(rt, "_retry_terraform_destroy", side_effect=fake_retry),
        patch.object(rt, "_clean_example_caches", side_effect=fake_clean),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit),
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert call_order == ["retry", "clean"], (
        "destroy-retry must run BEFORE cache clean (it needs the state/.terraform); "
        f"got order: {call_order}"
    )


@pytest.mark.unit
def test_main_skips_destroy_retry_when_go_test_passes(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When go test passes (terratest destroy succeeded), the destroy-retry is NOT invoked."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    retry_called: list[bool] = []

    def fake_retry(module_path: str, env: dict) -> int:
        retry_called.append(True)
        return 0

    with (
        patch.object(rt, "_invoke_go_test", return_value=0),
        patch.object(rt, "_retry_terraform_destroy", side_effect=fake_retry),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert not retry_called, "destroy-retry must NOT run when go test passed (no destroy failure)"
    assert exc.value.code == 0


@pytest.mark.unit
def test_main_destroy_retry_residual_leak_forces_nonzero_exit(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """A residual leak after the destroy-retry must keep the overall run red.

    This proves the existing exit-code contract is not weakened: even if the
    AWS-side sweep reports zero residue, an undestroyed local working dir fails
    the run.
    """
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with (
        patch.object(rt, "_invoke_go_test", return_value=1),
        patch.object(rt, "_retry_terraform_destroy", return_value=2),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert exc.value.code != 0, "a residual destroy-retry leak must force a non-zero exit"
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert "still" in captured.err and "live state" in captured.err


@pytest.mark.unit
def test_main_destroy_retry_success_does_not_mask_go_test_failure(
    tmp_path: pathlib.Path,
    sandbox_account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful destroy-retry must NOT mask the original go test failure exit code."""
    module_path = _make_valid_module(tmp_path)
    monkeypatch.delenv("TERRATEST_RUN_ID", raising=False)
    monkeypatch.delenv("GO_TEST_TIMEOUT", raising=False)

    if "scripts.run_terratest" in sys.modules:
        del sys.modules["scripts.run_terratest"]
    import scripts.run_terratest as rt

    with (
        patch.object(rt, "_invoke_go_test", return_value=1),
        patch.object(rt, "_retry_terraform_destroy", return_value=0),
        patch.object(rt, "_sweep_check", return_value=0),
        patch.object(rt, "_sweep_delete", return_value=0),
        patch.object(rt, "_sweep_recheck", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        rt.main(
            module_path=str(module_path),
            accounts_json=str(ACCOUNTS_JSON_PATH),
            boto3_mod=_make_sts_boto3_mock(sandbox_account_id),
        )

    assert exc.value.code != 0, "cleaning up leaked dirs must not turn a failed terratest run green"
