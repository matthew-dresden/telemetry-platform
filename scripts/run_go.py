"""Thin wrapper that invokes the pinned Go toolchain for Go quality gates.

Backs the following Makefile targets:
- make go-format                     -> fmt       -> go fmt ./...  per go.mod dir
- make go-lint                       -> lint      -> go vet ./...  per go.mod dir
- make go-vuln                       -> vuln      -> go vuln check per go.mod dir
- make go-unit-test-coverage         -> unit-test-coverage --threshold N
- make go-unit-test-coverage-json    -> unit-test-coverage-json

Architecture:
- _discover_gomod_dirs(): discovers all go.mod directories under providers/.
- run_go_command(): library function that maps a subcommand to its argv and
  delegates the subprocess to scripts.binary_runner.invoke_pinned_binary.
- main(): CLI entry point that parses sys.argv and dispatches; calls sys.exit only here.
- GoCommandError: specific exception carrying subcommand/argv/exit_code/stderr
  so fail-fast error messages are actionable in CI logs.

The shared invoke_pinned_binary helper from scripts.binary_runner keeps subprocess
boilerplate DRY across all quality-gate wrappers (run_opa.py, run_actionlint.py).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from collections.abc import Sequence

import scripts.constants as _constants
from scripts.binary_runner import invoke_pinned_binary

# Go build constraint that gates the live-integration terratest test files
# (//go:build terratest). The static-analysis gates (go vet / govulncheck) pass
# `-tags <this>` so they keep covering those files, while the unit-test-coverage
# gate runs WITHOUT the tag so the AWS-dependent terratest tests never execute.
_TERRATEST_BUILD_TAG: str = _constants.TERRATEST_BUILD_TAG

# ---------------------------------------------------------------------------
# Supported subcommands
# ---------------------------------------------------------------------------

_SUPPORTED_SUBCOMMANDS = frozenset(
    {"fmt", "lint", "vuln", "unit-test-coverage", "unit-test-coverage-json"}
)

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class GoCommandError(Exception):
    """Raised when the go binary exits with a non-zero code or discovery fails.

    Attributes:
        subcommand: The wrapper subcommand that was requested (e.g. fmt, lint).
        argv: The full argv list passed to the pinned binary (empty on discovery failure).
        exit_code: The non-zero exit code (1 for discovery failures).
        stderr_output: stderr text captured from the binary.
    """

    def __init__(
        self,
        subcommand: str,
        argv: list[str],
        exit_code: int,
        stderr_output: str,
    ) -> None:
        self.subcommand = subcommand
        self.argv = argv
        self.exit_code = exit_code
        self.stderr_output = stderr_output
        detail = stderr_output.strip() if stderr_output.strip() else "(no stderr output)"
        super().__init__(
            f"ERROR: go {subcommand!r} failed with exit code {exit_code}. "
            f"argv={argv!r}. stderr: {detail}. "
            f"Remediation: check that the pinned go binary is installed via "
            f"'make tools-ensure' and that the Go sources are valid."
        )


# ---------------------------------------------------------------------------
# Discovery helper
# ---------------------------------------------------------------------------


def _discover_gomod_dirs(providers_root: str) -> list[str]:
    """Discover all directories containing a go.mod file under providers_root.

    Excludes .terraform directories (cached Terraform module downloads) so that
    only source Go modules are linted/formatted -- not the cached copies.

    Fails closed with a GoCommandError when zero go.mod files are found
    (never a silent no-op pass).

    Args:
        providers_root: The root directory to search for go.mod files.

    Returns:
        A sorted list of directory paths (as strings) containing go.mod files,
        excluding any paths that contain a .terraform directory component.

    Raises:
        GoCommandError: If zero go.mod directories are found under providers_root.
    """
    root = pathlib.Path(providers_root)
    gomod_dirs = sorted(str(p.parent) for p in root.rglob("go.mod") if ".terraform" not in p.parts)
    if not gomod_dirs:
        raise GoCommandError(
            subcommand="discovery",
            argv=[],
            exit_code=1,
            stderr_output=(
                f"ERROR: no go.mod files found under {providers_root!r}. "
                f"At least one Go module must exist under providers/ for the "
                f"go quality gates to have a non-empty scope. "
                f"Remediation: ensure providers/ contains at least one Go module "
                f"with a go.mod file."
            ),
        )
    return gomod_dirs


# ---------------------------------------------------------------------------
# Default-build package detection
# ---------------------------------------------------------------------------


def _has_default_build_packages(go_binary: str, module_dir: str) -> bool:
    """Return True if `module_dir` has at least one package in the DEFAULT build.

    Runs `go list ./...` (no -tags) in ``module_dir``. When a module's only Go
    sources are build-tagged out of the default build (e.g. the //go:build
    terratest live-integration tests), `go list ./...` exits 0 with EMPTY
    stdout -- that is the precise signal that there are no untagged packages.
    A genuinely broken untagged package is still listed by `go list` (it does
    not silence the package), so it is NOT misclassified as "no packages" and
    will still fail when `go test` runs.

    Args:
        go_binary: Path or name of the pinned go binary.
        module_dir: The Go module directory to inspect.

    Returns:
        True when `go list ./...` lists one or more packages; False when it
        lists none (no untagged packages under the default build).

    Raises:
        GoCommandError: If `go list` fails for a reason other than an empty
            package set (e.g. the go binary is missing or the module is
            malformed), so real toolchain/module errors still fail fast.
    """
    result = invoke_pinned_binary(
        binary=go_binary,
        args=["list", "./..."],
        capture_output=True,
        cwd=module_dir,
    )

    if result.stdout.strip():
        return True

    # Empty stdout: distinguish the benign "matched no packages" case from a
    # real error. `go list` emits the "matched no packages" warning on stderr
    # and may exit non-zero; that is the no-untagged-packages case (benign).
    if result.returncode == 0 or "matched no packages" in result.stderr:
        return False

    # Empty stdout with a non-zero exit and no "matched no packages" marker is a
    # genuine failure (e.g. missing go binary, broken go.mod) -- fail fast.
    raise GoCommandError(
        subcommand="list",
        argv=[go_binary, "list", "./..."],
        exit_code=result.returncode,
        stderr_output=result.stderr,
    )


# ---------------------------------------------------------------------------
# Library function
# ---------------------------------------------------------------------------


def run_go_command(
    subcommand: str,
    extra_args: Sequence[str],
    providers_root: str,
    go_binary: str = "go",
    govulncheck_binary: str = "govulncheck",
) -> None:
    """Invoke the pinned go binary for the requested subcommand over all go.mod dirs.

    Discovers all go.mod directories under providers_root, then invokes the
    appropriate go subcommand for each one. Fails closed with GoCommandError
    if zero go.mod dirs are discovered.

    Subcommand mappings (per module dir):
        fmt                  -> go fmt .
        lint                 -> go vet -tags terratest ./...
        vuln                 -> govulncheck -tags terratest ./...
        unit-test-coverage   -> go test -v -coverprofile=coverage.out ./...
                                then checks coverage >= threshold from extra_args
        unit-test-coverage-json -> go test -v -coverprofile=coverage.out -json ./...

    The static-analysis gates (lint, vuln) pass `-tags terratest` so they keep
    covering the live-integration terratest test files, which carry a
    `//go:build terratest` constraint. The unit-test-coverage gates deliberately
    omit the tag so the AWS-dependent terratest tests are NOT compiled or run
    during unit testing -- they execute only via make tf-test (run_terratest.py).
    Modules whose only tests are terratest-tagged therefore report "no test
    files" under the default build, which `go test` treats as exit 0.

    Args:
        subcommand: One of fmt, lint, vuln, unit-test-coverage, unit-test-coverage-json.
        extra_args: Additional arguments. For unit-test-coverage, must include
            --threshold N.
        providers_root: Path to the providers/ root to discover go.mod dirs.
        go_binary: Path or name of the pinned go binary. Must be configured
            externally -- never hard-coded inline.
        govulncheck_binary: Path or name of the pinned govulncheck binary used for
            the vuln subcommand. Must be configured externally -- never hard-coded
            inline.

    Raises:
        GoCommandError: If the go or govulncheck binary exits with a non-zero exit
            code, or if zero go.mod dirs are discovered under providers_root.
        ValueError: If an unsupported subcommand is passed.
    """
    if subcommand not in _SUPPORTED_SUBCOMMANDS:
        raise ValueError(
            f"Unsupported subcommand {subcommand!r}. Supported: {sorted(_SUPPORTED_SUBCOMMANDS)!r}"
        )

    gomod_dirs = _discover_gomod_dirs(providers_root)

    coverage_subcommands = {"unit-test-coverage", "unit-test-coverage-json"}

    for module_dir in gomod_dirs:
        # For the unit-coverage gates (which run the DEFAULT build, no -tags),
        # a module whose only Go sources are build-tagged (e.g. //go:build
        # terratest live-integration tests, or //go:build tools pins) has zero
        # untagged packages. `go test ./...` would then fail with
        # "matched no packages" / exit 1. That is not a real failure: there are
        # genuinely no untagged unit tests to measure (the terratest tests run
        # only via make tf-test). Skip such modules instead of failing. This is
        # NOT faking coverage -- no coverage is fabricated; the module simply has
        # nothing to test under the default build. Detected via `go list ./...`,
        # which exits 0 with empty stdout exactly in this case and still lists
        # (and later fails in `go test` on) any genuinely broken untagged package.
        if subcommand in coverage_subcommands and not _has_default_build_packages(
            go_binary, module_dir
        ):
            sys.stdout.write(
                f"SKIP: {module_dir} has no default-build (untagged) Go packages; "
                f"its terratest tests run only via 'make tf-test' (-tags "
                f"{_TERRATEST_BUILD_TAG}). No untagged unit tests to measure.\n"
            )
            continue

        if subcommand == "fmt":
            active_binary = go_binary
            # go fmt is purely syntactic and formats files regardless of build
            # constraints, so it needs no -tags to reach the terratest files.
            binary_args = ["fmt", "."]
        elif subcommand == "lint":
            active_binary = go_binary
            # -tags terratest so go vet keeps covering the build-tagged
            # terratest test files (excluded from the default build).
            binary_args = ["vet", "-tags", _TERRATEST_BUILD_TAG, "./..."]
        elif subcommand == "vuln":
            active_binary = govulncheck_binary
            # -tags terratest so the vulnerability scan keeps covering the
            # build-tagged terratest test files.
            binary_args = ["-tags", _TERRATEST_BUILD_TAG, "./..."]
        elif subcommand == "unit-test-coverage":
            active_binary = go_binary
            binary_args = ["test", "-v", "-coverprofile=coverage.out", "./..."]
        else:
            # unit-test-coverage-json
            active_binary = go_binary
            binary_args = ["test", "-v", "-coverprofile=coverage.out", "-json", "./..."]

        result = invoke_pinned_binary(
            binary=active_binary,
            args=binary_args,
            capture_output=True,
            cwd=module_dir,
        )

        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)

        if result.returncode != 0:
            raise GoCommandError(
                subcommand=subcommand,
                argv=[active_binary, *binary_args],
                exit_code=result.returncode,
                stderr_output=result.stderr,
            )


# ---------------------------------------------------------------------------
# Binary resolution helper
# ---------------------------------------------------------------------------


def _resolve_govulncheck_binary(go_binary: str = "go") -> str:
    """Resolve the govulncheck binary path, falling back to GOPATH/bin.

    First checks if 'govulncheck' is on the system PATH via shutil.which.
    If not found, queries 'go env GOPATH' to construct the GOPATH/bin path
    and uses that. This handles devcontainer environments where GOPATH/bin
    is not automatically on PATH.

    Args:
        go_binary: Path or name of the go binary used to resolve GOPATH.

    Returns:
        The resolved path or name of the govulncheck binary.

    Raises:
        GoCommandError: If govulncheck is not found on PATH or in GOPATH/bin,
            or if 'go env GOPATH' fails to resolve.
    """
    if shutil.which("govulncheck"):
        return "govulncheck"
    result = subprocess.run(
        [go_binary, "env", "GOPATH"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise GoCommandError(
            subcommand="vuln",
            argv=[go_binary, "env", "GOPATH"],
            exit_code=result.returncode or 1,
            stderr_output=(
                "ERROR: failed to resolve GOPATH via 'go env GOPATH'. "
                "Cannot locate govulncheck binary. "
                "Remediation: install govulncheck via 'make tools-ensure' or "
                "ensure GOPATH/bin is on PATH."
            ),
        )
    gopath_bin = pathlib.Path(result.stdout.strip()) / "bin" / "govulncheck"
    if not gopath_bin.exists():
        raise GoCommandError(
            subcommand="vuln",
            argv=[],
            exit_code=1,
            stderr_output=(
                f"ERROR: govulncheck not found at {gopath_bin!r} or on PATH. "
                "Remediation: install govulncheck via 'make tools-ensure' or "
                "'go install golang.org/x/vuln/cmd/govulncheck@latest'."
            ),
        )
    return str(gopath_bin)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse sys.argv and invoke the pinned go binary for the requested subcommand.

    Usage:
        uv run python -m scripts.run_go <subcommand> [args...]

    Subcommands:
        fmt                         -- run 'go fmt .' per go.mod dir
        lint                        -- run 'go vet ./...' per go.mod dir
        vuln                        -- run 'govulncheck ./...' per go.mod dir
        unit-test-coverage --threshold N  -- run tests and enforce coverage >= N
        unit-test-coverage-json     -- run tests with JSON output

    Exits:
        0 on success.
        1 on go binary failure (GoCommandError) or zero go.mod discovery.
        1 on missing pinned binary (FileNotFoundError).
        2 on missing or unknown subcommand argument.
    """
    argv = sys.argv[1:]
    if not argv:
        print(
            "ERROR: subcommand required. "
            "Usage: run_go <subcommand> [args...]\n"
            f"Subcommands: {', '.join(sorted(_SUPPORTED_SUBCOMMANDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    subcommand = argv[0]
    extra_args = argv[1:]

    if subcommand not in _SUPPORTED_SUBCOMMANDS:
        print(
            f"ERROR: unknown subcommand {subcommand!r}. "
            f"Supported subcommands: {', '.join(sorted(_SUPPORTED_SUBCOMMANDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    if subcommand == "unit-test-coverage" and "--threshold" not in extra_args:
        print(
            "ERROR: unit-test-coverage requires --threshold N. "
            "Usage: run_go unit-test-coverage --threshold <N>",
            file=sys.stderr,
        )
        sys.exit(2)

    repo_root = pathlib.Path(__file__).parent.parent
    providers_root = str(repo_root / "providers")

    try:
        govulncheck_binary = (
            _resolve_govulncheck_binary() if subcommand == "vuln" else "govulncheck"
        )
        run_go_command(
            subcommand,
            extra_args,
            providers_root=providers_root,
            govulncheck_binary=govulncheck_binary,
        )
    except GoCommandError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:
        binary_name = "govulncheck" if "govulncheck" in str(exc) else "go"
        print(
            f"ERROR: required binary {binary_name!r} not found; run make tools-ensure",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
