"""Regression tests for monorepo-config.json and .tool-versions contract files.

These tests assert the exact structural and value constraints enumerated in the
work unit acceptance criteria (AC-1, AC-16), the D22 coverage-threshold floor,
and the D34 toolchain pinning requirements. A failure here means a configuration
contract consumed by the Makefile task interface and downstream CI has drifted
from its declared spec.
"""

import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def config() -> dict:
    """Parse monorepo-config.json once for the whole module."""
    config_path = REPO_ROOT / "monorepo-config.json"
    assert config_path.exists(), (
        f"monorepo-config.json not found at {config_path}; "
        "the file must be present at the repository root."
    )
    with config_path.open() as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def tool_versions() -> dict[str, str]:
    """Parse .tool-versions into a {tool: version} mapping."""
    tv_path = REPO_ROOT / ".tool-versions"
    assert tv_path.exists(), (
        f".tool-versions not found at {tv_path}; the file must be present at the repository root."
    )
    mapping: dict[str, str] = {}
    for lineno, raw_line in enumerate(tv_path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 2, (
            f".tool-versions line {lineno}: expected '<tool> <version>', got {raw_line!r}."
        )
        tool, version = parts
        assert tool not in mapping, (
            f".tool-versions line {lineno}: duplicate entry for tool {tool!r}."
        )
        mapping[tool] = version
    return mapping


# ---------------------------------------------------------------------------
# monorepo-config.json -- structural validity (AC-1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_monorepo_config_valid_json(config: dict) -> None:
    """monorepo-config.json must parse as valid JSON and be a mapping."""
    assert isinstance(config, dict), "monorepo-config.json must be a JSON object at the top level."


@pytest.mark.unit
@pytest.mark.parametrize(
    "top_level_key",
    [
        "module_roots",
        "provider",
        "scripts",
        "coverage_groups",
        "coverage_thresholds",
    ],
)
def test_monorepo_config_required_keys_present(config: dict, top_level_key: str) -> None:
    """Each required top-level key must be present in monorepo-config.json."""
    assert top_level_key in config, (
        f"monorepo-config.json is missing required key {top_level_key!r}. "
        "This key is consumed by downstream CI and the Makefile task interface."
    )


# ---------------------------------------------------------------------------
# coverage_thresholds -- D22 floor (AC-1)
# ---------------------------------------------------------------------------

_COVERAGE_THRESHOLD_KEYS = [
    ("python_cov_min", "python"),
    ("go_cov_min", "go"),
    ("rego_cov_min", "rego"),
]


@pytest.mark.unit
@pytest.mark.parametrize("key,language", _COVERAGE_THRESHOLD_KEYS)
def test_coverage_threshold_key_present(config: dict, key: str, language: str) -> None:
    """coverage_thresholds must declare a threshold for each required language."""
    thresholds = config.get("coverage_thresholds", {})
    assert key in thresholds, (
        f"coverage_thresholds is missing key {key!r} (language: {language}). "
        "The D22 floor requires explicit thresholds for python, go, and rego."
    )


@pytest.mark.unit
@pytest.mark.parametrize("key,language", _COVERAGE_THRESHOLD_KEYS)
def test_coverage_threshold_meets_d22_floor(config: dict, key: str, language: str) -> None:
    """Every coverage threshold must be >= 90 (D22 floor)."""
    thresholds = config["coverage_thresholds"]
    value = thresholds[key]
    assert isinstance(value, int | float), (
        f"coverage_thresholds.{key} must be a number, got {type(value).__name__!r}."
    )
    assert value >= 90, (
        f"coverage_thresholds.{key} is {value}, which is below the D22 floor of 90. "
        f"Language: {language}. Raise the threshold to at least 90."
    )


# ---------------------------------------------------------------------------
# coverage_groups consistency with lint_directories (AC-16)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_coverage_groups_paths_subset_of_lint_directories(config: dict) -> None:
    """Every coverage_groups testPath must appear in scripts.lint_directories."""
    lint_dirs: list[str] = config.get("scripts", {}).get("lint_directories", [])
    # Normalise: strip leading './' for comparison
    normalised_lint = {d.lstrip("./") for d in lint_dirs}

    coverage_groups: list[dict] = config.get("coverage_groups", [])
    for group in coverage_groups:
        test_path: str = group.get("testPath", "")
        normalised_test_path = test_path.lstrip("./")
        assert normalised_test_path in normalised_lint, (
            f"coverage_groups entry {group.get('name')!r} has testPath {test_path!r} "
            f"which is not present in scripts.lint_directories. "
            f"lint_directories: {lint_dirs!r}. "
            "Drifting coverage_groups from lint_directories violates the consistency rule."
        )


# ---------------------------------------------------------------------------
# .tool-versions -- D34 exact pins (AC-16)
# ---------------------------------------------------------------------------

_D34_REQUIRED_TOOLS = {
    "golang": "1.26.5",
    "golangci-lint": "2.12.2",
    "jq": "1.8.1",
    "opa": "1.17.0",
    "pre-commit": "4.6.0",
    "python": "3.14.5",
    "terraform": "1.15.5",
    "terraform-docs": "0.24.0",
    "trivy": "0.71.0",
    "tflint": "0.63.1",
    "yq": "4.53.2",
    "terragrunt": "1.0.7",
    "uv": "0.11.19",
    "gh": "2.93.0",
}


@pytest.mark.unit
@pytest.mark.parametrize("tool,expected_version", list(_D34_REQUIRED_TOOLS.items()))
def test_tool_versions_pins_exact_d34_version(
    tool_versions: dict[str, str], tool: str, expected_version: str
) -> None:
    """Each D34 tool must be pinned to the exact latest-stable version."""
    assert tool in tool_versions, (
        f".tool-versions is missing tool {tool!r} (D34 required). "
        f"Expected version: {expected_version}."
    )
    actual = tool_versions[tool]
    assert actual == expected_version, (
        f".tool-versions pins {tool} at {actual!r}, "
        f"but the D34 latest-stable version is {expected_version!r}. "
        "Update .tool-versions to match the D34 specification."
    )


@pytest.mark.unit
def test_tool_versions_no_tfsec_entry(tool_versions: dict[str, str]) -> None:
    """tfsec must NOT appear in .tool-versions -- Trivy replaces it (D11)."""
    assert "tfsec" not in tool_versions, (
        ".tool-versions contains a 'tfsec' entry. "
        "tfsec was replaced by Trivy (D11); remove the tfsec line."
    )


@pytest.mark.unit
def test_tool_versions_trivy_present(tool_versions: dict[str, str]) -> None:
    """trivy must be present in .tool-versions (D11 -- replaces tfsec)."""
    assert "trivy" in tool_versions, (
        ".tool-versions is missing 'trivy'. Trivy is required as the replacement for tfsec (D11)."
    )


@pytest.mark.unit
def test_tool_versions_python_is_d34_version_not_upstream_skeleton(
    tool_versions: dict[str, str],
) -> None:
    """python must be pinned to 3.14.5, NOT the upstream skeleton 3.12.9."""
    python_version = tool_versions.get("python", "")
    assert python_version == "3.14.5", (
        f".tool-versions pins python at {python_version!r}. "
        "The D34 latest-stable version is '3.14.5' (not the upstream skeleton '3.12.9'). "
        "Update .tool-versions to pin python 3.14.5."
    )


@pytest.mark.unit
def test_tool_versions_terragrunt_is_1x_not_0_66x(
    tool_versions: dict[str, str],
) -> None:
    """terragrunt must be pinned to the 1.x line (D35), not 0.66.x."""
    terragrunt_version = tool_versions.get("terragrunt", "")
    assert terragrunt_version == "1.0.7", (
        f".tool-versions pins terragrunt at {terragrunt_version!r}. "
        "The D35 requirement mandates the 1.x line; expected '1.0.7', not 0.66.x. "
        "Update .tool-versions to pin terragrunt 1.0.7."
    )


# ---------------------------------------------------------------------------
# provider.aws.module_types presence (AC-1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("module_type", ["primitive", "collection", "reference", "data"])
def test_provider_aws_module_types_complete(config: dict, module_type: str) -> None:
    """provider.aws.module_types must declare all four required module types."""
    module_types = config.get("provider", {}).get("aws", {}).get("module_types", {})
    assert module_type in module_types, (
        f"provider.aws.module_types is missing {module_type!r}. "
        "All four module types (primitive, collection, reference, data) are required."
    )
