"""Unit tests for scripts/check_terratest_coverage.py.

Covers FR-19 item-1 gate semantics (spec section 4.19, D-25, AC #44, #45):

  - Module discovery: go.mod + examples/ directories under providers/aws/{primitives,references}/
  - Per-example mapping: at least one first-party RunSingleExample test per example
  - Vendored-exclusion rule: RunSingleExample hits inside **/.terraform/** must NOT count
  - Per-example gap detection: one ERROR line per gap + remediation, exit 1 on any gap
  - Supplement-only negative path: module with only static supplement tests must not pass
  - main() exit codes via subprocess and direct call
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

from scripts.check_terratest_coverage import (
    CoverageError,
    check_module_coverage,
    collect_modules,
    main,
    run_coverage_check,
)

# ---------------------------------------------------------------------------
# Helpers for building synthetic fixture trees
# ---------------------------------------------------------------------------


def _make_module(
    tmp_path: pathlib.Path,
    module_name: str = "kms-key",
    kind: str = "primitives",
    examples: list[str] | None = None,
    test_variants: dict[str, list[str]] | None = None,
    vendored_tests: dict[str, list[str]] | None = None,
) -> pathlib.Path:
    """Build a minimal module tree under tmp_path.

    Args:
        tmp_path: Base temp directory.
        module_name: Name of the module (e.g. "kms-key").
        kind: "primitives" or "references".
        examples: List of example subdirectory names (default: ["basic"]).
        test_variants: Dict mapping test variant name to list of example names
                       that their module_test.go files call RunSingleExample for.
                       Uses first-party path (tests/<variant>/module_test.go).
        vendored_tests: Dict mapping variant name to list of example names covered
                        by vendored copies (.terraform/.../module_test.go).

    Returns:
        Path to the module root.
    """
    if examples is None:
        examples = ["basic"]
    if test_variants is None:
        test_variants = {"basic": ["basic"]}
    if vendored_tests is None:
        vendored_tests = {}

    root = tmp_path / "providers" / "aws" / kind / module_name

    # Create go.mod
    (root).mkdir(parents=True, exist_ok=True)
    (root / "go.mod").write_text(
        f"module github.com/matthew-dresden/telemetry-platform/providers/aws/{kind}/{module_name}\n"
        "\ngo 1.26.4\n"
    )

    # Create examples
    for ex in examples:
        ex_dir = root / "examples" / ex
        ex_dir.mkdir(parents=True, exist_ok=True)
        (ex_dir / "main.tf").write_text("# placeholder\n")

    # Create first-party test files
    for variant, covered_examples in test_variants.items():
        test_dir = root / "tests" / variant
        test_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"package {variant.replace('-', '_')}_test\n\n"]
        for ex in covered_examples:
            lines.append(
                f"// TestSomething tests the {ex} example.\n"
                f"func TestSomething_{ex}(t *testing.T) {{\n"
                f'\ttestctx.RunSingleExample(t, "../../examples", "{ex}", testctx.TestConfig{{}})\n'
                f"}}\n\n"
            )
        (test_dir / "module_test.go").write_text("".join(lines))

    # Create vendored test files (inside .terraform/...)
    for variant, covered_examples in vendored_tests.items():
        # Simulate vendored copy: .terraform/modules/xxx/tests/<variant>/module_test.go
        vendored_dir = (
            root / "examples" / "basic" / ".terraform" / "modules" / "upstream" / "tests" / variant
        )
        vendored_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"package {variant}_test\n\n"]
        for ex in covered_examples:
            lines.append(
                f"func TestVendored_{ex}(t *testing.T) {{\n"
                f'\ttestctx.RunSingleExample(t, "../../examples", "{ex}", testctx.TestConfig{{}})\n'
                f"}}\n\n"
            )
        (vendored_dir / "module_test.go").write_text("".join(lines))

    return root


def _make_roots(tmp_path: pathlib.Path) -> list[pathlib.Path]:
    """Return the two default scan roots under tmp_path, creating them if absent."""
    roots = [
        tmp_path / "providers" / "aws" / "primitives",
        tmp_path / "providers" / "aws" / "references",
    ]
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    return roots


# ---------------------------------------------------------------------------
# collect_modules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collect_modules_returns_module_with_gomod_and_examples(tmp_path: pathlib.Path) -> None:
    """collect_modules must return modules that have both go.mod and examples/."""
    _make_module(tmp_path, module_name="kms-key", kind="primitives")
    roots = _make_roots(tmp_path)
    modules = collect_modules(roots)
    assert len(modules) == 1
    assert modules[0].name == "kms-key"


@pytest.mark.unit
def test_collect_modules_skips_module_without_gomod(tmp_path: pathlib.Path) -> None:
    """collect_modules must skip directories without go.mod."""
    root = tmp_path / "providers" / "aws" / "primitives" / "no-gomod"
    root.mkdir(parents=True)
    (root / "examples" / "basic").mkdir(parents=True)
    roots = _make_roots(tmp_path)
    modules = collect_modules(roots)
    assert modules == []


@pytest.mark.unit
def test_collect_modules_skips_module_without_examples(tmp_path: pathlib.Path) -> None:
    """collect_modules must skip directories without examples/."""
    root = tmp_path / "providers" / "aws" / "primitives" / "no-examples"
    root.mkdir(parents=True)
    (root / "go.mod").write_text("module example.com/no-examples\n\ngo 1.26.4\n")
    roots = _make_roots(tmp_path)
    modules = collect_modules(roots)
    assert modules == []


@pytest.mark.unit
def test_collect_modules_scans_both_kinds(tmp_path: pathlib.Path) -> None:
    """collect_modules must scan both primitives and references roots."""
    _make_module(tmp_path, module_name="kms-key", kind="primitives")
    _make_module(tmp_path, module_name="analytics", kind="references")
    roots = _make_roots(tmp_path)
    modules = collect_modules(roots)
    assert len(modules) == 2


@pytest.mark.unit
def test_collect_modules_raises_on_nonexistent_root(tmp_path: pathlib.Path) -> None:
    """collect_modules must raise CoverageError when any root does not exist."""
    nonexistent = tmp_path / "providers" / "aws" / "does-not-exist"
    with pytest.raises(CoverageError, match="does not exist"):
        collect_modules([nonexistent])


# ---------------------------------------------------------------------------
# check_module_coverage -- happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_module_coverage_passes_when_all_examples_covered(tmp_path: pathlib.Path) -> None:
    """check_module_coverage must return empty list when all examples have first-party tests."""
    module_root = _make_module(
        tmp_path,
        examples=["basic", "with-policy"],
        test_variants={"basic": ["basic"], "common": ["basic", "with-policy"]},
    )
    gaps = check_module_coverage(module_root)
    assert gaps == []


@pytest.mark.unit
def test_check_module_coverage_passes_single_example_single_test(tmp_path: pathlib.Path) -> None:
    """check_module_coverage passes with exactly one example and one covering test."""
    module_root = _make_module(
        tmp_path,
        examples=["basic"],
        test_variants={"basic": ["basic"]},
    )
    gaps = check_module_coverage(module_root)
    assert gaps == []


# ---------------------------------------------------------------------------
# check_module_coverage -- gap detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_module_coverage_detects_uncovered_example(tmp_path: pathlib.Path) -> None:
    """check_module_coverage must return a gap when an example has no first-party test."""
    module_root = _make_module(
        tmp_path,
        module_name="kms-key",
        kind="primitives",
        examples=["basic", "with-policy"],
        test_variants={"basic": ["basic"]},  # with-policy has no test
    )
    gaps = check_module_coverage(module_root)
    assert len(gaps) == 1
    assert "with-policy" in gaps[0]


@pytest.mark.unit
def test_check_module_coverage_error_message_shape(tmp_path: pathlib.Path) -> None:
    """Gap messages must match the ERROR shape: <module> example '<ex>' has no
    first-party apply-level test."""
    module_root = _make_module(
        tmp_path,
        module_name="kms-key",
        kind="primitives",
        examples=["missing-ex"],
        test_variants={},  # no tests at all
    )
    gaps = check_module_coverage(module_root)
    assert len(gaps) == 1
    assert "ERROR:" in gaps[0]
    assert "kms-key" in gaps[0]
    assert "missing-ex" in gaps[0]
    assert "no first-party apply-level test" in gaps[0]


@pytest.mark.unit
def test_check_module_coverage_multiple_gaps_reported(tmp_path: pathlib.Path) -> None:
    """check_module_coverage must report one line per uncovered example."""
    module_root = _make_module(
        tmp_path,
        examples=["alpha", "beta", "gamma"],
        test_variants={},  # nothing covered
    )
    gaps = check_module_coverage(module_root)
    assert len(gaps) == 3


# ---------------------------------------------------------------------------
# Vendored-exclusion rule
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_module_coverage_ignores_vendored_tests(tmp_path: pathlib.Path) -> None:
    """RunSingleExample hits inside .terraform/** must NOT satisfy the coverage gate."""
    module_root = _make_module(
        tmp_path,
        module_name="kms-key",
        kind="primitives",
        examples=["basic"],
        test_variants={},  # no first-party tests
        vendored_tests={"basic": ["basic"]},  # only vendored copy
    )
    gaps = check_module_coverage(module_root)
    assert len(gaps) == 1, "vendored-only coverage must NOT count as first-party coverage"


@pytest.mark.unit
def test_check_module_coverage_first_party_wins_even_with_vendored(tmp_path: pathlib.Path) -> None:
    """When both first-party and vendored tests exist for an example, coverage passes."""
    module_root = _make_module(
        tmp_path,
        examples=["basic"],
        test_variants={"basic": ["basic"]},
        vendored_tests={"copy": ["basic"]},
    )
    gaps = check_module_coverage(module_root)
    assert gaps == []


# ---------------------------------------------------------------------------
# Supplement-only negative path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_module_coverage_supplement_only_fails(tmp_path: pathlib.Path) -> None:
    """A module whose only tests do not call RunSingleExample must not pass coverage."""
    module_root = _make_module(
        tmp_path,
        examples=["basic"],
        test_variants={},  # no RunSingleExample calls
    )
    # Add a static supplement test (no RunSingleExample call)
    supplement_dir = module_root / "tests" / "static"
    supplement_dir.mkdir(parents=True, exist_ok=True)
    (supplement_dir / "module_test.go").write_text(
        textwrap.dedent("""\
            package static_test

            import "testing"

            func TestStaticSupplement(t *testing.T) {
                // This is a static (plan-only) test -- no RunSingleExample call.
                t.Log("static supplement")
            }
        """)
    )
    gaps = check_module_coverage(module_root)
    assert len(gaps) == 1, "supplement-only tests must not satisfy apply-level coverage"


# ---------------------------------------------------------------------------
# run_coverage_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_coverage_check_empty_on_full_coverage(tmp_path: pathlib.Path) -> None:
    """run_coverage_check must return empty list when all modules are fully covered."""
    _make_module(tmp_path, examples=["basic"], test_variants={"basic": ["basic"]})
    roots = _make_roots(tmp_path)
    gaps = run_coverage_check(roots)
    assert gaps == []


@pytest.mark.unit
def test_run_coverage_check_aggregates_across_modules(tmp_path: pathlib.Path) -> None:
    """run_coverage_check must aggregate gaps from multiple modules."""
    _make_module(
        tmp_path, module_name="kms-key", kind="primitives", examples=["basic"], test_variants={}
    )
    _make_module(
        tmp_path, module_name="analytics", kind="references", examples=["default"], test_variants={}
    )
    roots = _make_roots(tmp_path)
    gaps = run_coverage_check(roots)
    assert len(gaps) == 2


# ---------------------------------------------------------------------------
# main() -- exit codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_0_on_full_coverage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() must return 0 when all examples are covered."""
    _make_module(tmp_path, examples=["basic"], test_variants={"basic": ["basic"]})
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 0


@pytest.mark.unit
def test_main_returns_1_on_gap(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must return 1 when any example lacks first-party coverage."""
    _make_module(tmp_path, examples=["basic"], test_variants={})
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 1


@pytest.mark.unit
def test_main_subprocess_exit_0_on_full_coverage(tmp_path: pathlib.Path) -> None:
    """Subprocess invocation of main() must exit 0 when all examples are covered."""
    _make_module(tmp_path, examples=["basic"], test_variants={"basic": ["basic"]})
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_coverage"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


@pytest.mark.unit
def test_main_subprocess_exit_1_on_gap(tmp_path: pathlib.Path) -> None:
    """Subprocess invocation of main() must exit 1 when gaps exist."""
    _make_module(tmp_path, examples=["basic"], test_variants={})
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_coverage"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"


@pytest.mark.unit
def test_main_subprocess_stderr_contains_error_on_gap(tmp_path: pathlib.Path) -> None:
    """Subprocess invocation must print ERROR: lines to stderr on gap."""
    _make_module(
        tmp_path,
        module_name="kms-key",
        kind="primitives",
        examples=["basic"],
        test_variants={},
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_coverage"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert "ERROR:" in result.stderr, f"stderr must contain ERROR: lines; got: {result.stderr!r}"
    assert "kms-key" in result.stderr
    assert "basic" in result.stderr


@pytest.mark.unit
def test_main_no_roots_returns_1(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with no existing provider roots must return 1 (nothing to check = error)."""
    monkeypatch.chdir(tmp_path)
    result = main()
    assert result == 1


@pytest.mark.unit
def test_main_subprocess_configured_missing_root_exits_1_with_error(tmp_path: pathlib.Path) -> None:
    """A configured-but-nonexistent TERRATEST_COVERAGE_ROOTS root must exit 1
    with ERROR: on stderr."""
    nonexistent = str(tmp_path / "does-not-exist-xyz")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_terratest_coverage"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "TERRATEST_COVERAGE_ROOTS": nonexistent},
    )
    assert result.returncode == 1, (
        f"expected exit 1 for configured-missing root; got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "ERROR:" in result.stderr, f"expected ERROR: in stderr; got: {result.stderr!r}"


# ---------------------------------------------------------------------------
# Parametrized gap shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("example_name", ["basic", "with-policy", "alb-origin", "prod-subset"])
def test_gap_message_always_includes_example_name(
    tmp_path: pathlib.Path, example_name: str
) -> None:
    """Gap messages must include the example name regardless of its format."""
    module_root = _make_module(
        tmp_path,
        examples=[example_name],
        test_variants={},
    )
    gaps = check_module_coverage(module_root)
    assert len(gaps) == 1
    assert example_name in gaps[0]
