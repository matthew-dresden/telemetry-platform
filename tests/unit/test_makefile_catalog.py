"""Structural regression tests for the repo-root Makefile catalog.

These tests assert the exact structural constraints for the Makefile task
interface defined in doc-03 section 2.7 (ledger D11, D16, D29). A failure
here means the Makefile catalog has drifted from the canonical spec.

Assertions:
- AC-16: every canonical target from doc-03 section 2.7 is present
- AC-16: no recipe contains an inline shell pipeline (| outside uv run)
- AC-16: no numeric coverage threshold literal appears in any recipe
- AC-16: pyproject.toml declares all required tool configuration tables
"""

import pathlib
import re
import tomllib

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def makefile_text() -> str:
    """Read the Makefile once for the whole module."""
    makefile_path = REPO_ROOT / "Makefile"
    assert makefile_path.exists(), (
        f"Makefile not found at {makefile_path}; the file must be present at the repository root."
    )
    return makefile_path.read_text()


@pytest.fixture(scope="module")
def makefile_lines(makefile_text: str) -> list[str]:
    """Split the Makefile into individual lines."""
    return makefile_text.splitlines()


@pytest.fixture(scope="module")
def pyproject_tables() -> dict:
    """Parse pyproject.toml once for the whole module."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), (
        f"pyproject.toml not found at {pyproject_path}; "
        "the file must be present at the repository root."
    )
    with pyproject_path.open("rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# Canonical target presence -- AC-16 (doc-03 section 2.7)
# ---------------------------------------------------------------------------

_CANONICAL_TARGETS = [
    # Top-level orchestrators
    "ci",
    "validate",
    "configure",
    # Setup/provisioning
    "tools-ensure",
    "py-sync",
    "hooks-install",
    "help",
    # Python quality gates
    "py-format",
    "py-format-check",
    "py-lint",
    "py-typecheck",
    "py-security",
    "py-test",
    "python-quality",
    # Go quality gates
    "go-format",
    "go-lint",
    "go-vuln",
    "go-unit-test-coverage",
    "go-unit-test-coverage-json",
    # Rego quality gates
    "rego-format",
    "rego-lint",
    "rego-unit-test-coverage",
    "rego-unit-test-coverage-json",
    # Terraform module gates
    "tf-format",
    "tf-format-check",
    "tf-lint",
    "tf-security",
    "tf-docs-check",
    "module-validate",
    "tf-plan",
    "tf-test",
    "tf-validate",
    # Terragrunt gates
    "tg-format-check",
    "tg-validate",
    "tg-security",
    "tg-plan",
    "tg-apply",
    "tg-detect-units",
    "tg-output",
    "tg-regression",
    "tf-state-preflight",
    "tf-validate-dependency-paths",
    "tf-guard-pinned-sources",
    "tg-bucket-name-unique",
    # Terratest gates
    "terratest-tags-check",
    "terratest-coverage-check",
    "terratest-sweep",
    "tf-test-all",
    # YAML gates
    "yaml-format-check",
    "yaml-lint",
    "actionlint",
    "actions-sha-pin-check",
    # Markdown gate
    "md-lint",
    # Scope / release / orchestration targets
    "scope-detect",
    "resolve-module-type",
    "validate-pr-title",
    "calculate-version",
    "check-version-immutability",
    "simulate-merge",
    "check-staleness",
    "update-version",
    "generate-changelog",
    "lock-branch",
    "safety-net-unlock",
    "check-release-commit",
    "check-scope-override",
    "git-fetch",
    "git-reset-hard",
    "git-identity",
    "publish-release",
    "required-checks-aggregate",
    "detect-module-changes",
    # Git history checker (FR-6, AC-14, spec section 4.6)
    "git-history-check",
    # Live verification prober (FR-13, AC-30, spec section 4.13)
    "live-verify",
    # OIDC provider bootstrap (FR-10, spec section 4.10)
    "bootstrap-oidc-provider",
]


@pytest.mark.unit
@pytest.mark.parametrize("target", _CANONICAL_TARGETS)
def test_canonical_target_present(makefile_lines: list[str], target: str) -> None:
    """Every canonical target from doc-03 section 2.7 must be defined in the Makefile.

    Ledger D16 requires target names to match the doc-03 catalog exactly so
    downstream stories and CI jobs can call them by name. A missing target
    breaks the single task interface contract.
    """
    target_lines = [line for line in makefile_lines if line.startswith(f"{target}:")]
    assert target_lines, (
        f"Target {target!r} not found in Makefile. "
        f"doc-03 section 2.7 (ledger D16) requires this canonical target name. "
        f"Add a recipe for '{target}:' to the repo-root Makefile."
    )


# ---------------------------------------------------------------------------
# No inline shell pipeline in recipes -- AC-16 (ledger D11)
# ---------------------------------------------------------------------------

_PIPE_IN_UV_RUN_PATTERN = re.compile(r"uv\s+run\b.*\|")


def _extract_recipe_lines(makefile_lines: list[str]) -> list[str]:
    """Return only tab-indented recipe lines from the Makefile."""
    return [line for line in makefile_lines if line.startswith("\t")]


@pytest.mark.unit
def test_no_inline_pipeline_in_recipes(makefile_lines: list[str]) -> None:
    """No recipe line may contain an inline shell pipeline outside a uv run invocation.

    Ledger D11 forbids inline shell pipelines in recipes. All logic must live
    in Python scripts invoked via uv run. A pipe character (|) is only
    permitted within a uv run invocation (e.g. piping output of uv run to
    another uv run). Bare pipes that drive shell logic violate the single-task-
    interface contract.
    """
    recipe_lines = _extract_recipe_lines(makefile_lines)
    violations: list[str] = []
    for line in recipe_lines:
        stripped = line.strip()
        # Skip lines that are comments
        if stripped.startswith("#"):
            continue
        # Count pipe characters
        if "|" not in stripped:
            continue
        # Allow pipes that appear ONLY within a uv run invocation (the pipe
        # is inside the argument list of uv run, not a shell pipe between two
        # separate commands). A bare shell pipe connects two top-level commands
        # on the same recipe line: detect it as a | not preceded by uv run on
        # the same line.
        # A violation: the pipe is a shell-level separator, not an arg pipe
        # inside uv run. We detect this by checking whether the characters
        # before the | include "uv run" as the first command token.
        # Simple heuristic: if the line does not start with "uv run" (after
        # stripping the leading tab and optional @/-), it is a shell pipeline.
        # If it starts with "uv run" the pipe is inside the uv invocation.
        cmd_start = stripped.lstrip("@-").lstrip()
        if not cmd_start.startswith("uv "):
            violations.append(line)
    assert not violations, (
        f"Found {len(violations)} recipe line(s) with inline shell pipelines "
        f"(violates ledger D11):\n"
        + "\n".join(f"  {v!r}" for v in violations)
        + "\nMove logic into a Python script and invoke it via 'uv run python -m scripts.<x>'."
    )


# ---------------------------------------------------------------------------
# No numeric coverage threshold literals in recipes -- AC-16 (D22)
# ---------------------------------------------------------------------------

_COVERAGE_LITERAL_PATTERN = re.compile(r"\b(cov.fail.under|threshold)\s*[=:]\s*\d+", re.IGNORECASE)
_BARE_NUMERIC_THRESHOLD = re.compile(r"--cov-fail-under\s+\d+|--threshold\s+\d+")


@pytest.mark.unit
def test_no_numeric_coverage_threshold_in_recipes(makefile_lines: list[str]) -> None:
    """No recipe line may contain an inlined numeric coverage threshold.

    Coverage thresholds must be read from monorepo-config.json at invocation
    time, never hard-coded into a recipe (ledger D22, D29 single source of
    truth). A threshold literal in a recipe creates divergence between the
    config file and the gate behaviour.
    """
    recipe_lines = _extract_recipe_lines(makefile_lines)
    violations: list[str] = []
    for line in recipe_lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _COVERAGE_LITERAL_PATTERN.search(stripped) or _BARE_NUMERIC_THRESHOLD.search(stripped):
            violations.append(line)
    assert not violations, (
        f"Found {len(violations)} recipe line(s) with an inlined numeric coverage "
        f"threshold (violates ledger D22):\n"
        + "\n".join(f"  {v!r}" for v in violations)
        + "\nRead thresholds from monorepo-config.json via a Make variable, never inline."
    )


# ---------------------------------------------------------------------------
# make ci prerequisite set -- AC-16 (doc-03 section 2.7 line 435)
# ---------------------------------------------------------------------------


def _extract_ci_prerequisites(makefile_lines: list[str]) -> set[str]:
    """Parse the prerequisites listed on the 'ci:' target line(s).

    Handles backslash-continuation lines -- including tab-indented
    continuation lines that are part of the prerequisite list, not recipes.
    A line is a continuation line when the previous line ended with a
    backslash. A line is a recipe line only when it starts with a tab AND
    the previous line did NOT end with a backslash.
    """
    prereqs: set[str] = set()
    collecting = False
    continued = False
    for line in makefile_lines:
        if not collecting:
            if not line.startswith("ci:"):
                continue
            # Start collecting from the ci: line
            rest = line[len("ci:") :].rstrip("\\").strip()
            tokens = rest.split()
            prereqs.update(t for t in tokens if t)
            collecting = True
            continued = line.rstrip().endswith("\\")
        else:
            if not continued:
                # Previous line did not end with \\ -- done with prerequisites.
                break
            # This line is a continuation of the prerequisite list.
            stripped = line.rstrip("\\").strip()
            tokens = stripped.split()
            prereqs.update(t for t in tokens if t)
            continued = line.rstrip().endswith("\\")
    return prereqs


_CI_REQUIRED_MEMBERS = {
    "terratest-tags-check",
    "terratest-coverage-check",
}


@pytest.mark.unit
@pytest.mark.parametrize("member", sorted(_CI_REQUIRED_MEMBERS))
def test_ci_includes_required_members(makefile_lines: list[str], member: str) -> None:
    """make ci must list the required terratest gate members as prerequisites.

    The terratest tag/coverage gates must run at PR validation time; a missing
    member would let a change bypass its gate in the single full-suite target.
    """
    prereqs = _extract_ci_prerequisites(makefile_lines)
    assert member in prereqs, (
        f"'make ci' prerequisites do not include {member!r}. "
        f"Current prerequisites: {sorted(prereqs)!r}. "
        f"Add '{member}' to the ci: prerequisite list."
    )


# ---------------------------------------------------------------------------
# Target recipe validation helper -- AC-16
# ---------------------------------------------------------------------------


def _get_recipe_lines_for_target(makefile_lines: list[str], target: str) -> list[str]:
    """Return the recipe lines (tab-indented) immediately following target:."""
    target_index: int | None = None
    for i, line in enumerate(makefile_lines):
        if line.startswith(f"{target}:"):
            target_index = i
            break

    if target_index is None:
        return []

    recipe_lines: list[str] = []
    for line in makefile_lines[target_index + 1 :]:
        if line.startswith("\t"):
            recipe_lines.append(line.strip())
        elif line.strip() == "" or line.startswith("#"):
            continue
        else:
            break
    return recipe_lines


# ---------------------------------------------------------------------------
# pyproject.toml tool tables -- AC-16
# ---------------------------------------------------------------------------

_REQUIRED_TOOL_TABLES = [
    "ruff",
    "mypy",
    "bandit",
    "pytest.ini_options",
    "coverage.run",
    "coverage.report",
]


@pytest.mark.unit
@pytest.mark.parametrize("table", _REQUIRED_TOOL_TABLES)
def test_pyproject_tool_table_present(pyproject_tables: dict, table: str) -> None:
    """pyproject.toml must declare each required [tool.<table>] section.

    The make py-* gates read tool configuration exclusively from pyproject.toml
    (doc-03 section 2.7). A missing tool table causes silent gate failure or
    falls back to tool defaults, violating the fail-fast and config-driven
    principles.
    """
    tool_section = pyproject_tables.get("tool", {})
    # Handle dotted table names like "coverage.run" -> tool.coverage.run
    parts = table.split(".")
    current = tool_section
    for part in parts:
        assert part in current, (
            f"pyproject.toml is missing [tool.{table}] section. "
            f"The make py-* gates require this configuration table. "
            f"Add [tool.{table}] to pyproject.toml."
        )
        current = current[part]


# ---------------------------------------------------------------------------
# terratest-tags-check target recipe -- AC-3 (spec section 4.3, ledger D11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terratest_tags_check_wraps_check_terratest_tags(
    makefile_lines: list[str],
) -> None:
    """terratest-tags-check must invoke uv run python -m scripts.check_terratest_tags.

    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    Spec section 4.3 specifies this exact wrapper command.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "terratest-tags-check")
    assert recipe, (
        "terratest-tags-check has no recipe lines. "
        "The target must invoke uv run python -m scripts.check_terratest_tags "
        "(ledger D11, spec section 4.3)."
    )
    assert any("uv run python -m scripts.check_terratest_tags" in r for r in recipe), (
        f"terratest-tags-check recipe does not call "
        f"'uv run python -m scripts.check_terratest_tags'. "
        f"Found recipe lines: {recipe!r}. "
        f"Ledger D11 requires this exact wrapper pattern."
    )


