"""Toolchain provisioner -- reads pinned versions from .tool-versions and ensures
each tool is installed at the correct version by downloading the official pinned
release binary.

D28 policy (no external version manager, no hardcoded versions):
- Versions are always read from .tool-versions at runtime.
- No external version-manager code path exists anywhere in this module.
- Per-tool routing is driven by config maps (TOOL_CONFIG, VERSION_PROBE_ARGS,
  resolve_binary_spec), never embedded if/elif logic.

Provisioning strategy per tool:
- binary     : terragrunt, opa, tflint, terraform-docs, golangci-lint, yq, golang,
               actionlint, jq, gh, terraform, trivy. Each is installed from its
               official, pinned, OS/arch-specific release artifact.
- go-install : govulncheck. Published as a Go module command (not a single official
               OS/arch binary), so it is built from source at the pinned tag with the
               pinned go toolchain via `go install <pkg>@v<version>` into GOPATH/bin.
               Ordered after golang in .tool-versions so `go` is already provisioned.
- uv         : pre-commit (uv-managed, not externally provisioned).
- skip       : python, uv (handled by setup-python/setup-uv in CI; devcontainer
               locally).

Why binary (not apt) for jq/gh/terraform/trivy:
  Stock ubuntu-latest (noble) cannot pin the .tool-versions semver via apt. The
  Ubuntu repo carries an older jq than the pinned one, and the HashiCorp/Aqua/GitHub
  apt version strings carry distro suffixes (e.g. a "-1" build suffix) that never
  match a bare semver -- so `apt-get install pkg=<semver>` fails to resolve. Each of
  these tools publishes an official, exactly-pinned binary release, which is the
  reliable, OS/arch-agnostic, D28-consistent provisioning path.

Idempotency: verify-then-provision. If the installed version matches the pinned
version, no provisioning is performed.

Fail-fast: any provisioning failure raises ProvisioningError immediately with an
actionable message naming the tool, expected version, and remediation step.
"""

from __future__ import annotations

import os
import pathlib
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Per-tool routing config maps (D28 split -- drives routing, never hardcoded logic)
# ---------------------------------------------------------------------------

# Tools whose binary name on PATH differs from the .tool-versions key name.
BINARY_NAME_MAP: Final[dict[str, str]] = {
    "golang": "go",
}

# Per-tool version-probe argv suffix. Default is ["--version"]; golang and opa
# override to the `version` subcommand because their CLIs reject a --version flag
# (go: 'flag provided but not defined: -version'; opa: 'Error: unknown flag:
# --version'). Both expose their version through a `version` subcommand instead.
# govulncheck overrides to the single-dash `-version` flag (it rejects --version).
DEFAULT_VERSION_PROBE_ARGS: Final[list[str]] = ["--version"]
VERSION_PROBE_ARGS: Final[dict[str, list[str]]] = {
    "golang": ["version"],
    "opa": ["version"],
    "govulncheck": ["-version"],
}

# Regex for extracting the version token from probe output. Tolerates an optional
# leading "v" (terragrunt/yq/terraform) or "go" (go version) prefix, and refuses
# to start mid-number so a "goX.Y.Z" token yields "X.Y.Z" rather than "Y.Z".
_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])(?:v|go)?(\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.+-]+)?)"
)

# Per-tool version-extraction override. A tool whose probe output leads with an
# unrelated version (e.g. govulncheck's `-version` prints the Go compiler version
# on the first line and the scanner version on a later `Scanner: govulncheck@vX.Y.Z`
# line) needs a targeted pattern so the generic _VERSION_PATTERN does not latch
# onto the wrong token. Each pattern must expose the bare semver in group 1.
VERSION_EXTRACT_PATTERN: Final[dict[str, re.Pattern[str]]] = {
    "govulncheck": re.compile(r"Scanner:\s*govulncheck@v(\d+\.\d+\.\d+)"),
}

TOOL_CONFIG: Final[dict[str, str]] = {
    # official pinned binary tools
    "terragrunt": "binary",
    "opa": "binary",
    "tflint": "binary",
    "terraform-docs": "binary",
    "golangci-lint": "binary",
    "yq": "binary",
    "golang": "binary",
    "actionlint": "binary",
    "jq": "binary",
    "gh": "binary",
    "terraform": "binary",
    "trivy": "binary",
    # go-install tools (provisioned via `go install <pkg>@v<version>` into GOPATH/bin)
    "govulncheck": "go-install",
    # uv-managed tools (not externally provisioned)
    "pre-commit": "uv",
    # skipped -- provided by setup-python/setup-uv (CI) or devcontainer (local)
    "python": "skip",
    "uv": "skip",
}

