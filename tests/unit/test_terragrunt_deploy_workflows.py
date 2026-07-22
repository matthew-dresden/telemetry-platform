"""Unit regression tests for .github/workflows/terragrunt-pr.yml and
.github/workflows/terragrunt-apply.yml.

These tests assert the structural constraints for the Terragrunt deploy
workflows defined in E7-F2-S1-T1 and updated in E8-F7-S1-T1. They validate:

- AC-1: terragrunt-pr.yml assumes vars.AWS_TERRAGRUNT_PLAN_ROLE_ARN (no environment),
        terragrunt-apply.yml uses environment: prod-apply + path-derived role resolution
- AC-2: Both workflows authenticate via AWS OIDC only; no PATs or static AWS keys
- AC-3: terragrunt-pr.yml runs steps in the exact canonical order;
        terragrunt-apply.yml runs tf-state-preflight first after unit detection;
        empty unit scope aborts (delegated to tg-detect-units)
- AC-4: No removed v0.x Terragrunt CLI token --terragrunt-include-dir appears
- AC-20 (E8-F7-S1-T1): deploy workflow resolves account/region/role from unit path and
        common/accounts.json -- no AWS_PROD_ACCOUNT_ID / AWS_QA_ACCOUNT_ID /
        AWS_TERRAGRUNT_APPLY_ROLE_ARN repo-variable coupling

All assertions use file-content grep / YAML-parse to validate the workflow
YAML without requiring a live GitHub Actions environment.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent

PR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terragrunt-pr.yml"
APPLY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "terragrunt-apply.yml"

# SHA40 pattern: exactly 40 hex chars
_SHA40 = re.compile(r"^[0-9a-fA-F]{40}$")

# Removed v0.x Terragrunt CLI tokens (D35 -- no-backport policy, AC-4)
REMOVED_V0X_TOKENS = [
    "--terragrunt-include-dir",
    "run-all",
    "hclfmt",
    "output-all",
    "validate-all",
    "plan-all",
    "apply-all",
]

# Canonical plan step order (AC-3)
PLAN_STEP_ORDER = [
    "make tg-detect-units",
    "make tf-state-preflight",
    "make tf-validate-dependency-paths",
    "make tf-guard-pinned-sources",
    "make tg-bucket-name-unique",
    "make tg-format-check",
    "make tg-security",
    "make tg-validate",
    "make tg-plan",
    "make tg-regression",
]


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pr_workflow_text() -> str:
    """Read terragrunt-pr.yml. Fails immediately if the file does not exist."""
    assert PR_WORKFLOW.exists(), (
        f"terragrunt-pr.yml not found at {PR_WORKFLOW}. "
        "The file must be created by this work unit (E7-F2-S1-T1 AC-1)."
    )
    return PR_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def apply_workflow_text() -> str:
    """Read terragrunt-apply.yml. Fails immediately if the file does not exist."""
    assert APPLY_WORKFLOW.exists(), (
        f"terragrunt-apply.yml not found at {APPLY_WORKFLOW}. "
        "The file must be created by this work unit (E7-F2-S1-T1 AC-1)."
    )
    return APPLY_WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pr_workflow_yaml(pr_workflow_text: str) -> dict:
    """Parse terragrunt-pr.yml as YAML."""
    return yaml.safe_load(pr_workflow_text)


@pytest.fixture(scope="module")
def apply_workflow_yaml(apply_workflow_text: str) -> dict:
    """Parse terragrunt-apply.yml as YAML."""
    return yaml.safe_load(apply_workflow_text)


# ---------------------------------------------------------------------------
# AC-1: trigger + role + environment constraints
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pr_workflow_triggers_on_pull_request(pr_workflow_yaml: dict) -> None:
    """terragrunt-pr.yml must trigger on pull_request events.

    Note: YAML parses the bare 'on:' key as the boolean True. GitHub Actions uses
    'on:' (unquoted) which becomes True in PyYAML. Both 'on' (str) and True (bool)
    are checked so the test is robust to both quoted and unquoted forms.
    """
    # YAML parses 'on:' (unquoted) as boolean True; '"on":' (quoted) as string "on"
    on_triggers = pr_workflow_yaml.get(True) or pr_workflow_yaml.get("on") or {}
    assert "pull_request" in on_triggers, (
        "terragrunt-pr.yml must declare 'pull_request' trigger (AC-1, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_supports_manual_dispatch(apply_workflow_yaml: dict) -> None:
    """terragrunt-apply.yml must support workflow_dispatch with an include_dir_flags input so the
    OIDC apply path can be fired on demand (proving it without a merge to main)."""
    on_triggers = apply_workflow_yaml.get(True) or apply_workflow_yaml.get("on") or {}
    assert "workflow_dispatch" in on_triggers, (
        "terragrunt-apply.yml must declare a 'workflow_dispatch' trigger for on-demand applies."
    )
    inputs = (on_triggers.get("workflow_dispatch") or {}).get("inputs") or {}
    assert "include_dir_flags" in inputs, (
        "workflow_dispatch must accept an 'include_dir_flags' input (the explicit unit scope, "
        "since a manual run has no push diff to detect units from)."
    )


@pytest.mark.unit
def test_pr_workflow_scoped_to_terragrunt_path(pr_workflow_text: str) -> None:
    """terragrunt-pr.yml pull_request trigger must include terragrunt/** path filter."""
    assert "terragrunt/**" in pr_workflow_text, (
        "terragrunt-pr.yml must scope its pull_request trigger to 'terragrunt/**' paths "
        "(AC-1, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_pr_workflow_assumes_per_account_plan_role(pr_workflow_text: str) -> None:
    """terragrunt-pr.yml must assume a PER-ACCOUNT plan role resolved into the matrix.

    Multi-account CI WALL 1: a cross-env PR can touch leaves in more than one account, and the
    root provider allowed_account_ids guard (D4) rejects one account's plan role for another
    account's units. The plan job is a matrix over accounts; the role-to-assume is the
    per-account plan role ARN (matrix.role_arn), resolved by scripts.partition_units_by_account
    from common/accounts.json[<account>].plan_role_name -- NOT a single repo variable. The old
    single-role coupling (vars.AWS_TERRAGRUNT_PLAN_ROLE_ARN) is removed.
    """
    assert "matrix.role_arn" in pr_workflow_text, (
        "terragrunt-pr.yml must assume the per-account plan role via 'matrix.role_arn' "
        "from the partition matrix (multi-account CI WALL 1)."
    )
    assert "AWS_TERRAGRUNT_PLAN_ROLE_ARN" not in pr_workflow_text, (
        "terragrunt-pr.yml must NOT couple to the single 'AWS_TERRAGRUNT_PLAN_ROLE_ARN' repo "
        "variable -- the plan role is resolved per account from common/accounts.json "
        "(plan_role_name) so a cross-env PR plans each account under its own plan role (WALL 1)."
    )


@pytest.mark.unit
def test_pr_workflow_partitions_units_by_account(pr_workflow_text: str) -> None:
    """terragrunt-pr.yml must build a per-account plan matrix via tg-partition-units MODE=plan."""
    assert "make tg-partition-units" in pr_workflow_text, (
        "terragrunt-pr.yml must call 'make tg-partition-units' to group the changed units by "
        "resolved AWS account into the per-account plan matrix (multi-account CI WALL 1)."
    )
    assert "MODE=plan" in pr_workflow_text, (
        "terragrunt-pr.yml partition step must run in plan mode (MODE=plan) so each account's "
        "plan role (plan_role_name) is selected (WALL 1)."
    )
    assert "fromJSON(needs.setup.outputs.matrix)" in pr_workflow_text, (
        "terragrunt-pr.yml terragrunt-plan job must consume the partition matrix via "
        "fromJSON(needs.setup.outputs.matrix) (WALL 1)."
    )


@pytest.mark.unit
def test_pr_workflow_has_no_environment(pr_workflow_text: str) -> None:
    """terragrunt-pr.yml must NOT declare any GitHub environment (read-only plan, AC-1)."""
    # Look for 'environment:' that is not in a comment and not inside 'prod-apply'
    # The PR workflow should have no 'environment:' key at all
    assert "environment:" not in pr_workflow_text, (
        "terragrunt-pr.yml must NOT declare 'environment:' -- the plan role "
        "requires no human gate (AC-1, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_triggers_on_push_to_main(apply_workflow_yaml: dict) -> None:
    """terragrunt-apply.yml must trigger on push to main.

    Note: YAML parses the bare 'on:' key as the boolean True. GitHub Actions uses
    'on:' (unquoted) which becomes True in PyYAML. Both 'on' (str) and True (bool)
    are checked so the test is robust to both quoted and unquoted forms.
    """
    on_triggers = apply_workflow_yaml.get(True) or apply_workflow_yaml.get("on") or {}
    assert "push" in on_triggers, (
        "terragrunt-apply.yml must declare 'push' trigger (AC-1, E7-F2-S1-T1)."
    )
    push_branches = on_triggers["push"].get("branches", [])
    assert "main" in push_branches, (
        "terragrunt-apply.yml push trigger must be scoped to branch 'main' (AC-1)."
    )


@pytest.mark.unit
def test_apply_workflow_scoped_to_terragrunt_path(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml push trigger must include terragrunt/** path filter."""
    assert "terragrunt/**" in apply_workflow_text, (
        "terragrunt-apply.yml must scope its push trigger to 'terragrunt/**' paths "
        "(AC-1, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_declares_prod_apply_environment(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must declare environment: prod-apply (human gate, AC-1)."""
    assert "environment: prod-apply" in apply_workflow_text, (
        "terragrunt-apply.yml must declare 'environment: prod-apply' so the apply "
        "OIDC role is only assumed after human approval (AC-1, D4, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_assumes_role_from_resolve_step(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must resolve the role ARN from the tg-resolve-deploy-role step
    (AC-1, E8-F7-S1-T1 AC-20) rather than a hardcoded AWS_TERRAGRUNT_APPLY_ROLE_ARN variable."""
    assert "steps.resolve-role.outputs.role_arn" in apply_workflow_text, (
        "terragrunt-apply.yml must use 'steps.resolve-role.outputs.role_arn' for "
        "role-to-assume -- the role must be resolved from common/accounts.json via "
        "the tg-resolve-deploy-role step (AC-1, E8-F7-S1-T1 AC-20)."
    )


@pytest.mark.unit
def test_apply_workflow_has_no_hardcoded_apply_role_arn_variable(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must NOT reference AWS_TERRAGRUNT_APPLY_ROLE_ARN repo variable
    (E8-F7-S1-T1 AC-FUNC-003 -- coupling removed, role resolved from unit path)."""
    assert "AWS_TERRAGRUNT_APPLY_ROLE_ARN" not in apply_workflow_text, (
        "terragrunt-apply.yml must not reference 'AWS_TERRAGRUNT_APPLY_ROLE_ARN' -- "
        "the apply role is now resolved from common/accounts.json[account_id].deploy_role_name "
        "via the tg-resolve-deploy-role step (E8-F7-S1-T1 AC-FUNC-003, AC-20)."
    )


# ---------------------------------------------------------------------------
# AC-2: OIDC-only authentication; no static keys
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "workflow_path,workflow_text_fixture",
    [
        (PR_WORKFLOW, "pr_workflow_text"),
        (APPLY_WORKFLOW, "apply_workflow_text"),
    ],
    ids=["pr-workflow", "apply-workflow"],
)
def test_workflow_uses_aws_configure_credentials_action(
    workflow_path: pathlib.Path,
    workflow_text_fixture: str,
    pr_workflow_text: str,
    apply_workflow_text: str,
) -> None:
    """Both workflows must reference aws-actions/configure-aws-credentials (OIDC, AC-2)."""
    text = pr_workflow_text if workflow_text_fixture == "pr_workflow_text" else apply_workflow_text
    assert "aws-actions/configure-aws-credentials" in text, (
        f"{workflow_path.name} must use aws-actions/configure-aws-credentials for OIDC "
        "authentication (AC-2, E7-F2-S1-T1)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "workflow_path,workflow_text_fixture",
    [
        (PR_WORKFLOW, "pr_workflow_text"),
        (APPLY_WORKFLOW, "apply_workflow_text"),
    ],
    ids=["pr-workflow", "apply-workflow"],
)
def test_workflow_has_no_static_aws_keys(
    workflow_path: pathlib.Path,
    workflow_text_fixture: str,
    pr_workflow_text: str,
    apply_workflow_text: str,
) -> None:
    """Both workflows must NOT reference aws-access-key-id or AWS_SECRET_ACCESS_KEY (AC-2)."""
    text = pr_workflow_text if workflow_text_fixture == "pr_workflow_text" else apply_workflow_text
    assert "aws-access-key-id" not in text, (
        f"{workflow_path.name} must not reference 'aws-access-key-id' -- "
        "only OIDC authentication is permitted (AC-2, E7-F2-S1-T1)."
    )
    assert "AWS_SECRET_ACCESS_KEY" not in text, (
        f"{workflow_path.name} must not reference 'AWS_SECRET_ACCESS_KEY' -- "
        "only OIDC authentication is permitted (AC-2, E7-F2-S1-T1)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "workflow_path,workflow_text_fixture",
    [
        (PR_WORKFLOW, "pr_workflow_text"),
        (APPLY_WORKFLOW, "apply_workflow_text"),
    ],
    ids=["pr-workflow", "apply-workflow"],
)
def test_workflow_third_party_actions_sha_pinned(
    workflow_path: pathlib.Path,
    workflow_text_fixture: str,
    pr_workflow_text: str,
    apply_workflow_text: str,
) -> None:
    """Every third-party action uses: must be pinned to a 40-char SHA (AC-2)."""
    text = pr_workflow_text if workflow_text_fixture == "pr_workflow_text" else apply_workflow_text
    uses_pattern = re.compile(r"uses:\s+(?P<ref>[^\s#]+)")
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = uses_pattern.search(line)
        if not match:
            continue
        ref = match.group("ref")
        if ref.startswith("./"):
            # First-party local action -- exempt from SHA-pin rule
            continue
        if "@" not in ref:
            pytest.fail(
                f"{workflow_path.name} line {line_no}: uses: '{ref}' has no '@' separator -- "
                "all third-party actions must be SHA-pinned (AC-2, E7-F2-S1-T1)."
            )
        _action, sha_part = ref.rsplit("@", 1)
        assert _SHA40.match(sha_part), (
            f"{workflow_path.name} line {line_no}: uses: '{ref}' is not pinned to a 40-char SHA "
            f"(got '{sha_part}') -- all third-party actions must be SHA-pinned (AC-2, E7-F2-S1-T1)."
        )
        # Must have a trailing version comment
        assert "#" in line, (
            f"{workflow_path.name} line {line_no}: SHA-pinned action '{ref}' is missing a "
            "trailing version comment (e.g. '# v4.1.0') (AC-2, E7-F2-S1-T1)."
        )


# ---------------------------------------------------------------------------
# AC-3: step ordering in terragrunt-pr.yml
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pr_workflow_step_order(pr_workflow_text: str) -> None:
    """terragrunt-pr.yml must run plan steps in the exact canonical order (AC-3).

    Order: tg-detect-units, tf-state-preflight, tf-validate-dependency-paths,
           tf-guard-pinned-sources, tg-bucket-name-unique, tg-format-check,
           tg-security, tg-validate, tg-plan, tg-regression.
    """
    positions = {}
    for step in PLAN_STEP_ORDER:
        idx = pr_workflow_text.find(step)
        assert idx >= 0, f"terragrunt-pr.yml must contain step '{step}' (AC-3, E7-F2-S1-T1)."
        positions[step] = idx

    for i in range(len(PLAN_STEP_ORDER) - 1):
        current = PLAN_STEP_ORDER[i]
        following = PLAN_STEP_ORDER[i + 1]
        assert positions[current] < positions[following], (
            f"terragrunt-pr.yml: step '{current}' must appear BEFORE '{following}' "
            "(canonical order AC-3, E7-F2-S1-T1)."
        )


@pytest.mark.unit
def test_apply_workflow_detect_units_before_preflight(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must run tg-detect-apply-units before tf-state-preflight (AC-3).

    The apply path uses tg-detect-apply-units (--exclude-bootstrap): bootstrap units are
    operator-applied out-of-band (D40/D33/D-15), so they are dropped from the CI apply scope and
    a bootstrap/common-only push is a clean no-op (has_units=false).
    """
    detect_pos = apply_workflow_text.find("make tg-detect-apply-units")
    preflight_pos = apply_workflow_text.find("make tf-state-preflight")
    apply_pos = apply_workflow_text.find("make tg-apply")

    assert detect_pos >= 0, (
        "terragrunt-apply.yml must contain 'make tg-detect-apply-units' (AC-3; bootstrap-excluded "
        "apply scope)."
    )
    assert preflight_pos >= 0, (
        "terragrunt-apply.yml must contain 'make tf-state-preflight' (AC-3, E7-F2-S1-T1)."
    )
    assert apply_pos >= 0, "terragrunt-apply.yml must contain 'make tg-apply' (AC-3, E7-F2-S1-T1)."
    assert detect_pos < preflight_pos, (
        "terragrunt-apply.yml: 'make tg-detect-apply-units' must precede 'make tf-state-preflight' "
        "(AC-3, E7-F2-S1-T1)."
    )
    assert preflight_pos < apply_pos, (
        "terragrunt-apply.yml: 'make tf-state-preflight' must precede 'make tg-apply' "
        "(AC-3, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_has_concurrency_no_cancel(apply_workflow_yaml: dict) -> None:
    """terragrunt-apply.yml must declare concurrency with cancel-in-progress: false (AC-3)."""
    concurrency = apply_workflow_yaml.get("concurrency", {})
    assert concurrency, (
        "terragrunt-apply.yml must declare a 'concurrency' group to prevent concurrent applies "
        "(AC-3, E7-F2-S1-T1)."
    )
    assert concurrency.get("cancel-in-progress") is False, (
        "terragrunt-apply.yml concurrency must set 'cancel-in-progress: false' so "
        "in-flight applies are never cancelled (AC-3, E7-F2-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-4: no removed v0.x Terragrunt CLI tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "workflow_path,workflow_text_fixture",
    [
        (PR_WORKFLOW, "pr_workflow_text"),
        (APPLY_WORKFLOW, "apply_workflow_text"),
    ],
    ids=["pr-workflow", "apply-workflow"],
)
@pytest.mark.parametrize("token", REMOVED_V0X_TOKENS, ids=REMOVED_V0X_TOKENS)
def test_workflow_no_removed_v0x_token(
    workflow_path: pathlib.Path,
    workflow_text_fixture: str,
    token: str,
    pr_workflow_text: str,
    apply_workflow_text: str,
) -> None:
    """Neither workflow may contain a removed v0.x Terragrunt CLI token (AC-4, D35)."""
    text = pr_workflow_text if workflow_text_fixture == "pr_workflow_text" else apply_workflow_text
    assert token not in text, (
        f"{workflow_path.name} must not contain removed v0.x CLI token '{token}' -- "
        "use the 1.0.7 surface only (AC-4, D35, E7-F2-S1-T1)."
    )


# ---------------------------------------------------------------------------
# AC-20 (E8-F7-S1-T1): path-based role resolution, no per-account repo-variable coupling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_apply_workflow_has_resolve_role_step(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must contain a resolve-role step (E8-F7-S1-T1 AC-20)."""
    assert "id: resolve-role" in apply_workflow_text, (
        "terragrunt-apply.yml must contain a step with 'id: resolve-role' that resolves "
        "the deploy role from common/accounts.json via the unit path (E8-F7-S1-T1 AC-20)."
    )


@pytest.mark.unit
def test_apply_workflow_resolve_role_calls_tg_resolve_deploy_role(
    apply_workflow_text: str,
) -> None:
    """The resolve-role step must call make tg-resolve-deploy-role (E8-F7-S1-T1 AC-20)."""
    assert "make tg-resolve-deploy-role" in apply_workflow_text, (
        "terragrunt-apply.yml resolve-role step must call 'make tg-resolve-deploy-role' "
        "(E8-F7-S1-T1 AC-20)."
    )


@pytest.mark.unit
def test_apply_workflow_uses_path_derived_region(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must use steps.resolve-role.outputs.aws_region (E8-F7-S1-T1 AC-20)."""
    assert "steps.resolve-role.outputs.aws_region" in apply_workflow_text, (
        "terragrunt-apply.yml must use 'steps.resolve-role.outputs.aws_region' for aws-region -- "
        "the region is derived from the unit path (E8-F7-S1-T1 AC-20)."
    )


@pytest.mark.unit
def test_apply_workflow_detect_units_before_resolve_role(apply_workflow_text: str) -> None:
    """tg-detect-apply-units must precede tg-resolve-deploy-role in terragrunt-apply.yml."""
    detect_pos = apply_workflow_text.find("make tg-detect-apply-units")
    resolve_pos = apply_workflow_text.find("make tg-resolve-deploy-role")

    assert detect_pos >= 0, (
        "terragrunt-apply.yml must contain 'make tg-detect-apply-units' (bootstrap-excluded scope)."
    )
    assert resolve_pos >= 0, (
        "terragrunt-apply.yml must contain 'make tg-resolve-deploy-role' (E8-F7-S1-T1 AC-20)."
    )
    assert detect_pos < resolve_pos, (
        "terragrunt-apply.yml: 'make tg-detect-apply-units' must precede "
        "'make tg-resolve-deploy-role' (E8-F7-S1-T1 AC-20)."
    )


@pytest.mark.unit
def test_apply_workflow_resolve_role_before_configure_credentials(
    apply_workflow_text: str,
) -> None:
    """tg-resolve-deploy-role must precede configure-aws-credentials (E8-F7-S1-T1 AC-20)."""
    resolve_pos = apply_workflow_text.find("make tg-resolve-deploy-role")
    creds_pos = apply_workflow_text.find("aws-actions/configure-aws-credentials")

    assert resolve_pos >= 0, (
        "terragrunt-apply.yml must contain 'make tg-resolve-deploy-role' (E8-F7-S1-T1 AC-20)."
    )
    assert creds_pos >= 0, (
        "terragrunt-apply.yml must contain 'aws-actions/configure-aws-credentials' (AC-2)."
    )
    assert resolve_pos < creds_pos, (
        "terragrunt-apply.yml: role resolution must precede AWS credential configuration "
        "(E8-F7-S1-T1 AC-20)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "workflow_path,workflow_text_fixture",
    [
        (PR_WORKFLOW, "pr_workflow_text"),
        (APPLY_WORKFLOW, "apply_workflow_text"),
    ],
    ids=["pr-workflow", "apply-workflow"],
)
def test_workflow_has_no_hardcoded_account_id_coupling(
    workflow_path: pathlib.Path,
    workflow_text_fixture: str,
    pr_workflow_text: str,
    apply_workflow_text: str,
) -> None:
    """Neither workflow may reference AWS_PROD_ACCOUNT_ID or AWS_QA_ACCOUNT_ID.

    E8-F7-S1-T1 AC-FUNC-003: account ids are resolved from the unit path.
    """
    text = pr_workflow_text if workflow_text_fixture == "pr_workflow_text" else apply_workflow_text
    assert "AWS_PROD_ACCOUNT_ID" not in text, (
        f"{workflow_path.name} must not reference 'AWS_PROD_ACCOUNT_ID' -- "
        "account ids are resolved from the unit path (E8-F7-S1-T1 AC-FUNC-003)."
    )
    assert "AWS_QA_ACCOUNT_ID" not in text, (
        f"{workflow_path.name} must not reference 'AWS_QA_ACCOUNT_ID' -- "
        "account ids are resolved from the unit path (E8-F7-S1-T1 AC-FUNC-003)."
    )


# ---------------------------------------------------------------------------
# Additional structural constraints (D11, D35 surface)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pr_workflow_has_id_token_write_permission(pr_workflow_yaml: dict) -> None:
    """terragrunt-pr.yml must grant id-token: write permission for OIDC (AC-2)."""
    permissions = pr_workflow_yaml.get("permissions", {})
    assert permissions.get("id-token") == "write", (
        "terragrunt-pr.yml must grant 'id-token: write' for OIDC authentication "
        "(AC-2, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_has_id_token_write_permission(apply_workflow_yaml: dict) -> None:
    """terragrunt-apply.yml must grant id-token: write permission for OIDC (AC-2)."""
    # permissions may be at top level or per-job
    permissions = apply_workflow_yaml.get("permissions", {})
    jobs = apply_workflow_yaml.get("jobs", {})
    # Check top-level permissions or the deploy job permissions
    has_permission = permissions.get("id-token") == "write"
    if not has_permission:
        for _job_name, job_def in jobs.items():
            job_perms = job_def.get("permissions", {})
            if job_perms.get("id-token") == "write":
                has_permission = True
                break
    assert has_permission, (
        "terragrunt-apply.yml must grant 'id-token: write' for OIDC authentication "
        "(AC-2, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_pr_workflow_has_contents_read_permission(pr_workflow_yaml: dict) -> None:
    """terragrunt-pr.yml must grant contents: read permission (least privilege)."""
    permissions = pr_workflow_yaml.get("permissions", {})
    assert permissions.get("contents") == "read", (
        "terragrunt-pr.yml must grant 'contents: read' (least privilege, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_apply_workflow_uses_setup_tools_composite(apply_workflow_text: str) -> None:
    """terragrunt-apply.yml must use the .github/actions/setup-tools composite action."""
    assert ".github/actions/setup-tools" in apply_workflow_text, (
        "terragrunt-apply.yml must use '.github/actions/setup-tools' to provision "
        "the pinned toolchain (D28, E7-F2-S1-T1)."
    )


@pytest.mark.unit
def test_pr_workflow_uses_setup_tools_composite(pr_workflow_text: str) -> None:
    """terragrunt-pr.yml must use the .github/actions/setup-tools composite action."""
    assert ".github/actions/setup-tools" in pr_workflow_text, (
        "terragrunt-pr.yml must use '.github/actions/setup-tools' to provision "
        "the pinned toolchain (D28, E7-F2-S1-T1)."
    )
