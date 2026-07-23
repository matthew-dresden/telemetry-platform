"""Unit tests for the GitHub repo-settings contract (FR-8, spec section 4.8, AC #29).

These tests assert the repo Actions variables + environments contract for
matthew-dresden/telemetry-platform against a committed gh JSON fixture
(tests/unit/fixtures/repo_settings_gh.json) so the repo-settings contract is
regression-checked offline (spec section 4.8/10, E10-F2-S3-T1).

The fixture is captured from real gh output:
- variables: `gh variable list --repo matthew-dresden/telemetry-platform --json name,value`
- environments: `gh api repos/matthew-dresden/telemetry-platform/environments/<name>`
  (only the protection-rule type strings are retained, which is sufficient to assert
  existence and the prod-apply required_reviewers rule, and avoids storing any
  reviewer login).

Assertions (per the unit Approach / Acceptance Criteria):
- Exactly the four expected variables exist with non-empty values (AC-3).
- Both environments terratest-approval and prod-apply exist (AC-1, AC-2).
- prod-apply carries a required_reviewers protection rule (AC-1, spec 4.8).
- The superseded AWS_PROD_ACCOUNT_ID and AWS_QA_ACCOUNT_ID are ABSENT
  (doc-03 supersession, spec section 4.8).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "unit" / "fixtures" / "repo_settings_gh.json"

# The four repo Actions variables that MUST exist non-empty (spec section 4.8, AC-3).
EXPECTED_VARIABLES: frozenset[str] = frozenset(
    {
        "AWS_DEFAULT_REGION",
        "AWS_QA_TERRATEST_ROLE_ARN",
        "AWS_TERRAGRUNT_PLAN_ROLE_ARN",
        "LOCK_MAX_AGE_MINUTES",
    }
)

# The two environments that MUST exist (spec section 4.8, AC-1, AC-2).
EXPECTED_ENVIRONMENTS: frozenset[str] = frozenset({"terratest-approval", "prod-apply"})

# The environment that MUST carry a required_reviewers protection rule (spec 4.8, AC-1).
PROD_APPLY_ENVIRONMENT = "prod-apply"
REQUIRED_REVIEWERS_RULE = "required_reviewers"

# The doc-03 account-id variables that are superseded and MUST be absent (spec section 4.8).
# The apply role is path-resolved via terragrunt/common/accounts.json, so these are removed.
SUPERSEDED_VARIABLES: frozenset[str] = frozenset({"AWS_PROD_ACCOUNT_ID", "AWS_QA_ACCOUNT_ID"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(path: pathlib.Path) -> dict[str, Any]:
    """Load and return the repo-settings gh fixture as a typed dict.

    Fails fast with FileNotFoundError if the fixture is not committed, and with
    TypeError if the root is not a JSON object.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"repo-settings gh fixture not found at {path}. "
            "Capture it from real gh output (`gh variable list` + "
            "`gh api repos/<repo>/environments/<name>`) as part of E10-F2-S3-T1."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(
            f"repo_settings_gh.json must contain a JSON object at the root, "
            f"got {type(data).__name__}. "
            "It must be a {{...}} object with 'variables' and 'environments' keys."
        )
    return data


def _variables(data: dict[str, Any]) -> dict[str, Any]:
    """Return the 'variables' name->value map from the fixture, failing fast if absent."""
    variables = data.get("variables")
    if not isinstance(variables, dict):
        raise TypeError(
            f"repo_settings_gh.json 'variables' must be a JSON object mapping variable "
            f"name to value, got {type(variables).__name__}."
        )
    return variables


def _environments(data: dict[str, Any]) -> dict[str, Any]:
    """Return the 'environments' name->entry map from the fixture, failing fast if absent."""
    environments = data.get("environments")
    if not isinstance(environments, dict):
        raise TypeError(
            f"repo_settings_gh.json 'environments' must be a JSON object mapping "
            f"environment name to its entry, got {type(environments).__name__}."
        )
    return environments


# ---------------------------------------------------------------------------
# Fixture-presence test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_repo_settings_fixture_is_committed() -> None:
    """The committed gh fixture must exist and parse as a JSON object (AC-4)."""
    data = _load_fixture(FIXTURE_PATH)
    assert isinstance(data, dict), (
        f"repo_settings_gh.json root must be a JSON object ({{...}}), got {type(data).__name__}."
    )