# Go module path for each go-install tool. govulncheck has no single official
# OS/arch binary release (it is published as a Go module command), so it is built
# and installed from source with the pinned go toolchain via `go install`. The
# version is read from .tool-versions at runtime (no hardcoded version here).
GO_INSTALL_PACKAGE_MAP: Final[dict[str, str]] = {
    "govulncheck": "golang.org/x/vuln/cmd/govulncheck",
}

# Environment variable that overrides the install directory. When unset, the
# install directory defaults to ~/.local/bin.
INSTALL_DIR_ENV: Final[str] = "ENSURE_TOOLS_INSTALL_DIR"

# Environment variable that overrides the per-artifact download timeout (seconds).
DOWNLOAD_TIMEOUT_ENV: Final[str] = "ENSURE_TOOLS_DOWNLOAD_TIMEOUT"
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS: Final[int] = 300

# Environment variable that overrides the number of download retries. Every artifact
# is fetched from a third-party host (go.dev, GitHub/HashiCorp releases) whose transient
# failures (curl exit 92 HTTP/2 stream error, connection resets, sporadic 5xx) otherwise
# fail the whole provisioning run. curl retries internally with exponential backoff.
DOWNLOAD_RETRIES_ENV: Final[str] = "ENSURE_TOOLS_DOWNLOAD_RETRIES"
DEFAULT_DOWNLOAD_RETRIES: Final[int] = 3

# Per-tool arch token override. The default arch token is the Go-style arch
# ("amd64"/"arm64"); trivy uses its own naming scheme.
_TRIVY_ARCH_TOKEN: Final[dict[str, str]] = {
    "amd64": "64bit",
    "arm64": "ARM64",
}

# Tools whose archive must be extracted as a whole tree (not a single member) and
# whose binary is then symlinked onto the install dir. The go distribution resolves
# GOROOT relative to the real path of its binary, so copying only go/bin/go away
# from its sibling lib/src tree breaks `go version`.
_TREE_EXTRACT_TOOLS: Final[frozenset[str]] = frozenset({"golang"})


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class ProvisioningError(Exception):
    """Raised when a tool cannot be provisioned at the required version.

    Attributes:
        tool: Name of the tool that failed to provision.
        expected_version: The pinned version that was required.
        detail: The underlying failure message.
    """

    def __init__(self, tool: str, expected_version: str, detail: str) -> None:
        self.tool = tool
        self.expected_version = expected_version
        self.detail = detail
        super().__init__(
            f"Failed to provision {tool!r} at version {expected_version!r}: {detail}. "
            f"Remediation: check the release source for {tool!r} or install manually "
            f"following the D28 provisioning policy."
        )


# ---------------------------------------------------------------------------
# Binary release specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinarySpec:
    """Fully-resolved download/extract/install plan for one binary tool.

    Attributes:
        tool: Tool name as it appears in .tool-versions.
        binary_name: Name the installed executable must have on PATH.
        url: Exact official artifact URL for the resolved version + arch.
        archive_kind: One of 'raw', 'zip', 'tar.gz'. Selects the extractor.
        member: For archives, the path of the binary inside the archive. None for
            'raw' artifacts (the download is the binary itself).
        extract_mode: 'member' (default) copies only the single member onto the
            install dir; 'tree' extracts the whole archive next to the install dir
            and symlinks the member onto the install dir. 'tree' is required for the
            go distribution, whose binary resolves GOROOT relative to its real
            location and fails if copied away from its sibling lib/src tree.
    """

    tool: str
    binary_name: str
    url: str
    archive_kind: str
    member: str | None
    extract_mode: str = "member"


