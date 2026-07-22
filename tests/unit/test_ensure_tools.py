"""Unit tests for scripts.ensure_tools -- toolchain provisioner.

Tests assert that:
- AC-16: pinned versions are read from .tool-versions, never hardcoded in source
- AC-16: no asdf code path exists in the provisioner
- AC-16: per-tool routing follows the explicit D28 split (official pinned binary vs uv vs skip)
- AC-16: python/uv are skipped; pre-commit is uv-managed
- AC-16: provisioning failure raises ProvisioningError and CLI exits non-zero with ERROR: message
- AC-16: idempotency -- already-installed tool is verified without provisioning
"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root for reading source files
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
ENSURE_TOOLS_SOURCE = REPO_ROOT / "scripts" / "ensure_tools.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_module():
    """Import (or re-import) scripts.ensure_tools fresh."""
    import scripts.ensure_tools as m

    return importlib.reload(m)


# ---------------------------------------------------------------------------
# AC-16: No hardcoded version in source
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_version_literal_hardcoded_in_source() -> None:
    """The ensure_tools source must not contain any hardcoded tool version strings.

    All versions must be read from .tool-versions at runtime. A version literal
    in source creates a second source of truth and violates the pinned-version
    contract (AC-16).
    """
    assert ENSURE_TOOLS_SOURCE.exists(), (
        f"scripts/ensure_tools.py not found at {ENSURE_TOOLS_SOURCE}. "
        "The provisioner module must exist before this test can pass."
    )
    source_text = ENSURE_TOOLS_SOURCE.read_text()

    # Read real pinned versions from .tool-versions to check they are absent from source
    tool_versions_path = REPO_ROOT / ".tool-versions"
    assert tool_versions_path.exists(), (
        f".tool-versions not found at {tool_versions_path}. "
        "The version file must exist in the repository root."
    )
    versions = _parse_tool_versions(tool_versions_path)

    for tool, version in versions.items():
        # Version strings like "1.8.1" or "2.12.2" must not appear as literals
        assert version not in source_text, (
            f"Version literal {version!r} for tool {tool!r} found in "
            f"scripts/ensure_tools.py. All versions must be read from "
            f".tool-versions at runtime, never hardcoded (AC-16)."
        )


def _parse_tool_versions(path: pathlib.Path) -> dict[str, str]:
    """Parse a .tool-versions file into a {tool: version} mapping."""
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


# ---------------------------------------------------------------------------
# AC-16: No asdf code path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_asdf_reference_in_provisioner_source() -> None:
    """The ensure_tools source must contain zero references to 'asdf' (D28).

    asdf is explicitly excluded from the provisioning strategy. Any reference
    to asdf in the provisioner violates D28 and must cause this test to fail.
    """
    assert ENSURE_TOOLS_SOURCE.exists(), (
        f"scripts/ensure_tools.py not found at {ENSURE_TOOLS_SOURCE}. "
        "The provisioner module must exist before this test can pass."
    )
    source_text = ENSURE_TOOLS_SOURCE.read_text()
    assert "asdf" not in source_text, (
        "The string 'asdf' appears in scripts/ensure_tools.py. "
        "D28 forbids any asdf code path in the provisioner. "
        "Remove all asdf references."
    )


# ---------------------------------------------------------------------------
# AC-16: Tool routing -- parametrized D28 per-tool split
# ---------------------------------------------------------------------------

# D28 per-tool routing: every externally provisioned tool is installed from its
# official pinned binary release; skipped tools are handled externally; pre-commit
# is uv-managed.
#
# jq, gh, terraform, and trivy were reclassified from apt to binary provisioning:
# stock ubuntu-latest (noble) cannot pin the .tool-versions semver via apt -- e.g.
# the Ubuntu repo only carries jq 1.7.1 while .tool-versions pins jq 1.8.1, and the
# HashiCorp/Aqua/GitHub apt version strings carry distro suffixes that never match a
# bare semver. The official binary releases provide exact, OS/arch-agnostic pinning,
# so binary provisioning is the reliable D28-consistent path for all of them.
_BINARY_TOOLS = [
    "terragrunt",
    "opa",
    "tflint",
    "terraform-docs",
    "golangci-lint",
    "yq",
    "golang",
    "actionlint",
    "jq",
    "gh",
    "terraform",
    "trivy",
]
_SKIPPED_TOOLS = ["python", "uv"]
_UV_MANAGED_TOOLS = ["pre-commit"]


@pytest.mark.unit
@pytest.mark.parametrize("tool", _BINARY_TOOLS)
def test_binary_tool_routes_to_binary_provisioner(tool: str) -> None:
    """Each binary-managed tool must resolve to the binary provisioning method.

    D28 specifies terragrunt, opa, tflint, terraform-docs, golangci-lint, yq,
    and golang are provisioned via official pinned binary download. A routing
    failure would silently skip binary installation.
    """
    import scripts.ensure_tools as m

    method = m.get_provisioning_method(tool)
    assert method == "binary", (
        f"Tool {tool!r} must route to 'binary' provisioning per D28 but got {method!r}. "
        "Update the TOOL_CONFIG map in scripts/ensure_tools.py."
    )


@pytest.mark.unit
@pytest.mark.parametrize("tool", _SKIPPED_TOOLS)
def test_skipped_tools_route_to_skip(tool: str) -> None:
    """python and uv must resolve to 'skip' -- they are handled by setup actions / devcontainer.

    Attempting to provision python or uv via apt or binary would conflict with
    the official setup-python / setup-uv GitHub Actions (AC-16).
    """
    import scripts.ensure_tools as m

    method = m.get_provisioning_method(tool)
    assert method == "skip", (
        f"Tool {tool!r} must route to 'skip' per AC-16 but got {method!r}. "
        "python and uv are provided by setup-python/setup-uv in CI and by "
        "the devcontainer locally; they must never be provisioned by ensure_tools."
    )


@pytest.mark.unit
@pytest.mark.parametrize("tool", _UV_MANAGED_TOOLS)
def test_uv_managed_tools_route_to_uv(tool: str) -> None:
    """pre-commit must resolve to 'uv' -- it is managed as a uv dependency.

    AC-16 specifies pre-commit is uv-managed. Attempting to provision it via
    apt or binary would create a second installation that conflicts with uv.
    """
    import scripts.ensure_tools as m

    method = m.get_provisioning_method(tool)
    assert method == "uv", (
        f"Tool {tool!r} must route to 'uv' per AC-16 but got {method!r}. "
        "pre-commit is managed by uv and must not be provisioned via apt or binary."
    )


# ---------------------------------------------------------------------------
# AC-16: Reading versions from .tool-versions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_tool_versions_returns_all_tools(tmp_path: pathlib.Path) -> None:
    """parse_tool_versions must return every tool-version pair from the file.

    A correct parse is the foundation for all subsequent provisioning decisions.
    Missing entries would silently skip tools that need provisioning.
    """
    tool_versions_content = textwrap.dedent("""\
        golang 1.22.0
        terraform 1.7.0
        jq 1.7.1
    """)
    tv_file = tmp_path / ".tool-versions"
    tv_file.write_text(tool_versions_content)

    import scripts.ensure_tools as m

    result = m.parse_tool_versions(tv_file)
    assert result == {"golang": "1.22.0", "terraform": "1.7.0", "jq": "1.7.1"}, (
        f"parse_tool_versions returned {result!r} but expected all three tools. "
        "The parser must handle every non-comment, non-blank line."
    )


@pytest.mark.unit
def test_parse_tool_versions_skips_comments_and_blank_lines(tmp_path: pathlib.Path) -> None:
    """parse_tool_versions must skip comment lines (# ...) and blank lines."""
    tool_versions_content = textwrap.dedent("""\
        # this is a comment
        golang 1.22.0

        # another comment
        terraform 1.7.0
    """)
    tv_file = tmp_path / ".tool-versions"
    tv_file.write_text(tool_versions_content)

    import scripts.ensure_tools as m

    result = m.parse_tool_versions(tv_file)
    assert result == {"golang": "1.22.0", "terraform": "1.7.0"}, (
        f"parse_tool_versions returned {result!r}; comments and blanks must be skipped."
    )


@pytest.mark.unit
def test_parse_tool_versions_raises_on_missing_file() -> None:
    """parse_tool_versions must raise FileNotFoundError when the file is absent.

    Fail-fast: if .tool-versions is missing, the provisioner cannot know what
    to install. A silent return would allow undetected mis-provisioning.
    """
    import scripts.ensure_tools as m

    missing = pathlib.Path("/nonexistent/path/.tool-versions")
    with pytest.raises(FileNotFoundError):
        m.parse_tool_versions(missing)


# ---------------------------------------------------------------------------
# AC-16: Version verification
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_version_installed_returns_true_when_version_matches() -> None:
    """is_version_installed must return True when the installed version matches pinned.

    Idempotency depends on accurate version detection: if it incorrectly
    reports a mismatch when versions match, the provisioner wastes work and
    may trigger unnecessary re-installs.
    """
    import scripts.ensure_tools as m

    with patch.object(m, "get_installed_version", return_value="1.22.0"):
        result = m.is_version_installed("golang", "1.22.0")
    assert result is True, (
        "is_version_installed must return True when the installed version matches "
        "the pinned version."
    )


@pytest.mark.unit
def test_is_version_installed_returns_false_when_version_differs() -> None:
    """is_version_installed must return False when the installed version differs."""
    import scripts.ensure_tools as m

    with patch.object(m, "get_installed_version", return_value="1.21.0"):
        result = m.is_version_installed("golang", "1.22.0")
    assert result is False, "is_version_installed must return False when installed != pinned."


@pytest.mark.unit
def test_is_version_installed_returns_false_when_tool_not_found() -> None:
    """is_version_installed must return False when the tool binary is absent."""
    import scripts.ensure_tools as m

    with patch.object(m, "get_installed_version", side_effect=FileNotFoundError("not found")):
        result = m.is_version_installed("golang", "1.22.0")
    assert result is False, (
        "is_version_installed must return False when the tool is not installed "
        "(FileNotFoundError from get_installed_version)."
    )


# ---------------------------------------------------------------------------
# AC-16: Provisioning error path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_provisioning_failure_raises_provisioning_error() -> None:
    """A provisioning failure must raise ProvisioningError with tool name and expected version.

    Fail-fast contract: any failure in binary provisioning must surface
    immediately as a specific, typed exception naming the tool and expected version.
    Swallowing the error would leave the toolchain in an undefined state.
    """
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=False),
        patch.object(m, "get_provisioning_method", return_value="binary"),
        patch.object(m, "provision_via_binary", side_effect=RuntimeError("download failed")),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.ensure_tool("jq", "1.8.1")
    error_msg = str(exc_info.value)
    assert "jq" in error_msg, f"ProvisioningError message must name the tool. Got: {error_msg!r}"
    assert "1.8.1" in error_msg, (
        f"ProvisioningError message must name the expected version. Got: {error_msg!r}"
    )


