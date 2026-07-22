"""run_terratest -- FR-1 single-module and FR-5 suite-mode terratest runner.

Single-module mode (default):
  uv run python -m scripts.run_terratest <MODULE_PATH>

Suite mode (FR-5):
  uv run python -m scripts.run_terratest --all [--summary-json <path>]

Single-module mode validates the module directory, parses test.config
(env-wins precedence), generates or inherits the run id, exports the
TF_VAR_* / TERRATEST_RUN_ID / PROJECT_TAG environment, pre-warms a shared
TF_PLUGIN_CACHE_DIR to defeat the parallel provider-install race, runs
go test via invoke_pinned_binary, and ALWAYS executes the post-run cleanup
and run-id-scoped sweep check.

Suite mode discovers every module with tests/ + go.mod under
providers/aws/{primitives,references}/ and runs the FR-1 single-module
semantics for each, SERIALLY. All modules are run to completion even if one
fails; a per-module pass/fail summary table is printed and the exit code is
non-zero listing the failing modules.

Exit code is 0 only when (for each module):
  - go test exits 0, AND
  - zero run-id-tagged residue remains after the sweep phase.

Deny-set: accounts whose account_role is prod-infra or dns-owner are refused
with exit 1 BEFORE any go test invocation (spec section 3.6).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import random
import shutil
import signal
import string
import sys
from typing import Any

import scripts.constants as _constants
from scripts.binary_runner import invoke_pinned_binary
from scripts.terratest_sweep import main as _sweep_main

# Suite-mode env var name (from constants; avoids inline literal).
_ENV_MODULE_ROSTER_COUNT: str = _constants.TERRATEST_MODULE_ROSTER_COUNT_ENV_VAR

# ---------------------------------------------------------------------------
# Module-level references to constants (no inline literals)
# ---------------------------------------------------------------------------

_RUN_ID_PREFIX: str = _constants.TERRATEST_RUN_ID_PREFIX
_RUN_ID_RANDOM_CHARS: int = _constants.TERRATEST_RUN_ID_RANDOM_CHARS
_PROJECT_TAG: str = _constants.TERRATEST_PROJECT_TAG
_TERRATEST_BUILD_TAG: str = _constants.TERRATEST_BUILD_TAG
_DENIED_ROLES: frozenset[str] = _constants.TERRATEST_DENIED_ROLES
_PLUGIN_CACHE_DIR_NAME: str = _constants.TF_PLUGIN_CACHE_DIR_NAME
_RUN_ID_FILE_NAME: str = _constants.TERRATEST_RUN_ID_FILE_NAME

# Env var name for the optional run-id override (spec section 7).
_ENV_TERRATEST_RUN_ID: str = "TERRATEST_RUN_ID"
_ENV_GO_TEST_TIMEOUT: str = "GO_TEST_TIMEOUT"
_ENV_TERRATEST_IDEMPOTENCY: str = "TERRATEST_IDEMPOTENCY"
_ENV_TF_PLUGIN_CACHE_DIR: str = "TF_PLUGIN_CACHE_DIR"

# Go module path requirement in go.mod (spec: must require terraform-terratest-framework)
_REQUIRED_GO_MODULE: str = "github.com/caylent-solutions/terraform-terratest-framework"

# Default go test timeout (env > test.config > constants-default).
_DEFAULT_GO_TEST_TIMEOUT: str = _constants.DEFAULT_GO_TEST_TIMEOUT


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_module_path(module_path: str) -> None:
    """Validate that MODULE_PATH exists, has a tests/ dir, and a valid go.mod.

    Args:
        module_path: Path to the terraform module directory.

    Raises:
        SystemExit: If any validation check fails.
    """
    path = pathlib.Path(module_path)
    if not path.exists():
        print(
            f"ERROR: MODULE_PATH {module_path!r} does not exist or has no tests/ + go.mod",
            file=sys.stderr,
        )
        sys.exit(1)

    tests_dir = path / "tests"
    if not tests_dir.exists():
        print(
            f"ERROR: MODULE_PATH {module_path!r} does not exist or has no tests/ + go.mod. "
            "Missing: tests/ directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    go_mod = path / "go.mod"
    if not go_mod.exists():
        print(
            f"ERROR: MODULE_PATH {module_path!r} does not exist or has no tests/ + go.mod. "
            "Missing: go.mod file.",
            file=sys.stderr,
        )
        sys.exit(1)

    go_mod_content = go_mod.read_text()
    if _REQUIRED_GO_MODULE not in go_mod_content:
        print(
            f"ERROR: MODULE_PATH {module_path!r} does not exist or has no tests/ + go.mod. "
            f"go.mod does not require {_REQUIRED_GO_MODULE}.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# test.config parsing
# ---------------------------------------------------------------------------


def _parse_test_config(module_path: str) -> dict[str, str]:
    """Parse test.config from the module directory.

    Lines with KEY=VALUE are parsed. Lines that are empty, start with '#',
    or do not contain '=', or have an empty key are skipped silently.
    Environment variables override values found in the file.

    Args:
        module_path: Path to the module directory.

    Returns:
        Dict of config key -> value (env-wins already applied).

    Raises:
        SystemExit: If test.config is missing.
    """
    config_path = pathlib.Path(module_path) / "test.config"
    if not config_path.exists():
        print(
            f"ERROR: test.config not found at {config_path}. "
            "Every module must declare a test.config.",
            file=sys.stderr,
        )
        sys.exit(1)

    config: dict[str, str] = {}
    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        config[key] = value.strip()

    # Env-wins: environment variables take precedence over test.config values.
    for key in (_ENV_TERRATEST_IDEMPOTENCY, _ENV_GO_TEST_TIMEOUT):
        env_value = os.environ.get(key)
        if env_value is not None:
            config[key] = env_value

    return config


# ---------------------------------------------------------------------------
# Run-id generation
# ---------------------------------------------------------------------------


def _generate_run_id() -> str:
    """Generate a run id: tt-<UTC yyyymmddHHMMSS>-<6-char random alphanum lowercase>.

    If TERRATEST_RUN_ID is set in the environment, it is returned as-is.

    Returns:
        The run id string.
    """
    override = os.environ.get(_ENV_TERRATEST_RUN_ID)
    if override:
        return override

    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    rand_chars = "".join(
        random.choices(string.ascii_lowercase + string.digits, k=_RUN_ID_RANDOM_CHARS)
    )
    return f"{_RUN_ID_PREFIX}-{ts}-{rand_chars}"


def _write_run_id_file(module_path: str, run_id: str) -> None:
    """Persist the resolved run id to a deterministic per-module file.

    The file lives at ``<module_path>/<TERRATEST_RUN_ID_FILE_NAME>`` and is
    overwritten on every invocation. Because the path is scoped to the module
    directory, concurrent runs of DIFFERENT modules each write their own file
    and never collide, so the terratest-sweep-module target can perform a
    run-id-scoped (rather than account-wide) zero-orphan check that is safe to
    run in parallel with other modules' checks.

    Args:
        module_path: Path to the module directory.
        run_id: The resolved terratest run id to persist.

    Raises:
        SystemExit: If the file cannot be written (fail fast).
    """
    run_id_path = pathlib.Path(module_path) / _RUN_ID_FILE_NAME
    try:
        run_id_path.write_text(f"{run_id}\n")
    except OSError as exc:
        print(
            f"ERROR: failed to write run-id file {run_id_path}: {exc}. "
            "The per-module zero-orphan proof (terratest-sweep-module) requires "
            "this file; resolve the filesystem error and re-run.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Deny-set check
# ---------------------------------------------------------------------------


def _check_deny_set(accounts_json: str, boto3_mod: Any) -> str:
    """Refuse prod-infra and dns-owner accounts before any go test invocation.

    Args:
        accounts_json: Path to terragrunt/common/accounts.json.
        boto3_mod: boto3 module (injected for testability).

    Returns:
        The caller's AWS account ID (used by the caller to export AWS_ACCOUNT_ID).

    Raises:
        SystemExit: If the caller's account has a denied role.
    """
    accounts_path = pathlib.Path(accounts_json)
    if not accounts_path.exists():
        print(
            f"ERROR: accounts.json not found at {accounts_json}.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        accounts_data: dict[str, Any] = json.loads(accounts_path.read_text())
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: Failed to parse {accounts_json}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    sts = boto3_mod.client("sts")
    identity = sts.get_caller_identity()
    caller_account = str(identity["Account"])

    # Look up the role for this account
    account_row = accounts_data.get(caller_account, {})
    account_role = account_row.get("account_role", "")

    if account_role in _DENIED_ROLES:
        print(
            f"ERROR: terratest is forbidden in account {caller_account} ({account_role}); "
            "use sandbox or qa credentials",
            file=sys.stderr,
        )
        sys.exit(1)

    return caller_account


# ---------------------------------------------------------------------------
# TF_PLUGIN_CACHE_DIR management
# ---------------------------------------------------------------------------


def _get_or_create_plugin_cache_dir() -> str:
    """Return path to the shared TF plugin cache dir, creating it if needed.

    The location is determined by:
    1. TF_PLUGIN_CACHE_DIR env var (if set by caller)
    2. <home-dir>/<TF_PLUGIN_CACHE_DIR_NAME> (from constants)

    Returns:
        Absolute path to the cache directory.
    """
    env_override = os.environ.get(_ENV_TF_PLUGIN_CACHE_DIR)
    if env_override:
        cache_dir = pathlib.Path(env_override)
    else:
        cache_dir = pathlib.Path.home() / _PLUGIN_CACHE_DIR_NAME

    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


# ---------------------------------------------------------------------------
# Stale provider process cleanup (prevents "text file busy" on cache binary)
# ---------------------------------------------------------------------------


def _kill_stale_provider_processes(plugin_cache_dir: str) -> None:
    """Terminate any processes currently executing binaries from the plugin cache.

    On Linux, if a previous terraform run was interrupted (e.g., by timeout),
    the provider subprocess may remain alive with the cache binary mapped into
    its address space. Any subsequent attempt to write to that binary inode
    (e.g., during terraform init's provider installation step) fails with ETXTBSY
    ("text file busy"). This function scans /proc to find such processes and
    sends SIGTERM so the cache can be written cleanly during pre-warm.

    This function is a no-op on non-Linux systems (where /proc does not exist)
    and on errors reading /proc entries (e.g., processes that exit mid-scan).

    Args:
        plugin_cache_dir: Absolute path to the TF plugin cache directory.
    """
    proc_root = pathlib.Path("/proc")
    if not proc_root.exists():
        return

    cache_path = pathlib.Path(plugin_cache_dir)
    if not cache_path.exists():
        return

    # Collect inodes of all provider binaries in the cache.
    cache_inodes: set[int] = set()
    for binary in cache_path.rglob("terraform-provider-*"):
        if not binary.is_file():
            continue
        try:
            cache_inodes.add(binary.stat().st_ino)
        except OSError:
            continue

    if not cache_inodes:
        return

    # Scan /proc/<pid>/exe symlinks to find processes executing cache binaries.
    for proc_dir in proc_root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        exe_link = proc_dir / "exe"
        try:
            exe_inode = exe_link.stat().st_ino
        except OSError:
            continue
        if exe_inode in cache_inodes:
            try:
                os.kill(int(proc_dir.name), signal.SIGTERM)
            except ProcessLookupError, PermissionError:
                continue


# ---------------------------------------------------------------------------
# Pre-run provider cache warm-up (defeats parallel provider-install race)
# ---------------------------------------------------------------------------


def _prewarm_provider_cache(module_path: str, plugin_cache_dir: str) -> None:
    """Run terraform init in each example directory to populate the plugin cache.

    When go test spawns goroutines that each call terraform init in parallel,
    they all try to install providers into the shared cache simultaneously.
    Pre-warming by running terraform init sequentially first ensures the
    provider binary is already resident in the cache before go test starts,
    so goroutine inits only read from the cache (no write race).

    Args:
        module_path: Path to the module directory.
        plugin_cache_dir: Absolute path to the TF plugin cache directory.
    """
    examples_dir = pathlib.Path(module_path) / "examples"
    if not examples_dir.exists():
        return

    env = dict(os.environ)
    env["TF_PLUGIN_CACHE_DIR"] = plugin_cache_dir

    for example_dir in sorted(examples_dir.iterdir()):
        if not example_dir.is_dir():
            continue
        # Check it has terraform files
        if not list(example_dir.glob("*.tf")):
            continue
        invoke_pinned_binary(
            binary="terraform",
            args=["init", "-upgrade=false"],
            capture_output=True,
            cwd=str(example_dir),
            env=env,
        )


# ---------------------------------------------------------------------------
# Destroy-retry: re-run terraform destroy in leaked terratest working dirs
# ---------------------------------------------------------------------------


def _state_has_resources(state_file: pathlib.Path) -> bool:
    """Return True if a terraform.tfstate file declares one or more resources.

    A leaked terratest working directory is identified by a state file whose
    top-level ``resources`` array is non-empty: that means a real apply ran and
    the matching destroy did not (or did not finish), so infrastructure remains.
    An empty/destroyed state (``resources: []``) or an unparseable/zero-byte file
    is treated as "no resources" so we never re-destroy an already-clean dir.

    Args:
        state_file: Path to a ``terraform.tfstate`` file.

    Returns:
        True when the state declares at least one resource, else False.
    """
    try:
        raw = state_file.read_text()
    except OSError:
        return False
    if not raw.strip():
        return False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # An unparseable state cannot be proven to hold resources; do not retry.
        return False
    resources = parsed.get("resources", [])
    return bool(resources)


def _find_leaked_terraform_dirs(module_path: str) -> list[str]:
    """Locate terraform working directories where a terratest run leaked state.

    terratest applies the module's example in a working directory (the example
    dir under ``<module_path>/examples/`` or a run-id-scoped copy of it) and,
    on a clean run, destroys it and removes the working tree. When the destroy
    FAILS, the working tree is left behind carrying a ``terraform.tfstate`` with
    a non-empty ``resources`` array and/or an initialised ``.terraform`` dir.

    This scan walks ``<module_path>/examples/`` for directories that hold a
    ``terraform.tfstate`` whose ``resources`` array is non-empty. Such dirs are
    exactly the ones still bound to live infrastructure, so they are the dirs we
    must ``cd`` into and re-run ``terraform destroy`` for. Directories whose
    state is empty/destroyed are excluded so the retry only targets real leaks.

    The example/run dir is not hardcoded: it is derived from the module's own
    ``examples/`` tree (the same tree the prewarm and cache-clean phases walk),
    so a run-id-scoped copy that terratest left under ``examples/`` is found too.

    Args:
        module_path: Path to the module directory.

    Returns:
        Sorted, de-duplicated list of absolute working-directory paths that hold
        leaked (non-empty) terraform state. Empty when nothing leaked.
    """
    examples_dir = pathlib.Path(module_path) / "examples"
    if not examples_dir.exists():
        return []

    leaked: set[str] = set()
    for state_file in examples_dir.glob("**/terraform.tfstate"):
        if not state_file.is_file():
            continue
        if _state_has_resources(state_file):
            leaked.add(str(state_file.parent.resolve()))

    return sorted(leaked)


def _retry_terraform_destroy(module_path: str, env: dict[str, str]) -> int:
    """Re-run ``terraform destroy`` in every leaked terratest working directory.

    Invoked from the post-run cleanup path when the terratest destroy failed
    (go test exited non-zero) or when residue was detected. For each working
    directory found by :func:`_find_leaked_terraform_dirs`, this re-initialises
    the directory (offline-safe ``init`` so the local backend/plugins are ready)
    and runs ``terraform destroy -auto-approve`` through the same pinned-binary
    mechanism :func:`_prewarm_provider_cache` and ``run_terraform`` use. The dir
    that was retried and the per-dir outcome are logged clearly to stderr.

    The function is fail-fast about the *result*: after the destroy attempt each
    directory is re-inspected, and the returned value is the count of working
    directories that STILL hold non-empty state. A non-zero return means the
    retry could not fully destroy the leaked infrastructure and the caller must
    keep the overall run red (the existing exit-code contract is preserved).

    Args:
        module_path: Path to the module directory.
        env: Environment dict to pass to the terraform subprocess (carries
            TF_PLUGIN_CACHE_DIR, AWS creds, TF_VAR_* exactly as go test saw).

    Returns:
        Count of working directories that still hold non-empty state after the
        retry (0 when every leaked dir was destroyed or none were found).
    """
    leaked_dirs = _find_leaked_terraform_dirs(module_path)
    if not leaked_dirs:
        return 0

    print(
        f"WARNING: terratest destroy left {len(leaked_dirs)} terraform working "
        f"director(y/ies) with live state under {module_path}/examples/; "
        "retrying 'terraform destroy -auto-approve' in each.",
        file=sys.stderr,
    )

    still_leaked = 0
    for work_dir in leaked_dirs:
        print(
            f"INFO: retrying terraform destroy in leaked working dir {work_dir}",
            file=sys.stderr,
        )
        # Re-init offline so the local plugins/backend are ready for destroy even
        # if the .terraform dir was partially removed; -upgrade=false reuses the
        # warmed plugin cache. A failed init does not abort the loop -- the
        # post-destroy re-inspection below is the authoritative pass/fail signal.
        invoke_pinned_binary(
            binary="terraform",
            args=["init", "-upgrade=false"],
            capture_output=True,
            cwd=work_dir,
            env=env,
        )
        destroy_result = invoke_pinned_binary(
            binary="terraform",
            args=["destroy", "-auto-approve"],
            capture_output=False,
            cwd=work_dir,
            env=env,
        )

        # Re-inspect: the destroy is only successful if no resources remain.
        state_file = pathlib.Path(work_dir) / "terraform.tfstate"
        if state_file.is_file() and _state_has_resources(state_file):
            still_leaked += 1
            print(
                f"ERROR: terraform destroy retry FAILED in {work_dir} "
                f"(exit code {destroy_result.returncode}); live state remains.",
                file=sys.stderr,
            )
        else:
            print(
                f"INFO: terraform destroy retry succeeded in {work_dir}; "
                "no resources remain in local state.",
                file=sys.stderr,
            )

    return still_leaked


# ---------------------------------------------------------------------------
# Post-run example cache cleanup
# ---------------------------------------------------------------------------


def _clean_example_caches(module_path: str) -> None:
    """Remove .terraform dirs, terraform.tfstate files, and lockfile deltas from examples/.

    Args:
        module_path: Path to the module directory.
    """
    examples_dir = pathlib.Path(module_path) / "examples"
    if not examples_dir.exists():
        return

    for terraform_dir in examples_dir.glob("**/.terraform"):
        if terraform_dir.is_dir():
            shutil.rmtree(str(terraform_dir), ignore_errors=True)

    for state_file in examples_dir.glob("**/terraform.tfstate*"):
        if state_file.is_file():
            state_file.unlink(missing_ok=True)

    for lock_file in examples_dir.glob("**/.terraform.lock.hcl"):
        if lock_file.is_file():
            lock_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Sweep wrappers (injectable for unit tests)
# ---------------------------------------------------------------------------


def _sweep_check(run_id: str, boto3_mod: Any, accounts_json: str) -> int:
    """Run a sweep check for the given run id.

    Args:
        run_id: The terratest run id to check for residue.
        boto3_mod: boto3 module.
        accounts_json: Path to accounts.json.

    Returns:
        0 if zero resources found; non-zero count of resources found.
    """
    try:
        _sweep_main(
            mode="check",
            accounts_json=accounts_json,
            report_path=None,
            run_id=run_id,
            boto3_mod=boto3_mod,
        )
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code


def _sweep_delete(run_id: str, boto3_mod: Any, accounts_json: str) -> int:
    """Run a sweep delete for the given run id.

    Args:
        run_id: The terratest run id to delete residue for.
        boto3_mod: boto3 module.
        accounts_json: Path to accounts.json.

    Returns:
        Exit code from sweep delete.
    """
    try:
        _sweep_main(
            mode="delete",
            accounts_json=accounts_json,
            report_path=None,
            run_id=run_id,
            boto3_mod=boto3_mod,
        )
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code


def _sweep_recheck(run_id: str, boto3_mod: Any, accounts_json: str) -> int:
    """Re-check residue after delete attempt.

    Args:
        run_id: The terratest run id.
        boto3_mod: boto3 module.
        accounts_json: Path to accounts.json.

    Returns:
        0 if zero resources remain; non-zero otherwise.
    """
    return _sweep_check(run_id, boto3_mod, accounts_json)


# ---------------------------------------------------------------------------
# Go test invocation
# ---------------------------------------------------------------------------


def _invoke_go_test(module_path: str, timeout: str, env: dict[str, str]) -> int:
    """Invoke go test via invoke_pinned_binary with cwd at the module root.

    Args:
        module_path: Working directory for the go test invocation.
        timeout: The -timeout value for go test.
        env: Full environment dict to pass to the subprocess.

    Returns:
        Go test exit code.
    """
    # Build go binary path from environment or use the default
    go_binary = os.environ.get("GO_BINARY", "go")

    # The terratest (live-integration) test files carry the `//go:build terratest`
    # constraint so they are EXCLUDED from the default `go test ./...` build that
    # backs make go-unit-test-coverage (which runs without AWS creds / run id).
    # make tf-test MUST compile and run them, so pass `-tags terratest` here.
    result = invoke_pinned_binary(
        binary=go_binary,
        args=[
            "test",
            "-tags",
            _TERRATEST_BUILD_TAG,
            "-p=1",
            "./tests/...",
            f"-timeout={timeout}",
            "-count=1",
            "-v",
        ],
        capture_output=False,
        cwd=module_path,
        env=env,
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Suite-mode: module discovery (FR-5, spec section 4.5, D-3)
# ---------------------------------------------------------------------------


def _discover_suite_modules(providers_root: str) -> list[str]:
    """Discover all first-party terratest modules under providers_root.

    A qualifying module is a directory that carries BOTH:
      - a ``tests/`` subdirectory (ref), AND
      - a ``go.mod`` file

    The returned list is sorted for deterministic serial ordering (D-9).

    When the ``TERRATEST_MODULE_ROSTER_COUNT`` environment variable is set to
    a positive integer, the discovered count must match it exactly; a mismatch
    is a fail-fast error (D-25, spec section 4.5).

    Args:
        providers_root: Path to the ``providers/aws/`` directory to scan.

    Returns:
        Sorted list of absolute module path strings.

    Raises:
        SystemExit(1): If zero modules are discovered (D-3 fail-closed), or if
            the roster count does not match the ``TERRATEST_MODULE_ROSTER_COUNT``
            env var when that var is set.
    """
    root = pathlib.Path(providers_root)
    modules: list[str] = []
    for candidate in sorted(root.rglob("go.mod")):
        mod_dir = candidate.parent
        if (mod_dir / "tests").exists():
            modules.append(str(mod_dir))
    modules.sort()

    if not modules:
        print(
            "ERROR: no terratest modules discovered under providers/aws/ -- check the repo layout",
            file=sys.stderr,
        )
        sys.exit(1)

    roster_env = os.environ.get(_ENV_MODULE_ROSTER_COUNT)
    if roster_env is not None:
        expected = int(roster_env)
        actual = len(modules)
        if expected != actual:
            print(
                f"ERROR: discovered {actual} terratest module(s) but roster count is "
                f"{expected}; expected exactly {expected} modules. "
                f"Check the repo layout or update TERRATEST_MODULE_ROSTER_COUNT.",
                file=sys.stderr,
            )
            sys.exit(1)

    return modules


# ---------------------------------------------------------------------------
# Suite-mode: run all modules serially (FR-5, spec section 4.5, D-9)
# ---------------------------------------------------------------------------


def run_suite(
    providers_root: str,
    module_paths: list[str],
    accounts_json: str,
    boto3_mod: Any,
    summary_json_path: str | None,
    single_module_main: Any = None,
) -> None:
    """Run the terratest suite serially over the supplied module list.

    Runs ALL modules to completion even when one fails. Prints a per-module
    pass/fail summary table. Exits non-zero listing the failing modules.
    Optionally writes machine-readable results to ``summary_json_path``.

    Args:
        providers_root: Path to ``providers/aws/``; used only for labelling.
        module_paths: Sorted list of module path strings to run.
        accounts_json: Path to ``terragrunt/common/accounts.json``.
        boto3_mod: boto3 module (injected for testability).
        summary_json_path: If non-None, write the machine-readable JSON summary
            to this path; fail fast with ``ERROR:`` exit 1 if unwritable.
        single_module_main: Single-module entry point (default: ``main``).
            Injected for unit testability.

    Raises:
        SystemExit: Always -- exit 0 when every module passes, exit 1 otherwise.
    """
    if single_module_main is None:
        single_module_main = main

    # Validate the summary JSON path is writable BEFORE running any modules.
    # The parent directory must already exist; we do not create it.
    if summary_json_path is not None:
        summary_path = pathlib.Path(summary_json_path)
        if not summary_path.parent.exists():
            print(
                f"ERROR: cannot write --summary-json path {summary_json_path!r}: "
                f"parent directory {str(summary_path.parent)!r} does not exist",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            # Test writability by opening for write then closing immediately.
            summary_path.write_text("{}")
        except OSError as exc:
            print(
                f"ERROR: cannot write --summary-json path {summary_json_path!r}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Run every module to completion; capture per-module exit codes.
    results: list[dict[str, Any]] = []
    for module_path in module_paths:
        module_name = pathlib.Path(module_path).name
        exit_code = 0
        try:
            single_module_main(
                module_path=module_path,
                accounts_json=accounts_json,
                boto3_mod=boto3_mod,
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            exit_code = code

        results.append({"module": module_path, "name": module_name, "exit_code": exit_code})

    # Print summary table.
    _print_suite_summary(results)

    # Write machine-readable JSON summary if requested.
    if summary_json_path is not None:
        import json as _json

        summary_data: dict[str, Any] = {
            "results": [
                {
                    "module": r["module"],
                    "status": "PASS" if r["exit_code"] == 0 else "FAIL",
                    "exit_code": r["exit_code"],
                }
                for r in results
            ]
        }
        try:
            pathlib.Path(summary_json_path).write_text(_json.dumps(summary_data, indent=2))
        except OSError as exc:
            print(
                f"ERROR: failed to write summary JSON to {summary_json_path!r}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Collect failures and exit.
    failing = [r["module"] for r in results if r["exit_code"] != 0]
    if failing:
        failing_names = ", ".join(pathlib.Path(p).name for p in failing)
        print(
            f"FAILED modules ({len(failing)}): {failing_names}",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.exit(0)


def _print_suite_summary(results: list[dict[str, Any]]) -> None:
    """Print a per-module pass/fail summary table to stdout.

    Args:
        results: List of dicts with 'name' and 'exit_code' keys.
    """
    col_width = max((len(r["name"]) for r in results), default=20)
    header = f"{'MODULE':<{col_width}}  STATUS"
    separator = "-" * len(header)
    print(separator)
    print(header)
    print(separator)
    for r in results:
        status = "PASS" if r["exit_code"] == 0 else "FAIL"
        print(f"{r['name']:<{col_width}}  {status}")
    print(separator)


# ---------------------------------------------------------------------------
# Main entry point (importable for tests) -- extended for suite mode
# ---------------------------------------------------------------------------


def main(
    module_path: str,
    accounts_json: str,
    boto3_mod: Any = None,
    all_mode: bool = False,
    summary_json: str | None = None,
    providers_root: str | None = None,
) -> None:
    """Run the terratest runner for a single module or the full suite.

    When ``all_mode=True``, discovers every first-party module under
    ``providers/aws/`` and runs them all serially (FR-5 suite mode).
    When ``all_mode=False`` (default), behaves exactly as the single-module
    FR-1 runner.

    Args:
        module_path: Path to the module directory (single-module mode).
        accounts_json: Path to terragrunt/common/accounts.json.
        boto3_mod: boto3 module (injected for testability).
        all_mode: When True, run the full suite instead of a single module.
        summary_json: Path to write the machine-readable JSON summary (suite mode).
        providers_root: Override for the providers/aws/ root directory (suite mode).

    Raises:
        SystemExit: Always; exit 0 on success.
    """
    if boto3_mod is None:
        import boto3

        boto3_mod = boto3

    if all_mode:
        # Resolve providers root relative to accounts_json (or use override).
        if providers_root is None:
            providers_root = str(
                pathlib.Path(accounts_json).parent.parent.parent / "providers" / "aws"
            )
        discovered = _discover_suite_modules(providers_root)
        run_suite(
            providers_root=providers_root,
            module_paths=discovered,
            accounts_json=accounts_json,
            boto3_mod=boto3_mod,
            summary_json_path=summary_json,
        )
        # run_suite always calls sys.exit; unreachable but satisfies type checker.
        return

    # ---------------------------------------------------------------------------
    # Single-module path (FR-1)
    # ---------------------------------------------------------------------------

    # ---------------------------------------------------------------------------
    # Phase 1: Validate MODULE_PATH (fail fast before any AWS calls)
    # ---------------------------------------------------------------------------
    _validate_module_path(module_path)

    # ---------------------------------------------------------------------------
    # Phase 2: Parse test.config (fail fast if missing)
    # ---------------------------------------------------------------------------
    config = _parse_test_config(module_path)
    go_timeout = config.get(_ENV_GO_TEST_TIMEOUT, _DEFAULT_GO_TEST_TIMEOUT)

    # ---------------------------------------------------------------------------
    # Phase 3: Deny-set check (refuse prod-infra / dns-owner before go test)
    # ---------------------------------------------------------------------------
    caller_account = _check_deny_set(accounts_json, boto3_mod)

    # ---------------------------------------------------------------------------
    # Phase 4: Generate or inherit run id, then persist it to the per-module file
    # so the parallel-safe terratest-sweep-module check can read it back.
    # ---------------------------------------------------------------------------
    run_id = _generate_run_id()
    _write_run_id_file(module_path, run_id)

    # ---------------------------------------------------------------------------
    # Phase 5: Prepare go-test environment
    # ---------------------------------------------------------------------------
    plugin_cache_dir = _get_or_create_plugin_cache_dir()

    go_env = dict(os.environ)
    go_env[_ENV_TERRATEST_RUN_ID] = run_id
    go_env["TF_VAR_terratest_run_id"] = run_id
    go_env["PROJECT_TAG"] = _PROJECT_TAG
    go_env["TF_VAR_project_tag"] = _PROJECT_TAG
    go_env[_ENV_TF_PLUGIN_CACHE_DIR] = plugin_cache_dir
    go_env["AWS_ACCOUNT_ID"] = caller_account

    # Propagate idempotency setting from config
    idempotency = config.get(_ENV_TERRATEST_IDEMPOTENCY)
    if idempotency is not None:
        go_env[_ENV_TERRATEST_IDEMPOTENCY] = idempotency

    # ---------------------------------------------------------------------------
    # Phase 6: Kill any stale provider processes then pre-warm provider cache
    # ---------------------------------------------------------------------------
    _kill_stale_provider_processes(plugin_cache_dir)
    _prewarm_provider_cache(module_path, plugin_cache_dir)

    # ---------------------------------------------------------------------------
    # Phase 7: Execute go test (ALWAYS post-run via try/finally)
    # ---------------------------------------------------------------------------
    go_exit = 0
    destroy_retry_leaks = 0
    try:
        go_exit = _invoke_go_test(module_path, go_timeout, go_env)
    finally:
        # Phase 8a: When the terratest destroy failed (go test exited non-zero),
        # cd into each leaked terraform working dir and re-run terraform destroy
        # BEFORE the cache clean wipes the state/.terraform it needs. This is the
        # local-filesystem counterpart to the AWS-side run-id sweep below.
        if go_exit != 0:
            destroy_retry_leaks = _retry_terraform_destroy(module_path, go_env)

        # Phase 8b: ALWAYS post-run cleanup
        _clean_example_caches(module_path)

        # Phase 9: Run-id-scoped sweep: check -> delete if residue -> re-check
        check_result = _sweep_check(run_id, boto3_mod, accounts_json)
        residue_count = check_result

        if residue_count != 0:
            _sweep_delete(run_id, boto3_mod, accounts_json)
            residue_count = _sweep_recheck(run_id, boto3_mod, accounts_json)

        if residue_count != 0:
            print(
                f"ERROR: {residue_count} resources tagged terratest-run={run_id} "
                "remain after cleanup; see sweep report under .sweep-reports/",
                file=sys.stderr,
            )

        if destroy_retry_leaks != 0:
            print(
                f"ERROR: {destroy_retry_leaks} terraform working director(y/ies) still "
                "hold live state after the destroy retry; manual cleanup required.",
                file=sys.stderr,
            )

    # ---------------------------------------------------------------------------
    # Phase 10: Compose exit code (0 only if go test green AND zero residue AND
    # the destroy retry left no working dir still bound to live state).
    # ---------------------------------------------------------------------------
    if go_exit != 0 or residue_count != 0 or destroy_retry_leaks != 0:
        sys.exit(max(go_exit, residue_count, destroy_retry_leaks, 1))
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    """Parse CLI arguments and invoke main()."""
    default_accounts_json = str(
        pathlib.Path(__file__).parent.parent / "terragrunt" / "common" / "accounts.json"
    )

    parser = argparse.ArgumentParser(
        description="Run terratest for a single Terraform module or the full suite."
    )
    # module_path is optional in suite mode; use nargs='?' so --all does not require it.
    parser.add_argument(
        "module_path",
        nargs="?",
        default="",
        help="Path to the module directory (single-module mode; omit with --all).",
    )
    parser.add_argument(
        "--accounts-json",
        default=default_accounts_json,
        help="Path to terragrunt/common/accounts.json.",
    )
    parser.add_argument(
        "--all",
        dest="all_mode",
        action="store_true",
        default=False,
        help="Run the full suite (FR-5 suite mode) across all first-party modules.",
    )
    parser.add_argument(
        "--summary-json",
        dest="summary_json",
        default=None,
        help="Path to write the machine-readable JSON summary (suite mode only).",
    )
    parser.add_argument(
        "--providers-root",
        dest="providers_root",
        default=None,
        help="Override the providers/aws/ root for module discovery (suite mode).",
    )
    args = parser.parse_args()

    if not args.all_mode and not args.module_path:
        parser.error("module_path is required when --all is not specified.")

    main(
        module_path=args.module_path,
        accounts_json=args.accounts_json,
        boto3_mod=None,
        all_mode=args.all_mode,
        summary_json=args.summary_json,
        providers_root=args.providers_root,
    )


if __name__ == "__main__":
    _cli_main()