# ---------------------------------------------------------------------------
# terratest-coverage-check target recipe -- AC-5 (spec section 4.19, ledger D11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terratest_coverage_check_wraps_check_terratest_coverage(
    makefile_lines: list[str],
) -> None:
    """terratest-coverage-check must invoke uv run python -m scripts.check_terratest_coverage.

    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    Spec section 4.19 (FR-19 item 1) specifies this exact wrapper command.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "terratest-coverage-check")
    assert recipe, (
        "terratest-coverage-check has no recipe lines. "
        "The target must invoke uv run python -m scripts.check_terratest_coverage "
        "(ledger D11, spec section 4.19)."
    )
    assert any("uv run python -m scripts.check_terratest_coverage" in r for r in recipe), (
        f"terratest-coverage-check recipe does not call "
        f"'uv run python -m scripts.check_terratest_coverage'. "
        f"Found recipe lines: {recipe!r}. "
        f"Ledger D11 requires this exact wrapper pattern."
    )


# ---------------------------------------------------------------------------
# terratest-sweep target recipe -- AC-4 (spec section 4.4, FR-4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_terratest_sweep_wraps_terratest_sweep_module(
    makefile_lines: list[str],
) -> None:
    """terratest-sweep must invoke uv run python -m scripts.terratest_sweep.

    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    Spec section 4.4 (FR-4) specifies this exact wrapper command.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "terratest-sweep")
    assert recipe, (
        "terratest-sweep has no recipe lines. "
        "The target must invoke uv run python -m scripts.terratest_sweep "
        "(ledger D11, spec section 4.4 FR-4)."
    )
    assert any("uv run python -m scripts.terratest_sweep" in r for r in recipe), (
        f"terratest-sweep recipe does not call "
        f"'uv run python -m scripts.terratest_sweep'. "
        f"Found recipe lines: {recipe!r}. "
        f"Ledger D11 requires this exact wrapper pattern."
    )