def resolve_binary_spec(tool: str, version: str, arch: str) -> BinarySpec:
    """Compose the official download/extract/install plan for a binary tool.

    The URL templates, archive kinds, and nested member paths below were each
    verified live against the tool's official release host.

    Args:
        tool: Tool name as it appears in .tool-versions.
        version: Pinned version (bare semver, no leading 'v').
        arch: Normalized arch token ('amd64' or 'arm64').

    Returns:
        A fully-resolved BinarySpec.

    Raises:
        ValueError: If the tool has no binary specification.
    """
    binary_name = BINARY_NAME_MAP.get(tool, tool)
    trivy_arch = _TRIVY_ARCH_TOKEN.get(arch, arch)
    gh_rel = "https://github.com"

    # Each entry: (url, archive_kind, member). Composed lazily so only the
    # requested tool's template is evaluated.
    specs: dict[str, tuple[str, str, str | None]] = {
        "terragrunt": (
            f"{gh_rel}/gruntwork-io/terragrunt/releases/download/"
            f"v{version}/terragrunt_linux_{arch}",
            "raw",
            None,
        ),
        "opa": (
            f"{gh_rel}/open-policy-agent/opa/releases/download/v{version}/opa_linux_{arch}_static",
            "raw",
            None,
        ),
        "yq": (
            f"{gh_rel}/mikefarah/yq/releases/download/v{version}/yq_linux_{arch}",
            "raw",
            None,
        ),
        "jq": (
            f"{gh_rel}/jqlang/jq/releases/download/jq-{version}/jq-linux-{arch}",
            "raw",
            None,
        ),
        "tflint": (
            f"{gh_rel}/terraform-linters/tflint/releases/download/"
            f"v{version}/tflint_linux_{arch}.zip",
            "zip",
            "tflint",
        ),
        "terraform": (
            f"https://releases.hashicorp.com/terraform/{version}/"
            f"terraform_{version}_linux_{arch}.zip",
            "zip",
            "terraform",
        ),
        "terraform-docs": (
            f"{gh_rel}/terraform-docs/terraform-docs/releases/download/"
            f"v{version}/terraform-docs-v{version}-linux-{arch}.tar.gz",
            "tar.gz",
            "terraform-docs",
        ),
        "golangci-lint": (
            f"{gh_rel}/golangci/golangci-lint/releases/download/"
            f"v{version}/golangci-lint-{version}-linux-{arch}.tar.gz",
            "tar.gz",
            f"golangci-lint-{version}-linux-{arch}/golangci-lint",
        ),
        "golang": (
            f"https://go.dev/dl/go{version}.linux-{arch}.tar.gz",
            "tar.gz",
            "go/bin/go",
        ),
        "actionlint": (
            f"{gh_rel}/rhysd/actionlint/releases/download/"
            f"v{version}/actionlint_{version}_linux_{arch}.tar.gz",
            "tar.gz",
            "actionlint",
        ),
        "gh": (
            f"{gh_rel}/cli/cli/releases/download/v{version}/gh_{version}_linux_{arch}.tar.gz",
            "tar.gz",
            f"gh_{version}_linux_{arch}/bin/gh",
        ),
        "trivy": (
            f"{gh_rel}/aquasecurity/trivy/releases/download/"
            f"v{version}/trivy_{version}_Linux-{trivy_arch}.tar.gz",
            "tar.gz",
            "trivy",
        ),
    }

    if tool not in specs:
        raise ValueError(
            f"No binary release specification for tool {tool!r}. "
            "Add a spec entry to resolve_binary_spec in scripts/ensure_tools.py "
            "following the D28 official-pinned-binary policy."
        )

    url, archive_kind, member = specs[tool]
    extract_mode = "tree" if tool in _TREE_EXTRACT_TOOLS else "member"
    return BinarySpec(
        tool=tool,
        binary_name=binary_name,
        url=url,
        archive_kind=archive_kind,
        member=member,
        extract_mode=extract_mode,
    )


# ---------------------------------------------------------------------------
# .tool-versions parser
# ---------------------------------------------------------------------------