@pytest.mark.unit
def test_cli_exits_nonzero_on_provisioning_error(tmp_path: pathlib.Path) -> None:
    """The CLI handler must exit non-zero with an ERROR: message on ProvisioningError.

    The error message must name the tool and expected version so an operator
    can act on it immediately (AC-16 error handling contract).
    """
    tool_versions_content = "jq 1.8.1\n"
    tv_file = tmp_path / ".tool-versions"
    tv_file.write_text(tool_versions_content)

    import scripts.ensure_tools as m

    with (
        patch.object(m, "parse_tool_versions", return_value={"jq": "1.8.1"}),
        patch.object(
            m, "ensure_tool", side_effect=m.ProvisioningError("jq", "1.8.1", "apt failed")
        ),
        patch("sys.exit") as mock_exit,
        patch("sys.stderr") as mock_stderr,
    ):
        m.main(str(tv_file))

    mock_exit.assert_called_once_with(1)
    # Verify ERROR: message was written to stderr
    stderr_calls = "".join(str(c) for c in mock_stderr.write.call_args_list)
    assert "ERROR:" in stderr_calls or any(
        "ERROR:" in str(arg)
        for call_item in mock_stderr.write.call_args_list
        for arg in call_item[0]
    ), (
        "CLI handler must write an 'ERROR:' message to stderr on ProvisioningError. "
        f"Stderr write calls: {mock_stderr.write.call_args_list!r}"
    )


# ---------------------------------------------------------------------------
# AC-16: Idempotency -- already-installed tool does no work
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ensure_tool_skips_provisioning_when_already_installed() -> None:
    """ensure_tool must not provision when the installed version already matches.

    Idempotency contract: a second invocation against an already-provisioned
    tool must perform no provisioning and must not raise. Violating idempotency
    causes unnecessary re-installations on every CI run.
    """
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=True),
        patch.object(m, "provision_via_binary") as mock_binary,
    ):
        m.ensure_tool("jq", "1.8.1")

    mock_binary.assert_not_called()


@pytest.mark.unit
def test_ensure_tool_calls_provisioner_when_version_missing() -> None:
    """ensure_tool must invoke the provisioner when the pinned version is absent.

    The verify-then-provision flow must trigger provisioning on a version
    mismatch or absence. Failing to provision silently leaves the environment
    at the wrong version.
    """
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=False),
        patch.object(m, "get_provisioning_method", return_value="binary"),
        patch.object(m, "provision_via_binary") as mock_binary,
    ):
        m.ensure_tool("jq", "1.8.1")

    mock_binary.assert_called_once_with("jq", "1.8.1")


# ---------------------------------------------------------------------------
# AC-16: Skipped tools are not provisioned
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("tool", _SKIPPED_TOOLS)
def test_skipped_tools_are_not_provisioned(tool: str) -> None:
    """python and uv must never be passed to any provisioner.

    They are managed by setup-python/setup-uv (CI) and the devcontainer
    (local). Provisioning them would interfere with those official mechanisms.
    """
    import scripts.ensure_tools as m

    with patch.object(m, "provision_via_binary") as mock_binary:
        m.ensure_tool(tool, "3.14.5")

    mock_binary.assert_not_called()


@pytest.mark.unit
def test_pre_commit_is_not_provisioned_via_binary() -> None:
    """pre-commit must never be passed to the binary provisioner.

    It is managed as a uv dependency. Provisioning it externally would create
    version conflicts with the uv-managed installation.
    """
    import scripts.ensure_tools as m

    with patch.object(m, "provision_via_binary") as mock_binary:
        m.ensure_tool("pre-commit", "4.6.0")

    mock_binary.assert_not_called()


# ---------------------------------------------------------------------------
# go-install routing: govulncheck (D28 -- provisioned via `go install`)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_govulncheck_routes_to_go_install_provisioner() -> None:
    """govulncheck must resolve to the 'go-install' provisioning method.

    It has no single official OS/arch binary release; it is built from source at
    the pinned tag with `go install`. A routing failure would silently skip its
    provisioning and the go-vuln gate would fail with 'govulncheck not found'.
    """
    import scripts.ensure_tools as m

    method = m.get_provisioning_method("govulncheck")
    assert method == "go-install", (
        f"govulncheck must route to 'go-install' but got {method!r}. "
        "Update the TOOL_CONFIG map in scripts/ensure_tools.py."
    )


@pytest.mark.unit
def test_govulncheck_has_pinned_version_in_tool_versions() -> None:
    """.tool-versions must pin govulncheck so the install target is version-pinned.

    The go-install step reads the version from .tool-versions at runtime; an absent
    pin would mean an unpinned (drifting) install.
    """
    tool_versions_path = REPO_ROOT / ".tool-versions"
    versions = _parse_tool_versions(tool_versions_path)
    assert "govulncheck" in versions, (
        ".tool-versions must pin govulncheck so CI installs it at a fixed version."
    )