# ---------------------------------------------------------------------------
# tf-test-all target recipe -- AC-4 (spec section 4.5, FR-5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tf_test_all_wraps_run_terratest_all(
    makefile_lines: list[str],
) -> None:
    """tf-test-all must invoke uv run python -m scripts.run_terratest --all.

    Spec section 4.5 (FR-5) requires a suite-mode target that runs all modules.
    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    The --all flag activates the suite mode discovery-driven serial execution.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "tf-test-all")
    assert recipe, (
        "tf-test-all has no recipe lines. "
        "The target must invoke uv run python -m scripts.run_terratest --all "
        "(spec section 4.5 FR-5, ledger D11)."
    )
    assert any("uv run python -m scripts.run_terratest" in r and "--all" in r for r in recipe), (
        f"tf-test-all recipe does not call "
        f"'uv run python -m scripts.run_terratest --all'. "
        f"Found recipe lines: {recipe!r}. "
        f"Spec section 4.5 requires this exact wrapper pattern."
    )


# ---------------------------------------------------------------------------
# git-history-check target recipe -- AC-1 (spec section 4.6, FR-6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_git_history_check_wraps_check_git_history(
    makefile_lines: list[str],
) -> None:
    """git-history-check must invoke uv run python -m scripts.check_git_history --max-blob-bytes.

    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    Spec section 4.6 (FR-6, AC-14) specifies this exact wrapper command with the
    100,000,000-byte threshold to assert no blob in the branch history exceeds
    GitHub's 100 MB hard limit.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "git-history-check")
    assert recipe, (
        "git-history-check has no recipe lines. "
        "The target must invoke uv run python -m scripts.check_git_history --max-blob-bytes "
        "(spec section 4.6 FR-6, ledger D11)."
    )
    assert any("uv run python -m scripts.check_git_history" in r for r in recipe), (
        f"git-history-check recipe does not call "
        f"'uv run python -m scripts.check_git_history'. "
        f"Found recipe lines: {recipe!r}. "
        f"Ledger D11 requires this exact wrapper pattern."
    )
    assert any("--max-blob-bytes" in r for r in recipe), (
        f"git-history-check recipe does not pass '--max-blob-bytes' to check_git_history. "
        f"Found recipe lines: {recipe!r}. "
        f"Spec section 4.6 requires the 100,000,000-byte threshold to be asserted."
    )