# ---------------------------------------------------------------------------
# Variable tests (AC-3, spec section 4.8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exactly_the_expected_variables_exist() -> None:
    """The fixture must contain exactly the four expected variables, no more, no fewer (AC-3)."""
    variables = _variables(_load_fixture(FIXTURE_PATH))
    actual = frozenset(variables.keys())
    missing = EXPECTED_VARIABLES - actual
    unexpected = actual - EXPECTED_VARIABLES
    assert not missing and not unexpected, (
        f"repo Actions variables do not match the expected set (spec section 4.8, AC-3). "
        f"Missing: {sorted(missing)}; unexpected: {sorted(unexpected)}. "
        f"Expected exactly: {sorted(EXPECTED_VARIABLES)}."
    )


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(EXPECTED_VARIABLES), ids=sorted(EXPECTED_VARIABLES))
def test_each_expected_variable_has_non_empty_value(name: str) -> None:
    """Each of the six expected variables must exist with a non-empty value (AC-3).

    The CI workflows fail fast on empty vars by design, so an empty value is a
    contract violation (spec section 4.8).
    """
    variables = _variables(_load_fixture(FIXTURE_PATH))
    assert name in variables, (
        f"Required repo Actions variable '{name}' is missing from the gh fixture "
        f"(spec section 4.8, AC-3)."
    )
    value = variables[name]
    assert isinstance(value, str) and value.strip() != "", (
        f"Repo Actions variable '{name}' must have a non-empty string value, got {value!r}. "
        "The CI workflows fail fast on empty vars by design (spec section 4.8, AC-3)."
    )


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(SUPERSEDED_VARIABLES), ids=sorted(SUPERSEDED_VARIABLES))
def test_superseded_account_id_variables_are_absent(name: str) -> None:
    """The doc-03 account-id variables must NOT be set (superseded, spec section 4.8).

    AWS_PROD_ACCOUNT_ID and AWS_QA_ACCOUNT_ID are superseded because the apply role is
    path-resolved via terragrunt/common/accounts.json; their presence is a regression.
    """
    variables = _variables(_load_fixture(FIXTURE_PATH))
    assert name not in variables, (
        f"Superseded variable '{name}' must NOT be set on the repo (doc-03 supersession, "
        f"spec section 4.8). The apply role is path-resolved via "
        "terragrunt/common/accounts.json. Remove it with "
        f"`gh variable delete {name} --repo matthew-dresden/telemetry-platform`."
    )


# ---------------------------------------------------------------------------
# Environment tests (AC-1, AC-2, spec section 4.8)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "environment_name", sorted(EXPECTED_ENVIRONMENTS), ids=sorted(EXPECTED_ENVIRONMENTS)
)
def test_expected_environment_exists(environment_name: str) -> None:
    """Both terratest-approval and prod-apply environments must exist (AC-1, AC-2)."""
    environments = _environments(_load_fixture(FIXTURE_PATH))
    assert environment_name in environments, (
        f"Required GitHub environment '{environment_name}' is missing from the gh fixture "
        f"(spec section 4.8). Expected environments: {sorted(EXPECTED_ENVIRONMENTS)}."
    )


@pytest.mark.unit
def test_prod_apply_environment_has_required_reviewers_rule() -> None:
    """The prod-apply environment must carry a required_reviewers protection rule (AC-1).

    Without a required-reviewers rule, the prod apply gate is unguarded, which is a
    contract failure (spec section 4.8, AC #29).
    """
    environments = _environments(_load_fixture(FIXTURE_PATH))
    assert PROD_APPLY_ENVIRONMENT in environments, (
        f"Environment '{PROD_APPLY_ENVIRONMENT}' is missing from the gh fixture "
        f"(spec section 4.8, AC-1)."
    )
    entry = environments[PROD_APPLY_ENVIRONMENT]
    assert isinstance(entry, dict), (
        f"Environment '{PROD_APPLY_ENVIRONMENT}' entry must be a JSON object, "
        f"got {type(entry).__name__}."
    )
    rule_types = entry.get("protection_rule_types", [])
    assert isinstance(rule_types, list), (
        f"Environment '{PROD_APPLY_ENVIRONMENT}' protection_rule_types must be a list, "
        f"got {type(rule_types).__name__}."
    )
    assert REQUIRED_REVIEWERS_RULE in rule_types, (
        f"Environment '{PROD_APPLY_ENVIRONMENT}' must carry a '{REQUIRED_REVIEWERS_RULE}' "
        f"protection rule (spec section 4.8, AC-1, AC #29), found rules: {rule_types}. "
        "Configure required reviewers via the GitHub environment protection rules."
    )