@pytest.mark.unit
def test_ensure_tool_calls_go_install_when_not_current() -> None:
    """ensure_tool must invoke the go-install provisioner when the tool is absent/stale."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "_is_go_install_tool_current", return_value=False),
        patch.object(m, "provision_via_go_install") as mock_go_install,
        patch.object(m, "provision_via_binary") as mock_binary,
    ):
        m.ensure_tool("govulncheck", "1.3.0")

    mock_go_install.assert_called_once_with("govulncheck", "1.3.0")
    mock_binary.assert_not_called()


@pytest.mark.unit
def test_ensure_tool_skips_go_install_when_already_current() -> None:
    """ensure_tool must not re-install a go-install tool already at the pinned version."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "_is_go_install_tool_current", return_value=True),
        patch.object(m, "provision_via_go_install") as mock_go_install,
    ):
        m.ensure_tool("govulncheck", "1.3.0")

    mock_go_install.assert_not_called()


@pytest.mark.unit
def test_provision_via_go_install_runs_pinned_go_install_and_verifies(
    tmp_path: pathlib.Path,
) -> None:
    """provision_via_go_install must run `go install <pkg>@v<version>` and verify.

    The composed install target must carry the pinned version, and the post-install
    version probe must be parsed so a wrong-version install cannot pass silently.
    """
    import scripts.ensure_tools as m

    gopath_bin = tmp_path / "bin"
    gopath_bin.mkdir()
    installed = gopath_bin / "govulncheck"
    installed.write_text("#!/bin/sh\n")

    recorded: dict[str, list[str]] = {}

    def fake_subprocess_run(argv: list[str], **kwargs: object) -> MagicMock:
        if argv[:2] == ["go", "install"]:
            recorded["install_argv"] = argv
            return MagicMock(returncode=0, stdout="", stderr="")
        # Version probe of the installed binary.
        return MagicMock(
            returncode=0,
            stdout="Go: go1.26.4\nScanner: govulncheck@v1.3.0\n",
            stderr="",
        )

    with (
        patch.object(m, "_resolve_gopath_bin", return_value=gopath_bin),
        patch.object(m.shutil, "which", return_value="/usr/bin/go"),
        patch.object(m.subprocess, "run", side_effect=fake_subprocess_run),
    ):
        m.provision_via_go_install("govulncheck", "1.3.0")

    assert recorded["install_argv"] == [
        "go",
        "install",
        "golang.org/x/vuln/cmd/govulncheck@v1.3.0",
    ], f"install target must be version-pinned. Got: {recorded.get('install_argv')!r}"


@pytest.mark.unit
def test_provision_via_go_install_raises_on_version_mismatch(tmp_path: pathlib.Path) -> None:
    """provision_via_go_install must fail-fast when the installed version != pinned."""
    import scripts.ensure_tools as m

    gopath_bin = tmp_path / "bin"
    gopath_bin.mkdir()
    (gopath_bin / "govulncheck").write_text("#!/bin/sh\n")

    def fake_subprocess_run(argv: list[str], **kwargs: object) -> MagicMock:
        if argv[:2] == ["go", "install"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        # Probe reports a DIFFERENT scanner version than pinned.
        return MagicMock(
            returncode=0,
            stdout="Go: go1.26.4\nScanner: govulncheck@v1.2.0\n",
            stderr="",
        )

    with (
        patch.object(m, "_resolve_gopath_bin", return_value=gopath_bin),
        patch.object(m.shutil, "which", return_value="/usr/bin/go"),
        patch.object(m.subprocess, "run", side_effect=fake_subprocess_run),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.provision_via_go_install("govulncheck", "1.3.0")

    assert "1.3.0" in str(exc_info.value)


@pytest.mark.unit
def test_provision_via_go_install_raises_when_go_install_fails(tmp_path: pathlib.Path) -> None:
    """provision_via_go_install must wrap a failing `go install` as ProvisioningError."""
    import scripts.ensure_tools as m

    def fake_subprocess_run(argv: list[str], **kwargs: object) -> MagicMock:
        return MagicMock(returncode=1, stdout="", stderr="build failed")

    with (
        patch.object(m.shutil, "which", return_value="/usr/bin/go"),
        patch.object(m.subprocess, "run", side_effect=fake_subprocess_run),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.provision_via_go_install("govulncheck", "1.3.0")

    assert "govulncheck" in str(exc_info.value)


@pytest.mark.unit
def test_is_go_install_tool_current_true_on_matching_version(tmp_path: pathlib.Path) -> None:
    """_is_go_install_tool_current must return True when the pinned version is installed."""
    import scripts.ensure_tools as m

    gopath_bin = tmp_path / "bin"
    gopath_bin.mkdir()
    (gopath_bin / "govulncheck").write_text("#!/bin/sh\n")

    with (
        patch.object(m, "_resolve_gopath_bin", return_value=gopath_bin),
        patch.object(
            m.subprocess,
            "run",
            return_value=MagicMock(
                returncode=0,
                stdout="Go: go1.26.4\nScanner: govulncheck@v1.3.0\n",
                stderr="",
            ),
        ),
    ):
        assert m._is_go_install_tool_current("govulncheck", "1.3.0") is True


@pytest.mark.unit
def test_is_go_install_tool_current_false_when_binary_absent(tmp_path: pathlib.Path) -> None:
    """_is_go_install_tool_current must return False when the binary is not in GOPATH/bin."""
    import scripts.ensure_tools as m

    gopath_bin = tmp_path / "bin"
    gopath_bin.mkdir()  # empty -- no govulncheck

    with patch.object(m, "_resolve_gopath_bin", return_value=gopath_bin):
        assert m._is_go_install_tool_current("govulncheck", "1.3.0") is False


# ---------------------------------------------------------------------------
# AC-16: Full main() flow -- all tools from .tool-versions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_processes_all_tools_from_tool_versions(tmp_path: pathlib.Path) -> None:
    """main() must call ensure_tool for every tool parsed from .tool-versions.

    If main() silently skips any tool, its version would not be verified or
    provisioned, breaking the reproducibility guarantee (AC-16).
    """
    tool_versions_content = textwrap.dedent("""\
        jq 1.8.1
        golang 1.22.0
        python 3.14.5
    """)
    tv_file = tmp_path / ".tool-versions"
    tv_file.write_text(tool_versions_content)

    import scripts.ensure_tools as m

    called_tools: list[str] = []

    def fake_ensure_tool(tool: str, version: str) -> None:
        called_tools.append(tool)

    with patch.object(m, "ensure_tool", side_effect=fake_ensure_tool):
        m.main(str(tv_file))

    assert "jq" in called_tools, "main() must call ensure_tool for 'jq'."
    assert "golang" in called_tools, "main() must call ensure_tool for 'golang'."
    assert "python" in called_tools, "main() must call ensure_tool for 'python'."


# ---------------------------------------------------------------------------
# AC-16: TOOL_CONFIG map drives routing (no hardcoded logic)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_config_map_exists_and_is_dict() -> None:
    """TOOL_CONFIG must be a module-level dict that drives per-tool routing.

    The routing must be config-driven (TOOL_CONFIG), not embedded in if/elif
    chains. A missing or non-dict TOOL_CONFIG means the routing is hardcoded
    logic, which violates the no-hardcoded-values standard (AC-16).
    """
    import scripts.ensure_tools as m

    assert hasattr(m, "TOOL_CONFIG"), (
        "scripts.ensure_tools must expose a module-level TOOL_CONFIG dict. "
        "Per-tool routing must be driven from this config map, not embedded logic."
    )
    assert isinstance(m.TOOL_CONFIG, dict), (
        f"TOOL_CONFIG must be a dict but is {type(m.TOOL_CONFIG).__name__!r}. "
        "The config map must map tool names to their provisioning method strings."
    )


@pytest.mark.unit
def test_tool_config_covers_all_expected_tools() -> None:
    """TOOL_CONFIG must contain an entry for every tool category defined by D28."""
    import scripts.ensure_tools as m

    all_known_tools = _BINARY_TOOLS + _SKIPPED_TOOLS + _UV_MANAGED_TOOLS
    config_keys = set(m.TOOL_CONFIG.keys())
    for tool in all_known_tools:
        assert tool in config_keys, (
            f"TOOL_CONFIG is missing an entry for {tool!r}. "
            "Every D28-classified tool must have a routing entry."
        )


# ---------------------------------------------------------------------------
# AC-16: get_provisioning_method raises on unknown tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_provisioning_method_raises_on_unknown_tool() -> None:
    """get_provisioning_method must raise ValueError for any tool not in TOOL_CONFIG.

    Fail-fast: an unknown tool in .tool-versions must not silently pass through.
    It must produce an actionable error so the operator can add the tool to
    TOOL_CONFIG.
    """
    import scripts.ensure_tools as m

    with pytest.raises((ValueError, KeyError)):
        m.get_provisioning_method("unknown-tool-xyz")


# ---------------------------------------------------------------------------
# AC-16: get_installed_version paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_installed_version_returns_parsed_version() -> None:
    """get_installed_version must return the version string parsed from stdout."""
    import scripts.ensure_tools as m

    mock_result = MagicMock()
    mock_result.stdout = "jq-1.8.1\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which", return_value="/usr/bin/jq"),
        patch("subprocess.run", return_value=mock_result),
    ):
        version = m.get_installed_version("jq")

    assert version == "1.8.1", (
        f"get_installed_version must parse '1.8.1' from 'jq-1.8.1' output. Got: {version!r}"
    )


@pytest.mark.unit
def test_get_installed_version_raises_file_not_found_when_binary_absent() -> None:
    """get_installed_version must raise FileNotFoundError when the binary is not on PATH."""
    import scripts.ensure_tools as m

    with patch("shutil.which", return_value=None), pytest.raises(FileNotFoundError):
        m.get_installed_version("jq")


@pytest.mark.unit
def test_get_installed_version_raises_provisioning_error_on_timeout() -> None:
    """get_installed_version must raise ProvisioningError when subprocess times out."""
    import scripts.ensure_tools as m

    with (
        patch("shutil.which", return_value="/usr/bin/jq"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["jq", "--version"], 10)),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.get_installed_version("jq")

    assert "timed out" in str(exc_info.value).lower(), (
        "ProvisioningError on timeout must mention 'timed out'."
    )


@pytest.mark.unit
def test_get_installed_version_raises_provisioning_error_when_no_version_in_output() -> None:
    """get_installed_version must raise ProvisioningError when no version can be parsed."""
    import scripts.ensure_tools as m

    mock_result = MagicMock()
    mock_result.stdout = "no version info here\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which", return_value="/usr/bin/jq"),
        patch("subprocess.run", return_value=mock_result),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.get_installed_version("jq")

    assert "Could not parse version" in str(exc_info.value), (
        "ProvisioningError must mention 'Could not parse version' when output has no version."
    )