# ---------------------------------------------------------------------------
# live-verify target recipe -- AC-3 (FR-13, spec section 4.13)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_live_verify_wraps_live_verify_module(
    makefile_lines: list[str],
) -> None:
    """live-verify must invoke uv run python -m scripts.live_verify with CHECK and ENV args.

    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    Spec section 4.13 (FR-13, AC-30) specifies this exact wrapper command with
    the --check $(CHECK) and --env $(ENV) Make variable injections so the caller
    can supply any of the eight checks and four environments.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "live-verify")
    assert recipe, (
        "live-verify has no recipe lines. "
        "The target must invoke uv run python -m scripts.live_verify --check $(CHECK) --env $(ENV) "
        "(spec section 4.13 FR-13, ledger D11)."
    )
    assert any("uv run python -m scripts.live_verify" in r for r in recipe), (
        f"live-verify recipe does not call "
        f"'uv run python -m scripts.live_verify'. "
        f"Found recipe lines: {recipe!r}. "
        f"Ledger D11 requires this exact wrapper pattern."
    )
    recipe_joined = " ".join(recipe)
    assert "$(CHECK)" in recipe_joined, (
        f"live-verify recipe does not inject CHECK via $(CHECK). "
        f"Found recipe lines: {recipe!r}. "
        "The caller must pass the check name as a Make variable."
    )
    assert "$(ENV)" in recipe_joined, (
        f"live-verify recipe does not inject ENV via $(ENV). "
        f"Found recipe lines: {recipe!r}. "
        "The caller must pass the environment as a Make variable."
    )


# ---------------------------------------------------------------------------
# bootstrap-oidc-provider target recipe -- AC-4 (FR-10, spec section 4.10)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bootstrap_oidc_provider_wraps_bootstrap_module(
    makefile_lines: list[str],
) -> None:
    """bootstrap-oidc-provider must invoke uv run python -m scripts.bootstrap_oidc_provider.

    Ledger D11 requires all targets to wrap uv run python -m scripts.<x>.
    Spec section 4.10 (FR-10) specifies this exact wrapper command with
    the --env $(ENV) Make variable injection so the caller can supply qa or prod.
    sandbox (D33) and root (D-11) are refused by the script, not the Makefile target.
    """
    recipe = _get_recipe_lines_for_target(makefile_lines, "bootstrap-oidc-provider")
    assert recipe, (
        "bootstrap-oidc-provider has no recipe lines. "
        "The target must invoke uv run python -m scripts.bootstrap_oidc_provider --env $(ENV) "
        "(spec section 4.10 FR-10, ledger D11)."
    )
    assert any("uv run python -m scripts.bootstrap_oidc_provider" in r for r in recipe), (
        f"bootstrap-oidc-provider recipe does not call "
        f"'uv run python -m scripts.bootstrap_oidc_provider'. "
        f"Found recipe lines: {recipe!r}. "
        f"Ledger D11 requires this exact wrapper pattern."
    )
    recipe_joined = " ".join(recipe)
    assert "$(ENV)" in recipe_joined, (
        f"bootstrap-oidc-provider recipe does not inject ENV via $(ENV). "
        f"Found recipe lines: {recipe!r}. "
        "The caller must pass the environment as a Make variable (qa or prod)."
    )
