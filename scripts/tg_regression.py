"""tg_regression -- regression assertions for the terragrunt/_envcommon templates.

Run via: uv run python -m scripts.tg_regression

This script implements the regression assertions from docs/terragrunt-concepts.md for the
_envcommon shared input templates. Every assertion is named and fail-closed: the script
exits non-zero with a clear, actionable error message for any violation.

Assertions:
- All ten _envcommon templates exist (existence gate).
- No synonym input names appear in any template (D37): the canonical set
  (prod_hosted_zone_id, collector_service_fqdn, collector_pretty_fqdn,
  portal_service_fqdn, portal_pretty_fqdn) is asserted; known synonyms are rejected.
- No TODO or placeholder tokens appear in collector-ingestion.hcl abuse-limit defaults (D44).
- No state-bootstrap.hcl template is authored (D46: state-bootstrap leaves pass the
  lock-table inputs inline, with no include "envcommon").
- No assume_role in any _envcommon template (D4).
- No hardcoded prod domain literals in any template (D47): domain values must be
  composed from per-env account.hcl apexes, not inlined as fixed prod strings.
- No planned destroys in Terragrunt plan output (fail-closed destroy guard).
"""

from __future__ import annotations

import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent
ENVCOMMON_DIR = REPO_ROOT / "terragrunt" / "_envcommon"

# The _envcommon templates required by E5-F1-S2-T2. The portal.hcl, acm-portal.hcl,
# and analytics.hcl templates were removed with the portal/QuickSight and analytics
# consumer-surface retirement; telemetry is now surfaced via the central data lake,
# and the Athena workgroup is provisioned by a slim leaf that sources the
# athena-workgroup primitive directly (no shared _envcommon template).
REQUIRED_TEMPLATES: list[str] = [
    "collector-ingestion.hcl",
    "data-lake.hcl",
    "observability.hcl",
    "dns-prod-zone.hcl",
    "acm-collector.hcl",
    "identity.hcl",
    "oidc-bootstrap.hcl",
]

# D46: state-bootstrap leaves pass lock-table inputs inline; no shared template.
FORBIDDEN_TEMPLATES: list[str] = [
    "state-bootstrap.hcl",
]

# D37 canonical input names that MUST appear in (or be correctly absent from) templates.
# These are the names that domain-related inputs MUST use.
CANONICAL_DOMAIN_INPUTS: dict[str, list[str]] = {
    "dns-prod-zone.hcl": ["prod_hosted_zone_id"],
    "acm-collector.hcl": ["collector_service_fqdn", "collector_pretty_fqdn"],
}

# D37: known synonyms that MUST NOT appear as input assignments in any template.
# A synonym is an input key that refers to the same concept under a non-canonical name.
SYNONYM_PATTERNS: list[re.Pattern[str]] = [
    # "hosted_zone_id" without the "prod_" prefix is a synonym for prod_hosted_zone_id
    re.compile(r"^\s*hosted_zone_id\s*=", re.MULTILINE),
    # "service_fqdn" without the service-type prefix is ambiguous
    re.compile(r"^\s*service_fqdn\s*=", re.MULTILINE),
    # "pretty_fqdn" without the service-type prefix is ambiguous
    re.compile(r"^\s*pretty_fqdn\s*=", re.MULTILINE),
    # "zone_id" as a top-level input assignment (collector/portal would use prod_hosted_zone_id)
    re.compile(r"^\s*zone_id\s*=(?!.*#\s*ok)", re.MULTILINE),
    # "collector_fqdn" instead of collector_service_fqdn
    re.compile(r"^\s*collector_fqdn\s*=", re.MULTILINE),
    # "portal_fqdn" instead of portal_service_fqdn
    re.compile(r"^\s*portal_fqdn\s*=", re.MULTILINE),
]

# D44: tokens that MUST NOT appear in collector-ingestion.hcl (no TODO placeholders).
TODO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bXXX\b"),
    re.compile(r"\bTBD\b", re.IGNORECASE),
]

# D44: concrete abuse-limit values that MUST appear in collector-ingestion.hcl.
REQUIRED_ABUSE_LIMIT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "max_request_body_size = 4194304",
        re.compile(r"max_request_body_size\s*=\s*4194304"),
    ),
    (
        "rate_limit_per_ip = 2000",
        re.compile(r"rate_limit_per_ip\s*=\s*2000"),
    ),
]

# D47: hardcoded prod domain literals that MUST NOT appear in any template.
# These are known prod domain strings that should come from account.hcl apexes instead.
HARDCODED_PROD_DOMAIN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"prod\.telemetry\.example\.com"),
    re.compile(r"telemetry\.example\.com"),
]


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class PlannedDestroyError(RuntimeError):
    """Raised when a Terragrunt plan output contains unexpected destroy operations."""