@pytest.mark.unit
def test_get_installed_version_uses_go_version_subcommand_for_golang() -> None:
    """get_installed_version must call `go version` (subcommand) for tool 'golang'.

    `go` does not accept a `--version` flag -- `go --version` errors with
    'flag provided but not defined: -version'. The version-probe-args override
    must route golang to the `version` subcommand so the probe succeeds, and the
    parser must extract the bare semver from `go version go1.26.4 linux/<arch>`.
    """
    import scripts.ensure_tools as m

    mock_result = MagicMock()
    mock_result.stdout = "go version go1.26.4 linux/arm64\n"
    mock_result.stderr = ""

    called_with: list[list[str]] = []

    def capture_run(cmd, **kwargs):
        called_with.append(list(cmd))
        return mock_result

    with (
        patch("shutil.which", return_value="/usr/local/go/bin/go"),
        patch("subprocess.run", side_effect=capture_run),
    ):
        version = m.get_installed_version("golang")

    assert called_with[0] == ["go", "version"], (
        "get_installed_version must call ['go', 'version'] (subcommand, not the "
        f"unsupported --version flag) for tool 'golang'. Got: {called_with[0]!r}"
    )
    assert version == "1.26.4", (
        "get_installed_version must parse the bare semver '1.26.4' from "
        f"'go version go1.26.4 ...'. Got: {version!r}"
    )


@pytest.mark.unit
def test_get_installed_version_uses_opa_version_subcommand_for_opa() -> None:
    """get_installed_version must call `opa version` (subcommand) for tool 'opa'.

    `opa` does not accept a `--version` flag -- `opa --version` errors with
    'Error: unknown flag: --version' and prints its usage. Like go, opa exposes
    its version through a `version` subcommand. The version-probe-args override
    must route opa to the `version` subcommand so the probe succeeds, and the
    parser must extract the bare semver from `Version: 1.17.0`. Without the
    override the idempotency check raises ProvisioningError even when opa is
    already installed at the pinned version, breaking the verify-only no-op path.
    """
    import scripts.ensure_tools as m

    mock_result = MagicMock()
    mock_result.stdout = "Version: 1.17.0\nBuild Commit: abc\nPlatform: linux/arm64\n"
    mock_result.stderr = ""

    called_with: list[list[str]] = []

    def capture_run(cmd, **kwargs):
        called_with.append(list(cmd))
        return mock_result

    with (
        patch("shutil.which", return_value="/usr/local/bin/opa"),
        patch("subprocess.run", side_effect=capture_run),
    ):
        version = m.get_installed_version("opa")

    assert called_with[0] == ["opa", "version"], (
        "get_installed_version must call ['opa', 'version'] (subcommand, not the "
        f"unsupported --version flag) for tool 'opa'. Got: {called_with[0]!r}"
    )
    assert version == "1.17.0", (
        "get_installed_version must parse the bare semver '1.17.0' from "
        f"'Version: 1.17.0'. Got: {version!r}"
    )


@pytest.mark.unit
def test_version_probe_args_map_overrides_opa() -> None:
    """VERSION_PROBE_ARGS must route opa to the `version` subcommand (config-driven).

    opa uses the `version` subcommand, not --version. Embedding this as an if/elif
    on the tool name would be hardcoded logic; it must be a module-level config map
    entry so routing stays declarative (D28).
    """
    import scripts.ensure_tools as m

    assert m.VERSION_PROBE_ARGS.get("opa") == ["version"], (
        "VERSION_PROBE_ARGS['opa'] must be ['version'] -- opa uses the `version` "
        f"subcommand, not --version. Got: {m.VERSION_PROBE_ARGS.get('opa')!r}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool", "output", "expected"),
    [
        ("terragrunt", "terragrunt version v1.0.7", "1.0.7"),
        ("yq", "yq (https://github.com/mikefarah/yq/) version v4.53.2", "4.53.2"),
        ("terraform", "Terraform v1.15.5\non linux_arm64", "1.15.5"),
        ("opa", "Version: 1.17.0", "1.17.0"),
        ("actionlint", "1.7.12", "1.7.12"),
    ],
)
def test_get_installed_version_strips_v_prefix(tool: str, output: str, expected: str) -> None:
    """get_installed_version must parse versions that carry a leading 'v' prefix.

    Several tools print 'v<semver>' (terragrunt, yq, terraform). The bare semver
    in .tool-versions has no 'v', so the parser must strip the prefix or the
    idempotency comparison (installed == pinned) would always mismatch and
    re-provision on every run.
    """
    import scripts.ensure_tools as m

    mock_result = MagicMock()
    mock_result.stdout = output + "\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which", return_value=f"/usr/bin/{tool}"),
        patch("subprocess.run", return_value=mock_result),
    ):
        version = m.get_installed_version(tool)

    assert version == expected, (
        f"get_installed_version must parse {expected!r} from {output!r} for {tool!r}. "
        f"Got: {version!r}"
    )