def parse_tool_versions(path: pathlib.Path) -> dict[str, str]:
    """Parse a .tool-versions file and return a mapping of tool name to version.

    Args:
        path: Absolute path to the .tool-versions file.

    Returns:
        Mapping of {tool_name: pinned_version}.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
    """
    if not path.exists():
        raise FileNotFoundError(
            f".tool-versions not found at {path}. "
            "The version file must be present before provisioning can proceed."
        )
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def get_provisioning_method(tool: str) -> str:
    """Return the provisioning method for the given tool name.

    The method is read from TOOL_CONFIG -- a config map, not embedded logic.

    Args:
        tool: Tool name as it appears in .tool-versions.

    Returns:
        One of: 'binary', 'uv', 'skip'.

    Raises:
        ValueError: If the tool is not present in TOOL_CONFIG.
    """
    if tool not in TOOL_CONFIG:
        raise ValueError(
            f"Unknown tool {tool!r}: no provisioning method configured in TOOL_CONFIG. "
            f"Add an entry for {tool!r} to scripts/ensure_tools.py TOOL_CONFIG "
            f"following the D28 per-tool split policy."
        )
    return TOOL_CONFIG[tool]


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def detect_arch() -> str:
    """Return the normalized Go-style architecture token for the host.

    Returns:
        'amd64' or 'arm64'.

    Raises:
        ProvisioningError: If the host architecture has no pinned release artifact.
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise ProvisioningError(
        "toolchain",
        "unknown",
        f"Unsupported host architecture {machine!r}. Pinned release artifacts are "
        "published only for amd64 and arm64.",
    )


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------


def get_installed_version(tool: str) -> str:
    """Return the currently installed version string for the given tool.

    Runs the tool's version-probe command (default `<tool> --version`; golang uses
    `go version`) and parses the version token from the output.

    Args:
        tool: Tool name as it appears in .tool-versions.

    Returns:
        Installed version string (e.g. a semver like '1.22.0').

    Raises:
        FileNotFoundError: If the tool binary is not on PATH.
        ProvisioningError: If the version cannot be parsed from the output.
    """
    binary = BINARY_NAME_MAP.get(tool, tool)

    if shutil.which(binary) is None:
        raise FileNotFoundError(
            f"Tool binary {binary!r} not found on PATH. "
            "The tool is not installed or is not in PATH."
        )

    probe_args = VERSION_PROBE_ARGS.get(tool, DEFAULT_VERSION_PROBE_ARGS)

    try:
        result = subprocess.run(
            [binary, *probe_args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        output = (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired as exc:
        raise ProvisioningError(
            tool,
            "unknown",
            f"'{binary} {' '.join(probe_args)}' timed out after 10 seconds",
        ) from exc

    pattern = VERSION_EXTRACT_PATTERN.get(tool, _VERSION_PATTERN)
    match = pattern.search(output)
    if not match:
        raise ProvisioningError(
            tool,
            "unknown",
            f"Could not parse version from '{binary} {' '.join(probe_args)}' output: {output!r}",
        )
    return match.group(1)


def is_version_installed(tool: str, expected_version: str) -> bool:
    """Return True if the installed version of tool matches expected_version.

    Args:
        tool: Tool name.
        expected_version: The pinned version required.

    Returns:
        True if installed version matches; False if not installed or version differs.
    """
    try:
        installed = get_installed_version(tool)
    except FileNotFoundError:
        return False
    return installed == expected_version


# ---------------------------------------------------------------------------
# Install directory resolution
# ---------------------------------------------------------------------------


def resolve_install_dir() -> pathlib.Path:
    """Return the writable directory binaries are installed into, creating it.

    The directory is read from $ENSURE_TOOLS_INSTALL_DIR when set (no hardcoded
    path), otherwise it defaults to ~/.local/bin. The directory must be on PATH
    for the installed tools to be discoverable.

    Returns:
        Absolute path to the install directory (guaranteed to exist).
    """
    override = os.environ.get(INSTALL_DIR_ENV)
    install_dir = pathlib.Path(override) if override else pathlib.Path.home() / ".local" / "bin"
    install_dir.mkdir(parents=True, exist_ok=True)
    return install_dir


# ---------------------------------------------------------------------------
# Download + extract + install
# ---------------------------------------------------------------------------


def _download(url: str, dest: pathlib.Path) -> None:
    """Download url to dest over HTTPS using curl resolved from PATH.

    curl is invoked by its absolute path (resolved via shutil.which) with an
    HTTPS-only protocol restriction (``--proto =https``) and a redirect follow
    (``-L``) so the GitHub/HashiCorp release redirects resolve. The HTTPS scheme is
    enforced twice: by the prefix guard here and by curl's ``--proto`` flag. The
    timeout is read from the environment so it is configurable (no hardcoded value).

    Args:
        url: HTTPS URL of the artifact.
        dest: Local path the artifact is written to.

    Raises:
        ValueError: If the URL is not HTTPS (defense against scheme downgrade).
        FileNotFoundError: If curl is not available on PATH.
        subprocess.CalledProcessError: If curl exits non-zero.
        subprocess.TimeoutExpired: If the download exceeds the configured timeout.
    """
    if not url.startswith("https://"):
        raise ValueError(f"Refusing to download from non-HTTPS URL: {url!r}")
    curl = shutil.which("curl")
    if curl is None:
        raise FileNotFoundError(
            "curl was not found on PATH. The binary provisioner downloads official "
            "release artifacts with curl; install curl or add it to PATH."
        )
    timeout = _download_timeout_seconds()
    retries = _download_retries()
    subprocess.run(
        [
            curl,
            "--fail",
            "--show-error",
            "--silent",
            "--location",
            # Retry transient third-party-host failures (go.dev / GitHub / HashiCorp):
            # curl exit 92 (HTTP/2 stream error), connection resets, sporadic 5xx. curl
            # retries internally with exponential backoff so a flaky download does not
            # fail the whole provisioning run. --retry-all-errors covers non-transient-by-
            # default exit codes (e.g. 92); --retry-connrefused covers connection refused.
            "--retry",
            str(retries),
            "--retry-connrefused",
            "--retry-all-errors",
            "--proto",
            "=https",
            "--output",
            str(dest),
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _download_timeout_seconds() -> int:
    """Return the per-download timeout in seconds, read from the environment.

    Configurable via $ENSURE_TOOLS_DOWNLOAD_TIMEOUT (no hardcoded value baked into
    logic); falls back to a documented default when unset.

    Raises:
        ProvisioningError: If the env var is set to a non-integer value (fail-fast).
    """
    raw = os.environ.get(DOWNLOAD_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_DOWNLOAD_TIMEOUT_SECONDS
    try:
        return int(raw)
    except ValueError as exc:
        raise ProvisioningError(
            "toolchain",
            "unknown",
            f"${DOWNLOAD_TIMEOUT_ENV}={raw!r} is not a valid integer number of seconds.",
        ) from exc


def _download_retries() -> int:
    """Return the number of curl download retries, read from the environment.

    Configurable via $ENSURE_TOOLS_DOWNLOAD_RETRIES (no hardcoded value baked into
    logic); falls back to a documented default when unset. A negative value is
    rejected fail-fast (curl requires a non-negative retry count).

    Raises:
        ProvisioningError: If the env var is set to a non-integer or negative value.
    """
    raw = os.environ.get(DOWNLOAD_RETRIES_ENV)
    if raw is None:
        return DEFAULT_DOWNLOAD_RETRIES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProvisioningError(
            "toolchain",
            "unknown",
            f"${DOWNLOAD_RETRIES_ENV}={raw!r} is not a valid integer number of retries.",
        ) from exc
    if value < 0:
        raise ProvisioningError(
            "toolchain",
            "unknown",
            f"${DOWNLOAD_RETRIES_ENV}={raw!r} must be a non-negative integer number of retries.",
        )
    return value


def _make_executable(path: pathlib.Path) -> None:
    """Add the owner/group/other execute bits to path."""
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_and_install(
    spec: BinarySpec,
    archive_path: pathlib.Path,
    install_dir: pathlib.Path,
) -> pathlib.Path:
    """Extract the tool binary from archive_path and install it onto install_dir.

    Member mode (default) handles the three artifact kinds:
    - 'raw'    : the downloaded file is the binary itself.
    - 'zip'    : the binary is the named member inside a zip archive.
    - 'tar.gz' : the binary is the named member inside a gzip tarball.
    In member mode the single binary is copied onto install_dir/binary_name and
    chmod +x.

    Tree mode (spec.extract_mode == 'tree', used by go) extracts the whole tarball
    next to install_dir and symlinks install_dir/binary_name at the extracted real
    binary, so the binary keeps its sibling lib/src tree (GOROOT) intact.

    Args:
        spec: The resolved binary specification.
        archive_path: Path to the downloaded artifact.
        install_dir: Directory the binary is installed into.

    Returns:
        Absolute path to the installed executable (a symlink in tree mode).

    Raises:
        ProvisioningError: If the expected member is absent from the archive.
    """
    if spec.extract_mode == "tree":
        return _extract_tree_and_symlink(spec, archive_path, install_dir)

    target = install_dir / spec.binary_name
    if spec.archive_kind == "raw":
        shutil.copyfile(archive_path, target)
    elif spec.archive_kind == "zip":
        _extract_zip_member(spec, archive_path, target)
    elif spec.archive_kind == "tar.gz":
        _extract_tar_member(spec, archive_path, target)
    else:
        raise ProvisioningError(
            spec.tool,
            "unknown",
            f"Unknown archive kind {spec.archive_kind!r} for tool {spec.tool!r}.",
        )

    _make_executable(target)
    return target


def _extract_tree_and_symlink(
    spec: BinarySpec,
    archive_path: pathlib.Path,
    install_dir: pathlib.Path,
) -> pathlib.Path:
    """Extract a whole tarball next to install_dir and symlink the binary onto it.

    Used for tools (go) whose binary resolves a sibling directory tree by its real
    path. The tarball is extracted into install_dir.parent (a stable location that
    survives the temp download dir), then install_dir/binary_name is replaced with a
    symlink pointing at the extracted real binary (spec.member).

    Args:
        spec: The resolved binary specification (member must be the in-archive path).
        archive_path: Path to the downloaded tarball.
        install_dir: Directory the symlink is installed into.

    Returns:
        Absolute path to the installed symlink.

    Raises:
        ProvisioningError: If the member is absent after extraction.
    """
    if spec.member is None:
        raise ProvisioningError(
            spec.tool, "unknown", "tree extraction requires a member path but none was specified."
        )
    tree_root = install_dir.parent / spec.tool
    if tree_root.exists():
        shutil.rmtree(tree_root)
    tree_root.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        archive.extractall(tree_root, filter="data")

    real_binary = tree_root / spec.member
    if not real_binary.is_file():
        raise ProvisioningError(
            spec.tool,
            "unknown",
            f"Expected binary {spec.member!r} not found after extracting "
            f"{archive_path.name!r} into {tree_root}.",
        )
    _make_executable(real_binary)

    link = install_dir / spec.binary_name
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(real_binary)
    return link


def _extract_zip_member(spec: BinarySpec, archive_path: pathlib.Path, target: pathlib.Path) -> None:
    """Extract spec.member from a zip archive into target."""
    if spec.member is None:
        raise ProvisioningError(
            spec.tool, "unknown", "zip archive requires a member path but none was specified."
        )
    with zipfile.ZipFile(archive_path) as archive:
        try:
            with archive.open(spec.member) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
        except KeyError as exc:
            raise ProvisioningError(
                spec.tool,
                "unknown",
                f"Member {spec.member!r} not found in zip archive {archive_path.name!r}.",
            ) from exc


def _extract_tar_member(spec: BinarySpec, archive_path: pathlib.Path, target: pathlib.Path) -> None:
    """Extract spec.member from a gzip tarball into target."""
    if spec.member is None:
        raise ProvisioningError(
            spec.tool, "unknown", "tar.gz archive requires a member path but none was specified."
        )
    with tarfile.open(archive_path, mode="r:gz") as archive:
        try:
            member = archive.getmember(spec.member)
        except KeyError as exc:
            raise ProvisioningError(
                spec.tool,
                "unknown",
                f"Member {spec.member!r} not found in tar archive {archive_path.name!r}.",
            ) from exc
        source = archive.extractfile(member)
        if source is None:
            raise ProvisioningError(
                spec.tool,
                "unknown",
                f"Member {spec.member!r} in {archive_path.name!r} is not a regular file.",
            )
        with source, target.open("wb") as out:
            shutil.copyfileobj(source, out)


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------


def provision_via_binary(tool: str, version: str) -> None:
    """Provision a tool by downloading and installing its official pinned binary.

    Sequence (fail-fast at every step):
    1. Resolve the host arch and compose the official artifact URL/spec.
    2. Download the artifact to a temporary directory.
    3. Extract + install the binary onto the writable install directory (on PATH).
    4. Verify the installed version equals the pinned version.

    Args:
        tool: Tool name (one of the binary-routed tools).
        version: Pinned version to install.

    Raises:
        ProvisioningError: If any step fails or the post-install version mismatches.
    """
    arch = detect_arch()
    spec = resolve_binary_spec(tool, version, arch)
    install_dir = resolve_install_dir()

    with tempfile.TemporaryDirectory(prefix=f"ensure-tools-{tool}-") as tmp:
        tmp_dir = pathlib.Path(tmp)
        archive_path = tmp_dir / "artifact"
        try:
            _download(spec.url, archive_path)
            _extract_and_install(spec, archive_path, install_dir)
        except ProvisioningError:
            raise
        except Exception as exc:
            raise ProvisioningError(
                tool,
                version,
                f"download/install from {spec.url!r} failed: {exc}",
            ) from exc

    if not is_version_installed(tool, version):
        raise ProvisioningError(
            tool,
            version,
            f"installed binary from {spec.url!r} did not report the pinned version "
            f"{version!r} after install into {install_dir}.",
        )


def _resolve_gopath_bin() -> pathlib.Path:
    """Resolve the GOPATH/bin directory that `go install` writes binaries into.

    Queries `go env GOPATH` with the pinned go binary (must already be on PATH via
    the earlier binary-provisioning pass) so the location is never hardcoded.

    Returns:
        Absolute path to <GOPATH>/bin.

    Raises:
        ProvisioningError: If go is not on PATH or `go env GOPATH` fails/returns empty.
    """
    go_binary = BINARY_NAME_MAP["golang"]
    if shutil.which(go_binary) is None:
        raise ProvisioningError(
            "govulncheck",
            "unknown",
            f"the {go_binary!r} binary is not on PATH; go-install tools require the "
            "pinned go toolchain to be provisioned first (it precedes go-install tools "
            "in .tool-versions ordering).",
        )
    result = subprocess.run(
        [go_binary, "env", "GOPATH"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    gopath = result.stdout.strip()
    if result.returncode != 0 or not gopath:
        raise ProvisioningError(
            "govulncheck",
            "unknown",
            f"'{go_binary} env GOPATH' failed (exit {result.returncode}); "
            f"stderr: {result.stderr.strip()!r}.",
        )
    return pathlib.Path(gopath) / "bin"


def provision_via_go_install(tool: str, version: str) -> None:
    """Provision a go-install tool via `go install <pkg>@v<version>` into GOPATH/bin.

    Used for tools published as a Go module command (not a single official OS/arch
    binary release), e.g. govulncheck. The package path is read from
    GO_INSTALL_PACKAGE_MAP and the version from .tool-versions -- neither is
    hardcoded inline. The pinned go toolchain (provisioned in the earlier binary
    pass) builds the command from source at the exact pinned tag.

    Sequence (fail-fast at every step):
    1. Resolve the go package path for the tool.
    2. Run `go install <pkg>@v<version>`.
    3. Verify the installed binary in GOPATH/bin reports the pinned version.

    Args:
        tool: Tool name (one of the go-install-routed tools).
        version: Pinned version to install (bare semver from .tool-versions).

    Raises:
        ProvisioningError: If the package is unmapped, `go install` fails, or the
            post-install version does not match the pinned version.
    """
    package = GO_INSTALL_PACKAGE_MAP.get(tool)
    if package is None:
        raise ProvisioningError(
            tool,
            version,
            f"no go-install package mapped for {tool!r}; add an entry to "
            "GO_INSTALL_PACKAGE_MAP following the D28 provisioning policy.",
        )

    go_binary = BINARY_NAME_MAP["golang"]
    if shutil.which(go_binary) is None:
        raise ProvisioningError(
            tool,
            version,
            f"the {go_binary!r} binary is not on PATH; the pinned go toolchain must "
            "be provisioned before go-install tools.",
        )

    target = f"{package}@v{version}"
    result = subprocess.run(
        [go_binary, "install", target],
        capture_output=True,
        text=True,
        check=False,
        timeout=_download_timeout_seconds(),
    )
    if result.returncode != 0:
        raise ProvisioningError(
            tool,
            version,
            f"'{go_binary} install {target}' failed (exit {result.returncode}); "
            f"stderr: {result.stderr.strip()!r}.",
        )

    # Verify against the absolute GOPATH/bin path (go install does not put the
    # binary on PATH, so shutil.which-based probing would not find it).
    gopath_bin = _resolve_gopath_bin()
    installed_binary = gopath_bin / BINARY_NAME_MAP.get(tool, tool)
    if not installed_binary.is_file():
        raise ProvisioningError(
            tool,
            version,
            f"'{go_binary} install {target}' did not produce {installed_binary}.",
        )

    probe_args = VERSION_PROBE_ARGS.get(tool, DEFAULT_VERSION_PROBE_ARGS)
    probe = subprocess.run(
        [str(installed_binary), *probe_args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    output = (probe.stdout + probe.stderr).strip()
    pattern = VERSION_EXTRACT_PATTERN.get(tool, _VERSION_PATTERN)
    match = pattern.search(output)
    if match is None or match.group(1) != version:
        raise ProvisioningError(
            tool,
            version,
            f"installed {installed_binary} reported "
            f"{match.group(1) if match else 'an unparseable version'!r}, "
            f"expected the pinned version {version!r}. Probe output: {output!r}.",
        )


# ---------------------------------------------------------------------------
# Core provisioning logic
# ---------------------------------------------------------------------------


def ensure_tool(tool: str, version: str) -> None:
    """Verify and provision a single tool at the pinned version.

    Implements the verify-then-provision idempotency contract:
    1. Determine the provisioning method from TOOL_CONFIG.
    2. If method is 'skip' or 'uv', return immediately (no external provisioning).
    3. Check whether the installed version already matches.
    4. If version matches, return (no-op -- idempotent).
    5. If version does not match, provision using the configured method.

    Args:
        tool: Tool name as it appears in .tool-versions.
        version: Pinned version to ensure is installed.

    Raises:
        ValueError: If the tool is not in TOOL_CONFIG.
        ProvisioningError: If provisioning fails.
    """
    method = get_provisioning_method(tool)

    if method in ("skip", "uv"):
        # python/uv: provided by setup-python/setup-uv or devcontainer.
        # pre-commit: managed by uv sync, not externally provisioned.
        return

    if method == "go-install":
        # go-install tools land in GOPATH/bin (not on PATH), so the idempotency
        # check must probe that absolute location rather than shutil.which.
        if _is_go_install_tool_current(tool, version):
            return
        try:
            provision_via_go_install(tool, version)
        except ProvisioningError:
            raise
        except Exception as exc:
            raise ProvisioningError(tool, version, str(exc)) from exc
        return

    if is_version_installed(tool, version):
        return

    if method == "binary":
        try:
            provision_via_binary(tool, version)
        except ProvisioningError:
            raise
        except Exception as exc:
            raise ProvisioningError(
                tool,
                version,
                str(exc),
            ) from exc
    else:
        raise ProvisioningError(
            tool,
            version,
            f"Unknown provisioning method {method!r} for tool {tool!r}. "
            "Update TOOL_CONFIG to use one of: binary, uv, skip, go-install.",
        )


def _is_go_install_tool_current(tool: str, version: str) -> bool:
    """Return True if a go-install tool is already installed at the pinned version.

    Probes the binary at GOPATH/bin (where `go install` writes it) rather than
    relying on PATH, then parses its version with the tool's extraction pattern.
    Returns False (provision needed) when go is unavailable, the binary is absent,
    or its reported version differs from the pinned version.

    Args:
        tool: go-install tool name.
        version: Pinned version from .tool-versions.

    Returns:
        True if the installed version matches; False otherwise.
    """
    try:
        gopath_bin = _resolve_gopath_bin()
    except ProvisioningError:
        return False
    installed_binary = gopath_bin / BINARY_NAME_MAP.get(tool, tool)
    if not installed_binary.is_file():
        return False
    probe_args = VERSION_PROBE_ARGS.get(tool, DEFAULT_VERSION_PROBE_ARGS)
    try:
        probe = subprocess.run(
            [str(installed_binary), *probe_args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False
    output = (probe.stdout + probe.stderr).strip()
    pattern = VERSION_EXTRACT_PATTERN.get(tool, _VERSION_PATTERN)
    match = pattern.search(output)
    return match is not None and match.group(1) == version


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(tool_versions_path: str | None = None) -> None:
    """Parse .tool-versions and ensure every tool is installed at its pinned version.

    Args:
        tool_versions_path: Path to the .tool-versions file. Defaults to the
            repository root .tool-versions file (resolved relative to this
            module's location).
    """
    if tool_versions_path is None:
        repo_root = pathlib.Path(__file__).parent.parent
        path = repo_root / ".tool-versions"
    else:
        path = pathlib.Path(tool_versions_path)

    try:
        tools = parse_tool_versions(path)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
        return

    for tool, version in tools.items():
        try:
            ensure_tool(tool, version)
        except ProvisioningError as exc:
            print(
                f"ERROR: Provisioning failed for {tool!r} (expected version {version!r}): {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
            return
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
            return


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