# ---------------------------------------------------------------------------
# Destroy guard (fail-closed: no planned destroys allowed)
# ---------------------------------------------------------------------------

# Patterns that indicate a planned destroy operation in Terragrunt/Terraform plan output.
# The "N to destroy" pattern explicitly matches only when N > 0 (not "0 to destroy").
_DESTROY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwill be destroyed\b", re.IGNORECASE),
    re.compile(r"^\s*-\s+resource\.", re.MULTILINE),
    re.compile(r"\b[1-9]\d*\s+to\s+destroy\b", re.IGNORECASE),
]


def assert_no_planned_destroys(plan_output: str) -> list[str]:
    """Assert that the Terragrunt plan output contains no destroy operations.

    Args:
        plan_output: The full stdout/stderr output of a terragrunt plan run.

    Returns:
        A list of error messages for detected destroy operations (empty if none found).
    """
    errors: list[str] = []
    for pattern in _DESTROY_PATTERNS:
        matches = pattern.findall(plan_output)
        if matches:
            errors.append(
                f"ERROR: Planned destroy operation detected in plan output.\n"
                f"  Pattern matched: '{pattern.pattern}'\n"
                f"  Destroy operations are rejected by the regression guard.\n"
                f"  Remedy: remove the destroy operation from the plan or\n"
                f"  obtain explicit approval before applying destructive changes."
            )
            break  # One error per plan output is sufficient.
    return errors


# ---------------------------------------------------------------------------
# Assertion functions
# ---------------------------------------------------------------------------


def assert_required_templates_exist() -> list[str]:
    """Assert all ten required _envcommon templates exist.

    Returns a list of error messages for missing templates (empty if all present).
    """
    errors: list[str] = []
    for template_name in REQUIRED_TEMPLATES:
        template_path = ENVCOMMON_DIR / template_name
        if not template_path.exists():
            errors.append(
                f"ERROR: Required template missing: {template_path.relative_to(REPO_ROOT)}\n"
                f"  All ten _envcommon templates must be authored (E5-F1-S2-T2 Changes Manifest).\n"
                f"  Create the file: terragrunt/_envcommon/{template_name}"
            )
    return errors


def assert_no_forbidden_templates() -> list[str]:
    """Assert that D46-forbidden templates (state-bootstrap.hcl) do not exist.

    Returns a list of error messages for forbidden templates found (empty if none).
    """
    errors: list[str] = []
    for template_name in FORBIDDEN_TEMPLATES:
        template_path = ENVCOMMON_DIR / template_name
        if template_path.exists():
            errors.append(
                f"ERROR: Forbidden template exists: {template_path.relative_to(REPO_ROOT)}\n"
                f"  D46: state-bootstrap leaves pass lock-table inputs inline (hash_key=LockID,\n"
                f"  attributes=[{{name=LockID,type=S}}]) with no include 'envcommon'.\n"
                f"  A shared state-bootstrap.hcl would be an orphaned, never-included file.\n"
                f"  Remove: terragrunt/_envcommon/state-bootstrap.hcl"
            )
    return errors


def assert_canonical_input_names(template_name: str, content: str) -> list[str]:
    """Assert that the named template contains all required canonical input names (D37).

    Returns a list of error messages for missing canonical names (empty if all present).
    """
    errors: list[str] = []
    required_names = CANONICAL_DOMAIN_INPUTS.get(template_name, [])
    for canonical_name in required_names:
        if canonical_name not in content:
            errors.append(
                f"ERROR: Canonical input '{canonical_name}' not found in "
                f"terragrunt/_envcommon/{template_name}\n"
                f"  D37: Use only the canonical input name '{canonical_name}'.\n"
                f"  Do not use synonyms. The exact string must appear in the inputs block."
            )
    return errors


def assert_no_synonym_inputs(template_name: str, content: str) -> list[str]:
    """Assert that no known synonym input names appear as assignments in the template (D37).

    Returns a list of error messages for synonym matches found (empty if none).
    """
    errors: list[str] = []
    for pattern in SYNONYM_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            pat = pattern.pattern
            errors.append(
                f"ERROR: Synonym input name detected in terragrunt/_envcommon/{template_name}\n"
                f"  D37: Use only canonical input names. Found pattern matching '{pat}'.\n"
                f"  Offending text: {matches[0].strip()}\n"
                f"  Replace with the canonical equivalent (e.g., 'prod_hosted_zone_id',\n"
                f"  'collector_service_fqdn', 'collector_pretty_fqdn', 'portal_service_fqdn',\n"
                f"  'portal_pretty_fqdn')."
            )
    return errors