@pytest.mark.unit
def test_version_probe_args_map_exists_and_overrides_golang() -> None:
    """A VERSION_PROBE_ARGS config map must drive per-tool probe args (D28 config-driven).

    The default probe is ['--version']; golang overrides to ['version']. Embedding
    this as an if/elif on the tool name would be hardcoded logic. It must be a
    module-level config map so routing stays declarative.
    """
    import scripts.ensure_tools as m

    assert hasattr(m, "VERSION_PROBE_ARGS"), (
        "scripts.ensure_tools must expose a module-level VERSION_PROBE_ARGS dict "
        "mapping a tool name to its version-probe argv suffix."
    )
    assert isinstance(m.VERSION_PROBE_ARGS, dict), (
        f"VERSION_PROBE_ARGS must be a dict but is {type(m.VERSION_PROBE_ARGS).__name__!r}."
    )
    assert m.VERSION_PROBE_ARGS.get("golang") == ["version"], (
        "VERSION_PROBE_ARGS['golang'] must be ['version'] -- go uses the `version` "
        f"subcommand, not --version. Got: {m.VERSION_PROBE_ARGS.get('golang')!r}"
    )


# ---------------------------------------------------------------------------
# AC-16: arch / OS detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "amd64"),
        ("amd64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
    ],
)
def test_detect_arch_normalizes_machine(machine: str, expected: str) -> None:
    """detect_arch must normalize platform.machine() to the Go-style {amd64,arm64}.

    Release artifacts are named with Go arch strings. A wrong mapping would
    compose an artifact URL that 404s.
    """
    import scripts.ensure_tools as m

    with patch("platform.machine", return_value=machine):
        assert m.detect_arch() == expected, (
            f"detect_arch must map {machine!r} -> {expected!r}. Got: {m.detect_arch()!r}"
        )


