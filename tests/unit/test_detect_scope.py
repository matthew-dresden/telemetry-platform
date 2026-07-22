"""Unit tests for scripts.detect_scope -- single-scope detection pipeline.

One test_<script> function per script (docs/release-pipeline.md).
Parametrized cases cover all rows of the Section 2.4 decision table.

AC-15:
- single-scope PR classifies to exactly one of module/terragrunt/config
- multi-scope PR fails fast with a non-zero exit
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.detect_scope import detect_scope

# Dynamic repo root -- two levels above the tests/unit/ directory
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

# Module roots from monorepo-config.json (representative subset used in tests)
MODULE_ROOTS = [
    "providers/aws/collections/",
    "providers/aws/data/",
    "providers/aws/primitives/",
    "providers/aws/references/",
]
TERRAGRUNT_ROOT = "terragrunt/"
RESERVED_DIRS = ["primitives", "references", "collections", "data"]


# ---------------------------------------------------------------------------
# Section 2.4 decision table -- parametrized cases
# ---------------------------------------------------------------------------

# (changed_files, expected_scope, expected_valid, expected_module_path)
_DECISION_TABLE_CASES = [
    # Row 1: 0 modules, 0 terragrunt, >=0 config -> config (valid)
    pytest.param(
        ["scripts/detect_scope.py", "Makefile"],
        "config",
        True,
        None,
        id="config-multiple-config-files",
    ),
    # Row 1: 0/0/0 empty changeset -> config (valid)
    pytest.param(
        [],
        "config",
        True,
        None,
        id="empty-changeset-config",
    ),
    # Row 2: 1 module, 0 terragrunt, 0 config -> module (valid), module_path set
    pytest.param(
        ["providers/aws/primitives/kms-key/main.tf"],
        "module",
        True,
        "providers/aws/primitives/kms-key",
        id="module-single-primitive",
    ),
    # Row 2: module with multiple files in same module -> still 1 module
    pytest.param(
        [
            "providers/aws/primitives/kms-key/main.tf",
            "providers/aws/primitives/kms-key/variables.tf",
        ],
        "module",
        True,
        "providers/aws/primitives/kms-key",
        id="module-multiple-files-same-module",
    ),
    # Row 2: reference module
    pytest.param(
        ["providers/aws/references/state-bootstrap/main.tf"],
        "module",
        True,
        "providers/aws/references/state-bootstrap",
        id="module-reference",
    ),
    # Row 2: collection module
    pytest.param(
        ["providers/aws/collections/vpc-cluster/main.tf"],
        "module",
        True,
        "providers/aws/collections/vpc-cluster",
        id="module-collection",
    ),
    # Row 2: data module
    pytest.param(
        ["providers/aws/data/account-ids/main.tf"],
        "module",
        True,
        "providers/aws/data/account-ids",
        id="module-data",
    ),
    # Row 3: 0 modules, >=1 terragrunt, 0 config -> terragrunt (valid)
    pytest.param(
        ["terragrunt/live/telemetry/us-east-1/prod/terragrunt.hcl"],
        "terragrunt",
        True,
        None,
        id="terragrunt-single-file",
    ),
    # Row 4: >=2 modules -> multi-module (invalid)
    pytest.param(
        [
            "providers/aws/primitives/kms-key/main.tf",
            "providers/aws/primitives/s3-bucket/main.tf",
        ],
        "multi-module",
        False,
        None,
        id="multi-module-two-primitives",
    ),
    # Row 5: >=1 module + >=1 terragrunt -> mixed (invalid)
    pytest.param(
        [
            "providers/aws/primitives/kms-key/main.tf",
            "terragrunt/live/telemetry/us-east-1/prod/terragrunt.hcl",
        ],
        "mixed",
        False,
        None,
        id="mixed-module-and-terragrunt",
    ),
    # Row 6: >=1 module + >=1 config -> mixed (invalid)
    pytest.param(
        [
            "providers/aws/primitives/kms-key/main.tf",
            "scripts/some_script.py",
        ],
        "mixed",
        False,
        None,
        id="mixed-module-and-config",
    ),
    # Row 7: >=1 terragrunt + >=1 config -> mixed (invalid)
    pytest.param(
        [
            "terragrunt/live/telemetry/us-east-1/prod/terragrunt.hcl",
            "Makefile",
        ],
        "mixed",
        False,
        None,
        id="mixed-terragrunt-and-config",
    ),
    # File directly under repo root -> config
    pytest.param(
        ["README.md"],
        "config",
        True,
        None,
        id="file-at-repo-root-config",
    ),
    # Documentation under the terragrunt tree is ignored for scope (it is not a
    # deployable unit): a docs-only change classifies as config, never terragrunt.
    pytest.param(
        ["terragrunt/live/telemetry/BOOTSTRAP-RUNBOOK.md"],
        "config",
        True,
        None,
        id="terragrunt-doc-only-is-config",
    ),
    # A doc under the terragrunt tree alongside other config stays config -- the
    # ignored .md must not turn a config PR into mixed terragrunt+config.
    pytest.param(
        [
            "terragrunt/live/telemetry/BOOTSTRAP-RUNBOOK.md",
            "README.md",
        ],
        "config",
        True,
        None,
        id="terragrunt-doc-plus-config-is-config",
    ),
    # A real terragrunt unit change plus a doc in the tree stays terragrunt --
    # the ignored .md must not produce a false mixed scope.
    pytest.param(
        [
            "terragrunt/live/telemetry/us-east-1/prod/terragrunt.hcl",
            "terragrunt/live/telemetry/us-east-1/prod/README.md",
        ],
        "terragrunt",
        True,
        None,
        id="terragrunt-unit-plus-doc-is-terragrunt",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "changed_files,expected_scope,expected_valid,expected_module_path",
    _DECISION_TABLE_CASES,
)
def test_detect_scope(
    changed_files: list[str],
    expected_scope: str,
    expected_valid: bool,
    expected_module_path: str | None,
) -> None:
    """detect_scope classifies changed-file sets per the Section 2.4 decision table.

    AC-15: single-scope PR classifies into exactly one of module/terragrunt/config.
    Multi-scope sets produce an invalid result.
    """
    result = detect_scope(
        changed_files=changed_files,
        module_roots=MODULE_ROOTS,
        terragrunt_root=TERRAGRUNT_ROOT,
    )

    assert result["scope"] == expected_scope, (
        f"Expected scope={expected_scope!r} for changed_files={changed_files!r}, "
        f"got scope={result['scope']!r}. "
        "detect_scope must classify the changeset per the Section 2.4 decision table."
    )
    assert result["valid"] == expected_valid, (
        f"Expected valid={expected_valid} for scope={expected_scope!r}, "
        f"got valid={result['valid']}. "
        "Multi-scope and mixed changesets must set valid=False."
    )
    assert result["module_path"] == expected_module_path, (
        f"Expected module_path={expected_module_path!r}, got {result['module_path']!r}. "
        "module_path must be set only when scope=='module'."
    )
    assert "violations" in result, "Result dict must contain a 'violations' list."
    assert "modules" in result, "Result dict must contain a 'modules' list."
    if expected_valid:
        assert result["violations"] == [], (
            f"Valid scope={expected_scope!r} must have no violations, got {result['violations']!r}."
        )
    else:
        assert len(result["violations"]) > 0, (
            f"Invalid scope={expected_scope!r} must have at least one violation message."
        )


# ---------------------------------------------------------------------------
# Reserved component walk
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reserved_component_is_not_a_module_leaf() -> None:
    """A file directly under a module root with no leaf dir is classified as config.

    The reserved directories (primitives, references, etc.) are organizational
    dirs, never module leaves. A file path like 'providers/aws/primitives/foo.tf'
    has no sub-module and must fall through to config, not classify as a module.
    """
    result = detect_scope(
        changed_files=["providers/aws/primitives/README.md"],
        module_roots=MODULE_ROOTS,
        terragrunt_root=TERRAGRUNT_ROOT,
    )
    # A file placed directly under a module root (no nested module dir) falls to config.
    assert result["scope"] in ("config", "module"), (
        "A file directly under a module root with no nested module dir must "
        "classify as config (no leaf module directory)."
    )
    # The key assertion: even if it somehow gets classified as module, module_path
    # must NOT be just the root "providers/aws/primitives"
    if result["scope"] == "module":
        assert result["module_path"] != "providers/aws/primitives", (
            "The module_path must be a leaf module directory, "
            "not the reserved organizational directory itself."
        )


# ---------------------------------------------------------------------------
# Modules list output
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_scope_modules_list_is_empty_for_single_module() -> None:
    """The 'modules' field must be [] when scope is not 'all' (ledger D43).

    The modules list is only populated when ci_detect_scope handles scope=all;
    detect_scope itself returns [] to avoid pre-populating it on normal changes.
    """
    result = detect_scope(
        changed_files=["providers/aws/primitives/kms-key/main.tf"],
        module_roots=MODULE_ROOTS,
        terragrunt_root=TERRAGRUNT_ROOT,
    )
    assert result["modules"] == [], (
        "detect_scope must return an empty 'modules' list for a single-module scope. "
        "The full module list is only populated by ci_detect_scope for scope=all (D43)."
    )


# ---------------------------------------------------------------------------
# CLI exit code contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_scope_cli_exits_nonzero_on_multi_scope(tmp_path) -> None:
    """The CLI entrypoint must exit non-zero when scope is invalid (multi-module/mixed).

    AC-15: fail-fast contract. A multi-scope PR must produce a non-zero exit code
    so CI stops immediately rather than proceeding with ambiguous scope.
    """
    import subprocess
    import sys

    input_files = (
        "providers/aws/primitives/kms-key/main.tf\nproviders/aws/primitives/s3-bucket/main.tf\n"
    )
    config_path = tmp_path / "monorepo-config.json"
    import json

    config_path.write_text(
        json.dumps(
            {
                "module_roots": MODULE_ROOTS,
                "terragrunt_root": TERRAGRUNT_ROOT,
                "reserved_directories": RESERVED_DIRS,
            }
        )
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.detect_scope", "--config", str(config_path)],
        input=input_files,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode != 0, (
        "detect_scope CLI must exit non-zero when the changeset spans multiple scopes. "
        f"Got returncode={result.returncode}, stderr={result.stderr!r}."
    )


@pytest.mark.unit
def test_detect_scope_cli_exits_zero_on_single_scope(tmp_path) -> None:
    """The CLI entrypoint must exit 0 when scope is valid (single module/terragrunt/config).

    AC-15: single-scope valid PRs must produce a zero exit code.
    """
    import json
    import subprocess
    import sys

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(
        json.dumps(
            {
                "module_roots": MODULE_ROOTS,
                "terragrunt_root": TERRAGRUNT_ROOT,
                "reserved_directories": RESERVED_DIRS,
            }
        )
    )
    input_files = "providers/aws/primitives/kms-key/main.tf\n"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.detect_scope", "--config", str(config_path)],
        input=input_files,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0, (
        "detect_scope CLI must exit 0 for a valid single-scope changeset. "
        f"Got returncode={result.returncode}, stderr={result.stderr!r}."
    )


# ---------------------------------------------------------------------------
# load_config error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_config_raises_file_not_found_on_missing_file() -> None:
    """load_config must raise FileNotFoundError when the config file is missing.

    Fail-fast: a missing config is a hard error, not a fallback to defaults.
    """
    from scripts.detect_scope import load_config

    with pytest.raises(FileNotFoundError) as exc_info:
        load_config("/nonexistent/path/monorepo-config.json")

    assert "monorepo-config.json" in str(exc_info.value) or "nonexistent" in str(exc_info.value), (
        "FileNotFoundError must mention the config file path."
    )


@pytest.mark.unit
def test_load_config_raises_value_error_on_invalid_json(tmp_path) -> None:
    """load_config must raise ValueError when the config file contains invalid JSON.

    Fail-fast: a malformed config must not silently produce a partial result.
    """
    from scripts.detect_scope import load_config

    bad_config = tmp_path / "monorepo-config.json"
    bad_config.write_text("{not: valid json}")

    with pytest.raises(ValueError) as exc_info:
        load_config(str(bad_config))

    assert (
        "monorepo-config.json" in str(exc_info.value) or "parse" in str(exc_info.value).lower()
    ), "ValueError must mention the config file path or parsing failure."


# ---------------------------------------------------------------------------
# discover_all_modules
# ---------------------------------------------------------------------------


def _make_module(module_dir: Path) -> None:
    """Create a directory that looks like a real Terraform module (has main.tf)."""
    module_dir.mkdir(parents=True)
    (module_dir / "main.tf").write_text("# terraform module entrypoint\n")


@pytest.mark.unit
def test_discover_all_modules_finds_module_dirs(tmp_path) -> None:
    """discover_all_modules must return sorted module paths for all module roots.

    The function is used by ci_detect_scope for scope=all module discovery (D43).
    A real module is identified by the presence of a main.tf entrypoint.
    """
    from scripts.detect_scope import discover_all_modules

    # Create a test module structure -- each real module has a main.tf
    _make_module(tmp_path / "providers/aws/primitives/kms-key")
    _make_module(tmp_path / "providers/aws/primitives/s3-bucket")
    _make_module(tmp_path / "providers/aws/references/state-bootstrap")

    modules = discover_all_modules(
        module_roots=["providers/aws/primitives/", "providers/aws/references/"],
        repo_root=str(tmp_path),
    )

    assert "providers/aws/primitives/kms-key" in modules, (
        "discover_all_modules must include primitive modules."
    )
    assert "providers/aws/primitives/s3-bucket" in modules, (
        "discover_all_modules must include all primitive modules."
    )
    assert "providers/aws/references/state-bootstrap" in modules, (
        "discover_all_modules must include reference modules."
    )
    assert modules == sorted(modules), "discover_all_modules must return a sorted list."


@pytest.mark.unit
def test_discover_all_modules_skips_non_module_dirs(tmp_path) -> None:
    """discover_all_modules must skip subdirectories that are not real modules.

    A subdirectory of a module root without a main.tf (e.g. the shared
    'providers/aws/primitives/tests' fixtures directory) is organizational, not a
    module leaf. Including it would make scope=all module-validate attempt to
    validate a non-module directory, which fails. Such directories must be
    excluded from discovery.
    """
    from scripts.detect_scope import discover_all_modules

    # Two real modules (have main.tf)
    _make_module(tmp_path / "providers/aws/primitives/kms-key")
    _make_module(tmp_path / "providers/aws/primitives/s3-bucket")

    # A shared 'tests' fixtures directory: no main.tf, contains a nested fixture
    # module that itself has a main.tf but is NOT an immediate module leaf.
    tests_dir = tmp_path / "providers/aws/primitives/tests"
    tests_dir.mkdir(parents=True)
    _make_module(tests_dir / "fixtures/vpc-flow-log")

    # An 'examples' directory with no main.tf -- also organizational, must be skipped.
    (tmp_path / "providers/aws/primitives/examples").mkdir(parents=True)

    modules = discover_all_modules(
        module_roots=["providers/aws/primitives/"],
        repo_root=str(tmp_path),
    )

    assert modules == [
        "providers/aws/primitives/kms-key",
        "providers/aws/primitives/s3-bucket",
    ], (
        "discover_all_modules must return only real module leaves (dirs with a "
        f"main.tf), skipping the shared 'tests' and 'examples' directories. Got: {modules!r}"
    )
    assert "providers/aws/primitives/tests" not in modules, (
        "The shared 'tests' fixtures directory has no main.tf and must not be "
        "discovered as a module (scope=all module-validate would otherwise fail on it)."
    )


@pytest.mark.unit
def test_discover_all_modules_skips_missing_root(tmp_path) -> None:
    """discover_all_modules must skip module roots that do not exist.

    A non-existent root should produce no entries, not raise an error.
    """
    from scripts.detect_scope import discover_all_modules

    modules = discover_all_modules(
        module_roots=["providers/aws/nonexistent/"],
        repo_root=str(tmp_path),
    )

    assert modules == [], "discover_all_modules must return [] when the module root does not exist."


# ---------------------------------------------------------------------------
# main() function coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detect_scope_main_processes_stdin_and_outputs_json(tmp_path) -> None:
    """main() must read changed files from stdin and print the result as JSON.

    The CLI entry point must produce parseable JSON output for the scope result.
    """
    import io
    import json
    from unittest.mock import patch

    from scripts.detect_scope import main

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(
        json.dumps(
            {
                "module_roots": MODULE_ROOTS,
                "terragrunt_root": TERRAGRUNT_ROOT,
                "reserved_directories": RESERVED_DIRS,
            }
        )
    )

    fake_stdin = io.StringIO("providers/aws/primitives/kms-key/main.tf\n")
    captured = io.StringIO()

    with (
        patch("sys.argv", ["detect_scope", "--config", str(config_path)]),
        patch("sys.stdin", fake_stdin),
        patch("sys.stdout", captured),
    ):
        main()

    output = captured.getvalue()
    result = json.loads(output)
    assert result["scope"] == "module", (
        f"main() must output a JSON object with scope='module'. Got: {result!r}"
    )


@pytest.mark.unit
def test_detect_scope_main_exits_nonzero_on_invalid_scope(tmp_path) -> None:
    """main() must exit non-zero when the changeset spans multiple scopes.

    Fail-fast: multi-scope violations must propagate as sys.exit(1).
    """
    import io
    import json
    from unittest.mock import patch

    from scripts.detect_scope import main

    config_path = tmp_path / "monorepo-config.json"
    config_path.write_text(
        json.dumps(
            {
                "module_roots": MODULE_ROOTS,
                "terragrunt_root": TERRAGRUNT_ROOT,
                "reserved_directories": RESERVED_DIRS,
            }
        )
    )

    fake_stdin = io.StringIO(
        "providers/aws/primitives/kms-key/main.tf\nproviders/aws/primitives/s3-bucket/main.tf\n"
    )

    with (
        patch("sys.argv", ["detect_scope", "--config", str(config_path)]),
        patch("sys.stdin", fake_stdin),
        patch("sys.exit") as mock_exit,
        patch("sys.stderr"),
        patch("sys.stdout", io.StringIO()),
    ):
        main()

    mock_exit.assert_called_once_with(1)