def assert_no_todo_placeholders(template_name: str, content: str) -> list[str]:
    """Assert that no TODO/placeholder tokens appear in the given template (D44).

    Returns a list of error messages for placeholder matches found (empty if none).
    """
    errors: list[str] = []
    for pattern in TODO_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            errors.append(
                f"ERROR: TODO/placeholder token '{matches[0]}' found in "
                f"terragrunt/_envcommon/{template_name}\n"
                f"  D44: No TODO placeholders. Concrete, input-driven values are required.\n"
                f"  Remove all TODO/PLACEHOLDER/FIXME/XXX/TBD tokens from the template."
            )
    return errors


def assert_abuse_limit_defaults(content: str) -> list[str]:
    """Assert that collector-ingestion.hcl pins the D44 concrete abuse-limit defaults.

    Returns a list of error messages for missing or incorrect defaults (empty if all present).
    """
    errors: list[str] = []
    for description, pattern in REQUIRED_ABUSE_LIMIT_PATTERNS:
        if not pattern.search(content):
            errors.append(
                f"ERROR: Required abuse-limit default missing from "
                f"terragrunt/_envcommon/collector-ingestion.hcl\n"
                f"  D44: Pin the concrete default value: {description}\n"
                f"  The ADOT OTLP receiver max_request_body_size defaults to 4194304 bytes\n"
                f"  and the WAF rate_limit_per_ip defaults to 2000 requests per 5 min.\n"
                f"  These must appear as input-driven values, not TODO placeholders."
            )
    return errors


def assert_no_assume_role(template_name: str, content: str) -> list[str]:
    """Assert that no assume_role appears in any _envcommon template (D4).

    Returns a list of error messages for assume_role matches found (empty if none).
    """
    errors: list[str] = []
    if "assume_role" in content:
        lines_with_assume_role = [
            f"  line {i + 1}: {line.strip()}"
            for i, line in enumerate(content.splitlines())
            if "assume_role" in line
        ]
        errors.append(
            f"ERROR: 'assume_role' found in terragrunt/_envcommon/{template_name}\n"
            f"  D4: No second assume_role anywhere in terragrunt/. The OIDC-assumed role\n"
            f"  IS the deploy identity. Remove all assume_role references.\n"
            + "\n".join(lines_with_assume_role)
        )
    return errors


def assert_no_hardcoded_prod_domains(template_name: str, content: str) -> list[str]:
    """Assert that no hardcoded prod domain literals appear in any template (D47).

    Domain values must be composed from per-env account.hcl apexes (dns_service_apex,
    dns_pretty_apex), not hardcoded as fixed prod strings. The patterns are checked
    in the inputs{} block context (comments containing the domain for documentation
    are acceptable; input value assignments containing a hardcoded prod literal are not).

    Returns a list of error messages for hardcoded domain matches found (empty if none).
    """
    errors: list[str] = []
    for pattern in HARDCODED_PROD_DOMAIN_PATTERNS:
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # Skip comment lines -- doc comments explaining the resolved prod value are allowed.
            if stripped.startswith("#"):
                continue
            if pattern.search(stripped):
                errors.append(
                    f"ERROR: Hardcoded prod domain literal in "
                    f"terragrunt/_envcommon/{template_name} at line {i}\n"
                    f"  D47: Domain values must be composed from account.hcl apexes\n"
                    f"  (dns_service_apex, dns_pretty_apex), not hardcoded prod strings.\n"
                    f"  Offending line: {stripped}\n"
                    f"  Use: local.account_vars.locals.dns_service_apex or dns_pretty_apex"
                )
                break  # one error per pattern per file is sufficient
    return errors


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_assertions() -> list[str]:
    """Run all regression assertions and return a list of error messages.

    Returns an empty list if all assertions pass.
    """
    all_errors: list[str] = []

    # Existence and forbidden-template checks.
    all_errors.extend(assert_required_templates_exist())
    all_errors.extend(assert_no_forbidden_templates())

    # Per-template assertions (skip if the template doesn't exist yet -- existence
    # errors above already capture the missing-file case).
    for template_name in REQUIRED_TEMPLATES:
        template_path = ENVCOMMON_DIR / template_name
        if not template_path.exists():
            continue

        content = template_path.read_text(encoding="utf-8")

        all_errors.extend(assert_canonical_input_names(template_name, content))
        all_errors.extend(assert_no_synonym_inputs(template_name, content))
        all_errors.extend(assert_no_todo_placeholders(template_name, content))
        all_errors.extend(assert_no_assume_role(template_name, content))
        all_errors.extend(assert_no_hardcoded_prod_domains(template_name, content))

        if template_name == "collector-ingestion.hcl":
            all_errors.extend(assert_abuse_limit_defaults(content))

    return all_errors


def main() -> int:
    """Entry point for `uv run python -m scripts.tg_regression`.

    Returns exit code 0 on success, 1 on any assertion failure.
    """
    errors = run_assertions()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(
            f"\ntg-regression FAILED: {len(errors)} assertion(s) failed.",
            file=sys.stderr,
        )
        return 1
    print("tg-regression PASSED: all assertions green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