@pytest.mark.unit
def test_detect_arch_raises_on_unsupported_machine() -> None:
    """detect_arch must fail fast on an architecture with no pinned release artifact."""
    import scripts.ensure_tools as m

    with (
        patch("platform.machine", return_value="riscv64"),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.detect_arch()

    assert "riscv64" in str(exc_info.value), (
        "ProvisioningError must name the unsupported architecture."
    )


# ---------------------------------------------------------------------------
# AC-16: provision_via_binary -- per-tool URL composition (no network)
# ---------------------------------------------------------------------------

# (tool, version, arch) -> expected download URL. Verified live against each
# tool's official release host before being encoded here.
_BINARY_URL_CASES = [
    (
        "terragrunt",
        "1.0.7",
        "arm64",
        "https://github.com/gruntwork-io/terragrunt/releases/download/v1.0.7/terragrunt_linux_arm64",
    ),
    (
        "terragrunt",
        "1.0.7",
        "amd64",
        "https://github.com/gruntwork-io/terragrunt/releases/download/v1.0.7/terragrunt_linux_amd64",
    ),
    (
        "opa",
        "1.17.0",
        "amd64",
        "https://github.com/open-policy-agent/opa/releases/download/v1.17.0/opa_linux_amd64_static",
    ),
    (
        "opa",
        "1.17.0",
        "arm64",
        "https://github.com/open-policy-agent/opa/releases/download/v1.17.0/opa_linux_arm64_static",
    ),
    (
        "tflint",
        "0.63.1",
        "amd64",
        "https://github.com/terraform-linters/tflint/releases/download/v0.63.1/tflint_linux_amd64.zip",
    ),
    (
        "terraform-docs",
        "0.24.0",
        "arm64",
        "https://github.com/terraform-docs/terraform-docs/releases/download/v0.24.0/terraform-docs-v0.24.0-linux-arm64.tar.gz",
    ),
    (
        "golangci-lint",
        "2.12.2",
        "amd64",
        "https://github.com/golangci/golangci-lint/releases/download/v2.12.2/golangci-lint-2.12.2-linux-amd64.tar.gz",
    ),
    (
        "yq",
        "4.53.2",
        "arm64",
        "https://github.com/mikefarah/yq/releases/download/v4.53.2/yq_linux_arm64",
    ),
    (
        "golang",
        "1.26.4",
        "amd64",
        "https://go.dev/dl/go1.26.4.linux-amd64.tar.gz",
    ),
    (
        "actionlint",
        "1.7.12",
        "arm64",
        "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_arm64.tar.gz",
    ),
    (
        "jq",
        "1.8.1",
        "arm64",
        "https://github.com/jqlang/jq/releases/download/jq-1.8.1/jq-linux-arm64",
    ),
    (
        "gh",
        "2.93.0",
        "amd64",
        "https://github.com/cli/cli/releases/download/v2.93.0/gh_2.93.0_linux_amd64.tar.gz",
    ),
    (
        "terraform",
        "1.15.5",
        "arm64",
        "https://releases.hashicorp.com/terraform/1.15.5/terraform_1.15.5_linux_arm64.zip",
    ),
    (
        "trivy",
        "0.71.0",
        "amd64",
        "https://github.com/aquasecurity/trivy/releases/download/v0.71.0/trivy_0.71.0_Linux-64bit.tar.gz",
    ),
    (
        "trivy",
        "0.71.0",
        "arm64",
        "https://github.com/aquasecurity/trivy/releases/download/v0.71.0/trivy_0.71.0_Linux-ARM64.tar.gz",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("tool", "version", "arch", "expected_url"), _BINARY_URL_CASES)
def test_binary_download_spec_composes_official_url(
    tool: str, version: str, arch: str, expected_url: str
) -> None:
    """resolve_binary_spec must compose the exact official artifact URL per tool+arch.

    The URL is the single most fragile part of binary provisioning: a wrong host,
    path, version-interpolation, or arch token produces a 404 and a failed CI run.
    These expectations were verified live against each tool's release host.
    """
    import scripts.ensure_tools as m

    spec = m.resolve_binary_spec(tool, version, arch)
    assert spec.url == expected_url, (
        f"resolve_binary_spec({tool!r}, {version!r}, {arch!r}) must compose "
        f"{expected_url!r}. Got: {spec.url!r}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool", "kind"),
    [
        ("terragrunt", "raw"),
        ("opa", "raw"),
        ("yq", "raw"),
        ("jq", "raw"),
        ("tflint", "zip"),
        ("terraform", "zip"),
        ("terraform-docs", "tar.gz"),
        ("golangci-lint", "tar.gz"),
        ("golang", "tar.gz"),
        ("actionlint", "tar.gz"),
        ("gh", "tar.gz"),
        ("trivy", "tar.gz"),
    ],
)
def test_binary_spec_archive_kind(tool: str, kind: str) -> None:
    """resolve_binary_spec must classify each artifact's archive kind correctly.

    The archive kind selects the extraction path (raw copy / unzip / untar). A
    misclassification would corrupt the installed binary.
    """
    import scripts.ensure_tools as m

    spec = m.resolve_binary_spec(tool, "9.9.9", "amd64")
    assert spec.archive_kind == kind, (
        f"resolve_binary_spec for {tool!r} must report archive_kind {kind!r}. "
        f"Got: {spec.archive_kind!r}"
    )


@pytest.mark.unit
def test_binary_spec_nested_member_for_golangci_lint() -> None:
    """golangci-lint ships its binary inside a versioned nested directory.

    The member path must be 'golangci-lint-<V>-linux-<arch>/golangci-lint' or the
    extractor would look for the binary at the archive root and fail.
    """
    import scripts.ensure_tools as m

    spec = m.resolve_binary_spec("golangci-lint", "2.12.2", "arm64")
    assert spec.member == "golangci-lint-2.12.2-linux-arm64/golangci-lint", (
        f"golangci-lint member path must be the nested versioned dir. Got: {spec.member!r}"
    )


@pytest.mark.unit
def test_binary_spec_nested_member_for_golang_and_gh() -> None:
    """go and gh ship their binary under a nested directory inside the tarball."""
    import scripts.ensure_tools as m

    go_spec = m.resolve_binary_spec("golang", "1.26.4", "amd64")
    assert go_spec.member == "go/bin/go", (
        f"golang member path must be 'go/bin/go'. Got: {go_spec.member!r}"
    )
    gh_spec = m.resolve_binary_spec("gh", "2.93.0", "amd64")
    assert gh_spec.member == "gh_2.93.0_linux_amd64/bin/gh", (
        f"gh member path must be the nested bin/gh. Got: {gh_spec.member!r}"
    )


@pytest.mark.unit
def test_golang_uses_tree_extract_mode_others_use_member() -> None:
    """golang must extract as a whole tree (GOROOT); other tools use single-member mode.

    The go binary resolves GOROOT relative to its real path; copying only go/bin/go
    away from its sibling lib/src tree breaks `go version`. So golang must be marked
    'tree' while every other tool stays 'member'.
    """
    import scripts.ensure_tools as m

    go_spec = m.resolve_binary_spec("golang", "1.26.4", "arm64")
    assert go_spec.extract_mode == "tree", (
        f"golang must use extract_mode 'tree'. Got: {go_spec.extract_mode!r}"
    )
    for other in ("terragrunt", "gh", "tflint", "trivy"):
        spec = m.resolve_binary_spec(other, "9.9.9", "arm64")
        assert spec.extract_mode == "member", (
            f"{other!r} must use extract_mode 'member'. Got: {spec.extract_mode!r}"
        )


@pytest.mark.unit
def test_extract_tree_and_symlink_installs_symlink_to_real_binary(
    tmp_path: pathlib.Path,
) -> None:
    """Tree extraction must symlink install_dir/<binary> at the extracted real binary.

    A symlink (not a copy) preserves the binary's resolution of its sibling tree
    (GOROOT for go). The installed entry must be a symlink that resolves to the
    extracted go/bin/go path and the extracted binary must be executable.
    """
    import io
    import tarfile

    import scripts.ensure_tools as m

    # Build a tarball with the same nested layout as the go distribution:
    # go/bin/go (the binary) plus a sibling go/VERSION file (the "tree").
    archive_path = tmp_path / "go.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as tar:
        bin_bytes = b"#!/bin/sh\necho stub-go\n"
        info = tarfile.TarInfo("go/bin/go")
        info.size = len(bin_bytes)
        tar.addfile(info, io.BytesIO(bin_bytes))
        ver_bytes = b"go9.9.9\n"
        vinfo = tarfile.TarInfo("go/VERSION")
        vinfo.size = len(ver_bytes)
        tar.addfile(vinfo, io.BytesIO(ver_bytes))

    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    spec = m.resolve_binary_spec("golang", "9.9.9", "arm64")

    result = m._extract_and_install(spec, archive_path, install_dir)

    assert result == install_dir / "go", f"installed path must be install_dir/go. Got: {result!r}"
    assert result.is_symlink(), "tree mode must install a symlink, not a copy."
    real = result.resolve()
    assert real.name == "go" and real.parent.name == "bin", (
        f"symlink must resolve to the extracted go/bin/go. Got: {real!r}"
    )
    # The sibling tree (VERSION) must be present next to the real binary so GOROOT
    # resolution would succeed.
    assert (real.parent.parent / "VERSION").is_file(), (
        "tree extraction must keep the sibling tree intact next to the binary."
    )
    assert os.access(real, os.X_OK), "extracted real binary must be executable."


@pytest.mark.unit
def test_resolve_binary_spec_raises_on_unknown_tool() -> None:
    """resolve_binary_spec must fail fast for a tool with no binary spec."""
    import scripts.ensure_tools as m

    with pytest.raises((ValueError, KeyError)):
        m.resolve_binary_spec("not-a-real-tool", "1.0.0", "amd64")


@pytest.mark.unit
def test_provision_via_binary_downloads_extracts_installs_and_verifies() -> None:
    """provision_via_binary must download -> extract -> install onto PATH -> verify.

    The full provisioning sequence must run end to end with no network: the
    download and extraction are mocked, and the post-install version verification
    must be invoked so a mismatched download cannot pass silently.
    """
    import scripts.ensure_tools as m

    calls: dict[str, object] = {}

    def fake_download(url: str, dest: pathlib.Path) -> None:
        calls["download_url"] = url
        dest.write_bytes(b"fake-artifact")

    def fake_extract_and_install(spec, archive_path, install_dir):  # type: ignore[no-untyped-def]
        calls["installed_tool"] = spec.binary_name
        calls["install_dir"] = install_dir
        target = install_dir / spec.binary_name
        target.write_bytes(b"#!/bin/sh\n")
        return target

    with (
        patch.object(m, "detect_arch", return_value="arm64"),
        patch.object(m, "_download", side_effect=fake_download),
        patch.object(m, "_extract_and_install", side_effect=fake_extract_and_install),
        patch.object(m, "is_version_installed", return_value=True) as mock_verify,
    ):
        m.provision_via_binary("terragrunt", "1.0.7")

    assert calls["download_url"] == (
        "https://github.com/gruntwork-io/terragrunt/releases/download/v1.0.7/terragrunt_linux_arm64"
    ), f"provision_via_binary must download the composed URL. Got: {calls.get('download_url')!r}"
    assert calls["installed_tool"] == "terragrunt"
    mock_verify.assert_called_once_with("terragrunt", "1.0.7")


@pytest.mark.unit
def test_provision_via_binary_raises_when_post_install_version_mismatches() -> None:
    """provision_via_binary must raise ProvisioningError when the installed version != pinned.

    Fail-fast: a download that yields the wrong version (corrupt artifact, stale
    cache, wrong URL) must not be accepted silently.
    """
    import scripts.ensure_tools as m

    def fake_extract_and_install(spec, archive_path, install_dir):  # type: ignore[no-untyped-def]
        target = install_dir / spec.binary_name
        target.write_bytes(b"#!/bin/sh\n")
        return target

    with (
        patch.object(m, "detect_arch", return_value="arm64"),
        patch.object(m, "_download", lambda url, dest: dest.write_bytes(b"x")),
        patch.object(m, "_extract_and_install", side_effect=fake_extract_and_install),
        patch.object(m, "is_version_installed", return_value=False),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.provision_via_binary("terragrunt", "1.0.7")

    assert "terragrunt" in str(exc_info.value)
    assert "1.0.7" in str(exc_info.value)


@pytest.mark.unit
def test_provision_via_binary_raises_provisioning_error_on_download_failure() -> None:
    """provision_via_binary must wrap a download failure as ProvisioningError (fail-fast)."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "detect_arch", return_value="amd64"),
        patch.object(m, "_download", side_effect=OSError("connection reset")),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.provision_via_binary("opa", "1.17.0")

    assert "opa" in str(exc_info.value), "ProvisioningError must name the tool on download failure."


@pytest.mark.unit
def test_install_dir_honours_env_override(monkeypatch, tmp_path: pathlib.Path) -> None:
    """resolve_install_dir must honour the configured env var and create the directory.

    The install dir must be configurable (no hardcoded path) and must be created
    if absent so the extractor can write into it.
    """
    import scripts.ensure_tools as m

    target = tmp_path / "custom-bin"
    monkeypatch.setenv(m.INSTALL_DIR_ENV, str(target))
    resolved = m.resolve_install_dir()
    assert resolved == target, (
        f"resolve_install_dir must use ${m.INSTALL_DIR_ENV}. Got: {resolved!r}"
    )
    assert resolved.is_dir(), "resolve_install_dir must create the install directory if absent."


@pytest.mark.unit
def test_install_dir_defaults_when_env_unset(monkeypatch, tmp_path: pathlib.Path) -> None:
    """resolve_install_dir must fall back to ~/.local/bin when the env var is unset."""
    import scripts.ensure_tools as m

    monkeypatch.delenv(m.INSTALL_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved = m.resolve_install_dir()
    assert resolved == tmp_path / ".local" / "bin", (
        f"resolve_install_dir default must be ~/.local/bin. Got: {resolved!r}"
    )


# ---------------------------------------------------------------------------
# AC-16: _download invokes curl over HTTPS (no network in unit tests)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_download_invokes_curl_over_https_to_dest(tmp_path: pathlib.Path) -> None:
    """_download must invoke curl by absolute path with HTTPS-only flags writing to dest.

    Invoking curl by an absolute path (not 'curl') avoids B607, and --proto =https
    enforces the scheme at the transport layer in addition to the prefix guard.
    """
    import scripts.ensure_tools as m

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return MagicMock(returncode=0)

    dest = tmp_path / "artifact"
    url = "https://go.dev/dl/go1.26.4.linux-arm64.tar.gz"
    with (
        patch("shutil.which", return_value="/usr/bin/curl"),
        patch("subprocess.run", side_effect=fake_run),
    ):
        m._download(url, dest)

    assert len(captured) == 1, "exactly one curl invocation expected."
    argv = captured[0]
    assert argv[0] == "/usr/bin/curl", (
        f"curl must be invoked by its absolute path (avoids B607). Got: {argv[0]!r}"
    )
    assert "--proto" in argv and argv[argv.index("--proto") + 1] == "=https", (
        "curl must restrict the protocol to https via --proto =https."
    )
    assert "--location" in argv, "curl must follow redirects (release assets redirect)."
    assert str(dest) in argv, "curl must write to the dest path via --output."
    assert url in argv, "the composed URL must be passed to curl."
    # Transient third-party-host failures (go.dev exit 92, connection resets, 5xx) must
    # be retried internally by curl so a flaky download does not fail the whole run.
    assert "--retry" in argv and argv[argv.index("--retry") + 1] == "3", (
        "curl must retry the default number of times (3) on transient failures."
    )
    assert "--retry-connrefused" in argv, "curl must retry on connection refused."
    assert "--retry-all-errors" in argv, (
        "curl must retry on all errors incl. non-transient-by-default exit codes (e.g. 92)."
    )


@pytest.mark.unit
def test_download_retries_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """_download_retries returns the default, and honours a valid env override."""
    import scripts.ensure_tools as m

    monkeypatch.delenv(m.DOWNLOAD_RETRIES_ENV, raising=False)
    assert m._download_retries() == m.DEFAULT_DOWNLOAD_RETRIES == 3
    monkeypatch.setenv(m.DOWNLOAD_RETRIES_ENV, "7")
    assert m._download_retries() == 7
    monkeypatch.setenv(m.DOWNLOAD_RETRIES_ENV, "0")
    assert m._download_retries() == 0


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["abc", "-1", "3.5"])
def test_download_retries_rejects_invalid(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """_download_retries fails fast on a non-integer or negative retry count."""
    import scripts.ensure_tools as m

    monkeypatch.setenv(m.DOWNLOAD_RETRIES_ENV, bad)
    with pytest.raises(m.ProvisioningError):
        m._download_retries()


@pytest.mark.unit
def test_download_uses_env_retry_count(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_download must pass the env-configured retry count to curl."""
    import scripts.ensure_tools as m

    monkeypatch.setenv(m.DOWNLOAD_RETRIES_ENV, "5")
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return MagicMock(returncode=0)

    with (
        patch("shutil.which", return_value="/usr/bin/curl"),
        patch("subprocess.run", side_effect=fake_run),
    ):
        m._download("https://go.dev/dl/go1.26.4.linux-arm64.tar.gz", tmp_path / "artifact")

    argv = captured[0]
    assert argv[argv.index("--retry") + 1] == "5", "curl must use the env-configured retry count."


@pytest.mark.unit
def test_download_rejects_non_https_url(tmp_path: pathlib.Path) -> None:
    """_download must refuse a non-HTTPS URL (scheme-downgrade defense, fail-fast)."""
    import scripts.ensure_tools as m

    with (
        patch("shutil.which", return_value="/usr/bin/curl"),
        pytest.raises(ValueError),
    ):
        m._download("http://example.com/artifact", tmp_path / "artifact")


@pytest.mark.unit
def test_download_raises_file_not_found_when_curl_absent(tmp_path: pathlib.Path) -> None:
    """_download must raise FileNotFoundError when curl is not on PATH."""
    import scripts.ensure_tools as m

    with patch("shutil.which", return_value=None), pytest.raises(FileNotFoundError):
        m._download("https://example.com/artifact", tmp_path / "artifact")


@pytest.mark.unit
def test_download_timeout_defaults_when_env_unset(monkeypatch) -> None:
    """_download_timeout_seconds must return the documented default when the env is unset."""
    import scripts.ensure_tools as m

    monkeypatch.delenv(m.DOWNLOAD_TIMEOUT_ENV, raising=False)
    assert m._download_timeout_seconds() == m.DEFAULT_DOWNLOAD_TIMEOUT_SECONDS


@pytest.mark.unit
def test_download_timeout_reads_env_override(monkeypatch) -> None:
    """_download_timeout_seconds must read the configured timeout from the environment."""
    import scripts.ensure_tools as m

    monkeypatch.setenv(m.DOWNLOAD_TIMEOUT_ENV, "45")
    assert m._download_timeout_seconds() == 45


@pytest.mark.unit
def test_download_timeout_raises_on_non_integer_env(monkeypatch) -> None:
    """_download_timeout_seconds must fail fast when the env value is not an integer."""
    import scripts.ensure_tools as m

    monkeypatch.setenv(m.DOWNLOAD_TIMEOUT_ENV, "not-a-number")
    with pytest.raises(m.ProvisioningError):
        m._download_timeout_seconds()


# ---------------------------------------------------------------------------
# AC-16: _extract_and_install -- member-mode extraction (raw / zip / tar.gz)
# ---------------------------------------------------------------------------


def _make_spec(tool: str, archive_kind: str, member: str | None):
    """Build a minimal member-mode BinarySpec for extraction tests."""
    import scripts.ensure_tools as m

    return m.BinarySpec(
        tool=tool,
        binary_name=tool,
        url="https://example.com/artifact",
        archive_kind=archive_kind,
        member=member,
        extract_mode="member",
    )


@pytest.mark.unit
def test_extract_and_install_raw_copies_and_chmods(tmp_path: pathlib.Path) -> None:
    """Raw extraction must copy the downloaded file to install_dir and make it executable."""
    import scripts.ensure_tools as m

    archive = tmp_path / "artifact"
    archive.write_bytes(b"raw-binary-bytes")
    install_dir = tmp_path / "bin"
    install_dir.mkdir()

    result = m._extract_and_install(_make_spec("yq", "raw", None), archive, install_dir)

    assert result == install_dir / "yq"
    assert result.read_bytes() == b"raw-binary-bytes", "raw artifact must be copied byte-for-byte."
    assert os.access(result, os.X_OK), "installed raw binary must be executable."


@pytest.mark.unit
def test_extract_and_install_zip_extracts_named_member(tmp_path: pathlib.Path) -> None:
    """Zip extraction must pull the named member out of the archive onto install_dir."""
    import zipfile

    import scripts.ensure_tools as m

    archive = tmp_path / "tflint.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tflint", b"tflint-binary")
    install_dir = tmp_path / "bin"
    install_dir.mkdir()

    result = m._extract_and_install(_make_spec("tflint", "zip", "tflint"), archive, install_dir)

    assert result.read_bytes() == b"tflint-binary"
    assert os.access(result, os.X_OK)


@pytest.mark.unit
def test_extract_and_install_zip_raises_when_member_absent(tmp_path: pathlib.Path) -> None:
    """Zip extraction must raise ProvisioningError when the named member is missing."""
    import zipfile

    import scripts.ensure_tools as m

    archive = tmp_path / "tflint.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("something-else", b"x")
    install_dir = tmp_path / "bin"
    install_dir.mkdir()

    with pytest.raises(m.ProvisioningError):
        m._extract_and_install(_make_spec("tflint", "zip", "tflint"), archive, install_dir)


@pytest.mark.unit
def test_extract_and_install_tar_extracts_nested_member(tmp_path: pathlib.Path) -> None:
    """tar.gz extraction must pull a nested member out onto install_dir/binary_name."""
    import io
    import tarfile

    import scripts.ensure_tools as m

    archive = tmp_path / "golangci.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"golangci-binary"
        info = tarfile.TarInfo("golangci-lint-1.0.0-linux-amd64/golangci-lint")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    install_dir = tmp_path / "bin"
    install_dir.mkdir()

    spec = _make_spec("golangci-lint", "tar.gz", "golangci-lint-1.0.0-linux-amd64/golangci-lint")
    result = m._extract_and_install(spec, archive, install_dir)

    assert result == install_dir / "golangci-lint"
    assert result.read_bytes() == b"golangci-binary"
    assert os.access(result, os.X_OK)


@pytest.mark.unit
def test_extract_and_install_tar_raises_when_member_absent(tmp_path: pathlib.Path) -> None:
    """tar.gz extraction must raise ProvisioningError when the named member is missing."""
    import io
    import tarfile

    import scripts.ensure_tools as m

    archive = tmp_path / "x.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"x"
        info = tarfile.TarInfo("not-the-member")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    install_dir = tmp_path / "bin"
    install_dir.mkdir()

    with pytest.raises(m.ProvisioningError):
        m._extract_and_install(
            _make_spec("actionlint", "tar.gz", "actionlint"), archive, install_dir
        )


@pytest.mark.unit
def test_extract_tree_raises_when_member_absent_after_extract(tmp_path: pathlib.Path) -> None:
    """Tree extraction must raise ProvisioningError when the member is absent post-extract."""
    import io
    import tarfile

    import scripts.ensure_tools as m

    archive = tmp_path / "go.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"only-version"
        info = tarfile.TarInfo("go/VERSION")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    install_dir = tmp_path / "bin"
    install_dir.mkdir()

    spec = m.resolve_binary_spec("golang", "1.26.4", "amd64")
    with pytest.raises(m.ProvisioningError):
        m._extract_and_install(spec, archive, install_dir)


@pytest.mark.unit
def test_provision_via_binary_real_extract_member_mode(tmp_path: pathlib.Path, monkeypatch) -> None:
    """provision_via_binary must run the real member-mode extractor (download mocked).

    Only the network fetch is faked; resolve_binary_spec, _extract_and_install, the
    chmod, and the post-install verification all run for real, proving the wiring.
    """
    import scripts.ensure_tools as m

    install_dir = tmp_path / "bin"
    monkeypatch.setenv(m.INSTALL_DIR_ENV, str(install_dir))

    def fake_download(url: str, dest: pathlib.Path) -> None:
        dest.write_bytes(b"#!/bin/sh\necho stub\n")

    with (
        patch.object(m, "detect_arch", return_value="arm64"),
        patch.object(m, "_download", side_effect=fake_download),
        patch.object(m, "is_version_installed", return_value=True),
    ):
        m.provision_via_binary("terragrunt", "1.0.7")

    installed = install_dir / "terragrunt"
    assert installed.is_file(), "the raw binary must be installed onto the install dir."
    assert os.access(installed, os.X_OK), "the installed binary must be executable."


# ---------------------------------------------------------------------------
# AC-16: ensure_tool with binary method
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ensure_tool_calls_binary_provisioner_when_version_missing() -> None:
    """ensure_tool must invoke provision_via_binary when a binary-method tool is absent."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=False),
        patch.object(m, "get_provisioning_method", return_value="binary"),
        patch.object(m, "provision_via_binary") as mock_binary,
    ):
        m.ensure_tool("terragrunt", "0.67.0")

    mock_binary.assert_called_once_with("terragrunt", "0.67.0")


@pytest.mark.unit
def test_ensure_tool_raises_provisioning_error_on_binary_failure() -> None:
    """ensure_tool must re-raise ProvisioningError from provision_via_binary."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=False),
        patch.object(m, "get_provisioning_method", return_value="binary"),
        patch.object(
            m,
            "provision_via_binary",
            side_effect=m.ProvisioningError("terragrunt", "0.67.0", "download failed"),
        ),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.ensure_tool("terragrunt", "0.67.0")

    assert "terragrunt" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AC-16: main() with missing .tool-versions and ValueError path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_exits_nonzero_when_tool_versions_missing() -> None:
    """main() must exit non-zero with ERROR: when .tool-versions is not found."""
    import scripts.ensure_tools as m

    with (
        patch.object(
            m,
            "parse_tool_versions",
            side_effect=FileNotFoundError("file not found"),
        ),
        patch("sys.exit") as mock_exit,
        patch("sys.stderr"),
    ):
        m.main("/nonexistent/.tool-versions")

    mock_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_main_exits_nonzero_on_value_error_from_ensure_tool(tmp_path: pathlib.Path) -> None:
    """main() must exit non-zero with ERROR: when ensure_tool raises ValueError."""
    tv_file = tmp_path / ".tool-versions"
    tv_file.write_text("unknown-tool-xyz 1.0.0\n")

    import scripts.ensure_tools as m

    with (
        patch.object(m, "parse_tool_versions", return_value={"unknown-tool-xyz": "1.0.0"}),
        patch.object(m, "ensure_tool", side_effect=ValueError("Unknown tool")),
        patch("sys.exit") as mock_exit,
        patch("sys.stderr"),
    ):
        m.main(str(tv_file))

    mock_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_ensure_tool_raises_provisioning_error_on_unexpected_binary_exception() -> None:
    """ensure_tool must wrap unexpected exceptions from binary provisioning as ProvisioningError."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=False),
        patch.object(m, "get_provisioning_method", return_value="binary"),
        patch.object(
            m,
            "provision_via_binary",
            side_effect=RuntimeError("unexpected"),
        ),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.ensure_tool("terragrunt", "0.67.0")

    assert "terragrunt" in str(exc_info.value)


@pytest.mark.unit
def test_ensure_tool_raises_provisioning_error_for_unknown_method() -> None:
    """ensure_tool must raise ProvisioningError when method is an unknown string."""
    import scripts.ensure_tools as m

    with (
        patch.object(m, "is_version_installed", return_value=False),
        patch.object(m, "get_provisioning_method", return_value="unknown-method"),
        pytest.raises(m.ProvisioningError) as exc_info,
    ):
        m.ensure_tool("sometool", "1.0.0")

    assert "Unknown provisioning method" in str(exc_info.value) or "sometool" in str(exc_info.value)


@pytest.mark.unit
def test_main_uses_repo_root_tool_versions_when_no_path_given() -> None:
    """main() with no argument must use the repo-root .tool-versions by default."""
    import scripts.ensure_tools as m

    parsed_paths: list[pathlib.Path] = []

    def capture_parse(path: pathlib.Path) -> dict[str, str]:
        parsed_paths.append(path)
        return {}

    with patch.object(m, "parse_tool_versions", side_effect=capture_parse):
        m.main()

    assert len(parsed_paths) == 1, "parse_tool_versions must be called exactly once."
    assert parsed_paths[0].name == ".tool-versions", (
        "Default path must resolve to a file named '.tool-versions'."
    )
    # The default .tool-versions must sit at the repository root. Assert this via
    # stable root markers rather than the checkout directory name, which is
    # environment-specific (the checkout dir may be named anything).
    repo_root = parsed_paths[0].parent
    assert (repo_root / "pyproject.toml").exists(), (
        f"Default .tool-versions must be in the repository root, but no pyproject.toml "
        f"marker was found next to it at {repo_root}."
    )
    assert (repo_root / ".git").exists(), (
        f"Default .tool-versions must be in the repository root, but no .git marker "
        f"was found next to it at {repo_root}."
    )
